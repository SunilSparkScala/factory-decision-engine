import re
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, Union

from app.exceptions import UnknownMachineError, InvalidDowntimeError
from app.recovery.models import RecoveryStrategyType
from app.factory.state import get_machine, get_machine_dependencies
from app.simulation import simulate_machine_failure
from app.recovery import generate_recovery_plans
from app.evaluation import (
    evaluate_recovery_plans as engine_evaluate_recovery_plans,
    select_optimal_plan,
)
from app.agent.tools import (
    search_engineering_knowledge,
    map_user_priorities_to_weights,
)
from app.agent.schemas import (
    DecisionResponse,
    CandidatePlanSummary,
    EvidenceItem,
)

logger = logging.getLogger(__name__)

WORD_NUMBERS = {
    "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0, "five": 5.0,
    "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0, "ten": 10.0,
    "eleven": 11.0, "twelve": 12.0, "sixteen": 16.0, "twenty-four": 24.0,
}

EVIDENCE_CATEGORY_MAP = {
    "incident_report": "historical_precedent",
    "machine_manual": "machine_specification",
    "quality": "quality_requirement",
    "production_sop": "operational_procedure",
    "engineering_constraint": "engineering_constraint",
}

class ScenarioParser:
    """
    Deterministic fallback parser for operational decision requests.
    Provides deterministic parameter extraction when Gemini / Google ADK is unavailable.
    In live mode, natural-language orchestration is performed by Gemini/ADK.
    """

    @staticmethod
    def parse(prompt: str) -> Dict[str, Any]:
        """
        Extract machine ID, downtime duration, start time, priorities, and constraints
        from natural language prompt.
        """
        # 1. Machine ID extraction (e.g. M17, M04, M999)
        machine_match = re.search(r"\b(M\d+)\b", prompt, re.IGNORECASE)
        machine_id = machine_match.group(1).upper() if machine_match else None

        # 2. Downtime duration extraction
        downtime_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours|hrs|hr|h)\b", prompt, re.IGNORECASE)
        if downtime_match:
            downtime_hours = float(downtime_match.group(1))
        else:
            word_match = re.search(
                r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|sixteen|twenty-four)\s*(?:hours|hrs|hr|h)\b",
                prompt,
                re.IGNORECASE,
            )
            downtime_hours = WORD_NUMBERS[word_match.group(1).lower()] if word_match else 8.0

        # 3. Policy / operational constraints extraction
        text = prompt.lower()
        disallow_overtime = any(p in text for p in [
            "no overtime",
            "overtime is not allowed",
            "don't use overtime",
            "dont use overtime",
            "do not use overtime",
            "overtime is unavailable",
            "avoid overtime",
            "without overtime",
            "prohibit overtime",
            "overtime disallowed",
            "no ot",
        ])

        disallow_cross_line_transfer = any(p in text for p in [
            "cannot transfer to another line",
            "no cross-line transfer",
            "no cross line transfer",
            "don't move work to another line",
            "dont move work to another line",
            "do not move work to another line",
            "keep everything on line",
            "stay on line",
            "stay on the same line",
            "no line transfer",
            "prohibit line transfer",
        ])

        disallow_all_transfers = any(p in text for p in [
            "no transfer",
            "cannot transfer",
            "do not transfer",
            "cannot move work",
            "cannot move the work",
        ])

        # 4. Priority description
        priorities = prompt

        # 5. Clarification detection: missing machine identifier
        clarification_needed = None
        if not machine_id:
            clarification_needed = (
                "Target machine ID is unspecified in prompt. Please provide an asset ID (e.g. M17, M04, M01) to simulate."
            )

        return {
            "machine_id": machine_id,
            "downtime_hours": downtime_hours,
            "priorities": priorities,
            "disallow_overtime": disallow_overtime,
            "disallow_cross_line_transfer": disallow_cross_line_transfer,
            "disallow_all_transfers": disallow_all_transfers,
            "clarification_needed": clarification_needed,
        }

class DecisionOrchestrator:
    """
    End-to-end Factory Decision Intelligence Orchestrator.
    Executes deterministic simulation, feasibility filtering, multi-objective optimization,
    and engineering knowledge grounding.
    """

    def orchestrate(
        self,
        machine_id: Optional[str],
        downtime_hours: float,
        priorities: Optional[Union[Dict[str, float], str]] = None,
        start_time: Optional[str] = None,
        constraints: Optional[Dict[str, bool]] = None,
        execution_mode: str = "deterministic_fallback",
        fallback_reason: Optional[str] = None,
        clarification_needed: Optional[str] = None,
        eval_fn: Optional[Any] = None,
    ) -> DecisionResponse:
        """Execute complete factory decision intelligence workflow."""
        if not machine_id:
            return DecisionResponse(
                scenario_summary="Unspecified target machine scenario",
                execution_mode=execution_mode,
                fallback_reason=fallback_reason,
                impact_summary={"error": "Target machine ID is unspecified."},
                rationale="Cannot formulate decision: Target machine identifier is required but was not provided.",
                risks=["No target asset identified for scenario evaluation."],
                human_approval_required=True,
                clarification_needed=clarification_needed or "Target machine ID is unspecified in prompt. Please provide an asset ID (e.g. M17, M04, M01) to simulate.",
            )

        constraints = constraints or {}
        disallow_overtime = constraints.get("disallow_overtime", False)
        disallow_cross_line_transfer = constraints.get("disallow_cross_line_transfer", False)
        disallow_all_transfers = constraints.get("disallow_all_transfers", False)

        # ---------------------------------------------------------
        # Step 1: Query machine status & asset dependency chain
        # ---------------------------------------------------------
        try:
            machine = get_machine(machine_id)
            deps = get_machine_dependencies(machine_id)
        except UnknownMachineError as e:
            return DecisionResponse(
                scenario_summary=f"Failure analysis for {machine_id}",
                execution_mode=execution_mode,
                fallback_reason=fallback_reason,
                impact_summary={"error": str(e), "machine_id": machine_id},
                rationale=f"Cannot formulate decision: Unknown machine '{machine_id}' was not found in factory registry.",
                risks=["Target machine is unrecognized in factory database."],
                human_approval_required=True,
                clarification_needed=f"Machine '{machine_id}' is not recognized. Please provide a valid machine ID (e.g. M17).",
            )
        except Exception as e:
            return DecisionResponse(
                scenario_summary=f"Failure analysis for {machine_id}",
                execution_mode=execution_mode,
                fallback_reason=fallback_reason,
                impact_summary={"error": str(e), "machine_id": machine_id},
                rationale=f"Error querying machine dependency state: {e}",
                risks=["Database query error."],
                human_approval_required=True,
            )

        station = deps["station"]
        line = deps["line"]
        dependency_chain = {
            "machine_id": machine.machine_id,
            "machine_type": machine.machine_type,
            "machine_status": machine.status.value,
            "station_id": station["station_id"],
            "station_name": station["name"],
            "operation_type": station["operation_type"],
            "line_id": line["line_id"],
            "line_name": line["name"],
            "affected_order_count": len(deps["affected_orders"]),
            "trace_summary": (
                f"{machine.machine_id} ({machine.machine_type}) -> "
                f"{station['station_id']} ({station['name']}) -> "
                f"{line['line_id']} ({line['name']}) -> "
                f"{station['operation_type']} -> "
                f"{len(deps['affected_orders'])} Production Orders -> Delivery Deadlines"
            ),
        }

        # ---------------------------------------------------------
        # Step 2: Simulate failure impact
        # ---------------------------------------------------------
        try:
            impact = simulate_machine_failure(
                machine_id=machine_id,
                downtime_hours=downtime_hours,
                start_time=start_time,
            )
        except InvalidDowntimeError as e:
            return DecisionResponse(
                scenario_summary=f"Failure simulation on {machine_id}",
                execution_mode=execution_mode,
                fallback_reason=fallback_reason,
                impact_summary={"error": str(e), "machine_id": machine_id, "downtime_hours": downtime_hours},
                rationale=f"Invalid simulation parameter: {e}",
                risks=["Unscheduled downtime duration must be greater than 0.0 hours."],
                human_approval_required=True,
            )
        except Exception as e:
            logger.error(f"Simulation failed: {e}")
            return DecisionResponse(
                scenario_summary=f"Failure simulation on {machine_id}",
                execution_mode=execution_mode,
                fallback_reason=fallback_reason,
                impact_summary={"error": str(e), "machine_id": machine_id},
                rationale=f"Simulation failed: {e}",
                risks=["Unexpected simulation engine failure."],
                human_approval_required=True,
            )

        affected_product_models = sorted(list(set(o.product_model for o in impact.affected_orders)))
        impact_summary = {
            "machine_id": machine_id,
            "downtime_hours": downtime_hours,
            "capacity_loss_units": impact.capacity_impact.capacity_loss_units,
            "capacity_loss_percentage": impact.capacity_impact.capacity_loss_percentage,
            "delivery_risk": impact.delivery_risk.value,
            "affected_order_count": len(impact.affected_orders),
            "high_priority_affected_count": len(impact.high_priority_orders),
            "affected_product_models": affected_product_models,
            "bottleneck_station_id": impact.station.station_id,
            "bottleneck_station_name": impact.station.name,
            "line_id": machine.line_id,
            "alternative_candidates": [
                {
                    "machine_id": a.machine_id,
                    "line_id": a.line_id,
                    "station_id": a.station_id,
                    "status": a.status.value,
                    "health_score": a.health_score,
                }
                for a in impact.alternative_candidates
            ],
        }

        # ---------------------------------------------------------
        # Step 3: Generate recovery plans & apply policy constraints
        # ---------------------------------------------------------
        plans = generate_recovery_plans(impact)

        # Enforce policy / what-if constraints
        for p in plans:
            if disallow_overtime and p.strategy_type == RecoveryStrategyType.OVERTIME:
                p.feasibility.is_feasible = False
                p.feasibility.infeasible_reasons.append("Overtime is strictly disallowed by operational policy / scenario constraint.")

            if disallow_all_transfers and p.strategy_type == RecoveryStrategyType.MACHINE_TRANSFER:
                p.feasibility.is_feasible = False
                p.feasibility.infeasible_reasons.append("Machine transfer is disallowed by operational policy / scenario constraint.")

            if disallow_cross_line_transfer and p.strategy_type == RecoveryStrategyType.MACHINE_TRANSFER:
                target_m_id = p.actions[0].target_machine_id if p.actions else None
                # Check target machine line
                cand_obj = next((c for c in impact.alternative_candidates if c.machine_id == target_m_id), None)
                if cand_obj and cand_obj.line_id != machine.line_id:
                    p.feasibility.is_feasible = False
                    p.feasibility.infeasible_reasons.append(
                        f"Cross-line transfer from {machine.line_id} to {cand_obj.line_id} is disallowed by scenario constraint."
                    )

        # ---------------------------------------------------------
        # ---------------------------------------------------------
        # Step 4: Multi-objective evaluation & optimization
        # ---------------------------------------------------------
        weights_obj = map_user_priorities_to_weights(priorities)
        applied_weights = weights_obj.normalized_weights()

        candidate_summaries: List[CandidatePlanSummary] = []
        selected_plan_summary: Optional[CandidatePlanSummary] = None
        trade_off_metrics: Dict[str, Any] = {}

        if eval_fn is not None:
            mock_eval = eval_fn(
                machine_id=machine_id,
                downtime_hours=downtime_hours,
                weights=priorities,
                start_time=start_time,
            )
            if isinstance(mock_eval, dict):
                if "applied_weights" in mock_eval:
                    applied_weights = mock_eval["applied_weights"]
                for c in mock_eval.get("candidates", []):
                    candidate_summaries.append(
                        CandidatePlanSummary(
                            plan_id=c["plan_id"],
                            strategy_type=c["strategy_type"],
                            description=c.get("description", f"Strategy: {c['strategy_type']}"),
                            is_feasible=c["is_feasible"],
                            infeasibility_reasons=c.get("infeasibility_reasons", []),
                            actions_summary=c.get("actions_summary", "N/A"),
                            weighted_score=c.get("weighted_score"),
                            cost_impact_usd=c.get("cost_usd"),
                            delivery_delay_score=c.get("delay_score"),
                        )
                    )
                if mock_eval.get("selected_plan"):
                    sp_data = mock_eval["selected_plan"]
                    selected_plan_summary = CandidatePlanSummary(
                        plan_id=sp_data["plan_id"],
                        strategy_type=sp_data["strategy_type"],
                        description=sp_data.get("description", f"Strategy: {sp_data['strategy_type']}"),
                        is_feasible=sp_data["is_feasible"],
                        actions_summary=sp_data.get("actions_summary", "N/A"),
                        weighted_score=sp_data.get("weighted_score"),
                        cost_impact_usd=sp_data.get("cost_impact_usd", 0.0),
                        delivery_delay_score=sp_data.get("delivery_delay_score", 0.0),
                        quality_penalty_score=sp_data.get("quality_penalty_score", 0.0),
                        energy_kwh=sp_data.get("energy_kwh", 0.0),
                        risk_score=sp_data.get("risk_score", 0.0),
                    )
                    trade_off_metrics = {
                        "delivery_delay_score": sp_data.get("delivery_delay_score", 0.0),
                        "cost_impact_usd": sp_data.get("cost_impact_usd", 0.0),
                        "quality_penalty_score": sp_data.get("quality_penalty_score", 0.0),
                        "quality_metric_label": "Synthetic quality penalty proxy",
                        "energy_kwh": sp_data.get("energy_kwh", 0.0),
                        "energy_metric_label": "Synthetic energy consumption proxy (kWh)",
                        "risk_score": sp_data.get("risk_score", 0.0),
                        "risk_metric_label": "Synthetic operational risk proxy",
                        "weighted_score": sp_data.get("weighted_score"),
                    }
        else:
            evaluations = engine_evaluate_recovery_plans(plans, impact, weights=weights_obj)
            opt_result = select_optimal_plan(evaluations, weights=weights_obj)

            for e in opt_result.evaluated_plans:
                target_mach = e.plan.actions[0].target_machine_id if e.plan.actions else None
                candidate_summaries.append(
                    CandidatePlanSummary(
                        plan_id=e.plan.plan_id,
                        strategy_type=e.plan.strategy_type.value,
                        description=e.plan.description,
                        is_feasible=e.is_feasible,
                        infeasibility_reasons=e.plan.feasibility.infeasible_reasons,
                        actions_summary="; ".join(
                            f"{a.strategy_type.value}: {a.target_machine_id or 'resequence'}" for a in e.plan.actions
                        ) if e.plan.actions else "No action",
                        weighted_score=round(e.weighted_objective_score, 4),
                        cost_impact_usd=round(e.raw_metrics.cost_impact_usd, 2),
                        delivery_delay_score=round(e.raw_metrics.delivery_impact_score, 2),
                        quality_penalty_score=round(e.raw_metrics.quality_penalty_score, 2),
                        energy_kwh=round(e.raw_metrics.energy_consumption_kwh, 2),
                        risk_score=round(e.raw_metrics.risk_impact_score, 2),
                        target_machine_id=target_mach,
                        target_line_id=e.plan.actions[0].details.get("target_line_id") if e.plan.actions else None,
                    )
                )

            if opt_result.selected_plan:
                sp = opt_result.selected_plan
                target_mach = sp.plan.actions[0].target_machine_id if sp.plan.actions else None
                selected_plan_summary = CandidatePlanSummary(
                    plan_id=sp.plan.plan_id,
                    strategy_type=sp.plan.strategy_type.value,
                    description=sp.plan.description,
                    is_feasible=sp.is_feasible,
                    infeasibility_reasons=sp.plan.feasibility.infeasible_reasons,
                    actions_summary="; ".join(
                        f"{a.strategy_type.value}: {a.target_machine_id or 'resequence'}" for a in sp.plan.actions
                    ) if sp.plan.actions else "No action",
                    weighted_score=round(sp.weighted_objective_score, 4),
                    cost_impact_usd=round(sp.raw_metrics.cost_impact_usd, 2),
                    delivery_delay_score=round(sp.raw_metrics.delivery_impact_score, 2),
                    quality_penalty_score=round(sp.raw_metrics.quality_penalty_score, 2),
                    energy_kwh=round(sp.raw_metrics.energy_consumption_kwh, 2),
                    risk_score=round(sp.raw_metrics.risk_impact_score, 2),
                    target_machine_id=target_mach,
                    target_line_id=sp.plan.actions[0].details.get("target_line_id") if sp.plan.actions else None,
                )
                trade_off_metrics = {
                    "delivery_delay_score": round(sp.raw_metrics.delivery_impact_score, 2),
                    "delivery_metric_label": "Normalized delivery impact score (0.0 best, 1.0 worst)",
                    "cost_impact_usd": round(sp.raw_metrics.cost_impact_usd, 2),
                    "quality_penalty_score": round(sp.raw_metrics.quality_penalty_score, 2),
                    "quality_metric_label": "Synthetic quality penalty proxy",
                    "energy_kwh": round(sp.raw_metrics.energy_consumption_kwh, 2),
                    "energy_metric_label": "Synthetic energy consumption proxy (kWh)",
                    "risk_score": round(sp.raw_metrics.risk_impact_score, 2),
                    "risk_metric_label": "Synthetic operational risk proxy",
                    "weighted_score": round(sp.weighted_objective_score, 4),
                }

        feasible_alternatives = [c for c in candidate_summaries if c.is_feasible]
        infeasible_alternatives = [c for c in candidate_summaries if not c.is_feasible]

        # ---------------------------------------------------------
        # Step 5: Engineering knowledge evidence retrieval
        # ---------------------------------------------------------
        evidence_items: List[EvidenceItem] = []
        seen_doc_ids = set()

        def _add_evidence_doc(doc: Dict[str, Any]) -> None:
            if doc.get("document_id") and doc["document_id"] not in seen_doc_ids:
                seen_doc_ids.add(doc["document_id"])
                ev_cat = EVIDENCE_CATEGORY_MAP.get(doc.get("document_type"), "operational_procedure")
                evidence_items.append(EvidenceItem(**doc, evidence_category=ev_cat))

        # Query A: Machine-specific manual and specs
        kb_machine = search_engineering_knowledge(machine_id=machine_id, limit=2)
        for doc in kb_machine.get("evidence", []):
            _add_evidence_doc(doc)

        # Query B: Strategy-specific SOP or guidelines
        selected_strategy = ""
        if selected_plan_summary:
            selected_strategy = selected_plan_summary.strategy_type.lower()

        strategy_query_map = {
            "machine_transfer": ("quality inspection machine transfer requirements", "quality"),
            "resequence": ("production order resequencing priority handling", "production_sop"),
            "overtime": ("extended shift overtime authorization rules", "production_sop"),
        }

        if selected_strategy in strategy_query_map:
            q_text, topic_filter = strategy_query_map[selected_strategy]
            kb_strat = search_engineering_knowledge(query=q_text, topic=topic_filter, limit=2)
            for doc in kb_strat.get("evidence", []):
                _add_evidence_doc(doc)

        # General operational safety check if machine is degraded
        if machine.status.value == "DEGRADED":
            kb_degraded = search_engineering_knowledge(query="degraded machinery operating guidelines", topic="maintenance", limit=1)
            for doc in kb_degraded.get("evidence", []):
                _add_evidence_doc(doc)

        # ---------------------------------------------------------
        # Step 6: Structured Decision Rationale ("Recommended because...")
        # ---------------------------------------------------------
        high_pri_count = len(impact.high_priority_orders)
        rationale_lines = []

        if selected_plan_summary:
            rationale_lines.append(
                f"Selected plan '{selected_plan_summary.plan_id}' ({selected_plan_summary.strategy_type.upper()}) is recommended because:"
            )
            rationale_lines.append(
                f"1. **Hard Constraints:** It satisfies all operational, station type, and target-line model compatibility constraints."
            )
            if high_pri_count > 0:
                rationale_lines.append(
                    f"2. **High-Priority Protection:** It mitigates schedule disruption for {high_pri_count} critical customer order(s)."
                )
            else:
                rationale_lines.append(
                    "2. **Schedule Integrity:** It successfully preserves delivery schedules for all active production orders."
                )

            deliv_score = selected_plan_summary.delivery_delay_score if selected_plan_summary.delivery_delay_score is not None else 0.0
            cost_val = selected_plan_summary.cost_impact_usd if selected_plan_summary.cost_impact_usd is not None else 0.0
            w_score = selected_plan_summary.weighted_score if selected_plan_summary.weighted_score is not None else 0.0
            rationale_lines.append(
                f"3. **Delivery Impact Score:** Achieves an optimal delivery impact score of {deliv_score:.2f} (normalized penalty score, 0.0 best to 1.0 worst)."
            )
            rationale_lines.append(
                f"4. **Cost Impact:** Incurs an estimated operational cost impact of ${cost_val:.2f} USD."
            )
            rationale_lines.append(
                f"5. **Multi-Objective Trade-Off:** Attains the optimal composite score of {w_score:.4f} "
                f"under applied weights (Delivery: {applied_weights.get('delivery', 0.0):.1%}, Cost: {applied_weights.get('cost', 0.0):.1%}, "
                f"Quality: {applied_weights.get('quality', 0.0):.1%}, Energy: {applied_weights.get('energy', 0.0):.1%}, Risk: {applied_weights.get('risk', 0.0):.1%})."
            )

            # Grounding in knowledge evidence
            transfer_evidence = [
                e for e in evidence_items
                if any(k in e.document_id for k in ["KB-QTY", "KB-SOP", "KB-MAN", "KB-INC", "KB-MNT"])
            ] or evidence_items
            if transfer_evidence:
                top_ev = transfer_evidence[0]
                rationale_lines.append(
                    f"6. **Engineering Guidance:** According to [{top_ev.document_id}] ('{top_ev.title}'): \"{top_ev.excerpt}\""
                )
            else:
                rationale_lines.append(
                    "6. **Engineering Guidance:** No specific engineering document retrieved for this operational pattern."
                )
        else:
            rationale_lines.append(
                "None of the candidate recovery plans met feasibility criteria. "
                "All evaluated alternatives violated hard constraints (such as model compatibility, schedule conflict, or capacity threshold). "
                "Immediate manual supervisory intervention or expedited external maintenance is required."
            )

        # Integrity check: selected_plan MUST exist in candidate_summaries and be feasible
        if selected_plan_summary is not None:
            matching_cand = next(
                (c for c in candidate_summaries if c.plan_id == selected_plan_summary.plan_id), None
            )
            if not matching_cand or not matching_cand.is_feasible:
                logger.error(f"Integrity check failed: Selected plan '{selected_plan_summary.plan_id}' is invalid or infeasible.")
                selected_plan_summary = None

        # Operational risks
        risks = []
        if selected_plan_summary:
            st = selected_plan_summary.strategy_type.lower()
            if "transfer" in st:
                risks.append("Setup and tooling calibration required on target machine before volume run.")
                risks.append("Mandatory First Article Inspection (FAI) must be signed off by QA per standard SOP.")
            elif "overtime" in st:
                risks.append(f"Overtime labor premium incurred ($50.00/hr).")
                risks.append("Shift fatigue and maintenance window compression risk.")
            elif "resequence" in st:
                risks.append("Lower-priority orders will absorb delivery delay in downstream production queues.")
        else:
            risks.append("Complete line throughput halt during downtime window.")

        risks.append("Bottleneck station starvation risk if downtime exceeds forecasted window.")

        # Hard constraints checked
        hard_constraints = [
            "failed_machine_exclusion",
            "machine_status_operational",
            "operation_type_compatibility",
            "target_line_model_compatibility",
            "capacity_availability",
            "schedule_conflict_check",
            "overtime_threshold_limit",
        ]

        # Assumptions
        assumptions = [
            f"Failure initiated at {start_time or 'immediate operational shift'}.",
            "Machine nominal throughput rates and line speeds remain at baseline specifications.",
            "Deterministic cost benchmarks and capacity limits applied per factory policy.",
            "Multi-objective optimization executed strictly via Python evaluation engine.",
        ]

        return DecisionResponse(
            scenario_summary=f"Unscheduled downtime on {machine_id} for {downtime_hours} hours",
            execution_mode=execution_mode,
            fallback_reason=fallback_reason,
            dependency_chain=dependency_chain,
            impact_summary=impact_summary,
            affected_orders=[
                {
                    "order_id": o.order_id,
                    "product_model": o.product_model,
                    "priority": o.priority.value,
                    "quantity": o.quantity,
                    "due_time": o.due_time.isoformat(),
                    "status": o.status.value,
                }
                for o in impact.affected_orders
            ],
            candidate_plans=candidate_summaries,
            feasible_alternatives=feasible_alternatives,
            infeasible_alternatives=infeasible_alternatives,
            selected_plan=selected_plan_summary,
            objective_weights=applied_weights,
            trade_off_metrics=trade_off_metrics,
            engineering_evidence=evidence_items,
            hard_constraints_checked=hard_constraints,
            objectives_scored=["delivery", "cost", "quality", "energy", "risk"],
            assumptions=assumptions,
            rationale="\n".join(rationale_lines),
            risks=risks,
            human_approval_required=True,
            clarification_needed=clarification_needed,
        )

    def what_if(
        self,
        previous_response: DecisionResponse,
        follow_up_prompt: Optional[str] = None,
        updated_downtime: Optional[float] = None,
        updated_priorities: Optional[Union[Dict[str, float], str]] = None,
        updated_machine_id: Optional[str] = None,
        updated_constraints: Optional[Dict[str, bool]] = None,
    ) -> DecisionResponse:
        """
        Execute sensitivity analysis, re-running a fresh deterministic simulation and optimization run.
        Does NOT mutate the factory database.
        """
        # Baseline from previous response
        machine_id = (
            updated_machine_id
            or (previous_response.impact_summary.get("machine_id") if previous_response.impact_summary else None)
            or (previous_response.dependency_chain.get("machine_id") if previous_response.dependency_chain else None)
        )
        downtime = (
            updated_downtime
            if updated_downtime is not None
            else (previous_response.impact_summary.get("downtime_hours", 8.0) if previous_response.impact_summary else 8.0)
        )
        priorities = updated_priorities if updated_priorities is not None else previous_response.objective_weights
        constraints = dict(updated_constraints or {})

        # If a follow-up natural-language prompt is provided, parse it
        if follow_up_prompt:
            parsed = ScenarioParser.parse(follow_up_prompt)
            # Check if prompt specifies a different machine
            if re.search(r"\b(M\d+)\b", follow_up_prompt, re.IGNORECASE):
                machine_id = parsed["machine_id"]
            # Check if prompt specifies a different downtime
            if re.search(r"(\d+(?:\.\d+)?)\s*(?:hours|hrs|hr|h)\b", follow_up_prompt, re.IGNORECASE):
                downtime = parsed["downtime_hours"]

            # Check if prompt specifies constraint changes
            if parsed.get("disallow_overtime"):
                constraints["disallow_overtime"] = True
            if parsed.get("disallow_cross_line_transfer"):
                constraints["disallow_cross_line_transfer"] = True
            if parsed.get("disallow_all_transfers"):
                constraints["disallow_all_transfers"] = True

            # Check if prompt specifies priority change
            p_text = follow_up_prompt.lower()
            if any(w in p_text for w in ["prioritize", "priority", "only", "minimize", "cost", "delivery", "energy", "quality"]):
                priorities = follow_up_prompt

        return self.orchestrate(
            machine_id=machine_id,
            downtime_hours=downtime,
            priorities=priorities,
            constraints=constraints,
            execution_mode=previous_response.execution_mode,
            fallback_reason=previous_response.fallback_reason,
        )
