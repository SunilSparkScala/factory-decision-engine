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
from app.agent.orchestrator import (
    DecisionOrchestrator,
    ScenarioParser,
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

        # Decision Orchestrator handles deterministic workflow, dependency trace & multi-objective scoring
        self.orchestrator = DecisionOrchestrator()

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

        # Retained state for interactive / conversational what-if sensitivity analysis
        self.last_response: Optional[DecisionResponse] = None

    def orchestrate_scenario(
        self,
        machine_id: str,
        downtime_hours: float,
        priorities: Optional[Union[Dict[str, float], str]] = None,
        start_time: Optional[str] = None,
        constraints: Optional[Dict[str, bool]] = None,
        execution_mode: str = "deterministic_fallback",
        fallback_reason: Optional[str] = None,
        clarification_needed: Optional[str] = None,
    ) -> DecisionResponse:
        """
        Execute deterministic end-to-end decision orchestration:
        1. Inspect machine status & dependency trace
        2. Simulate failure impact
        3. Generate recovery plans & filter operational constraints
        4. Evaluate recovery plans with user priorities
        5. Retrieve relevant engineering evidence
        6. Formulate structured recommendation with human approval boundary
        """
        from app.agent.tools import evaluate_recovery_plans as default_eval_fn
        import app.agent.agent as agent_mod
        tool_eval_fn = getattr(agent_mod, "evaluate_recovery_plans", None)
        passed_eval_fn = tool_eval_fn if tool_eval_fn is not default_eval_fn else None
        res = self.orchestrator.orchestrate(
            machine_id=machine_id,
            downtime_hours=downtime_hours,
            priorities=priorities,
            start_time=start_time,
            constraints=constraints,
            execution_mode=execution_mode,
            fallback_reason=fallback_reason,
            clarification_needed=clarification_needed,
            eval_fn=passed_eval_fn,
        )
        if res.selected_plan is not None and not res.clarification_needed:
            self.last_response = res
        return res

    def _parse_scenario_prompt(self, prompt: str) -> Dict[str, Any]:
        """Extract machine ID, downtime hours, priority keywords, and constraints from natural-language text."""
        return ScenarioParser.parse(prompt)

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

        # Check if simulate_machine_failure was executed during live run
        for tr in reversed(tool_responses):
            if tr.get("name") == "simulate_machine_failure" and isinstance(tr.get("response"), dict) and tr["response"].get("success"):
                machine_id = tr["response"].get("machine_id", machine_id)
                downtime_hours = float(tr["response"].get("downtime_hours", downtime_hours))
                break

        # Check if evaluate_recovery_plans was executed during live run
        eval_resp = None
        for tr in reversed(tool_responses):
            if tr.get("name") == "evaluate_recovery_plans" and isinstance(tr.get("response"), dict) and tr["response"].get("success"):
                eval_resp = tr["response"]
                break

        # Check if search_engineering_knowledge was executed during live run
        live_evidence: List[EvidenceItem] = []
        seen_doc_ids = set()
        for tr in tool_responses:
            if tr.get("name") == "search_engineering_knowledge" and isinstance(tr.get("response"), dict):
                for doc in tr["response"].get("evidence", []):
                    if isinstance(doc, dict) and doc.get("document_id") and doc["document_id"] not in seen_doc_ids:
                        seen_doc_ids.add(doc["document_id"])
                        live_evidence.append(EvidenceItem(**doc))

        # Produce complete deterministic response using harvested tool results
        base_decision = self.orchestrator.orchestrate(
            machine_id=machine_id,
            downtime_hours=downtime_hours,
            priorities=priorities,
            constraints={
                "disallow_overtime": parsed_params.get("disallow_overtime", False),
                "disallow_cross_line_transfer": parsed_params.get("disallow_cross_line_transfer", False),
                "disallow_all_transfers": parsed_params.get("disallow_all_transfers", False),
            },
            execution_mode="live_gemini",
            fallback_reason=None,
            clarification_needed=parsed_params.get("clarification_needed"),
            eval_fn=(lambda *a, **kw: eval_resp) if eval_resp else None,
        )

        if live_evidence:
            base_decision.engineering_evidence = live_evidence

        # Overlay Gemini model rationale if generated
        if model_text and model_text.strip():
            base_decision.rationale = model_text.strip()

        if base_decision.selected_plan is not None and not base_decision.clarification_needed:
            self.last_response = base_decision

        return base_decision

    def run(self, prompt: str, previous_response: Optional[DecisionResponse] = None) -> DecisionResponse:
        """
        Execute user request against the decision agent.
        Uses live Google ADK Runner + Gemini tool-call loop when API credentials exist,
        or deterministic local workflow orchestration if credentials are unavailable or if live execution fails.
        """
        if previous_response is not None:
            self.last_response = previous_response

        parsed = self._parse_scenario_prompt(prompt)

        # If machine ID is not provided, check if this is a follow-up what-if on the active base scenario
        if not parsed.get("machine_id") and self.last_response is not None:
            is_what_if = (
                parsed.get("disallow_overtime")
                or parsed.get("disallow_cross_line_transfer")
                or parsed.get("disallow_all_transfers")
                or bool(re.search(r"\b(what\s+if|suppose|assume|constraint|overtime|delay|cost|priority|deliver|speed|downtime|hours?)\b", prompt, re.I))
            )
            if is_what_if:
                return self.what_if(self.last_response, follow_up_prompt=prompt)

        constraints = {
            "disallow_overtime": parsed.get("disallow_overtime", False),
            "disallow_cross_line_transfer": parsed.get("disallow_cross_line_transfer", False),
            "disallow_all_transfers": parsed.get("disallow_all_transfers", False),
        }

        if self.api_key:
            try:
                return self._run_live_adk(prompt)
            except Exception as e:
                logger.warning(
                    f"Live Gemini ADK execution failed: {e}. Falling back to deterministic local orchestration.",
                    exc_info=True,
                )
                return self.orchestrate_scenario(
                    machine_id=parsed["machine_id"],
                    downtime_hours=parsed["downtime_hours"],
                    priorities=parsed["priorities"],
                    constraints=constraints,
                    execution_mode="deterministic_fallback",
                    fallback_reason=f"Live Gemini ADK execution failed ({type(e).__name__}: {str(e)}); used deterministic fallback.",
                    clarification_needed=parsed.get("clarification_needed"),
                )
        else:
            return self.orchestrate_scenario(
                machine_id=parsed["machine_id"],
                downtime_hours=parsed["downtime_hours"],
                priorities=parsed["priorities"],
                constraints=constraints,
                execution_mode="deterministic_fallback",
                fallback_reason="No GEMINI_API_KEY configured; running in deterministic fallback mode.",
                clarification_needed=parsed.get("clarification_needed"),
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
        Perform what-if sensitivity analysis, executing a fresh deterministic simulation and optimization run.
        """
        res = self.orchestrator.what_if(
            previous_response=previous_response,
            follow_up_prompt=follow_up_prompt,
            updated_downtime=updated_downtime,
            updated_priorities=updated_priorities,
            updated_machine_id=updated_machine_id,
            updated_constraints=updated_constraints,
        )
        if res.selected_plan is not None and not res.clarification_needed:
            self.last_response = res
        return res

    def reset_state(self) -> None:
        """Reset cached scenario state."""
        self.last_response = None
