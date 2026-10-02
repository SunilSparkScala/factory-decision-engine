from typing import Dict, List, Optional, Any, Union
import logging

from app.exceptions import UnknownMachineError, InvalidDowntimeError
from app.factory import get_factory_state as _get_factory_state
from app.factory import get_machine_status as _get_machine_status
from app.factory.state import get_factory_summary as _get_factory_summary
from app.simulation import simulate_machine_failure as _simulate_machine_failure
from app.recovery import generate_recovery_plans as _generate_recovery_plans
from app.evaluation import (
    ObjectiveWeights,
    evaluate_recovery_plans as _evaluate_recovery_plans,
    select_optimal_plan as _select_optimal_plan,
)
from app.knowledge import search_engineering_knowledge as _search_engineering_knowledge

logger = logging.getLogger(__name__)

def map_user_priorities_to_weights(
    priorities: Optional[Union[Dict[str, float], str, List[str]]] = None
) -> ObjectiveWeights:
    """
    Map natural-language priorities or numerical dictionaries into a valid ObjectiveWeights object.
    
    Supports explicit weighting, single-intent dominance, dual-intent blending, and all-objectives balancing:
    - Explicit dict/list: normalized directly to ObjectiveWeights.
    - Single intent (delivery only, cost only, quality only, etc.): dominant weight (~0.65).
    - Dual intents (delivery + cost, delivery + energy, quality + delivery, etc.): blended dominant weights (0.40 / 0.40).
    - All objectives / balanced: equal representation (0.20 each).
    - Default factory baseline if unspecified: delivery 0.35, cost 0.30, quality 0.15, energy 0.10, risk 0.10.
    """
    ALL_DIMENSIONS = {"delivery", "cost", "quality", "energy", "risk"}

    # If full explicit 5-dimension dictionary is provided by test/API caller
    if isinstance(priorities, dict):
        keys = {k.replace("_weight", "") for k in priorities.keys()}
        # If all 5 dimensions are explicitly provided in dict
        if ALL_DIMENSIONS.issubset(keys):
            d = float(priorities.get("delivery", priorities.get("delivery_weight", 0.35)))
            c = float(priorities.get("cost", priorities.get("cost_weight", 0.30)))
            q = float(priorities.get("quality", priorities.get("quality_weight", 0.15)))
            e = float(priorities.get("energy", priorities.get("energy_weight", 0.10)))
            r = float(priorities.get("risk", priorities.get("risk_weight", 0.10)))
            return ObjectiveWeights(
                delivery_weight=d,
                cost_weight=c,
                quality_weight=q,
                energy_weight=e,
                risk_weight=r,
            )
        # Otherwise, dict is an intent specification (e.g. from Gemini tool call {'delivery': 0.4, 'cost': 0.6})
        # Extract intended dimensions deterministically and route to authoritative weights
        prioritized_dims = [dim for dim in ["delivery", "cost", "quality", "energy", "risk"] if priorities.get(dim, priorities.get(f"{dim}_weight", 0)) > 0]
        if prioritized_dims:
            priorities = " ".join(prioritized_dims)
        else:
            priorities = None

    if isinstance(priorities, list):
        priorities = " ".join(priorities)

    # Defaults if empty or non-string
    if not priorities or not isinstance(priorities, str):
        return ObjectiveWeights(
            delivery_weight=0.35,
            cost_weight=0.30,
            quality_weight=0.15,
            energy_weight=0.10,
            risk_weight=0.10,
        )

    text = priorities.lower()

    # Explicit check for "all objectives" or balanced
    if "all objectives" in text or "balanced" in text or "equal weight" in text:
        return ObjectiveWeights(
            delivery_weight=0.20,
            cost_weight=0.20,
            quality_weight=0.20,
            energy_weight=0.20,
            risk_weight=0.20,
        )

    has_delivery = any(w in text for w in [
        "delivery", "deliveries", "deliver", "deadline", "on time", "on-time",
        "high priority", "high-priority", "protect customer", "delay", "schedule", "due date"
    ])
    has_cost = any(w in text for w in [
        "cost", "minimize cost", "minimizing cost", "budget", "cheap", "expense", "economic"
    ])
    has_quality = any(w in text for w in [
        "quality", "defect", "inspection", "tolerance", "scrap"
    ])
    has_energy = any(w in text for w in [
        "energy", "power", "green", "kwh", "carbon"
    ])
    has_risk = any(w in text for w in [
        "risk", "overtime", "avoid overtime", "shift fatigue", "safety"
    ])

    matched = []
    if has_delivery:
        matched.append("delivery")
    if has_cost:
        matched.append("cost")
    if has_quality:
        matched.append("quality")
    if has_energy:
        matched.append("energy")
    if has_risk:
        matched.append("risk")

    if len(matched) == 5:
        return ObjectiveWeights(
            delivery_weight=0.20,
            cost_weight=0.20,
            quality_weight=0.20,
            energy_weight=0.20,
            risk_weight=0.20,
        )

    # Specific documented dual intents
    matched_set = set(matched)
    if matched_set == {"delivery", "cost"}:
        return ObjectiveWeights(
            delivery_weight=0.40,
            cost_weight=0.40,
            quality_weight=0.10,
            energy_weight=0.05,
            risk_weight=0.05,
        )
    if matched_set == {"delivery", "energy"}:
        return ObjectiveWeights(
            delivery_weight=0.40,
            cost_weight=0.10,
            quality_weight=0.05,
            energy_weight=0.40,
            risk_weight=0.05,
        )
    if matched_set == {"delivery", "quality"} or matched_set == {"quality", "delivery"}:
        return ObjectiveWeights(
            delivery_weight=0.40,
            cost_weight=0.10,
            quality_weight=0.40,
            energy_weight=0.05,
            risk_weight=0.05,
        )
    if matched_set == {"cost", "quality"}:
        return ObjectiveWeights(
            delivery_weight=0.10,
            cost_weight=0.40,
            quality_weight=0.40,
            energy_weight=0.05,
            risk_weight=0.05,
        )
    if matched_set == {"cost", "energy"}:
        return ObjectiveWeights(
            delivery_weight=0.10,
            cost_weight=0.40,
            quality_weight=0.05,
            energy_weight=0.40,
            risk_weight=0.05,
        )

    # Generic multi-intent blending (if 2+ arbitrary matches)
    if len(matched) >= 2:
        weights_dict = {}
        for dim in ["delivery", "cost", "quality", "energy", "risk"]:
            weights_dict[dim] = 4.0 if dim in matched_set else 0.5
        total = sum(weights_dict.values())
        return ObjectiveWeights(
            delivery_weight=round(weights_dict["delivery"] / total, 4),
            cost_weight=round(weights_dict["cost"] / total, 4),
            quality_weight=round(weights_dict["quality"] / total, 4),
            energy_weight=round(weights_dict["energy"] / total, 4),
            risk_weight=round(weights_dict["risk"] / total, 4),
        )

    # Single intent dominance
    if len(matched) == 1:
        single = matched[0]
        if single == "delivery":
            return ObjectiveWeights(delivery_weight=0.65, cost_weight=0.15, quality_weight=0.10, energy_weight=0.05, risk_weight=0.05)
        elif single == "cost":
            return ObjectiveWeights(delivery_weight=0.15, cost_weight=0.65, quality_weight=0.10, energy_weight=0.05, risk_weight=0.05)
        elif single == "quality":
            return ObjectiveWeights(delivery_weight=0.15, cost_weight=0.10, quality_weight=0.65, energy_weight=0.05, risk_weight=0.05)
        elif single == "energy":
            return ObjectiveWeights(delivery_weight=0.15, cost_weight=0.15, quality_weight=0.05, energy_weight=0.60, risk_weight=0.05)
        elif single == "risk":
            return ObjectiveWeights(delivery_weight=0.15, cost_weight=0.15, quality_weight=0.05, energy_weight=0.05, risk_weight=0.60)

    # Default factory baseline if no matching keywords found
    return ObjectiveWeights(
        delivery_weight=0.35,
        cost_weight=0.30,
        quality_weight=0.15,
        energy_weight=0.10,
        risk_weight=0.10,
    )

def get_factory_state() -> Dict[str, Any]:
    """
    Query the current factory operational metrics, total machines, lines, and active order counts.
    
    Returns:
        Structured dictionary containing summary counts of active assets and production status.
    """
    try:
        summary = _get_factory_summary()
        return {
            "success": True,
            "total_machines": summary.get("total_machines", 0),
            "healthy_machines": summary.get("healthy_machines", 0),
            "degraded_machines": summary.get("degraded_machines", 0),
            "down_machines": summary.get("down_machines", 0),
            "total_lines": summary.get("total_lines", 0),
            "total_stations": summary.get("total_stations", 0),
            "active_orders_count": summary.get("active_orders_count", 0),
        }
    except Exception as e:
        logger.error(f"Error querying factory state: {e}")
        return {"success": False, "error": str(e)}

def get_machine_status(machine_id: str) -> Dict[str, Any]:
    """
    Retrieve operational status, station, line, and health score for a specific machine.
    
    Args:
        machine_id: Unique machine identifier (e.g. 'M17', 'M01').
        
    Returns:
        Structured dictionary with machine operational details or error information.
    """
    try:
        from app.factory.state import get_machine
        machine = get_machine(machine_id)
        status = _get_machine_status(machine_id)
        status_val = status.value if hasattr(status, "value") else str(status)
        return {
            "success": True,
            "machine_id": machine.machine_id,
            "machine_type": machine.machine_type,
            "status": status_val,
            "health_score": machine.health_score,
            "station_id": machine.station_id,
            "line_id": machine.line_id,
            "capacity_per_hour": machine.capacity_per_hour,
            "cycle_time_seconds": machine.cycle_time_seconds,
            "is_available": status_val in ("HEALTHY", "DEGRADED"),
        }
    except UnknownMachineError as e:
        return {"success": False, "error": f"Unknown machine '{machine_id}': {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def simulate_machine_failure(
    machine_id: str,
    downtime_hours: float,
    start_time: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Deterministically simulate machine failure impact without modifying database state.
    
    Args:
        machine_id: Identifier of the failing machine.
        downtime_hours: Duration of unscheduled downtime in hours (> 0).
        start_time: Optional ISO timestamp of failure initiation.
        
    Returns:
        Structured dictionary detailing lost capacity hours, affected orders, and bottleneck stations.
    """
    try:
        impact = _simulate_machine_failure(
            machine_id=machine_id,
            downtime_hours=downtime_hours,
            start_time=start_time,
        )
        return {
            "success": True,
            "scenario_type": impact.scenario.scenario_type.value if hasattr(impact.scenario.scenario_type, "value") else str(impact.scenario.scenario_type),
            "machine_id": impact.machine.machine_id,
            "downtime_hours": impact.capacity_impact.downtime_hours,
            "capacity_loss_units": impact.capacity_impact.capacity_loss_units,
            "capacity_loss_percentage": impact.capacity_impact.capacity_loss_percentage,
            "delivery_risk": impact.delivery_risk.value if hasattr(impact.delivery_risk, "value") else str(impact.delivery_risk),
            "affected_order_count": len(impact.affected_orders),
            "high_priority_affected_count": len(impact.high_priority_orders),
            "affected_orders": [
                {
                    "order_id": o.order_id,
                    "product_model": o.product_model,
                    "priority": o.priority.value if hasattr(o.priority, "value") else str(o.priority),
                    "quantity": o.quantity,
                    "due_time": o.due_time.isoformat() if hasattr(o.due_time, "isoformat") else str(o.due_time),
                    "status": o.status.value if hasattr(o.status, "value") else str(o.status),
                }
                for o in impact.affected_orders
            ],
            "bottleneck_station_id": impact.station.station_id,
            "alternative_machines": [
                {
                    "machine_id": a.machine_id,
                    "station_id": a.station_id,
                    "line_id": a.line_id,
                    "status": a.status.value if hasattr(a.status, "value") else str(a.status),
                    "health_score": a.health_score,
                }
                for a in impact.alternative_candidates
            ],
        }
    except (UnknownMachineError, InvalidDowntimeError) as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Unexpected simulation failure: {e}")
        return {"success": False, "error": str(e)}

def generate_recovery_plans(
    machine_id: str,
    downtime_hours: float,
    start_time: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate alternative recovery plans (machine transfer, priority resequencing, overtime)
    for a machine failure scenario, validating feasibility against operational constraints.
    
    Args:
        machine_id: Machine suffering downtime.
        downtime_hours: Downtime duration in hours.
        start_time: Optional start timestamp.
        
    Returns:
        Structured dictionary containing candidate recovery plans with feasibility flags.
    """
    try:
        impact = _simulate_machine_failure(
            machine_id=machine_id,
            downtime_hours=downtime_hours,
            start_time=start_time,
        )
        plans = _generate_recovery_plans(impact)
        return {
            "success": True,
            "machine_id": machine_id,
            "downtime_hours": downtime_hours,
            "total_plans_generated": len(plans),
            "plans": [
                {
                    "plan_id": p.plan_id,
                    "strategy_type": p.strategy_type.value if hasattr(p.strategy_type, "value") else str(p.strategy_type),
                    "description": p.description,
                    "is_feasible": p.feasibility.is_feasible,
                    "infeasibility_reasons": p.feasibility.infeasible_reasons,
                    "actions_summary": "; ".join(
                        f"{a.strategy_type.value if hasattr(a.strategy_type, 'value') else str(a.strategy_type)}: {a.target_machine_id or 'resequence'}" for a in p.actions
                    ) if p.actions else "No action",
                }
                for p in plans
            ],
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def evaluate_recovery_plans(
    machine_id: str,
    downtime_hours: float,
    weights: Optional[Union[Dict[str, float], str]] = None,
    start_time: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Perform multi-objective evaluation and optimization across candidate recovery plans,
    scoring candidates across cost, delivery delay, quality penalty, energy, and risk.
    
    Args:
        machine_id: Machine suffering downtime.
        downtime_hours: Downtime duration in hours.
        weights: Optional priority description, intent dimensions, or dictionary. Deterministic application logic maps intent to authoritative predefined objective weights.
        start_time: Optional start timestamp.
        
    Returns:
        Structured dictionary indicating the mathematically optimal plan and metric trade-offs.
    """
    try:
        impact = _simulate_machine_failure(
            machine_id=machine_id,
            downtime_hours=downtime_hours,
            start_time=start_time,
        )
        plans = _generate_recovery_plans(impact)
        weights_obj = map_user_priorities_to_weights(weights)
        evaluations = _evaluate_recovery_plans(plans, impact, weights=weights_obj)
        opt_result = _select_optimal_plan(evaluations, weights=weights_obj)

        selected_plan_info = None
        if opt_result.selected_plan:
            sp = opt_result.selected_plan
            selected_plan_info = {
                "plan_id": sp.plan.plan_id,
                "strategy_type": sp.plan.strategy_type.value if hasattr(sp.plan.strategy_type, "value") else str(sp.plan.strategy_type),
                "description": sp.plan.description,
                "weighted_score": round(sp.weighted_objective_score, 4),
                "is_feasible": sp.is_feasible,
                "cost_impact_usd": round(sp.raw_metrics.cost_impact_usd, 2),
                "delivery_delay_score": round(sp.raw_metrics.delivery_impact_score, 2),
                "quality_penalty_score": round(sp.raw_metrics.quality_penalty_score, 2),
                "energy_kwh": round(sp.raw_metrics.energy_consumption_kwh, 2),
                "risk_score": round(sp.raw_metrics.risk_impact_score, 2),
                "actions_summary": "; ".join(
                    f"{a.strategy_type.value if hasattr(a.strategy_type, 'value') else str(a.strategy_type)}: {a.target_machine_id or 'resequence'}" for a in sp.plan.actions
                ) if sp.plan.actions else "No action",
            }

        return {
            "success": True,
            "machine_id": machine_id,
            "downtime_hours": downtime_hours,
            "selected_plan": selected_plan_info,
            "has_feasible_plan": selected_plan_info is not None,
            "comparison_summary": opt_result.comparison_summary,
            "applied_weights": weights_obj.normalized_weights(),
            "total_plans_evaluated": len(opt_result.evaluated_plans),
            "feasible_plans_count": sum(1 for e in opt_result.evaluated_plans if e.is_feasible),
            "candidates": [
                {
                    "plan_id": e.plan.plan_id,
                    "strategy_type": e.plan.strategy_type.value if hasattr(e.plan.strategy_type, "value") else str(e.plan.strategy_type),
                    "is_feasible": e.is_feasible,
                    "weighted_score": round(e.weighted_objective_score, 4),
                    "cost_usd": round(e.raw_metrics.cost_impact_usd, 2),
                    "delay_score": round(e.raw_metrics.delivery_impact_score, 2),
                    "infeasibility_reasons": e.plan.feasibility.infeasible_reasons,
                }
                for e in opt_result.evaluated_plans
            ],
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def search_engineering_knowledge(
    query: Optional[str] = None,
    machine_id: Optional[str] = None,
    station_id: Optional[str] = None,
    line_id: Optional[str] = None,
    topic: Optional[str] = None,
    document_type: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """
    Search engineering SOPs, machine manuals, quality standards, and incident reports for verifiable evidence.
    
    Args:
        query: Free-text search terms (e.g. 'quality inspection after machine transfer').
        machine_id: Target machine filter (e.g. 'M17').
        station_id: Station filter (e.g. 'STA_B2').
        line_id: Line filter (e.g. 'LINE_B').
        topic: Topic filter (e.g. 'quality', 'overtime', 'welding').
        document_type: Category filter ('machine_manual', 'maintenance', 'quality', 'production_sop', 'incident_report', 'engineering_constraint').
        limit: Maximum results to return (default 5).
        
    Returns:
        Structured dictionary containing list of retrieved evidence documents with excerpts.
    """
    try:
        results = _search_engineering_knowledge(
            query=query,
            machine_id=machine_id,
            station_id=station_id,
            line_id=line_id,
            topic=topic,
            document_type=document_type,
            limit=limit,
        )
        return {
            "success": True,
            "query": query,
            "count": len(results),
            "evidence": [
                {
                    "document_id": r.document_id,
                    "title": r.title,
                    "document_type": r.document_type,
                    "relevance_score": r.relevance_score,
                    "excerpt": r.excerpt,
                    "matched_topics": r.matched_topics,
                    "machine_ids": r.machine_ids,
                    "station_ids": r.station_ids,
                    "line_ids": r.line_ids,
                    "source_type": r.source_type,
                }
                for r in results
            ],
        }
    except Exception as e:
        return {"success": False, "error": str(e), "evidence": []}

ALL_AGENT_TOOLS = [
    get_factory_state,
    get_machine_status,
    simulate_machine_failure,
    generate_recovery_plans,
    evaluate_recovery_plans,
    search_engineering_knowledge,
]
