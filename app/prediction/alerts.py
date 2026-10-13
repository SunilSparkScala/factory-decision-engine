"""
Alert inbox: machine fault events and predictive early warnings.

Fault events are scripted (the prototype has no live PLC/MES feed); in production they would
arrive from the plant's event stream (e.g. Pub/Sub). Each alert carries a ready-made analysis
prompt, so opening it runs the full decision pipeline with no typing.
"""
import threading
from datetime import timedelta
from typing import Any, Dict, List, Optional

from app.config import config
from app.domains import current_domain, get_pack


# Fired alerts and the next scripted fault, kept separately for each industry pack
_lock = threading.Lock()
_fired: Dict[str, List[Dict[str, Any]]] = {}
_next_index: Dict[str, int] = {}


def _fault_alert(fault: Dict[str, Any], seq: int) -> Dict[str, Any]:
    lo, hi = fault["repair_hours"]
    mid = fault["machine_id"]
    line_id = None
    station_id = None
    hourly_cap = 25.0
    affected_count = 0
    high_pri_count = 0
    try:
        from app.factory.state import get_machine, get_factory_state
        m = get_machine(mid)
        line_id = m.line_id
        station_id = m.station_id
        hourly_cap = m.capacity_per_hour
        state = get_factory_state()
        line_orders = [o for o in state.production_orders if o.assigned_line == line_id]
        affected_count = len(line_orders)
        high_pri_count = sum(1 for o in line_orders if o.priority.value == "HIGH")
    except Exception:
        pass

    prompt = (
        f"{fault['machine_id']} fault {fault['code']}: {fault['title'].lower()}. {fault['detail']} "
        f"Maintenance estimates {lo} to {hi} hours to repair. Protect high-priority deliveries."
    )
    downtime_mid = round((lo + hi) / 2, 1)

    return {
        "alert_id": f"ALT-F{seq:03d}",
        "kind": "fault",
        "machine_id": fault["machine_id"],
        "line_id": line_id,
        "station_id": station_id,
        "severity": fault["severity"],
        "title": f"{fault['machine_id']} fault {fault['code']}: {fault['title']}",
        "detail": fault["detail"],
        "estimated_repair_hours": [lo, hi],
        "raised_at": (config.PLANT_CLOCK + timedelta(minutes=5 * seq)).isoformat(),
        "affected_orders_count": affected_count,
        "high_priority_orders_count": high_pri_count,
        "loss_estimate_units": round(hourly_cap * lo, 1),
        "recommended_action": "Isolate unit and transfer queue to parallel station in Decision Studio",
        "analysis_prompt": prompt,
        "scenario_payload": {
            "machine_id": fault["machine_id"],
            "downtime_hours": downtime_mid,
            "prompt": prompt,
        },
    }


def simulate_fault() -> Dict[str, Any]:
    """Raise the next scripted fault of the active industry pack (demo trigger for a machine event)."""
    domain = current_domain()
    faults = get_pack()["faults"]
    with _lock:
        seq = _next_index.get(domain, 0)
        _next_index[domain] = seq + 1
        alert = _fault_alert(faults[seq % len(faults)], seq + 1)
        _fired.setdefault(domain, []).insert(0, alert)
        return alert


def reset_alerts() -> None:
    with _lock:
        _fired.clear()
        _next_index.clear()


def predictive_alerts(watch_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    alerts = []
    from app.factory.state import get_factory_state
    try:
        state = get_factory_state()
        all_orders = state.production_orders
    except Exception:
        all_orders = []

    for a in watch_list:
        if a["risk_level"] not in ("CRITICAL", "HIGH"):
            continue
        plan = a.get("prebuilt_plan") or {}
        line_id = a.get("line_id")
        line_orders = [o for o in all_orders if o.assigned_line == line_id] if line_id else []
        lo, hi = a["typical_repair_hours"]
        prompt = plan.get("analysis_prompt") or (
            f"{a['machine_id']} is projected to reach alarm in ~{round(a['hours_to_alarm'])} hours. "
            f"Estimated repair duration is {lo} to {hi} hours. Protect deliveries while minimizing cost."
        )
        downtime_mid = round((lo + hi) / 2, 1)

        alerts.append({
            "alert_id": f"ALT-P-{a['machine_id']}",
            "kind": "predictive",
            "machine_id": a["machine_id"],
            "line_id": line_id,
            "station_id": a.get("station_id"),
            "severity": a["risk_level"],
            "title": f"{a['machine_id']} predicted to reach alarm in ~{round(a['hours_to_alarm'])} h",
            "detail": a["explanation"],
            "estimated_repair_hours": a["typical_repair_hours"],
            "raised_at": config.PLANT_CLOCK.isoformat(),
            "affected_orders_count": len(line_orders),
            "high_priority_orders_count": sum(1 for o in line_orders if o.priority.value == "HIGH"),
            "loss_estimate_units": plan.get("orders_at_risk") or 0,
            "recommended_action": plan.get("description") or "Pre-emptive maintenance transfer before alarm",
            "prebuilt_plan": plan or None,
            "analysis_prompt": prompt,
            "scenario_payload": {
                "machine_id": a["machine_id"],
                "downtime_hours": downtime_mid,
                "prompt": prompt,
            },
        })
    return alerts


def list_alerts(watch_list: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    with _lock:
        fired = list(_fired.get(current_domain(), []))
    combined = fired + predictive_alerts(watch_list or [])
    # Assign explicit 1-indexed priority ranking based on order of operational urgency
    for idx, alert in enumerate(combined):
        alert["priority_rank"] = idx + 1
    return combined
