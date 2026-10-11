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
    return {
        "alert_id": f"ALT-F{seq:03d}",
        "kind": "fault",
        "machine_id": fault["machine_id"],
        "severity": fault["severity"],
        "title": f"{fault['machine_id']} fault {fault['code']}: {fault['title']}",
        "detail": fault["detail"],
        "estimated_repair_hours": [lo, hi],
        "raised_at": (config.PLANT_CLOCK + timedelta(minutes=5 * seq)).isoformat(),
        "analysis_prompt": (
            f"{fault['machine_id']} fault {fault['code']}: {fault['title'].lower()}. {fault['detail']} "
            f"Maintenance estimates {lo} to {hi} hours to repair. Protect high-priority deliveries."
        ),
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
    for a in watch_list:
        if a["risk_level"] not in ("CRITICAL", "HIGH"):
            continue
        plan = a.get("prebuilt_plan") or {}
        alerts.append({
            "alert_id": f"ALT-P-{a['machine_id']}",
            "kind": "predictive",
            "machine_id": a["machine_id"],
            "severity": a["risk_level"],
            "title": f"{a['machine_id']} predicted to reach alarm in ~{round(a['hours_to_alarm'])} h",
            "detail": a["explanation"],
            "estimated_repair_hours": a["typical_repair_hours"],
            "raised_at": config.PLANT_CLOCK.isoformat(),
            "prebuilt_plan": plan or None,
            "analysis_prompt": plan.get("analysis_prompt"),
        })
    return alerts


def list_alerts(watch_list: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    with _lock:
        fired = list(_fired.get(current_domain(), []))
    return fired + predictive_alerts(watch_list or [])
