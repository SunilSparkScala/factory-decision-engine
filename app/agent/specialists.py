"""
Specialist "war room" built with Google ADK.

Four domain specialists (Production, Quality, Maintenance, Energy) review the deterministic
decision in parallel (ADK ParallelAgent), then a Decision Lead synthesizes their views into a
plain-language brief (ADK SequentialAgent). Agents only reason over the numbers and documents
they are given; citations are validated against the retrieved evidence afterwards.
"""
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from app.ai.llm import compact_json
from app.domains import get_pack, machine_label
from app.config import config

logger = logging.getLogger(__name__)


class SpecialistAssessment(BaseModel):
    stance: Literal["support", "caution", "oppose"] = Field(
        description="support: plan is sound from your domain. caution: acceptable with conditions. oppose: unsafe/unsound."
    )
    headline: str = Field(description="At most 14 words, plain English.")
    assessment: str = Field(description="2-3 sentences for a plant manager, grounded in the provided data.")
    key_concern: Optional[str] = Field(None, description="The single most important condition or risk, if any.")
    citations: List[str] = Field(default_factory=list, description="Document IDs from EVIDENCE you relied on.")


AgentKey = Literal["production", "quality", "maintenance", "energy"]


class ConcernLink(BaseModel):
    from_agent: AgentKey = Field(description="Specialist whose condition or concern has the knock-on effect.")
    to_agent: AgentKey = Field(description="Department affected by it.")
    note: str = Field(description="One short plain sentence describing the knock-on effect, without invented durations.")


class DecisionBrief(BaseModel):
    headline: str = Field(description="The recommended action in plain words, at most 14 words.")
    summary: str = Field(description="2-3 sentences a plant manager can act on.")
    why: List[str] = Field(description="3-4 short reasons, each grounded in the data.")
    watch_outs: List[str] = Field(description="1-3 conditions or risks to manage.")
    specialist_consensus: str = Field(description="One line, e.g. '3 of 4 specialists support; Quality requires FAI first.'")
    next_steps: List[str] = Field(description="2-4 concrete next steps for the shift, in a sensible order.")
    dependencies: List[ConcernLink] = Field(
        default_factory=list,
        description="Where one specialist's condition affects another department (0-3 links).",
    )
    conflicts: List[str] = Field(
        default_factory=list,
        description="Where specialists disagree with each other or with the plan (empty if none).",
    )


SPECIALISTS: List[Dict[str, str]] = [
    {
        "key": "production",
        "name": "production_specialist",
        "title": "Production",
        "focus": "capacity recovery, schedule adherence, which orders and customers are protected or delayed, line balance.",
    },
    {
        "key": "quality",
        "name": "quality_specialist",
        "title": "Quality",
        "focus": "defect and process risk of the change, first-article inspection and transfer requirements, target machine health.",
    },
    {
        "key": "maintenance",
        "name": "maintenance_specialist",
        "title": "Maintenance",
        "focus": "health and status of the failed and target machines, restart and repair protocol, risk of loading degraded equipment.",
    },
    {
        "key": "energy",
        "name": "energy_specialist",
        "title": "Energy & Sustainability",
        "focus": "energy use (kWh) and energy cost of each option, overtime energy, sustainability trade-offs.",
    },
]

DOMAIN_QUERIES = {
    "production": ("production order resequencing priority handling overtime authorization", "production_sop"),
    "quality": ("quality inspection machine transfer first article", "quality"),
    "maintenance": ("restart protocol degraded machinery maintenance", "maintenance"),
    "energy": ("energy consumption overtime shift extension", None),
}

COMMON_RULES = """Rules:
- Stay strictly in your own field. The panel is Production (orders, schedule, capacity), Quality (inspections,
  defects, first-article inspection), Maintenance (machine condition, repair, restart) and Energy (kWh, energy
  cost). Never raise a concern that belongs to another specialist: they cover it. Your headline and key_concern
  must be about your own field.
- Make one concrete point from YOUR documents in the EVIDENCE section where they apply, and cite that document.
- The cause of the current breakdown is unknown unless REPORTED PROBLEM in the DECISION DATA states it. Never
  write that the current downtime is "due to", "caused by" or "because of" anything else.
- The failed machine is out of service now; never describe it as healthy or available.
- Mention only conditions that apply to THIS plan (e.g. rules for degraded equipment only if a machine in the
  plan is degraded). Avoid jargon such as "deterministically".
- Never call a figure, rate or plant "synthetic" or "simulated"; present them as the plant's figures.
- Use ONLY numbers that appear in the DECISION DATA. Never compute or invent new figures.
- Cite document IDs (e.g. KB-QTY-001) only from the EVIDENCE section; never invent IDs.
- Incident reports describe PAST events. Never present a past incident's cause as the cause of the current
  disruption; the current cause is unknown unless the DECISION DATA states it. Use incidents only as precedent.
- Write for a busy plant manager: concrete, short, no jargon like 'normalized score'.
- Never quote the internal 0-1 scores (delivery impact, quality risk, operational risk, weights) or health
  scores as bare numbers; describe them in words (e.g. "lower delivery impact", "healthy machine").
  Dollar amounts, hours, units, kWh and order counts may be quoted exactly.
- Human approval is always required; never say the plan has been executed."""


def _level(score: Optional[float], bands: Tuple[float, float, float]) -> Optional[str]:
    """Translate an internal 0-1 score into words so agents describe rather than quote it."""
    if score is None:
        return None
    very_low, low, medium = bands
    if score < very_low:
        return "very low"
    if score < low:
        return "low"
    if score < medium:
        return "medium"
    return "high"


def _plan_view(p, best_delivery: Optional[float] = None) -> Dict[str, Any]:
    energy_cost = round((p.energy_kwh or 0.0) * config.ENERGY_COST_PER_KWH, 2)
    delivery = None
    if p.delivery_delay_score is not None and best_delivery is not None:
        gap = p.delivery_delay_score - best_delivery
        delivery = "best achievable" if gap <= 0.01 else "minor extra delay" if gap < 0.15 else "significant delays"
    return {
        "plan_id": p.plan_id,
        "strategy": p.strategy_type,
        "description": p.description,
        "feasible": p.is_feasible,
        "why_infeasible": p.infeasibility_reasons,
        "extra_cost_usd": p.cost_impact_usd,
        "delivery_outcome": delivery,
        "quality_risk": _level(p.quality_penalty_score, (0.05, 0.1, 0.25)),
        "operational_risk": _level(p.risk_score, (0.0, 0.25, 0.4)),
        "energy_kwh": p.energy_kwh,
        "energy_cost_usd": energy_cost,
        "target_machine": p.target_machine_id,
        "target_line": _line_name(p.target_line_id),
    }


def _line_name(line_id: Optional[str]) -> Optional[str]:
    """'LINE_C' -> 'Line C (Thermostats)', so the AI never writes raw codes."""
    if not line_id:
        return line_id
    return get_pack().get("lines", {}).get(line_id, line_id)


def _asset_chain(chain: Dict[str, Any]) -> Optional[str]:
    """'M17 (Pick-and-place Unit 2) -> Station B2 - Pick-and-place -> Line B (Smart speakers) -> 6 orders'."""
    if not chain.get("machine_id"):
        return chain.get("trace_summary")
    return (
        f"{chain['machine_id']} ({machine_label(chain.get('machine_type') or '')}) -> {chain.get('station_name')} -> "
        f"{chain.get('line_name')} -> {chain.get('affected_order_count')} production orders -> delivery deadlines"
    )


def _readable_machines(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for c in candidates or []:
        c = dict(c)
        if c.get("machine_type"):
            c["machine_type"] = machine_label(c["machine_type"])
        if c.get("line_id"):
            c["line"] = _line_name(c.pop("line_id"))
        out.append(c)
    return out


def build_decision_context(decision, robustness: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Deterministic facts the agents may reason over."""
    by_strategy: Dict[str, Any] = {}
    for p in decision.feasible_alternatives:
        best = by_strategy.get(p.strategy_type)
        if best is None or (p.weighted_score or 0) > (best.weighted_score or 0):
            by_strategy[p.strategy_type] = p
    delivery_scores = [p.delivery_delay_score for p in decision.feasible_alternatives if p.delivery_delay_score is not None]
    best_delivery = min(delivery_scores) if delivery_scores else None
    # The next-best plans overall, so specialists can see close alternatives of the same strategy
    # (e.g. another transfer target) that "best per strategy" hides.
    selected_id = decision.selected_plan.plan_id if decision.selected_plan else None
    runners_up = sorted(
        (p for p in decision.feasible_alternatives if p.plan_id != selected_id),
        key=lambda p: -(p.weighted_score or 0),
    )[:2]
    interp = decision.scenario_interpretation or {}
    return {
        "scenario": decision.scenario_summary,
        "reported_problem": interp.get("summary") or "Only the machine and downtime were reported; the cause is not stated.",
        "asset_chain": _asset_chain(decision.dependency_chain),
        # The database status is from before the breakdown; say so, or agents call a down machine "healthy"
        "failed_machine_status": (
            f"down now (out of service for this scenario); its recorded status before the breakdown was "
            f"{decision.dependency_chain.get('machine_status')}"
        ),
        "impact": {k: v for k, v in decision.impact_summary.items() if k != "alternative_candidates"},
        "alternative_machines": _readable_machines(decision.impact_summary.get("alternative_candidates", [])),
        "affected_orders": decision.affected_orders,
        "recommended_plan": _plan_view(decision.selected_plan, best_delivery) if decision.selected_plan else None,
        "best_option_per_strategy": {k: _plan_view(v, best_delivery) for k, v in by_strategy.items()},
        "runner_up_plans": [_plan_view(p, best_delivery) for p in runners_up],
        "infeasible_options": [
            {"plan_id": p.plan_id, "why": p.infeasibility_reasons} for p in decision.infeasible_alternatives
        ],
        "priority_focus": [k for k, v in sorted(decision.objective_weights.items(), key=lambda kv: -kv[1]) if v >= 0.3],
        "policy_constraints": decision.applied_constraints,
        "downtime_robustness": {
            "summary": robustness.get("summary"),
            "best_plan_by_downtime_hours": [
                {"from_h": s["from_hours"], "to_h": s["to_hours"], "plan_id": s["plan_id"], "strategy": s["strategy"]}
                for s in robustness.get("segments", [])
            ],
        } if robustness else None,
        "rule_based_risks": decision.risks,
    }


def gather_domain_evidence(decision) -> Dict[str, List[Dict[str, Any]]]:
    """Evidence already attached to the decision plus one targeted search per domain."""
    from app.agent.tools import search_engineering_knowledge

    machine_id = decision.impact_summary.get("machine_id")
    base = [
        {"id": e.document_id, "title": e.title, "type": e.document_type, "excerpt": e.excerpt}
        for e in decision.engineering_evidence
    ]
    out: Dict[str, List[Dict[str, Any]]] = {}
    for key, (query, topic) in DOMAIN_QUERIES.items():
        found = list(base)
        try:
            res = search_engineering_knowledge(query=query, topic=topic, machine_id=None, limit=2)
            for d in res.get("evidence", []):
                if d.get("document_id") and all(x["id"] != d["document_id"] for x in found):
                    found.append({
                        "id": d["document_id"], "title": d.get("title"),
                        "type": d.get("document_type"), "excerpt": d.get("excerpt"),
                    })
        except Exception as e:
            logger.warning(f"Evidence search failed for {key} ({machine_id}): {e}")
        out[key] = found[:5]
    return out


def _specialist_instruction(spec: Dict[str, str]) -> str:
    return (
        f"You are the {spec['title']} specialist in a factory disruption war room at {get_pack()['plant']['name']}.\n"
        f"Review the RECOMMENDED PLAN strictly from your domain: {spec['focus']}\n"
        f"Compare it with the other options only where relevant to your domain.\n{COMMON_RULES}"
    )


LEAD_ROLE = """You are the Decision Lead. Using the DECISION DATA in the conversation and the specialist reviews
below, write a decision brief for the plant manager about the RECOMMENDED PLAN. Do not change the
recommendation; explain it, surface specialist conditions, and list next steps in a sensible order.

Connect the reviews like a real plant meeting:
- dependencies: where one specialist's condition affects another department (e.g. a Quality inspection
  and a Maintenance calibration both delay full output, which matters to Production). Describe the effect
  qualitatively; never invent durations or figures that are not in the DECISION DATA.
- conflicts: where specialists disagree with each other or with the plan. Leave empty if they agree.
- If any specialist OPPOSES the plan, the summary must start with that objection, and the headline must
  not present the plan as settled."""


def _lead_instruction(views: Optional[str] = None) -> str:
    """Lead system prompt; `views` defaults to ADK state placeholders ({production_view}, ...)."""
    if views is None:
        views = "\n".join(f"{s['title']} specialist: {{{s['key']}_view}}" for s in SPECIALISTS)
    return f"{LEAD_ROLE}\n\nSPECIALIST REVIEWS:\n{views}\n\n{COMMON_RULES}"


# Identifiers such as KB-QTY-001, PLAN_TRANSFER_07, M17, LINE_B, STA_B2, O1038, Model-C are not quantities
_IDENTIFIER_RE = re.compile(r"\b(?:KB-[A-Z]+-[A-Z0-9]+|PLAN_[A-Z]+_\d+|M\d+|LINE_[A-Z]|STA_[A-Z]\d|O\d+|Model-[A-Z])\b")


def _numbers_in(text: str) -> List[str]:
    cleaned = _IDENTIFIER_RE.sub(" ", text.replace(",", ""))
    return [_normalize_number(n) for n in re.findall(r"\d+(?:\.\d+)?", cleaned)]


def _normalize_number(n: str) -> str:
    return n.rstrip("0").rstrip(".") if "." in n else n


def find_ungrounded_numbers(texts: List[str], grounding_text: str) -> List[str]:
    """Numbers stated by the agents that appear nowhere in the data and evidence they were given."""
    known = set(_numbers_in(grounding_text))
    flagged = {n for t in texts for n in _numbers_in(t or "") if n not in known and len(n) > 1}
    return sorted(flagged)


_CAUSE_RE = re.compile(
    r"\b(?:due to|caused by|because of|as a result of|resulting from|triggered by)\s+(?:an?\s+|the\s+)?"
    r"([a-z][a-z\- ]{2,60}?)(?=[.,;:)]|\s+(?:and|which|that|on|in|at|for|with|while)\b|$)",
    re.IGNORECASE,
)
_PAST_RE = re.compile(r"\b(?:past|previous(?:ly)?|precedent|earlier|historical(?:ly)?|history|last time|incident report|KB-INC-\w+)\b", re.IGNORECASE)
_CAUSE_STOPWORDS = {"this", "that", "with", "from", "into", "machine", "current", "recent", "expected", "planned"}


def find_unsupported_causes(texts: List[str], reported_text: str) -> List[str]:
    """Causes the agents state ("due to a vacuum fault") whose key words appear nowhere in what was reported.

    Generic phrases such as "due to the downtime" or "because of the capacity loss" pass, because those
    words are in the scenario; a cause borrowed from a past incident report does not.
    """
    known = reported_text.lower()
    flagged = []
    for text in texts:
        # Sentences that are explicitly about the past ("a past incident on M17 was due to …") are allowed:
        # citing a precedent is fine, presenting it as today's cause is not
        current = " ".join(s for s in re.split(r"(?<=[.!?])\s+", text or "") if not _PAST_RE.search(s))
        for m in _CAUSE_RE.finditer(current):
            phrase = m.group(1).strip()
            words = [w for w in re.findall(r"[a-z]{4,}", phrase.lower()) if w not in _CAUSE_STOPWORDS]
            if words and sum(w in known for w in words) * 2 < len(words):
                flagged.append(m.group(0).strip())
    return sorted(set(flagged))


def _consensus_line(assessments: List[Dict[str, Any]]) -> str:
    counts = {s: sum(1 for a in assessments if a["stance"] == s) for s in ("support", "caution", "oppose")}
    total = len(assessments)
    parts = []
    if counts["support"]:
        parts.append(f"{counts['support']} support")
    if counts["caution"]:
        parts.append(f"{counts['caution']} support with conditions")
    if counts["oppose"]:
        parts.append(f"{counts['oppose']} oppose")
    return f"{total} specialists reviewed: " + ", ".join(parts) + "."


def run_war_room_resilient(decision, robustness: Optional[Dict[str, Any]] = None
                           ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Run the war room on the specialists model, moving to fallback models on quota/overload errors."""
    from app.ai.llm import chain_for, is_retryable, mark_unavailable, model_for, usable_models

    last_exc: Optional[Exception] = None
    for model in usable_models(chain_for(model_for("specialists"))):
        try:
            return run_war_room(decision, robustness, model)
        except Exception as e:
            last_exc = e
            if not is_retryable(e):
                raise
            mark_unavailable(model, e)
    raise last_exc


def _specialist_system(spec: Dict[str, str], evidence: Dict[str, List[Dict[str, Any]]]) -> str:
    return _specialist_instruction(spec) + f"\n\nEVIDENCE for your domain:\n{compact_json(evidence[spec['key']], 4000)}"


def _run_parallel(model: str, context_json: str, evidence) -> Dict[str, Any]:
    """
    Default engine: the four specialists call Gemini concurrently (one structured call each),
    then the Decision Lead synthesizes. About 2 model round-trips of latency in total.
    """
    from concurrent.futures import ThreadPoolExecutor

    from app.ai.llm import GeminiService

    llm = GeminiService(model=model)
    message = f"DECISION DATA:\n{context_json}"

    def review(spec):
        return spec, llm.generate_json(message, SpecialistAssessment, system=_specialist_system(spec, evidence), temperature=0.2)

    with ThreadPoolExecutor(max_workers=len(SPECIALISTS)) as pool:
        results = list(pool.map(review, SPECIALISTS))

    state: Dict[str, Any] = {f"{s['key']}_view": r.model_dump() for s, r in results if r is not None}
    if not state:
        raise RuntimeError(f"All specialists failed: {llm.last_error}")

    views = "\n".join(
        f"{s['title']} specialist: {json.dumps(state.get(s['key'] + '_view'))}" for s in SPECIALISTS
    )
    brief = llm.generate_json(message, DecisionBrief, system=_lead_instruction(views), temperature=0.2)
    if brief is not None:
        state["decision_brief"] = brief.model_dump()
    return state


def _run_adk(model: str, context_json: str, evidence) -> Dict[str, Any]:
    """Alternative engine (FDE_WAR_ROOM_ENGINE=adk): ADK ParallelAgent of specialists + SequentialAgent lead."""
    from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    gen_cfg = types.GenerateContentConfig(temperature=0.2)
    specialists = [
        LlmAgent(
            name=s["name"],
            model=model,
            instruction=_specialist_system(s, evidence),
            output_schema=SpecialistAssessment,
            output_key=f"{s['key']}_view",
            generate_content_config=gen_cfg,
        )
        for s in SPECIALISTS
    ]
    lead = LlmAgent(
        name="decision_lead",
        model=model,
        instruction=_lead_instruction(),
        output_schema=DecisionBrief,
        output_key="decision_brief",
        generate_content_config=gen_cfg,
    )
    war_room = SequentialAgent(
        name="war_room",
        sub_agents=[ParallelAgent(name="specialist_panel", sub_agents=specialists), lead],
    )

    session_service = InMemorySessionService()
    runner = Runner(app_name="fde_war_room", agent=war_room, session_service=session_service,
                    auto_create_session=True)
    session_id = f"war_room_{uuid.uuid4().hex[:10]}"
    message = types.Content(role="user", parts=[types.Part.from_text(text=f"DECISION DATA:\n{context_json}")])

    for _ in runner.run(user_id="plant_operator", session_id=session_id, new_message=message):
        pass

    session = session_service.get_session_sync(
        app_name="fde_war_room", user_id="plant_operator", session_id=session_id
    )
    return dict(session.state) if session else {}


def run_war_room(decision, robustness: Optional[Dict[str, Any]] = None, model: Optional[str] = None
                 ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Run the specialist war room. Returns (specialist_assessments, decision_brief).
    Raises on failure; callers degrade gracefully.
    """
    import os

    from app.ai.llm import model_for

    model = model or model_for("specialists")
    context_json = compact_json(build_decision_context(decision, robustness))
    evidence = gather_domain_evidence(decision)
    valid_ids = {d["id"] for docs in evidence.values() for d in docs}

    engine = _run_adk if os.getenv("FDE_WAR_ROOM_ENGINE", "parallel").lower() == "adk" else _run_parallel
    state = engine(model, context_json, evidence)

    assessments: List[Dict[str, Any]] = []
    for s in SPECIALISTS:
        raw = state.get(f"{s['key']}_view")
        if raw is None:
            continue
        data = raw if isinstance(raw, dict) else json.loads(raw)
        a = SpecialistAssessment.model_validate(data)
        cited = [c for c in a.citations if c in valid_ids]
        assessments.append({
            "agent": s["key"],
            "title": s["title"],
            "stance": a.stance,
            "headline": a.headline,
            "assessment": a.assessment,
            "key_concern": a.key_concern,
            "citations": cited,
            "dropped_citations": [c for c in a.citations if c not in valid_ids],
        })

    brief = None
    raw_brief = state.get("decision_brief")
    if raw_brief is not None:
        b = DecisionBrief.model_validate(raw_brief if isinstance(raw_brief, dict) else json.loads(raw_brief))
        brief = b.model_dump()

    texts = [a["assessment"] for a in assessments] + [a["headline"] for a in assessments]
    if brief:
        # Only keep links between specialists that actually reviewed, and never self-links
        reviewed = {a["agent"] for a in assessments}
        brief["dependencies"] = [
            d for d in brief.get("dependencies", [])
            if d["from_agent"] in reviewed and d["to_agent"] in reviewed and d["from_agent"] != d["to_agent"]
        ]
        texts += [brief["summary"], brief["headline"], *brief["why"], *brief["watch_outs"],
                  *[d["note"] for d in brief["dependencies"]], *brief.get("conflicts", [])]
    ungrounded = find_ungrounded_numbers(texts, context_json + json.dumps(evidence, default=str, ensure_ascii=False))
    # Causes are checked against the facts only (not the documents, where past incidents live)
    unsupported_causes = find_unsupported_causes(texts, context_json)
    if brief is not None:
        brief["specialist_note"] = brief.pop("specialist_consensus", None)
        brief["specialist_consensus"] = _consensus_line(assessments) if assessments else None
        # Objections are decided by the specialists' own stances, not by the lead's wording
        brief["objections"] = objections_from(assessments)
        brief["grounding_check"] = {
            "ungrounded_numbers": ungrounded,
            "unsupported_causes": unsupported_causes,
            "passed": not ungrounded and not unsupported_causes,
        }
    return assessments, brief


def objections_from(assessments: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Specialists who oppose the plan, with their reason, for a deterministic warning banner."""
    return [
        {"agent": a["agent"], "title": a["title"], "reason": a.get("key_concern") or a.get("headline") or ""}
        for a in assessments if a.get("stance") == "oppose"
    ]
