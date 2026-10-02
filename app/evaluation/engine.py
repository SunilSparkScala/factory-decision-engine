import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from app.config import config
from app.models import MachineStatus, OrderPriority
from app.simulation import MachineFailureImpact, DeliveryRisk
from app.recovery.models import RecoveryPlan, RecoveryStrategyType
from app.evaluation.models import (
    ObjectiveWeights,
    PlanEvaluationMetrics,
    NormalizedMetrics,
    EvaluatedRecoveryPlan,
    OptimizationResult,
)

logger = logging.getLogger(__name__)

def evaluate_recovery_plans(
    plans: List[RecoveryPlan],
    impact: MachineFailureImpact,
    weights: Optional[ObjectiveWeights] = None,
    db_path: Optional[Path] = None,
) -> List[EvaluatedRecoveryPlan]:
    """
    Evaluate candidate RecoveryPlan objects across 5 objective dimensions:
    1. Delivery
    2. Cost
    3. Quality
    4. Energy
    5. Risk

    Calculates raw metrics, normalized scores, and a weighted objective score.
    """
    weights = weights or ObjectiveWeights()
    weights.validate_weights_sum()

    if not plans:
        return []

    # First pass: compute raw metrics for all plans
    raw_evals: List[tuple] = []
    max_cost_observed = 1.0
    max_energy_observed = 1.0

    for plan in plans:
        metrics, assumptions = _calculate_raw_metrics(plan, impact)
        raw_evals.append((plan, metrics, assumptions))
        if metrics.cost_impact_usd > max_cost_observed:
            max_cost_observed = metrics.cost_impact_usd
        if metrics.energy_consumption_kwh > max_energy_observed:
            max_energy_observed = metrics.energy_consumption_kwh

    norm_w = weights.normalized_weights()
    evaluated_plans: List[EvaluatedRecoveryPlan] = []

    for plan, metrics, assumptions in raw_evals:
        # Compute normalized desirability scores (1.0 = best, 0.0 = worst)
        s_delivery = max(0.0, round(1.0 - metrics.delivery_impact_score, 4))
        s_cost = max(0.0, round(1.0 - (metrics.cost_impact_usd / max(1.0, max_cost_observed * 1.2)), 4))
        s_quality = max(0.0, round(1.0 - metrics.quality_penalty_score, 4))
        s_energy = max(0.0, round(1.0 - (metrics.energy_consumption_kwh / max(1.0, max_energy_observed * 1.2)), 4))
        s_risk = max(0.0, round(1.0 - metrics.risk_impact_score, 4))

        norm_scores = NormalizedMetrics(
            delivery=s_delivery,
            cost=s_cost,
            quality=s_quality,
            energy=s_energy,
            risk=s_risk,
        )

        weighted_score = round(
            norm_w["delivery"] * s_delivery
            + norm_w["cost"] * s_cost
            + norm_w["quality"] * s_quality
            + norm_w["energy"] * s_energy
            + norm_w["risk"] * s_risk,
            4,
        )

        evaluated_plans.append(
            EvaluatedRecoveryPlan(
                plan=plan,
                raw_metrics=metrics,
                normalized_scores=norm_scores,
                weighted_objective_score=weighted_score,
                is_feasible=plan.feasibility.is_feasible,
                assumptions=assumptions,
            )
        )

    return evaluated_plans

def select_optimal_plan(
    evaluated_plans: List[EvaluatedRecoveryPlan],
    weights: Optional[ObjectiveWeights] = None,
) -> OptimizationResult:
    """
    Select the optimal recovery plan deterministically among FEASIBLE candidate plans.
    Infeasible plans are explicitly excluded from selection.
    """
    weights = weights or ObjectiveWeights()
    feasible_plans = [ep for ep in evaluated_plans if ep.is_feasible]

    if not feasible_plans:
        summary = (
            f"Evaluated {len(evaluated_plans)} plan(s). None of the candidate recovery plans "
            f"passed feasibility checks. No optimal plan selected."
        )
        return OptimizationResult(
            selected_plan=None,
            evaluated_plans=evaluated_plans,
            weights_used=weights,
            comparison_summary=summary,
        )

    # Sort feasible plans by weighted_objective_score desc, then plan_id asc (deterministic tie-break)
    sorted_feasible = sorted(
        feasible_plans,
        key=lambda ep: (-ep.weighted_objective_score, ep.plan.plan_id),
    )

    selected = sorted_feasible[0]

    norm_w = weights.normalized_weights()
    summary = (
        f"Selected optimal plan '{selected.plan.plan_id}' ({selected.plan.strategy_type.value}) "
        f"with weighted objective score {selected.weighted_objective_score:.4f} "
        f"among {len(feasible_plans)} feasible candidate plan(s) "
        f"(out of {len(evaluated_plans)} total). "
        f"Configured weights: Delivery ({norm_w['delivery']*100:.0f}%), "
        f"Cost ({norm_w['cost']*100:.0f}%), Quality ({norm_w['quality']*100:.0f}%), "
        f"Energy ({norm_w['energy']*100:.0f}%), Risk ({norm_w['risk']*100:.0f}%)."
    )

    return OptimizationResult(
        selected_plan=selected,
        evaluated_plans=evaluated_plans,
        weights_used=weights,
        comparison_summary=summary,
    )

def _calculate_raw_metrics(
    plan: RecoveryPlan,
    impact: MachineFailureImpact,
) -> tuple:
    assumptions: List[str] = []
    lost_units = max(1.0, impact.capacity_impact.capacity_loss_units)
    remaining_gap = plan.remaining_capacity_gap_units
    recovered_units = plan.recovered_capacity_units

    # 1. Delivery Impact Score (0.0 best, 1.0 worst)
    gap_ratio = remaining_gap / lost_units
    high_priority_count = len(impact.high_priority_orders)
    hp_factor = min(0.4, high_priority_count * 0.1)
    
    if impact.delivery_risk == DeliveryRisk.HIGH:
        risk_baseline = 0.3
    elif impact.delivery_risk == DeliveryRisk.MEDIUM:
        risk_baseline = 0.15
    else:
        risk_baseline = 0.05

    delivery_impact_score = min(1.0, round(gap_ratio * 0.5 + hp_factor + risk_baseline, 3))
    assumptions.append("Delivery impact is calculated from remaining unrecovered unit gap and high-priority order exposure.")

    # 2. Cost Impact USD
    overtime_hours = 0.0
    if plan.strategy_type == RecoveryStrategyType.OVERTIME:
        for action in plan.actions:
            overtime_hours += action.details.get("required_overtime_hours", 0.0)
        overtime_cost = overtime_hours * config.OVERTIME_COST_PER_HOUR
        cost_usd = overtime_cost + (remaining_gap * config.UNRECOVERED_GAP_COST_PER_UNIT)
        assumptions.append(f"Overtime cost calculated at synthetic rate of ${config.OVERTIME_COST_PER_HOUR}/hr.")
    elif plan.strategy_type == RecoveryStrategyType.MACHINE_TRANSFER:
        cost_usd = config.TRANSFER_BASE_COST + (remaining_gap * config.UNRECOVERED_GAP_COST_PER_UNIT)
        assumptions.append(f"Machine transfer includes synthetic base transfer setup cost of ${config.TRANSFER_BASE_COST}.")
    else: # RESEQUENCE
        cost_usd = config.RESEQUENCE_BASE_COST + (remaining_gap * config.UNRECOVERED_GAP_COST_PER_UNIT)
        assumptions.append(f"Resequencing includes synthetic administrative setup cost of ${config.RESEQUENCE_BASE_COST}.")

    cost_usd = round(cost_usd, 2)

    # 3. Quality Penalty Score (0.0 best, 1.0 worst constraint proxy)
    if plan.strategy_type == RecoveryStrategyType.MACHINE_TRANSFER and plan.actions:
        target_health = plan.actions[0].details.get("target_health_score", 100.0)
        quality_penalty_score = round(max(0.0, (100.0 - target_health) / 100.0), 3)
        assumptions.append("Quality penalty proxy is derived from alternative target machine health score.")
    else:
        quality_penalty_score = 0.05
        assumptions.append("Baseline quality penalty proxy applied for non-transfer operations.")

    # 4. Energy Consumption kWh
    energy_kwh = round(recovered_units * config.ENERGY_KWH_PER_UNIT, 1)
    assumptions.append(f"Energy consumption estimated using synthetic rate of {config.ENERGY_KWH_PER_UNIT} kWh/unit.")

    # 5. Risk Impact Score (0.0 best, 1.0 worst)
    if plan.strategy_type == RecoveryStrategyType.MACHINE_TRANSFER:
        strat_risk = 0.30
    elif plan.strategy_type == RecoveryStrategyType.OVERTIME:
        strat_risk = 0.20
    else:
        strat_risk = 0.10

    gap_risk = min(0.4, (remaining_gap / lost_units) * 0.4)
    infeasibility_risk = 0.5 if not plan.feasibility.is_feasible else 0.0
    risk_impact_score = min(1.0, round(strat_risk + gap_risk + infeasibility_risk, 3))
    assumptions.append("Risk score combines strategy complexity, unrecovered capacity gap, and feasibility status.")

    metrics = PlanEvaluationMetrics(
        delivery_impact_score=delivery_impact_score,
        cost_impact_usd=cost_usd,
        quality_penalty_score=quality_penalty_score,
        energy_consumption_kwh=energy_kwh,
        risk_impact_score=risk_impact_score,
        overtime_hours=overtime_hours,
        recovered_capacity_units=recovered_units,
        remaining_capacity_gap_units=remaining_gap,
    )

    return metrics, assumptions
