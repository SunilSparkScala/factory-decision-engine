import os
import re
import logging
from typing import Optional, Dict, List, Any, Union

from google.adk import Agent, Runner
from google.adk.sessions import InMemorySessionService
from app.agent.prompts import AGENT_SYSTEM_INSTRUCTION
from app.agent.tools import (
    ALL_AGENT_TOOLS,
    get_factory_state,
    get_machine_status,
    simulate_machine_failure,
    generate_recovery_plans,
    evaluate_recovery_plans,
    search_engineering_knowledge,
    map_user_priorities_to_weights,
)
from app.agent.schemas import (
    DecisionResponse,
    EvidenceItem,
    CandidatePlanSummary,
)

logger = logging.getLogger(__name__)

class FactoryDecisionAgent:
    """
    Decision-support agent for the Factory Decision Engine.
    Orchestrates deterministic simulation, optimization, and engineering knowledge retrieval.
    Powered by Gemini & Google ADK.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.model_name = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        
        # Propagate API key to os.environ so Google ADK and GenAI clients find it
        if self.api_key:
            os.environ["GEMINI_API_KEY"] = self.api_key
            os.environ["GOOGLE_API_KEY"] = self.api_key

        # Initialize Google ADK Agent
        self.adk_agent = Agent(
            name="factory_decision_engine_agent",
            model=self.model_name,
            instruction=AGENT_SYSTEM_INSTRUCTION,
            tools=ALL_AGENT_TOOLS,
        )

        # In-memory session service suitable for local development/testing
        self.session_service = InMemorySessionService()

        # Google ADK Runner
        self.runner = Runner(
            app_name="factory_decision_engine",
            agent=self.adk_agent,
            session_service=self.session_service,
            auto_create_session=True,
        )

    def orchestrate_scenario(
        self,
        machine_id: str,
        downtime_hours: float,
        priorities: Optional[Union[Dict[str, float], str]] = None,
        start_time: Optional[str] = None,
        execution_mode: str = "deterministic_fallback",
        fallback_reason: Optional[str] = None,
    ) -> DecisionResponse:
        """
        Execute deterministic end-to-end decision orchestration:
        1. Inspect machine status
        2. Simulate failure impact
        3. Evaluate recovery plans with user priorities
        4. Retrieve relevant engineering evidence
        5. Formulate structured recommendation
        """
        # Step 1: Query machine status
        machine_status = get_machine_status(machine_id)
        if not machine_status.get("success"):
            return DecisionResponse(
                scenario_summary=f"Failure analysis for {machine_id}",
                assumptions=[],
                impact_summary={"error": machine_status.get("error", "Unknown machine")},
                rationale=f"Cannot formulate recovery plan: {machine_status.get('error')}",
                risks=["Target machine is unrecognized in factory database."],
                human_approval_required=True,
                execution_mode=execution_mode,
                fallback_reason=fallback_reason,
            )

        # Step 2: Simulate failure impact
        sim_result = simulate_machine_failure(
            machine_id=machine_id,
            downtime_hours=downtime_hours,
            start_time=start_time,
        )
        if not sim_result.get("success"):
            return DecisionResponse(
                scenario_summary=f"Failure simulation on {machine_id}",
                assumptions=[],
                impact_summary={"error": sim_result.get("error", "Simulation failure")},
                rationale=f"Simulation failed: {sim_result.get('error')}",
                risks=["Invalid simulation parameters."],
                human_approval_required=True,
                execution_mode=execution_mode,
                fallback_reason=fallback_reason,
            )

        # Step 3: Multi-objective evaluation & optimal plan selection
        eval_result = evaluate_recovery_plans(
            machine_id=machine_id,
            downtime_hours=downtime_hours,
            weights=priorities,
            start_time=start_time,
        )

        # Step 4: Retrieve engineering evidence
        evidence_items: List[EvidenceItem] = []
        seen_doc_ids = set()

        # Query A: Machine-specific manual and specs
        kb_machine = search_engineering_knowledge(machine_id=machine_id, limit=2)
        for doc in kb_machine.get("evidence", []):
            if doc["document_id"] not in seen_doc_ids:
                seen_doc_ids.add(doc["document_id"])
                evidence_items.append(EvidenceItem(**doc))

        # Query B: Strategy-specific SOP or guidelines
        strategy_type = ""
        if eval_result.get("selected_plan"):
            strategy_type = str(eval_result["selected_plan"].get("strategy_type", "")).lower()

        strategy_query_map = {
            "machine_transfer": ("quality inspection machine transfer requirements", "quality"),
            "resequence": ("production order resequencing priority handling", "sop"),
            "resequencing": ("production order resequencing priority handling", "sop"),
            "overtime": ("extended shift overtime authorization rules", "overtime"),
        }

        if strategy_type in strategy_query_map:
            query_text, topic_filter = strategy_query_map[strategy_type]
            kb_strategy = search_engineering_knowledge(query=query_text, topic=topic_filter, limit=2)
            for doc in kb_strategy.get("evidence", []):
                if doc["document_id"] not in seen_doc_ids:
                    seen_doc_ids.add(doc["document_id"])
                    evidence_items.append(EvidenceItem(**doc))

        # General operational safety check if machine is degraded
        if machine_status.get("status") == "DEGRADED":
            kb_degraded = search_engineering_knowledge(query="degraded machinery operating guidelines", topic="maintenance", limit=1)
            for doc in kb_degraded.get("evidence", []):
                if doc["document_id"] not in seen_doc_ids:
                    seen_doc_ids.add(doc["document_id"])
                    evidence_items.append(EvidenceItem(**doc))

        # Step 5: Format candidates & selected plan
        candidate_plans: List[CandidatePlanSummary] = []
        for c in eval_result.get("candidates", []):
            candidate_plans.append(
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

        selected_plan_summary = None
        if eval_result.get("selected_plan"):
            sp = eval_result["selected_plan"]
            selected_plan_summary = CandidatePlanSummary(
                plan_id=sp["plan_id"],
                strategy_type=sp["strategy_type"],
                description=sp["description"],
                is_feasible=sp["is_feasible"],
                actions_summary=sp["actions_summary"],
                weighted_score=sp["weighted_score"],
                cost_impact_usd=sp["cost_impact_usd"],
                delivery_delay_score=sp["delivery_delay_score"],
                quality_penalty_score=sp["quality_penalty_score"],
                energy_kwh=sp["energy_kwh"],
                risk_score=sp["risk_score"],
            )

        # Step 6: Formulate rationale & risks
        applied_weights = eval_result.get("applied_weights", {})
        high_priority_count = sim_result.get("high_priority_affected_count", 0)

        rationale_parts = []
        if selected_plan_summary:
            rationale_parts.append(
                f"Selected plan '{selected_plan_summary.plan_id}' ({selected_plan_summary.strategy_type.upper()}) "
                f"achieves the optimal multi-objective score of {selected_plan_summary.weighted_score:.4f} "
                f"under applied weights (Delivery: {applied_weights.get('delivery', 0.0):.1%}, Cost: {applied_weights.get('cost', 0.0):.1%})."
            )
            if high_priority_count > 0:
                rationale_parts.append(
                    f"Successfully mitigates schedule disruption for {high_priority_count} critical high-priority customer order(s)."
                )

            # Evidence grounding
            transfer_evidence = [e for e in evidence_items if "KB-QTY" in e.document_id or "KB-SOP" in e.document_id]
            if transfer_evidence:
                top_ev = transfer_evidence[0]
                rationale_parts.append(
                    f"According to [{top_ev.document_id}] ('{top_ev.title}'): \"{top_ev.excerpt}\""
                )
            else:
                rationale_parts.append("No supporting engineering document was retrieved for this operational pattern.")
        else:
            rationale_parts.append(
                "None of the candidate recovery plans met feasibility criteria. "
                "Immediate manual supervisory intervention or expedited external maintenance is required."
            )

        risks = []
        if selected_plan_summary:
            st_lower = selected_plan_summary.strategy_type.lower()
            if st_lower == "machine_transfer":
                risks.append("Setup and tooling calibration required on target machine before volume run.")
                risks.append("Mandatory First Article Inspection (FAI) must be signed off by QA.")
            elif st_lower == "overtime":
                risks.append("Overtime labor premium incurred ($50.00/hr).")
                risks.append("Shift fatigue and maintenance window compression risk.")
            elif st_lower in ("resequence", "resequencing"):
                risks.append("Lower-priority orders will absorb delay into downstream production queues.")
        else:
            risks.append("Complete line throughput halt during downtime window.")

        risks.append("Bottleneck station starvation risk if downtime exceeds forecasted window.")

        # Step 7: Construct final structured DecisionResponse
        return DecisionResponse(
            scenario_summary=f"Unscheduled downtime on {machine_id} for {downtime_hours} hours",
            assumptions=[
                f"Failure initiated at {start_time or 'immediate operational shift'}.",
                "Machine nominal throughput rates and line speeds remain at baseline specifications.",
                "Deterministic cost benchmarks and capacity limits applied per factory policy.",
            ],
            impact_summary={
                "machine_id": machine_id,
                "downtime_hours": downtime_hours,
                "capacity_loss_units": sim_result.get("capacity_loss_units", 0.0),
                "capacity_loss_percentage": sim_result.get("capacity_loss_percentage", 0.0),
                "delivery_risk": sim_result.get("delivery_risk", "LOW"),
                "affected_order_count": sim_result.get("affected_order_count", 0),
                "high_priority_affected_count": sim_result.get("high_priority_affected_count", 0),
                "bottleneck_station_id": sim_result.get("bottleneck_station_id"),
                "alternative_machines": sim_result.get("alternative_machines", []),
            },
            affected_orders=sim_result.get("affected_orders", []),
            candidate_plans=candidate_plans,
            selected_plan=selected_plan_summary,
            objective_weights=applied_weights,
            engineering_evidence=evidence_items,
            rationale=" ".join(rationale_parts),
            risks=risks,
            human_approval_required=True,
            execution_mode=execution_mode,
            fallback_reason=fallback_reason,
        )

    def _parse_scenario_prompt(self, prompt: str) -> Dict[str, Any]:
        """Extract machine ID, downtime hours, and priority keywords from natural-language text."""
        # 1. Machine ID
        machine_match = re.search(r"\b(M\d{2})\b", prompt, re.IGNORECASE)
        machine_id = machine_match.group(1).upper() if machine_match else "M17"

        # 2. Downtime hours
        downtime_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours|hrs|hr|h)\b", prompt, re.IGNORECASE)
        downtime_hours = float(downtime_match.group(1)) if downtime_match else 8.0

        # 3. Priority description
        priorities = prompt

        return {
            "machine_id": machine_id,
            "downtime_hours": downtime_hours,
            "priorities": priorities,
        }

    def _run_live_adk(self, prompt: str) -> DecisionResponse:
        """
        Execute prompt via Google ADK Runner + Gemini Agent session.
        Listens to event stream, capturing tool calls, tool responses, and model rationale.
        """
        import uuid
        from google.genai import types

        session_id = f"session_{uuid.uuid4().hex[:12]}"
        user_msg = types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt)],
        )

        logger.info(f"Starting ADK Runner session '{session_id}' with model '{self.model_name}'...")
        tool_calls: List[Dict[str, Any]] = []
        tool_responses: List[Dict[str, Any]] = []
        final_text_parts: List[str] = []

        for event in self.runner.run(
            user_id="plant_operator",
            session_id=session_id,
            new_message=user_msg,
        ):
            fcs = event.get_function_calls()
            if fcs:
                for fc in fcs:
                    tool_calls.append({"name": fc.name, "args": fc.args or {}})
                    logger.info(f"ADK tool call: {fc.name}({fc.args})")

            frs = event.get_function_responses()
            if frs:
                for fr in frs:
                    resp_val = fr.response
                    if isinstance(resp_val, dict) and "result" in resp_val and isinstance(resp_val["result"], dict):
                        resp_val = resp_val["result"]
                    tool_responses.append({"name": fr.name, "response": resp_val})
                    logger.info(f"ADK tool response: {fr.name}")

            if event.is_final_response():
                if event.message and event.message.parts:
                    for part in event.message.parts:
                        if hasattr(part, "text") and part.text:
                            final_text_parts.append(part.text)

        final_rationale = "\n".join(final_text_parts).strip()
        logger.info(
            f"ADK live loop completed: {len(tool_calls)} calls, "
            f"{len(tool_responses)} responses, text length: {len(final_rationale)}"
        )

        return self._convert_adk_run_to_decision(
            prompt=prompt,
            tool_calls=tool_calls,
            tool_responses=tool_responses,
            model_text=final_rationale,
        )

    def _convert_adk_run_to_decision(
        self,
        prompt: str,
        tool_calls: List[Dict[str, Any]],
        tool_responses: List[Dict[str, Any]],
        model_text: str,
    ) -> DecisionResponse:
        """
        Controlled conversion layer: validates and formats ADK tool outputs into DecisionResponse.
        Strictly preserves deterministic numerical authority in Python.
        """
        parsed_params = self._parse_scenario_prompt(prompt)
        machine_id = parsed_params["machine_id"]
        downtime_hours = parsed_params["downtime_hours"]
        priorities = parsed_params["priorities"]

        # 1. Extract simulation results from tool responses or run deterministically
        sim_resp = None
        for tr in reversed(tool_responses):
            if tr.get("name") == "simulate_machine_failure" and isinstance(tr.get("response"), dict) and tr["response"].get("success"):
                sim_resp = tr["response"]
                break

        if sim_resp:
            machine_id = sim_resp.get("machine_id", machine_id)
            downtime_hours = float(sim_resp.get("downtime_hours", downtime_hours))
        else:
            sim_resp = simulate_machine_failure(machine_id, downtime_hours)

        # 2. Extract multi-objective evaluation results or run deterministically
        eval_resp = None
        for tr in reversed(tool_responses):
            if tr.get("name") == "evaluate_recovery_plans" and isinstance(tr.get("response"), dict) and tr["response"].get("success"):
                eval_resp = tr["response"]
                break

        if not eval_resp or not eval_resp.get("success"):
            eval_resp = evaluate_recovery_plans(machine_id, downtime_hours, weights=priorities)

        # 3. Extract knowledge retrieval results
        evidence_items: List[EvidenceItem] = []
        seen_doc_ids = set()
        for tr in tool_responses:
            if tr.get("name") == "search_engineering_knowledge" and isinstance(tr.get("response"), dict):
                for doc in tr["response"].get("evidence", []):
                    if isinstance(doc, dict) and doc.get("document_id") and doc["document_id"] not in seen_doc_ids:
                        seen_doc_ids.add(doc["document_id"])
                        evidence_items.append(EvidenceItem(**doc))

        if not evidence_items:
            kb_res = search_engineering_knowledge(machine_id=machine_id, limit=2)
            for doc in kb_res.get("evidence", []):
                if doc["document_id"] not in seen_doc_ids:
                    seen_doc_ids.add(doc["document_id"])
                    evidence_items.append(EvidenceItem(**doc))

        # 4. Construct candidate plans from deterministic evaluation
        candidate_plans: List[CandidatePlanSummary] = []
        for c in eval_resp.get("candidates", []):
            candidate_plans.append(
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

        selected_plan_summary = None
        if eval_resp.get("selected_plan"):
            sp = eval_resp["selected_plan"]
            selected_plan_summary = CandidatePlanSummary(
                plan_id=sp["plan_id"],
                strategy_type=sp["strategy_type"],
                description=sp["description"],
                is_feasible=sp["is_feasible"],
                actions_summary=sp["actions_summary"],
                weighted_score=sp["weighted_score"],
                cost_impact_usd=sp["cost_impact_usd"],
                delivery_delay_score=sp["delivery_delay_score"],
                quality_penalty_score=sp.get("quality_penalty_score"),
                energy_kwh=sp.get("energy_kwh"),
                risk_score=sp.get("risk_score"),
            )

        # 5. Rationale & operational risks
        rationale = model_text.strip() if model_text and model_text.strip() else (
            f"Selected optimal recovery plan {selected_plan_summary.plan_id} ({selected_plan_summary.strategy_type}) "
            f"achieving weighted objective score of {selected_plan_summary.weighted_score:.4f}."
            if selected_plan_summary
            else "None of the candidate recovery plans met feasibility criteria."
        )

        risks = []
        if selected_plan_summary:
            st_lower = selected_plan_summary.strategy_type.lower()
            if "transfer" in st_lower:
                risks.append("Setup and tooling calibration required on target machine before volume run.")
                risks.append("Mandatory First Article Inspection (FAI) must be signed off by QA.")
            elif "overtime" in st_lower:
                risks.append("Overtime labor premium incurred ($50.00/hr).")
                risks.append("Shift fatigue and maintenance window compression risk.")
            elif "resequence" in st_lower:
                risks.append("Lower-priority orders will absorb delay into downstream production queues.")
        risks.append("Bottleneck station starvation risk if downtime exceeds forecasted window.")

        return DecisionResponse(
            scenario_summary=f"Unscheduled downtime on {machine_id} for {downtime_hours} hours",
            assumptions=[
                "Machine nominal throughput rates and line speeds remain at baseline specifications.",
                "Deterministic cost benchmarks and capacity limits applied per factory policy.",
                "Multi-objective optimization executed via deterministic evaluation engine.",
            ],
            impact_summary={
                "machine_id": machine_id,
                "downtime_hours": downtime_hours,
                "capacity_loss_units": sim_resp.get("capacity_loss_units", 0.0),
                "capacity_loss_percentage": sim_resp.get("capacity_loss_percentage", 0.0),
                "delivery_risk": sim_resp.get("delivery_risk", "LOW"),
                "affected_order_count": sim_resp.get("affected_order_count", 0),
                "high_priority_affected_count": sim_resp.get("high_priority_affected_count", 0),
                "bottleneck_station_id": sim_resp.get("bottleneck_station_id"),
                "alternative_machines": sim_resp.get("alternative_machines", []),
            },
            affected_orders=sim_resp.get("affected_orders", []),
            candidate_plans=candidate_plans,
            selected_plan=selected_plan_summary,
            objective_weights=eval_resp.get("applied_weights", {}),
            engineering_evidence=evidence_items,
            rationale=rationale,
            risks=risks,
            human_approval_required=True,
            execution_mode="live_gemini",
            fallback_reason=None,
        )

    def run(self, prompt: str) -> DecisionResponse:
        """
        Execute user request against the decision agent.
        Uses live Google ADK Runner + Gemini tool-call loop when API credentials exist,
        or deterministic local workflow orchestration if credentials are unavailable or if live execution fails.
        """
        if self.api_key:
            try:
                return self._run_live_adk(prompt)
            except Exception as e:
                logger.warning(
                    f"Live Gemini ADK execution failed: {e}. Falling back to deterministic local orchestration.",
                    exc_info=True,
                )
                params = self._parse_scenario_prompt(prompt)
                return self.orchestrate_scenario(
                    machine_id=params["machine_id"],
                    downtime_hours=params["downtime_hours"],
                    priorities=params["priorities"],
                    execution_mode="deterministic_fallback",
                    fallback_reason=f"Live Gemini ADK execution failed ({type(e).__name__}: {str(e)}); used deterministic fallback.",
                )
        else:
            params = self._parse_scenario_prompt(prompt)
            return self.orchestrate_scenario(
                machine_id=params["machine_id"],
                downtime_hours=params["downtime_hours"],
                priorities=params["priorities"],
                execution_mode="deterministic_fallback",
                fallback_reason="No GEMINI_API_KEY configured; running in deterministic fallback mode.",
            )

    def what_if(
        self,
        previous_response: DecisionResponse,
        updated_downtime: Optional[float] = None,
        updated_priorities: Optional[Union[Dict[str, float], str]] = None,
        updated_machine_id: Optional[str] = None,
    ) -> DecisionResponse:
        """
        Perform what-if sensitivity analysis, executing a fresh deterministic simulation and optimization run.
        """
        machine_id = updated_machine_id or previous_response.impact_summary.get("machine_id", "M17")
        downtime = updated_downtime if updated_downtime is not None else previous_response.impact_summary.get("downtime_hours", 8.0)
        priorities = updated_priorities if updated_priorities is not None else previous_response.objective_weights

        return self.orchestrate_scenario(
            machine_id=machine_id,
            downtime_hours=downtime,
            priorities=priorities,
            execution_mode=previous_response.execution_mode,
            fallback_reason=previous_response.fallback_reason,
        )
