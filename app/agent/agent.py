import contextvars
import os
import re
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, List, Any, Union

from google.adk import Agent, Runner
from google.adk.sessions import InMemorySessionService
from app.ai.llm import GeminiService, chain_for, is_retryable, mark_unavailable, model_for, usable_models, vertex_enabled
from app.agent.prompts import AGENT_SYSTEM_INSTRUCTION
from app.domains import current_domain, get_pack, process_override
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
from app.agent.understanding import understand_scenario
from app.agent.specialists import run_war_room_resilient
from app.agent.assistant import answer_question
from app.simulation.robustness import analyze_robustness

logger = logging.getLogger(__name__)

CONSTRAINT_KEYS = ("disallow_overtime", "disallow_cross_line_transfer", "disallow_all_transfers")
WAR_ROOM_TIMEOUT_SECONDS = 90
RESPONSE_CACHE_TTL_SECONDS = 15 * 60
RESPONSE_CACHE_MAX_ENTRIES = 32

class FactoryDecisionAgent:
    """
    Decision-support agent for the Factory Decision Engine.
    Orchestrates deterministic simulation, optimization, and engineering knowledge retrieval.
    Powered by Gemini & Google ADK.

    Live pipeline for a new scenario:
      1. Gemini understands the operator message (validated against the plant registry).
      2. Deterministic Python engine simulates impact, generates and scores recovery plans.
      3. Downtime robustness sweep (deterministic).
      4. Specialist war room: four Gemini specialists review in parallel, a Decision Lead writes the brief.

    Follow-up questions (`ask`) go to the Google ADK tool-calling agent, which can run new
    simulations through the deterministic tools: deterministic where predictability matters,
    agentic where flexibility matters.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.model_name = model or os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

        # Propagate API key to os.environ so Google ADK and GenAI clients find it
        if self.api_key:
            os.environ["GEMINI_API_KEY"] = self.api_key
            os.environ["GOOGLE_API_KEY"] = self.api_key

        # Direct Gemini access for structured understanding, Q&A and document drafting
        self.llm = GeminiService(api_key=self.api_key, model=model_for("assistant"))

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

        # Short-lived cache of complete live results (demo replays cost no quota)
        self._response_cache: Dict[Any, Any] = {}

    @property
    def llm_available(self) -> bool:
        return self.llm.available

    @property
    def live_ai(self) -> bool:
        """Gemini reachable: via an API key, or via Vertex AI with the runtime's own credentials."""
        return bool(self.api_key) or vertex_enabled()

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

    def _scenario_context(self) -> Optional[Dict[str, Any]]:
        """Compact description of the scenario currently on screen, for follow-up understanding."""
        prev = self.last_response
        if prev is None or not prev.impact_summary.get("machine_id"):
            return None
        return {
            "machine_id": prev.impact_summary.get("machine_id"),
            "downtime_hours": prev.impact_summary.get("downtime_hours"),
            "recommended_plan": prev.selected_plan.description if prev.selected_plan else None,
            "constraints": {k: v for k, v in (prev.applied_constraints or {}).items() if v},
        }

    def _ask_with_agent(self, question: str, decision: DecisionResponse) -> Dict[str, Any]:
        """
        Answer a question with the Google ADK tool-calling agent. The agent may run NEW simulations
        and evaluations (other machines, durations, priorities) through the deterministic tools, so
        every figure it quotes comes from the engine. Raises on failure; `ask` falls back.
        """
        import json
        import uuid
        from google.genai import types

        from app.agent.specialists import build_decision_context, find_ungrounded_numbers
        from app.ai.llm import compact_json

        imp = decision.impact_summary or {}
        context = build_decision_context(decision, decision.robustness)
        evidence = [{"id": e.document_id, "title": e.title, "excerpt": e.excerpt} for e in decision.engineering_evidence]
        scenario_line = (
            f"machine_id={imp.get('machine_id')}; downtime_hours={imp.get('downtime_hours')}; "
            f"constraints={', '.join(k for k, v in (decision.applied_constraints or {}).items() if v) or 'none'}"
        )
        pack = get_pack()
        message = (
            f"PLANT: {pack['plant']['name']} ({pack['label']})\n"
            f"CURRENT SCENARIO: {scenario_line}\n\n"
            f"CURRENT DECISION (from the deterministic engine):\n{compact_json(context)}\n\n"
            f"EVIDENCE ALREADY RETRIEVED:\n{compact_json(evidence, 4000)}\n\n"
            f"QUESTION FROM THE PLANT MANAGER:\n{question}"
        )
        user_msg = types.Content(role="user", parts=[types.Part.from_text(text=message)])

        # Skip a model that is cooling down after a quota/overload error
        model = usable_models(chain_for(self.model_name))[0]
        self.adk_agent.model = model
        session_id = f"ask_{uuid.uuid4().hex[:12]}"
        try:
            # The ADK runner executes tools on its own thread, which doesn't inherit this request's
            # context; a scoped override keeps tool calls on the viewer's industry pack.
            with process_override(current_domain()):
                events = list(self.runner.run(user_id="plant_operator", session_id=session_id, new_message=user_msg))
        except Exception as e:
            if is_retryable(e):
                mark_unavailable(model, e)
            raise
        finally:
            self.adk_agent.model = self.model_name

        tool_calls: List[Dict[str, Any]] = []
        tool_responses: List[Any] = []
        text_parts: List[str] = []
        for event in events:
            for fc in event.get_function_calls() or []:
                tool_calls.append({"tool": fc.name, "args": fc.args or {}})
            for fr in event.get_function_responses() or []:
                resp_val = fr.response
                if isinstance(resp_val, dict) and isinstance(resp_val.get("result"), dict):
                    resp_val = resp_val["result"]
                tool_responses.append(resp_val)
            if event.is_final_response() and event.message and event.message.parts:
                text_parts.extend(p.text for p in event.message.parts if getattr(p, "text", None))

        answer = "\n".join(text_parts).strip()
        if not answer:
            raise RuntimeError("Agent returned no answer")

        grounding_text = compact_json(context, 50_000) + json.dumps(evidence, default=str) + json.dumps(tool_responses, default=str)
        known_docs = {e["id"] for e in evidence}
        for resp_val in tool_responses:
            if isinstance(resp_val, dict):
                known_docs.update(d.get("document_id") for d in resp_val.get("evidence", []) if isinstance(d, dict))
        citations = sorted(doc for doc in known_docs if doc and doc in answer)
        ungrounded = find_ungrounded_numbers([answer], grounding_text)
        logger.info(f"ADK ask completed: {len(tool_calls)} tool calls, {len(answer)} chars")
        return {
            "answer": answer,
            "citations": citations,
            "confidence": "high" if not ungrounded else "partial",
            "source": "agent",
            "tools_used": tool_calls,
            "grounding_check": {"ungrounded_numbers": ungrounded, "passed": not ungrounded},
        }

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def run(self, prompt: str, previous_response: Optional[DecisionResponse] = None) -> DecisionResponse:
        """
        Execute user request against the decision agent.
        Uses live Google ADK Runner + Gemini tool-call loop when API credentials exist,
        or deterministic local workflow orchestration if credentials are unavailable or if live execution fails.
        """
        if previous_response is not None:
            self.last_response = previous_response

        cache_key = None
        if self.llm_available:
            cache_key = (current_domain(), " ".join(prompt.lower().split()), repr(self._scenario_context()))
            cached = self._cache_get(cache_key)
            if cached is not None:
                if cached.selected_plan is not None and not cached.clarification_needed:
                    self.last_response = cached
                return cached

        result = self._run_uncached(prompt)
        if cache_key is not None and self._is_complete_live_result(result):
            self._cache_put(cache_key, result)
        return result

    def _run_uncached(self, prompt: str) -> DecisionResponse:
        understood = understand_scenario(prompt, self.llm, self._scenario_context())
        intent = understood.get("intent")
        interpretation = understood.get("interpretation")
        components = {"understanding": interpretation.get("source", "rules") if interpretation else "rules"}

        if intent == "not_a_scenario":
            resp = DecisionResponse(
                scenario_summary="Message is not an operational scenario",
                execution_mode="live_gemini" if self.llm_available else "deterministic_fallback",
                rationale="No disruption scenario was described.",
                assistant_message=(
                    "I analyze factory disruptions. Describe what happened, for example: "
                    "'M17 will be down for 6 hours, protect high-priority orders'."
                ),
                clarification_needed="Describe a machine disruption to analyze (machine, expected duration, priorities).",
                scenario_interpretation=interpretation,
                ai_components=components,
            )
            return resp

        if self.last_response is not None:
            if intent == "question":
                return self._answer_in_context(prompt, interpretation, components)
            if intent == "follow_up" or (intent == "unknown" and self._looks_like_what_if(understood, prompt)):
                return self._follow_up(understood, prompt, components)

        constraints = {k: understood.get(k, False) for k in CONSTRAINT_KEYS}

        if understood.get("clarification_needed") or not understood.get("machine_id"):
            resp = self.orchestrate_scenario(
                machine_id=understood.get("machine_id"),
                downtime_hours=understood["downtime_hours"],
                priorities=understood["priorities"] or prompt,
                constraints=constraints,
                execution_mode="live_gemini" if components["understanding"] == "gemini" else "deterministic_fallback",
                fallback_reason=None if self.live_ai else "No GEMINI_API_KEY configured; running in deterministic fallback mode.",
                clarification_needed=understood.get("clarification_needed"),
            )
            resp.scenario_interpretation = interpretation
            resp.ai_components = components
            return resp

        priorities = understood["priorities"] if components["understanding"] == "gemini" else understood["priorities"] or prompt
        if components["understanding"] == "gemini" and not priorities:
            priorities = None

        if not self.live_ai:
            decision = self.orchestrate_scenario(
                machine_id=understood["machine_id"],
                downtime_hours=understood["downtime_hours"],
                priorities=priorities,
                constraints=constraints,
                execution_mode="deterministic_fallback",
                fallback_reason="No GEMINI_API_KEY configured; running in deterministic fallback mode.",
                clarification_needed=understood.get("clarification_needed"),
            )
            return self._enrich(decision, understood, components, war_room=None)

        # Live pipeline: deterministic engine is the source of truth; Gemini specialists review it.
        # (The ADK tool-calling agent answers open questions in `ask`, where new simulations are needed.)
        decision = self.orchestrate_scenario(
            machine_id=understood["machine_id"],
            downtime_hours=understood["downtime_hours"],
            priorities=priorities,
            constraints=constraints,
            execution_mode="live_gemini",
            clarification_needed=understood.get("clarification_needed"),
        )
        decision.robustness = self._robustness(decision, understood, priorities, constraints)

        war_room = None
        if self.llm_available and decision.selected_plan is not None:
            with ThreadPoolExecutor(max_workers=1) as pool:
                war_room = self._collect_war_room(pool.submit(contextvars.copy_context().run, run_war_room_resilient, decision, decision.robustness))

        result = self._enrich(decision, understood, components, war_room=war_room)
        if result.ai_components.get("understanding") != "gemini" and result.ai_components.get("war_room") != "gemini":
            result.execution_mode = "deterministic_fallback"
            result.fallback_reason = "Gemini was unavailable for this request; showing the deterministic engine result."
        return result

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

    def follow_up(self, previous_response: DecisionResponse, prompt: str) -> DecisionResponse:
        """Natural-language what-if: understand the change with Gemini when available, then re-run."""
        self.last_response = previous_response
        understood = understand_scenario(prompt, self.llm, self._scenario_context())
        components = {"understanding": (understood.get("interpretation") or {}).get("source", "rules")}
        if understood.get("intent") == "question":
            return self._answer_in_context(prompt, understood.get("interpretation"), components)
        return self._follow_up(understood, prompt, components)

    def rerank(self, previous_response: DecisionResponse, updated_priorities) -> DecisionResponse:
        """Fast deterministic re-ranking under new priorities (no LLM calls); keeps interpretation."""
        res = self.what_if(previous_response, updated_priorities=updated_priorities)
        res.execution_mode = previous_response.execution_mode
        res.fallback_reason = previous_response.fallback_reason
        res.scenario_interpretation = previous_response.scenario_interpretation
        understood = {"downtime_range": (previous_response.scenario_interpretation or {}).get("downtime_range")}
        res.robustness = self._robustness(res, understood, updated_priorities, res.applied_constraints)
        res.ai_components = dict(previous_response.ai_components or {}, war_room="pending")
        if res.selected_plan is not None:
            self.last_response = res
        return res

    def review(self, decision: DecisionResponse) -> DecisionResponse:
        """Run the specialist war room on a decision (e.g. after a re-ranking)."""
        res = decision.model_copy(deep=True)
        if not self.llm_available or res.selected_plan is None:
            res.ai_components = dict(res.ai_components or {}, war_room="skipped")
            return res
        with ThreadPoolExecutor(max_workers=1) as pool:
            war_room = self._collect_war_room(pool.submit(contextvars.copy_context().run, run_war_room_resilient, res, res.robustness))
        comps = dict(res.ai_components or {})
        if war_room in (None, "failed"):
            comps["war_room"] = "failed"
        else:
            res.specialist_assessments, res.decision_brief = war_room
            comps["war_room"] = "gemini"
        res.ai_components = comps
        return res

    def ask(self, question: str, decision: DecisionResponse) -> Dict[str, Any]:
        """
        Grounded Q&A about a decision. The ADK tool-calling agent answers first (it can run new
        simulations); if it is unavailable or fails, fall back to a single grounded Gemini call,
        then to a rule-based template.
        """
        if self.live_ai:
            try:
                return self._ask_with_agent(question, decision)
            except Exception as e:
                # Never surface the raw error (it may contain credentials); just degrade
                logger.warning(f"ADK ask agent failed ({type(e).__name__}); using single-call answer.")
        return answer_question(self.llm, question, decision)

    def reset_state(self) -> None:
        """Reset cached scenario state."""
        self.last_response = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _cache_get(self, key) -> Optional[DecisionResponse]:
        entry = self._response_cache.get(key)
        if entry is None or time.monotonic() - entry[0] > RESPONSE_CACHE_TTL_SECONDS:
            self._response_cache.pop(key, None)
            return None
        return entry[1].model_copy(deep=True)

    def _cache_put(self, key, response: DecisionResponse) -> None:
        if len(self._response_cache) >= RESPONSE_CACHE_MAX_ENTRIES:
            oldest = min(self._response_cache, key=lambda k: self._response_cache[k][0])
            self._response_cache.pop(oldest, None)
        self._response_cache[key] = (time.monotonic(), response.model_copy(deep=True))

    @staticmethod
    def _is_complete_live_result(resp: DecisionResponse) -> bool:
        """Only cache results where every generative component succeeded."""
        comps = resp.ai_components or {}
        return bool(comps) and "failed" not in comps.values() and comps.get("understanding") == "gemini"

    @staticmethod
    def _looks_like_what_if(understood: Dict[str, Any], prompt: str) -> bool:
        if understood.get("machine_id"):
            return False
        return bool(
            any(understood.get(k) for k in CONSTRAINT_KEYS)
            or re.search(r"\b(what\s+if|suppose|assume|constraint|overtime|delay|cost|priority|deliver|speed|downtime|hours?)\b", prompt, re.I)
        )

    def _follow_up(self, understood: Dict[str, Any], prompt: str, components: Dict[str, str]) -> DecisionResponse:
        prev = self.last_response
        if components.get("understanding") != "gemini":
            res = self.what_if(prev, follow_up_prompt=prompt)
            return self._enrich(res, understood, components, war_room=None)

        interp = understood.get("interpretation") or {}
        res = self.what_if(
            prev,
            updated_machine_id=understood.get("machine_id"),
            updated_downtime=interp.get("downtime_hours"),
            updated_priorities=understood.get("priorities"),
            updated_constraints={k: True for k in CONSTRAINT_KEYS if understood.get(k)},
        )
        res.execution_mode = "live_gemini"
        res.fallback_reason = None
        res.assistant_message = interp.get("summary")
        priorities = understood.get("priorities") or res.objective_weights
        res.robustness = self._robustness(res, understood, priorities, res.applied_constraints)
        war_room = None
        if self.llm_available and res.selected_plan is not None:
            with ThreadPoolExecutor(max_workers=1) as pool:
                war_room = self._collect_war_room(pool.submit(contextvars.copy_context().run, run_war_room_resilient, res, res.robustness))
        return self._enrich(res, understood, components, war_room=war_room)

    def _answer_in_context(self, prompt: str, interpretation, components: Dict[str, str]) -> DecisionResponse:
        prev = self.last_response
        reply = self.ask(prompt, prev)
        res = prev.model_copy(deep=True)
        res.assistant_message = reply["answer"]
        res.ai_components = dict(prev.ai_components or {}, **components, answer=reply.get("source", "rules"))
        return res

    def _robustness(self, decision: DecisionResponse, understood: Dict[str, Any], priorities, constraints):
        if decision.selected_plan is None:
            return None
        try:
            return analyze_robustness(
                self.orchestrator,
                machine_id=decision.impact_summary.get("machine_id"),
                downtime_hours=decision.impact_summary.get("downtime_hours"),
                priorities=priorities,
                constraints=constraints,
                selected_plan_id=decision.selected_plan.plan_id,
                downtime_range=understood.get("downtime_range"),
            )
        except Exception as e:
            logger.warning(f"Robustness analysis failed: {e}")
            return None

    @staticmethod
    def _collect_war_room(future):
        if future is None:
            return None
        try:
            return future.result(timeout=WAR_ROOM_TIMEOUT_SECONDS)
        except Exception as e:
            logger.warning(f"Specialist war room failed: {type(e).__name__}: {e}")
            return "failed"

    def _enrich(self, decision: DecisionResponse, understood: Dict[str, Any], components: Dict[str, str],
                war_room) -> DecisionResponse:
        """Attach interpretation, robustness and war-room output to a decision."""
        decision.scenario_interpretation = understood.get("interpretation")
        if decision.robustness is None and decision.selected_plan is not None:
            priorities = understood.get("priorities") or decision.objective_weights
            decision.robustness = self._robustness(decision, understood, priorities, decision.applied_constraints)
        if war_room == "failed":
            components["war_room"] = "failed"
        elif war_room:
            decision.specialist_assessments, decision.decision_brief = war_room
            components["war_room"] = "gemini"
        else:
            components["war_room"] = "skipped"
        decision.ai_components = components
        if decision.selected_plan is not None and not decision.clarification_needed:
            self.last_response = decision
        return decision
