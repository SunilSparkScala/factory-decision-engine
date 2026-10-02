import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from app.config import config
from app.models import MachineStatus, OrderPriority
from app.simulation import MachineFailureImpact, DeliveryRisk
from app.factory import get_factory_state, get_machine
from app.exceptions import UnknownMachineError
from app.recovery.models import (
    RecoveryStrategyType,
    RecoveryAction,
    FeasibilityCheck,
    RecoveryPlan,
)

logger = logging.getLogger(__name__)

def generate_recovery_plans(
    impact: MachineFailureImpact,
    db_path: Optional[Path] = None,
) -> List[RecoveryPlan]:
    """
    Generate deterministic, candidate recovery plans across supported strategies:
    1. MACHINE_TRANSFER
    2. RESEQUENCE
    3. OVERTIME

    Does NOT rank, select, or optimize candidate plans.
    """
    logger.info(f"Generating recovery plans for failure simulation on machine {impact.machine.machine_id}")
    
    plans: List[RecoveryPlan] = []
    
    # 1. MACHINE_TRANSFER Plans
    transfer_plans = _generate_transfer_plans(impact, db_path=db_path)
    plans.extend(transfer_plans)

    # 2. RESEQUENCE Plan
    resequence_plan = _generate_resequence_plan(impact, db_path=db_path)
    if resequence_plan:
        plans.append(resequence_plan)

    # 3. OVERTIME Plan
    overtime_plan = _generate_overtime_plan(impact, db_path=db_path)
    if overtime_plan:
        plans.append(overtime_plan)

    return plans

def _generate_transfer_plans(
    impact: MachineFailureImpact,
    db_path: Optional[Path] = None,
) -> List[RecoveryPlan]:
    plans: List[RecoveryPlan] = []
    failed_machine_id = impact.machine.machine_id
    lost_units = impact.capacity_impact.capacity_loss_units
    downtime_hrs = impact.capacity_impact.downtime_hours
    affected_order_ids = [o.order_id for o in impact.affected_orders]

    for idx, candidate in enumerate(impact.alternative_candidates, start=1):
        plan_id = f"PLAN_TRANSFER_{idx:02d}"
        checked_constraints = [
            "failed_machine_exclusion",
            "machine_status_operational",
            "operation_type_compatibility",
            "capacity_availability",
            "schedule_conflict_check",
        ]
        infeasible_reasons: List[str] = []

        # Check 1: Exclude failed machine
        if candidate.machine_id == failed_machine_id:
            infeasible_reasons.append(f"Target machine {candidate.machine_id} is the failed machine itself.")

        # Check 2: Machine status check
        if candidate.status in (MachineStatus.DOWN, MachineStatus.MAINTENANCE):
            infeasible_reasons.append(
                f"Target machine {candidate.machine_id} is unavailable with status {candidate.status.value}."
            )

        # Check 3: Existing schedule conflict check
        state = get_factory_state(db_path=db_path)
        cand_schedules = [s for s in state.production_schedules if s.machine_id == candidate.machine_id]
        if impact.scenario.start_time and cand_schedules:
            end_window = impact.scenario.start_time + timedelta(hours=downtime_hrs)
            overlapping = [
                s for s in cand_schedules if not (s.end_time <= impact.scenario.start_time or s.start_time >= end_window)
            ]
            if len(overlapping) >= 3:  # Threshold for heavily loaded candidate machine schedule
                infeasible_reasons.append(
                    f"Candidate machine {candidate.machine_id} has {len(overlapping)} overlapping schedule conflicts during the recovery window."
                )

        # Check 4: Capacity recovery calculation
        potential_capacity = candidate.capacity_per_hour * downtime_hrs
        recovered_units = min(lost_units, potential_capacity)
        remaining_gap = max(0.0, lost_units - recovered_units)

        if potential_capacity <= 0:
            infeasible_reasons.append(f"Target machine {candidate.machine_id} has zero available capacity.")

        is_feasible = len(infeasible_reasons) == 0

        action = RecoveryAction(
            action_id=f"ACT_TRANSFER_{idx:02d}",
            strategy_type=RecoveryStrategyType.MACHINE_TRANSFER,
            target_machine_id=candidate.machine_id,
            affected_order_ids=affected_order_ids,
            recovered_capacity_units=round(recovered_units, 1),
            details={
                "target_line_id": candidate.line_id,
                "target_station_id": candidate.station_id,
                "target_capacity_per_hour": candidate.capacity_per_hour,
                "target_health_score": candidate.health_score,
            },
        )

        plan = RecoveryPlan(
            plan_id=plan_id,
            strategy_type=RecoveryStrategyType.MACHINE_TRANSFER,
            description=f"Transfer production from {failed_machine_id} to alternative machine {candidate.machine_id}",
            actions=[action],
            affected_order_ids=affected_order_ids,
            recovered_capacity_units=round(recovered_units, 1),
            remaining_capacity_gap_units=round(remaining_gap, 1),
            feasibility=FeasibilityCheck(
                is_feasible=is_feasible,
                checked_constraints=checked_constraints,
                infeasible_reasons=infeasible_reasons,
            ),
            explanation=f"Reassign operations to {candidate.machine_id} on {candidate.line_id} ({candidate.station_id}).",
        )

        plans.append(plan)

    return plans

def _generate_resequence_plan(
    impact: MachineFailureImpact,
    db_path: Optional[Path] = None,
) -> Optional[RecoveryPlan]:
    affected_orders = impact.affected_orders
    if not affected_orders:
        return RecoveryPlan(
            plan_id="PLAN_RESEQUENCE_01",
            strategy_type=RecoveryStrategyType.RESEQUENCE,
            description="Resequence affected orders according to priority and due time",
            actions=[],
            affected_order_ids=[],
            recovered_capacity_units=0.0,
            remaining_capacity_gap_units=impact.capacity_impact.capacity_loss_units,
            feasibility=FeasibilityCheck(
                is_feasible=False,
                checked_constraints=["order_priority_ordering", "due_time_constraints"],
                infeasible_reasons=["No affected orders to resequence."],
            ),
            explanation="No orders are affected by the simulated machine failure.",
        )

    # Priority sorting map: HIGH (0), MEDIUM (1), LOW (2)
    priority_order_map = {
        OrderPriority.HIGH: 0,
        OrderPriority.MEDIUM: 1,
        OrderPriority.LOW: 2,
    }

    # Deterministic sorting: Priority asc, due_time asc, order_id asc
    sorted_orders = sorted(
        affected_orders,
        key=lambda o: (priority_order_map.get(o.priority, 3), o.due_time, o.order_id),
    )

    resequenced_order_ids = [o.order_id for o in sorted_orders]

    # Partial capacity recovery estimate from resequencing (prioritizing high-priority delivery)
    high_priority_count = len(impact.high_priority_orders)
    recovered_units = round(min(impact.capacity_impact.capacity_loss_units * 0.5, 100.0), 1)
    remaining_gap = round(max(0.0, impact.capacity_impact.capacity_loss_units - recovered_units), 1)

    action = RecoveryAction(
        action_id="ACT_RESEQUENCE_01",
        strategy_type=RecoveryStrategyType.RESEQUENCE,
        target_machine_id=None,
        affected_order_ids=resequenced_order_ids,
        recovered_capacity_units=recovered_units,
        details={
            "resequenced_order_sequence": resequenced_order_ids,
            "high_priority_order_count": high_priority_count,
        },
    )

    return RecoveryPlan(
        plan_id="PLAN_RESEQUENCE_01",
        strategy_type=RecoveryStrategyType.RESEQUENCE,
        description="Resequence affected production orders: HIGH priority first, then earlier due time",
        actions=[action],
        affected_order_ids=resequenced_order_ids,
        recovered_capacity_units=recovered_units,
        remaining_capacity_gap_units=remaining_gap,
        feasibility=FeasibilityCheck(
            is_feasible=True,
            checked_constraints=["order_priority_ordering", "due_time_constraints", "schedule_resequencing"],
            infeasible_reasons=[],
        ),
        explanation=f"Deterministic order resequencing prioritizes {high_priority_count} HIGH-priority order(s) to protect delivery commitments.",
    )

def _generate_overtime_plan(
    impact: MachineFailureImpact,
    db_path: Optional[Path] = None,
) -> Optional[RecoveryPlan]:
    lost_units = impact.capacity_impact.capacity_loss_units
    machine_capacity_per_hr = max(1.0, impact.machine.capacity_per_hour)
    
    # Calculate required overtime hours
    required_overtime_hours = round(lost_units / machine_capacity_per_hr, 1)
    max_overtime = config.MAX_OVERTIME_HOURS_PER_MACHINE

    checked_constraints = [
        "overtime_limit",
        "machine_status_check",
        "capacity_recovery_math",
    ]
    infeasible_reasons: List[str] = []

    if impact.machine.status in (MachineStatus.DOWN, MachineStatus.MAINTENANCE):
        infeasible_reasons.append(
            f"Machine {impact.machine.machine_id} is in status {impact.machine.status.value} and cannot execute overtime."
        )

    if required_overtime_hours > max_overtime:
        infeasible_reasons.append(
            f"Required overtime of {required_overtime_hours} hours exceeds maximum allowed limit of {max_overtime} hours."
        )

    is_feasible = len(infeasible_reasons) == 0
    recovered_units = lost_units if is_feasible else round(max_overtime * machine_capacity_per_hr, 1)
    remaining_gap = round(max(0.0, lost_units - recovered_units), 1)

    affected_order_ids = [o.order_id for o in impact.affected_orders]

    action = RecoveryAction(
        action_id="ACT_OVERTIME_01",
        strategy_type=RecoveryStrategyType.OVERTIME,
        target_machine_id=impact.machine.machine_id,
        affected_order_ids=affected_order_ids,
        recovered_capacity_units=recovered_units,
        details={
            "required_overtime_hours": required_overtime_hours,
            "max_overtime_hours_allowed": max_overtime,
        },
    )

    return RecoveryPlan(
        plan_id="PLAN_OVERTIME_01",
        strategy_type=RecoveryStrategyType.OVERTIME,
        description=f"Extend operating hours by {required_overtime_hours} overtime hours on machine {impact.machine.machine_id}",
        actions=[action],
        affected_order_ids=affected_order_ids,
        recovered_capacity_units=recovered_units,
        remaining_capacity_gap_units=remaining_gap,
        feasibility=FeasibilityCheck(
            is_feasible=is_feasible,
            checked_constraints=checked_constraints,
            infeasible_reasons=infeasible_reasons,
        ),
        explanation=f"Overtime plan requests {required_overtime_hours} hours additional operation to recover {lost_units} lost units.",
    )
