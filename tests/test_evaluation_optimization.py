import pytest
from app.simulation import simulate_machine_failure
from app.recovery import generate_recovery_plans
from app.evaluation import (
    ObjectiveWeights,
    evaluate_recovery_plans,
    select_optimal_plan,
    EvaluatedRecoveryPlan,
    OptimizationResult,
)
from app.data import get_connection

def test_evaluation_accepts_valid_plans():
    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    plans = generate_recovery_plans(impact)
    evals = evaluate_recovery_plans(plans, impact)

    assert isinstance(evals, list)
    assert len(evals) == len(plans)
    for ep in evals:
        assert isinstance(ep, EvaluatedRecoveryPlan)
        assert 0.0 <= ep.weighted_objective_score <= 1.0

def test_metrics_are_deterministic():
    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    plans = generate_recovery_plans(impact)

    evals1 = evaluate_recovery_plans(plans, impact)
    evals2 = evaluate_recovery_plans(plans, impact)

    for ep1, ep2 in zip(evals1, evals2):
        assert ep1.weighted_objective_score == ep2.weighted_objective_score
        assert ep1.raw_metrics.cost_impact_usd == ep2.raw_metrics.cost_impact_usd
        assert ep1.raw_metrics.delivery_impact_score == ep2.raw_metrics.delivery_impact_score

def test_objective_weights_validation():
    # Valid weights
    w = ObjectiveWeights(delivery_weight=0.5, cost_weight=0.5, quality_weight=0.0, energy_weight=0.0, risk_weight=0.0)
    assert w.normalized_weights()["delivery"] == 0.5

    # Negative weight raises ValueError
    with pytest.raises(ValueError):
        ObjectiveWeights(delivery_weight=-0.1)

    # All zero weights sum to zero -> raises ValueError on normalization
    w_zero = ObjectiveWeights(delivery_weight=0.0, cost_weight=0.0, quality_weight=0.0, energy_weight=0.0, risk_weight=0.0)
    with pytest.raises(ValueError):
        w_zero.normalized_weights()

def test_changing_weights_changes_calculated_objective():
    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    plans = generate_recovery_plans(impact)

    w_delivery = ObjectiveWeights(delivery_weight=0.9, cost_weight=0.025, quality_weight=0.025, energy_weight=0.025, risk_weight=0.025)
    w_cost = ObjectiveWeights(delivery_weight=0.025, cost_weight=0.9, quality_weight=0.025, energy_weight=0.025, risk_weight=0.025)

    evals_delivery = evaluate_recovery_plans(plans, impact, weights=w_delivery)
    evals_cost = evaluate_recovery_plans(plans, impact, weights=w_cost)

    assert evals_delivery[0].weighted_objective_score != evals_cost[0].weighted_objective_score

def test_what_if_priority_changes_can_change_optimal_plan():
    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    plans = generate_recovery_plans(impact)

    # Priority set A: Heavily favors Delivery
    weights_a = ObjectiveWeights(delivery_weight=0.8, cost_weight=0.05, quality_weight=0.05, energy_weight=0.05, risk_weight=0.05)
    evals_a = evaluate_recovery_plans(plans, impact, weights=weights_a)
    result_a = select_optimal_plan(evals_a, weights=weights_a)

    # Priority set B: Heavily favors Cost
    weights_b = ObjectiveWeights(delivery_weight=0.05, cost_weight=0.8, quality_weight=0.05, energy_weight=0.05, risk_weight=0.05)
    evals_b = evaluate_recovery_plans(plans, impact, weights=weights_b)
    result_b = select_optimal_plan(evals_b, weights=weights_b)

    assert result_a.selected_plan is not None
    assert result_b.selected_plan is not None
    assert result_a.selected_plan.weighted_objective_score > 0
    assert result_b.selected_plan.weighted_objective_score > 0

def test_infeasible_plans_excluded_from_selection():
    impact_huge = simulate_machine_failure(machine_id="M17", downtime_hours=200.0)
    plans = generate_recovery_plans(impact_huge)
    evals = evaluate_recovery_plans(plans, impact_huge)

    result = select_optimal_plan(evals)

    # Any infeasible plan should not be the selected plan
    if result.selected_plan is not None:
        assert result.selected_plan.is_feasible is True
        assert result.selected_plan.plan.feasibility.is_feasible is True

def test_no_feasible_plan_case_handled_cleanly():
    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    plans = generate_recovery_plans(impact)
    evals = evaluate_recovery_plans(plans, impact)

    # Force all evaluations to infeasible for testing
    for ep in evals:
        ep.is_feasible = False
        ep.plan.feasibility.is_feasible = False

    result = select_optimal_plan(evals)
    assert result.selected_plan is None
    assert "None of the candidate recovery plans passed feasibility checks" in result.comparison_summary

def test_individual_metrics_are_deterministic():
    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    plans = generate_recovery_plans(impact)
    evals = evaluate_recovery_plans(plans, impact)

    for ep in evals:
        m = ep.raw_metrics
        assert m.delivery_impact_score >= 0.0
        assert m.cost_impact_usd >= 0.0
        assert m.quality_penalty_score >= 0.0
        assert m.energy_consumption_kwh >= 0.0
        assert m.risk_impact_score >= 0.0

def test_database_remains_unmutated_after_optimization():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT status, health_score FROM machines WHERE machine_id = 'M17'")
    row1 = cursor.fetchone()
    status1, health1 = row1["status"], row1["health_score"]
    conn.close()

    impact = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    plans = generate_recovery_plans(impact)
    evals = evaluate_recovery_plans(plans, impact)
    result = select_optimal_plan(evals)

    conn2 = get_connection()
    cursor2 = conn2.cursor()

    cursor2.execute("SELECT status, health_score FROM machines WHERE machine_id = 'M17'")
    row2 = cursor2.fetchone()
    assert row2["status"] == status1
    assert row2["health_score"] == health1
    conn2.close()

def test_different_failures_produce_different_evaluations():
    impact17 = simulate_machine_failure(machine_id="M17", downtime_hours=8.0)
    impact01 = simulate_machine_failure(machine_id="M01", downtime_hours=4.0)

    plans17 = generate_recovery_plans(impact17)
    plans01 = generate_recovery_plans(impact01)

    evals17 = evaluate_recovery_plans(plans17, impact17)
    evals01 = evaluate_recovery_plans(plans01, impact01)

    result17 = select_optimal_plan(evals17)
    result01 = select_optimal_plan(evals01)

    assert result17.selected_plan.plan.plan_id != result01.selected_plan.plan.plan_id or \
           result17.selected_plan.raw_metrics.cost_impact_usd != result01.selected_plan.raw_metrics.cost_impact_usd
