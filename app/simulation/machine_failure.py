import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from app.config import config
from app.exceptions import (
    UnknownMachineError,
    InvalidDowntimeError,
    InvalidScenarioError,
)
from app.models import (
    Machine,
    Station,
    ProductionLine,
    ProductionOrder,
    OrderPriority,
    MachineStatus,
)
from app.factory.state import (
    get_factory_state,
    get_machine,
    get_station,
    get_line,
)
from app.simulation.scenarios import Scenario, ScenarioType
from app.simulation.impact import (
    DeliveryRisk,
    CapacityImpact,
    AffectedOrderDetails,
    AlternativeCandidate,
    MachineFailureImpact,
)

logger = logging.getLogger(__name__)

def find_alternative_capacity(
    station_id: str,
    operation_type: str,
    current_machine_id: str,
    db_path: Optional[Path] = None,
) -> List[AlternativeCandidate]:
    """
    Find compatible alternative machines across the factory that perform the same operation type.
    Does NOT rank, select, or optimize alternatives.
    """
    state = get_factory_state(db_path)
    
    # Identify stations supporting the same operation_type
    compatible_station_ids = {
        st.station_id for st in state.stations if st.operation_type == operation_type
    }
    
    candidates = []
    for m in state.machines:
        if m.machine_id == current_machine_id:
            continue
        if m.station_id in compatible_station_ids and m.status in (MachineStatus.HEALTHY, MachineStatus.DEGRADED):
            candidates.append(
                AlternativeCandidate(
                    machine_id=m.machine_id,
                    line_id=m.line_id,
                    station_id=m.station_id,
                    machine_type=m.machine_type,
                    capacity_per_hour=m.capacity_per_hour,
                    status=m.status,
                    health_score=m.health_score,
                )
            )
            
    return candidates

def simulate_machine_failure(
    machine_id: str,
    downtime_hours: float,
    start_time: Optional[datetime] = None,
    db_path: Optional[Path] = None,
) -> MachineFailureImpact:
    """
    Simulate the operational impact of a machine failure / unavailability event.
    Deterministic and strictly READ-ONLY with respect to the persisted database.
    """
    logger.info(f"Simulating failure for machine {machine_id} with downtime {downtime_hours} hours")
    
    if downtime_hours <= 0:
        raise InvalidDowntimeError(f"downtime_hours must be positive, got {downtime_hours}")

    # Fetch machine (raises UnknownMachineError if missing)
    machine = get_machine(machine_id, db_path=db_path)
    station = get_station(machine.station_id, db_path=db_path)
    line = get_line(machine.line_id, db_path=db_path)

    scenario = Scenario(
        scenario_type=ScenarioType.MACHINE_UNAVAILABILITY,
        machine_id=machine_id,
        downtime_hours=downtime_hours,
        start_time=start_time,
    )

    state = get_factory_state(db_path=db_path)

    # Identify schedules assigned to this machine
    assigned_schedules = [
        s for s in state.production_schedules if s.machine_id == machine_id
    ]

    # Filter schedules by downtime window if start_time provided
    if start_time and assigned_schedules:
        end_window = start_time + timedelta(hours=downtime_hours)
        affected_schedules = [
            s for s in assigned_schedules if not (s.end_time <= start_time or s.start_time >= end_window)
        ]
    else:
        affected_schedules = assigned_schedules

    # Calculate Capacity Loss
    capacity_loss_units = round(machine.capacity_per_hour * downtime_hours, 1)
    daily_station_capacity = max(1.0, station.capacity_per_hour * config.OPERATING_HOURS_PER_DAY)
    capacity_loss_percentage = round(min(100.0, (capacity_loss_units / daily_station_capacity) * 100.0), 1)

    capacity_impact = CapacityImpact(
        downtime_hours=downtime_hours,
        capacity_loss_units=capacity_loss_units,
        capacity_loss_percentage=capacity_loss_percentage,
        affected_schedule_count=len(affected_schedules),
    )

    # Map affected schedules to affected orders
    schedule_by_order: Dict[str, int] = {}
    for s in affected_schedules:
        schedule_by_order[s.order_id] = schedule_by_order.get(s.order_id, 0) + 1

    orders_by_id = {o.order_id: o for o in state.production_orders}
    
    affected_orders: List[AffectedOrderDetails] = []
    for order_id, sched_count in schedule_by_order.items():
        if order_id in orders_by_id:
            ord_obj = orders_by_id[order_id]
            affected_orders.append(
                AffectedOrderDetails(
                    order_id=ord_obj.order_id,
                    product_model=ord_obj.product_model,
                    quantity=ord_obj.quantity,
                    priority=ord_obj.priority,
                    due_time=ord_obj.due_time,
                    status=ord_obj.status,
                    affected_schedule_count=sched_count,
                )
            )

    high_priority_orders = [
        o for o in affected_orders if o.priority == OrderPriority.HIGH
    ]

    # Deterministic Delivery Risk Rules
    if len(high_priority_orders) > 0 or capacity_loss_percentage >= 25.0:
        delivery_risk = DeliveryRisk.HIGH
    elif len(affected_orders) > 0 or capacity_loss_percentage >= 10.0:
        delivery_risk = DeliveryRisk.MEDIUM
    else:
        delivery_risk = DeliveryRisk.LOW

    # Discover alternative capacity candidates
    alternative_candidates = find_alternative_capacity(
        station_id=station.station_id,
        operation_type=station.operation_type,
        current_machine_id=machine_id,
        db_path=db_path,
    )

    return MachineFailureImpact(
        scenario=scenario,
        machine=machine,
        station=station,
        line=line,
        capacity_impact=capacity_impact,
        affected_orders=affected_orders,
        high_priority_orders=high_priority_orders,
        delivery_risk=delivery_risk,
        alternative_candidates=alternative_candidates,
    )
