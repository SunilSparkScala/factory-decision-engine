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

SCRIPTED_FAULTS: List[Dict[str, Any]] = [
    {
        "machine_id": "M17", "code": "E-42", "title": "Welding arm jam",
        "detail": "Robot arm stopped mid-cycle on Station B2; servo overcurrent detected.",
        "repair_hours": [6, 10], "severity": "HIGH",
    },
    {
        "machine_id": "M05", "code": "W-17", "title": "Wire feed fault",
        "detail": "Welding wire feed motor stalled on Station A2.",
        "repair_hours": [2, 4], "severity": "MEDIUM",
    },
    {
        "machine_id": "M31", "code": "T-08", "title": "Torque tool failure",
        "detail": "Nutrunner torque out of tolerance on Station C3; tool locked out.",
        "repair_hours": [4, 8], "severity": "HIGH",
    },
    {
        "machine_id": "M20", "code": "P-23", "title": "Paint booth humidity out of range",
        "detail": "Booth humidity above process window on Station B3; line held for quality.",
        "repair_hours": [3, 5], "severity": "MEDIUM",
    },
]

_lock = threading.Lock()
_fired: List[Dict[str, Any]] = []
_next_index = 0


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
    """Raise the next scripted fault (demo trigger for an incoming machine event)."""
    global _next_index
    with _lock:
        fault = SCRIPTED_FAULTS[_next_index % len(SCRIPTED_FAULTS)]
        _next_index += 1
        alert = _fault_alert(fault, _next_index)
        _fired.insert(0, alert)
        return alert


def reset_alerts() -> None:
    global _next_index
    with _lock:
        _fired.clear()
        _next_index = 0


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
        fired = list(_fired)
    return fired + predictive_alerts(watch_list or [])
