"""
Alert inbox: machine fault events and predictive early warnings.

Fault events are scripted (the prototype has no live PLC/MES feed); in production they would
arrive from the plant's event stream (e.g. Pub/Sub). Each alert carries a ready-made analysis
prompt, so opening it runs the full decision pipeline with no typing.

Demo faults belong to the viewer who raised them (browser id from the `X-FDE-Viewer` header),
so judges testing the live app at the same time never see each other's simulated faults.
"""
import re
import threading
from collections import OrderedDict
from contextvars import ContextVar
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.config import config
from app.domains import current_domain, get_pack

_viewer: ContextVar[str] = ContextVar("fde_viewer", default="shared")
MAX_VIEWERS = 1000  # oldest viewers' demo faults are dropped beyond this

# Fired alerts and the next scripted fault, per (industry pack, viewer)
_lock = threading.Lock()
_fired: "OrderedDict[Tuple[str, str], List[Dict[str, Any]]]" = OrderedDict()
_next_index: Dict[Tuple[str, str], int] = {}


def set_viewer(viewer_id: Optional[str]):
    """Set the viewer for this request; anything that isn't a short plain id counts as 'shared'."""
    v = (viewer_id or "").strip()
    return _viewer.set(v if re.fullmatch(r"[A-Za-z0-9-]{1,64}", v) else "shared")


def reset_viewer(token) -> None:
    _viewer.reset(token)


def _key() -> Tuple[str, str]:
    return current_domain(), _viewer.get()


def _engine_estimate(machine_id: str, repair_hours) -> Dict[str, Any]:
    """Impact and best plan from the deterministic engine, sized to the middle of the repair estimate.

    Uses the same priorities as the alert's analysis prompt (deliveries first, then cost), so the
    numbers on the alert match what the full analysis shows when the alert is opened.
    """
    from app.agent.orchestrator import DecisionOrchestrator

    lo, hi = repair_hours
    downtime = round((lo + hi) / 2, 1)
    try:
        decision = DecisionOrchestrator().orchestrate(machine_id=machine_id, downtime_hours=downtime, priorities="delivery cost")
    except Exception:
        return {"downtime_hours": downtime}
    imp = decision.impact_summary
    sp = decision.selected_plan
    return {
        "downtime_hours": downtime,
        "line_id": imp.get("line_id") or decision.dependency_chain.get("line_id"),
        "station_id": decision.dependency_chain.get("station_id"),
        "affected_orders_count": imp.get("affected_order_count"),
        "high_priority_orders_count": imp.get("high_priority_affected_count"),
        "loss_estimate_units": imp.get("capacity_loss_units"),
        "recommended_action": sp.description if sp else "No feasible plan; escalate to the supervisor.",
    }


def _fault_alert(fault: Dict[str, Any], seq: int) -> Dict[str, Any]:
    lo, hi = fault["repair_hours"]
    est = _engine_estimate(fault["machine_id"], (lo, hi))
    prompt = (
        f"{fault['machine_id']} fault {fault['code']}: {fault['title'].lower()}. {fault['detail']} "
        f"Maintenance estimates {lo} to {hi} hours to repair. Protect high-priority deliveries while keeping cost low."
    )
    return {
        "alert_id": f"ALT-F{seq:03d}",
        "kind": "fault",
        "machine_id": fault["machine_id"],
        "line_id": est.get("line_id"),
        "station_id": est.get("station_id"),
        "severity": fault["severity"],
        "title": f"{fault['machine_id']} fault {fault['code']}: {fault['title']}",
        "detail": fault["detail"],
        "estimated_repair_hours": [lo, hi],
        "raised_at": (config.PLANT_CLOCK + timedelta(minutes=5 * seq)).isoformat(),
        "affected_orders_count": est.get("affected_orders_count"),
        "high_priority_orders_count": est.get("high_priority_orders_count"),
        "loss_estimate_units": est.get("loss_estimate_units"),
        "recommended_action": est.get("recommended_action"),
        "analysis_prompt": prompt,
        "scenario_payload": {"machine_id": fault["machine_id"], "downtime_hours": est["downtime_hours"], "prompt": prompt},
    }


def simulate_fault() -> Dict[str, Any]:
    """Raise the next scripted fault of the active industry pack (demo trigger for a machine event)."""
    key = _key()
    faults = get_pack()["faults"]
    with _lock:
        seq = _next_index.get(key, 0)
        _next_index[key] = seq + 1
        alert = _fault_alert(faults[seq % len(faults)], seq + 1)
        # A machine that is already reported down appears once: the newest report replaces the old one
        fired = [x for x in _fired.get(key, []) if x["machine_id"] != alert["machine_id"]]
        _fired[key] = [alert] + fired
        _fired.move_to_end(key)
        while len(_fired) > MAX_VIEWERS:
            old, _ = _fired.popitem(last=False)
            _next_index.pop(old, None)
        return alert


def clear_faults() -> int:
    """Remove this viewer's demo faults (the next simulated fault starts from the first again)."""
    key = _key()
    with _lock:
        removed = len(_fired.pop(key, []))
        _next_index.pop(key, None)
        return removed


def resolve_fault(alert_id: str, machine_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """A fault whose recovery plan was approved leaves the inbox. Predictions stay (they come
    from the sensor trend). Returns the resolved alert, or None if nothing matched."""
    key = _key()
    with _lock:
        fired = _fired.get(key, [])
        match = next((a for a in fired if a["alert_id"] == alert_id and a["machine_id"] == machine_id), None)
        if match:
            _fired[key] = [a for a in fired if a is not match]
        return match


def reset_alerts() -> None:
    with _lock:
        _fired.clear()
        _next_index.clear()


def predictive_alerts(watch_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Machines predicted to reach an alarm soon, with the engine's pre-built backup plan."""
    alerts = []
    for a in watch_list:
        if a["risk_level"] not in ("CRITICAL", "HIGH"):
            continue
        plan = a.get("prebuilt_plan") or {}
        lo, hi = a["typical_repair_hours"]
        prompt = plan.get("analysis_prompt") or (
            f"{a['machine_id']} is at risk of failure: its sensor trend is heading for the alarm level. "
            f"If it fails, repair typically takes {lo:g} to {hi:g} hours. Protect high-priority deliveries while keeping cost low."
        )
        alerts.append({
            "alert_id": f"ALT-P-{a['machine_id']}",
            "kind": "predictive",
            "machine_id": a["machine_id"],
            "line_id": a.get("line_id"),
            "station_id": a.get("station_id"),
            "severity": a["risk_level"],
            "title": f"{a['machine_id']} predicted to reach alarm in ~{round(a['hours_to_alarm'])} h",
            "detail": a["explanation"],
            "hours_to_alarm": a["hours_to_alarm"],
            "hours_to_alarm_range": a.get("hours_to_alarm_range"),
            "estimated_repair_hours": a["typical_repair_hours"],
            "raised_at": config.PLANT_CLOCK.isoformat(),
            # Exposure if it fails, from the engine's pre-built plan (not every order on the line)
            "affected_orders_count": plan.get("orders_at_risk"),
            "high_priority_orders_count": plan.get("high_priority_at_risk"),
            "loss_estimate_units": plan.get("capacity_loss_units"),
            "recommended_action": plan.get("description"),
            "prebuilt_plan": plan or None,
            "analysis_prompt": prompt,
            "scenario_payload": {"machine_id": a["machine_id"], "downtime_hours": plan.get("assumed_downtime_hours") or round((lo + hi) / 2, 1), "prompt": prompt},
        })
    return alerts


_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def list_alerts(watch_list: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    with _lock:
        fired = list(_fired.get(_key(), []))
    # Python's sort is stable, so faults of equal severity keep their newest-first order
    fired.sort(key=lambda a: _SEVERITY_ORDER.get(a["severity"], len(_SEVERITY_ORDER)))
    combined = fired + predictive_alerts(watch_list or [])
    # Assign explicit 1-indexed priority ranking based on order of operational urgency
    for idx, alert in enumerate(combined):
        alert["priority_rank"] = idx + 1
    return combined
