import pytest
from app.config import config
from app.models import MachineStatus, OrderPriority
from app.simulation import simulate_machine_failure
from app.recovery import (
    generate_recovery_plans,
    RecoveryPlan,
    RecoveryStrategyType,
)
from app.data import get_connection

def test_recovery_planning_accepts_valid_impact():
    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    plans = generate_recovery_plans(impact)
    
    assert isinstance(plans, list)
    assert len(plans) > 0
    for p in plans:
        assert isinstance(p, RecoveryPlan)
        assert p.strategy_type in [
            RecoveryStrategyType.MACHINE_TRANSFER,
            RecoveryStrategyType.RESEQUENCE,
            RecoveryStrategyType.OVERTIME,
        ]

def test_machine_transfer_candidates_generated_dynamically():
    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    plans = generate_recovery_plans(impact)
    
    transfer_plans = [p for p in plans if p.strategy_type == RecoveryStrategyType.MACHINE_TRANSFER]
    assert len(transfer_plans) > 0
    
    for p in transfer_plans:
        action = p.actions[0]
        assert action.target_machine_id != "M17"

def test_failed_machine_never_proposed_as_transfer_target():
    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    plans = generate_recovery_plans(impact)
    
    for p in plans:
        if p.strategy_type == RecoveryStrategyType.MACHINE_TRANSFER:
            for action in p.actions:
                assert action.target_machine_id != "M17"

def test_resequencing_is_deterministic():
    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    plan1 = generate_recovery_plans(impact)
    plan2 = generate_recovery_plans(impact)
    
    reseq1 = next(p for p in plan1 if p.strategy_type == RecoveryStrategyType.RESEQUENCE)
    reseq2 = next(p for p in plan2 if p.strategy_type == RecoveryStrategyType.RESEQUENCE)
    
    assert reseq1.actions[0].details["resequenced_order_sequence"] == reseq2.actions[0].details["resequenced_order_sequence"]

def test_resequencing_prioritizes_high_priority_first():
    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    plans = generate_recovery_plans(impact)
    reseq_plan = next(p for p in plans if p.strategy_type == RecoveryStrategyType.RESEQUENCE)
    
    # Verify that HIGH priority orders precede LOW/MEDIUM priority orders in sequence
    order_seq = reseq_plan.actions[0].details["resequenced_order_sequence"]
    affected_map = {o.order_id: o for o in impact.affected_orders}
    
    priorities = [affected_map[oid].priority for oid in order_seq if oid in affected_map]
    # Check that HIGH priority comes before MEDIUM/LOW
    priority_ranks = [{"HIGH": 0, "MEDIUM": 1, "LOW": 2}[p] for p in priorities]
    assert priority_ranks == sorted(priority_ranks)

def test_overtime_feasibility_calculation():
    impact_small = simulate_machine_failure(machine_id="M17", downtime_hours=2.0)
    plans = generate_recovery_plans(impact_small)
    ot_plan = next(p for p in plans if p.strategy_type == RecoveryStrategyType.OVERTIME)
    
    # Small downtime should be feasible within max overtime limit (12h)
    assert ot_plan.feasibility.is_feasible is True
    assert len(ot_plan.feasibility.infeasible_reasons) == 0

def test_excessive_overtime_rejected():
    impact_huge = simulate_machine_failure(machine_id="M17", downtime_hours=200.0)
    plans = generate_recovery_plans(impact_huge)
    ot_plan = next(p for p in plans if p.strategy_type == RecoveryStrategyType.OVERTIME)
    
    # 200 hours downtime will exceed max overtime limit of 12.0 hours
    assert ot_plan.feasibility.is_feasible is False
    assert len(ot_plan.feasibility.infeasible_reasons) > 0
    assert "exceeds maximum allowed limit" in ot_plan.feasibility.infeasible_reasons[0]

def test_infeasible_plans_return_structured_reasons():
    impact_huge = simulate_machine_failure(machine_id="M17", downtime_hours=200.0)
    plans = generate_recovery_plans(impact_huge)
    
    infeasible_plans = [p for p in plans if not p.feasibility.is_feasible]
    assert len(infeasible_plans) > 0
    for p in infeasible_plans:
        assert isinstance(p.feasibility.infeasible_reasons, list)
        assert len(p.feasibility.infeasible_reasons) > 0

def test_no_candidate_plan_is_ranked_or_selected():
    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    plans = generate_recovery_plans(impact)
    
    # Verify no best/winning/rank field is added to RecoveryPlan model
    for p in plans:
        assert not hasattr(p, "rank")
        assert not hasattr(p, "score")
        assert not hasattr(p, "is_best")

def test_recovery_planning_does_not_mutate_database():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT status, health_score FROM machines WHERE machine_id = 'M17'")
    row1 = cursor.fetchone()
    status1, health1 = row1["status"], row1["health_score"]

    cursor.execute("SELECT COUNT(*) FROM production_orders")
    count1 = cursor.fetchone()[0]
    conn.close()

    # Run recovery planning
    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    plans = generate_recovery_plans(impact)

    # Re-verify database immutability
    conn2 = get_connection()
    cursor2 = conn2.cursor()

    cursor2.execute("SELECT status, health_score FROM machines WHERE machine_id = 'M17'")
    row2 = cursor2.fetchone()
    assert row2["status"] == status1
    assert row2["health_score"] == health1

    cursor2.execute("SELECT COUNT(*) FROM production_orders")
    count2 = cursor2.fetchone()[0]
    assert count2 == count1
    conn2.close()

def test_dynamic_plans_different_failures():
    impact17 = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    impact01 = simulate_machine_failure(machine_id="M01", downtime_hours=4.0)

    plans17 = generate_recovery_plans(impact17)
    plans01 = generate_recovery_plans(impact01)

    transfer17 = [p for p in plans17 if p.strategy_type == RecoveryStrategyType.MACHINE_TRANSFER]
    transfer01 = [p for p in plans01 if p.strategy_type == RecoveryStrategyType.MACHINE_TRANSFER]

    # Target machines available for M17 and M01 should differ based on station/operation capabilities
    targets17 = {p.actions[0].target_machine_id for p in transfer17}
    targets01 = {p.actions[0].target_machine_id for p in transfer01}

    assert targets17 != targets01
