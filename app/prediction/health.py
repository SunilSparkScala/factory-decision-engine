"""
Early-warning model and machine watch list.

A transparent, trend-based model (not a trained ML model): for each running machine it fits a
linear trend to the last 24 hours of vibration, temperature and health, extrapolates the time
until each signal crosses its alarm threshold, and reports the earliest crossing as the
estimated time-to-alarm. High-risk machines get a pre-built backup plan from the deterministic
decision engine, so the plant has a plan before anything breaks.
"""
from typing import Any, Dict, List, Optional

import numpy as np

from app.prediction.telemetry import (
    HEALTH_FAILURE_THRESHOLD,
    TEMPERATURE_ALARM_C,
    VIBRATION_ALARM_MM_S,
    generate_series,
)

TREND_WINDOW_HOURS = 24
HORIZON_HOURS = 168

# Synthetic typical repair durations by operation type (hours), used to size pre-built plans
TYPICAL_REPAIR_HOURS = {
    "STAMPING": (4.0, 8.0),
    "WELDING": (6.0, 10.0),
    "PAINTING": (5.0, 9.0),
    "ASSEMBLY": (3.0, 6.0),
    "INSPECTION": (2.0, 4.0),
}

SIGNALS = [
    # key, label, threshold, direction (+1 rising is bad, -1 falling is bad), unit
    ("vibration_mm_s", "Vibration", VIBRATION_ALARM_MM_S, 1, "mm/s"),
    ("temperature_c", "Temperature", TEMPERATURE_ALARM_C, 1, "°C"),
    ("health", "Health score", HEALTH_FAILURE_THRESHOLD, -1, "%"),
]


def _hours_to_threshold(values: List[float], threshold: float, direction: int) -> Dict[str, Optional[float]]:
    y = np.asarray(values[-TREND_WINDOW_HOURS:], dtype=float)
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    current = float(intercept + slope * x[-1])
    worsening = slope * direction
    if (current - threshold) * direction >= 0:
        eta = 0.0
    elif worsening <= 1e-6:
        eta = None
    else:
        eta = abs(threshold - current) / abs(slope)
        eta = eta if eta <= HORIZON_HOURS else None
    return {"current": round(current, 2), "slope_per_hour": round(float(slope), 4), "hours_to_threshold": None if eta is None else round(eta, 1)}


def _risk_level(eta: Optional[float]) -> str:
    if eta is None:
        return "NORMAL"
    if eta <= 12:
        return "CRITICAL"
    if eta <= 36:
        return "HIGH"
    if eta <= 96:
        return "ELEVATED"
    return "NORMAL"


def assess_machine(machine) -> Dict[str, Any]:
    status = machine.status.value if hasattr(machine.status, "value") else str(machine.status)
    series = generate_series(machine)
    op = machine.machine_type.split("_")[0].upper()
    signals = {}
    for key, label, threshold, direction, unit in SIGNALS:
        res = _hours_to_threshold([p[key] for p in series], threshold, direction)
        signals[key] = {"label": label, "threshold": threshold, "unit": unit, **res}

    offline = status in ("DOWN", "MAINTENANCE")
    etas = [(s["hours_to_threshold"], k) for k, s in signals.items() if s["hours_to_threshold"] is not None]
    eta, driver = min(etas) if etas and not offline else (None, None)
    risk = "OFFLINE" if offline else _risk_level(eta)

    if offline:
        explanation = f"{machine.machine_id} is already {status.lower()}; not forecast."
    elif driver:
        s = signals[driver]
        trend = "rising" if s["slope_per_hour"] > 0 else "falling"
        explanation = (
            f"{s['label']} {trend} at {abs(s['slope_per_hour']):.3f} {s['unit']}/h "
            f"(now {s['current']:g} {s['unit']}, alarm {s['threshold']:g} {s['unit']}); "
            f"projected to reach the alarm level in about {eta:g} h."
        )
    else:
        explanation = "All signals stable; no alarm projected within 7 days."

    return {
        "machine_id": machine.machine_id,
        "machine_type": machine.machine_type,
        "operation": op,
        "line_id": machine.line_id,
        "station_id": machine.station_id,
        "status": status,
        "health_score": machine.health_score,
        "risk_level": risk,
        "hours_to_alarm": eta,
        "driver_signal": signals[driver]["label"] if driver else None,
        "explanation": explanation,
        "signals": signals,
        "typical_repair_hours": list(TYPICAL_REPAIR_HOURS.get(op, (4.0, 8.0))),
        "sparkline": [p["vibration_mm_s"] for p in series[-48:]],
    }


RISK_ORDER = {"CRITICAL": 0, "HIGH": 1, "ELEVATED": 2, "OFFLINE": 3, "NORMAL": 4}


def get_watch_list(include_normal: bool = False, with_plans: bool = True) -> List[Dict[str, Any]]:
    """Machines ranked by predicted risk, each high-risk one with a pre-built backup plan."""
    from app.factory.state import get_factory_state

    state = get_factory_state()
    items = [assess_machine(m) for m in state.machines]
    items.sort(key=lambda a: (RISK_ORDER[a["risk_level"]], a["hours_to_alarm"] if a["hours_to_alarm"] is not None else 1e9, a["machine_id"]))
    if not include_normal:
        items = [a for a in items if a["risk_level"] != "NORMAL"]
    if with_plans:
        for a in items:
            if a["risk_level"] in ("CRITICAL", "HIGH", "ELEVATED"):
                a["prebuilt_plan"] = prebuild_plan(a)
    return items


def prebuild_plan(assessment: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Deterministic contingency plan sized to the machine's typical repair time."""
    from app.agent.orchestrator import DecisionOrchestrator

    lo, hi = assessment["typical_repair_hours"]
    downtime = round((lo + hi) / 2, 1)
    decision = DecisionOrchestrator().orchestrate(
        machine_id=assessment["machine_id"],
        downtime_hours=downtime,
        priorities="delivery cost",
    )
    sp = decision.selected_plan
    return {
        "assumed_downtime_hours": downtime,
        "repair_range_hours": [lo, hi],
        "plan_id": sp.plan_id if sp else None,
        "description": sp.description if sp else "No feasible plan; escalate to supervisor.",
        "cost_impact_usd": sp.cost_impact_usd if sp else None,
        "orders_at_risk": decision.impact_summary.get("affected_order_count"),
        "high_priority_at_risk": decision.impact_summary.get("high_priority_affected_count"),
        "analysis_prompt": (
            f"{assessment['machine_id']} is at risk of failure ({assessment['explanation']}) "
            f"If it fails, repair typically takes {lo:g} to {hi:g} hours. Protect high-priority deliveries "
            f"while keeping cost low."
        ),
    }


def get_machine_telemetry(machine_id: str) -> Dict[str, Any]:
    from app.factory.state import get_machine

    machine = get_machine(machine_id)
    return {"machine_id": machine_id, "series": generate_series(machine), "assessment": assess_machine(machine)}
