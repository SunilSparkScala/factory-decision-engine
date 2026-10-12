"""
Synthetic machine telemetry (hourly vibration, temperature, motor current, health) for the
72 hours before the plant clock.

Generated deterministically from the machine registry (seeded per machine), so it is stable
across runs without touching the factory database. A few machines carry scripted degradation
profiles that drive the early-warning demo:
- M03, M27: already DEGRADED, still declining.
- M23: status HEALTHY, but vibration is accelerating (hidden risk the watch list should surface).
"""
import math
import random
from datetime import timedelta
from typing import Dict, List

from app.config import config
from app.domains import operation_of, operation_profile

HISTORY_HOURS = 72

# Synthetic alarm thresholds used by the early-warning model
VIBRATION_ALARM_MM_S = 7.1
TEMPERATURE_ALARM_C = 85.0
HEALTH_FAILURE_THRESHOLD = 50.0

# Scripted degradation profiles: signal growth over the 72 h window (fraction of window -> extra units)
DEGRADATION_PROFILES: Dict[str, Dict[str, float]] = {
    "M23": {"vibration_rise": 3.9, "temperature_rise": 9.0, "health_drop": 6.0, "curve": 2.2},
    "M27": {"vibration_rise": 2.6, "temperature_rise": 12.0, "health_drop": 14.0, "curve": 1.4},
    "M03": {"vibration_rise": 2.2, "temperature_rise": 8.0, "health_drop": 18.0, "curve": 1.2},
}

FALLBACK_SIGNALS = {"vibration": 1.6, "temperature": 41.0, "current": 17.0}


def _base_signals(machine_type: str) -> dict:
    """Baseline signals for the machine's operation, defined per operation in the industry packs."""
    profile = operation_profile(operation_of(machine_type))
    return profile["signals"] if profile else FALLBACK_SIGNALS


def generate_series(machine) -> List[Dict[str, float]]:
    """Hourly telemetry for one machine, oldest first, ending at the plant clock."""
    seed = config.RANDOM_SEED * 1000 + int("".join(ch for ch in machine.machine_id if ch.isdigit()) or 0)
    rng = random.Random(seed)
    base = _base_signals(machine.machine_type)
    profile = DEGRADATION_PROFILES.get(machine.machine_id)
    status = machine.status.value if hasattr(machine.status, "value") else str(machine.status)
    offline = status in ("DOWN", "MAINTENANCE")

    end_health = float(machine.health_score)
    # Generic wear for degraded machines without a scripted profile
    if profile is None and status == "DEGRADED":
        profile = {"vibration_rise": 1.2, "temperature_rise": 5.0, "health_drop": 8.0, "curve": 1.0}

    series: List[Dict[str, float]] = []
    start = config.PLANT_CLOCK - timedelta(hours=HISTORY_HOURS - 1)
    for i in range(HISTORY_HOURS):
        frac = i / (HISTORY_HOURS - 1)
        shape = frac ** profile["curve"] if profile else 0.0
        vib = base["vibration"] * (1.0 + (100.0 - end_health) / 120.0)
        temp = base["temperature"]
        health = end_health
        if profile:
            vib += profile["vibration_rise"] * shape
            temp += profile["temperature_rise"] * shape
            health = end_health + profile["health_drop"] * (1.0 - shape)
        load = 0.0 if offline and frac > 0.85 else 1.0
        series.append({
            "timestamp": (start + timedelta(hours=i)).isoformat(),
            "vibration_mm_s": round(max(0.1, vib * load + rng.gauss(0, 0.08)), 3),
            "temperature_c": round(temp * (0.6 + 0.4 * load) + rng.gauss(0, 0.6) + 1.5 * math.sin(i / 3.8), 2),
            "current_a": round(base["current"] * load * (1 + rng.gauss(0, 0.02)), 2),
            "health": round(min(100.0, health + rng.gauss(0, 0.25)), 2),
        })
    return series
