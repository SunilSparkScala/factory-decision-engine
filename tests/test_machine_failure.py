import pytest
import sqlite3
from app.config import config
from app.exceptions import UnknownMachineError, InvalidDowntimeError
from app.models import MachineStatus
from app.data import get_connection
from app.simulation import (
    simulate_machine_failure,
    find_alternative_capacity,
    MachineFailureImpact,
    DeliveryRisk,
)

def test_valid_machine_failure_simulation():
    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    assert isinstance(impact, MachineFailureImpact)
    assert impact.machine.machine_id == "M17"
    assert impact.scenario.downtime_hours == 8.0
    assert impact.capacity_impact.capacity_loss_units > 0
    assert impact.delivery_risk in [DeliveryRisk.LOW, DeliveryRisk.MEDIUM, DeliveryRisk.HIGH]
    assert len(impact.alternative_candidates) > 0

def test_simulation_dynamic_variation_different_machines():
    impact_m17 = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    impact_m01 = simulate_machine_failure(machine_id="M01", downtime_hours=4.0)

    assert impact_m17.machine.machine_id == "M17"
    assert impact_m01.machine.machine_id == "M01"
    assert impact_m17.capacity_impact.downtime_hours == 8.0
    assert impact_m01.capacity_impact.downtime_hours == 4.0
    assert impact_m17.station.station_id != impact_m01.station.station_id

def test_zero_downtime_rejected():
    with pytest.raises(InvalidDowntimeError):
        simulate_machine_failure(machine_id="M17", downtime_hours=0.0)

def test_negative_downtime_rejected():
    with pytest.raises(InvalidDowntimeError):
        simulate_machine_failure(machine_id="M17", downtime_hours=-5.0)

def test_unknown_machine_rejected():
    with pytest.raises(UnknownMachineError):
        simulate_machine_failure(machine_id="UNKNOWN_M999", downtime_hours=8.0)

def test_affected_orders_and_high_priority_identification():
    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    assert isinstance(impact.affected_orders, list)
    assert isinstance(impact.high_priority_orders, list)
    
    # High priority orders should be a subset of affected orders
    high_priority_ids = {o.order_id for o in impact.high_priority_orders}
    affected_ids = {o.order_id for o in impact.affected_orders}
    assert high_priority_ids.issubset(affected_ids)

def test_alternative_capacity_discovery():
    candidates = find_alternative_capacity(
        station_id="STA_B2",
        operation_type="WELDING",
        current_machine_id="M17",
    )
    assert isinstance(candidates, list)
    assert len(candidates) > 0
    # Candidate machines should not include current machine M17
    candidate_ids = [c.machine_id for c in candidates]
    assert "M17" not in candidate_ids
    # Candidates must be operational (HEALTHY or DEGRADED)
    for c in candidates:
        assert c.status in [MachineStatus.HEALTHY, MachineStatus.DEGRADED]

def test_simulation_does_not_mutate_database():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT status, health_score FROM machines WHERE machine_id = 'M17'")
    initial_machine_row = cursor.fetchone()
    initial_status = initial_machine_row["status"]
    initial_health = initial_machine_row["health_score"]

    cursor.execute("SELECT COUNT(*) FROM production_orders")
    initial_order_count = cursor.fetchone()[0]
    conn.close()

    # Run simulation
    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)

    # Re-check database state
    conn2 = get_connection()
    cursor2 = conn2.cursor()

    cursor2.execute("SELECT status, health_score FROM machines WHERE machine_id = 'M17'")
    post_machine_row = cursor2.fetchone()
    assert post_machine_row["status"] == initial_status
    assert post_machine_row["health_score"] == initial_health

    cursor2.execute("SELECT COUNT(*) FROM production_orders")
    post_order_count = cursor2.fetchone()[0]
    assert post_order_count == initial_order_count
    conn2.close()

def test_json_serializability_of_simulation_result():
    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    json_str = impact.model_dump_json()
    assert isinstance(json_str, str)
    assert "M17" in json_str

def test_start_time_parameter_regression():
    from datetime import datetime, timezone
    
    # 1. Test with explicit start_time
    start_dt = datetime(2026, 10, 1, 8, 0, 0, tzinfo=timezone.utc)
    impact_with_time = simulate_machine_failure(
        machine_id="M17",
        downtime_hours=8.0,
        start_time=start_dt
    )
    assert isinstance(impact_with_time, MachineFailureImpact)
    assert impact_with_time.scenario.start_time == start_dt
    assert impact_with_time.scenario.downtime_hours == 8.0

    # 2. Verify start_time=None default behavior remains unchanged
    impact_none_time = simulate_machine_failure(
        machine_id="M17",
        downtime_hours=8.0,
        start_time=None
    )
    assert isinstance(impact_none_time, MachineFailureImpact)
    assert impact_none_time.scenario.start_time is None

