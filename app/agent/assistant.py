"""
Conversational assistance over a finished decision:
- answer_question: grounded Q&A ("Why not overtime?", "Explain this to my CEO").
- generate_action_documents: after human approval, draft the shop-floor work order,
  quality notice, revised schedule and customer communication.

Gemini only rephrases and organizes facts from the deterministic DecisionResponse. Every
function has a deterministic template fallback so the workflow never blocks on the LLM.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.ai.llm import GeminiService, compact_json
from app.agent.specialists import build_decision_context, find_ungrounded_numbers

logger = logging.getLogger(__name__)

GROUNDING_RULES = """Rules:
- Use ONLY facts and numbers present in DECISION DATA and EVIDENCE. Never compute new figures.
- If the data cannot answer something, say so plainly ("The model does not track labour skills").
- Cite document IDs from EVIDENCE when you rely on them.
- Describe internal 0-1 scores in words (low/medium/high) rather than quoting them; quote dollars, hours,
  units, kWh and order counts exactly.
- Human approval is mandatory; never claim anything was executed on the shop floor."""


class GroundedAnswer(BaseModel):
    answer: str = Field(description="Direct answer in 2-5 short sentences, plain English.")
    citations: List[str] = Field(default_factory=list, description="Document IDs relied on.")
    confidence: str = Field(description="'high' if fully answered by the data, 'partial' or 'low' otherwise.")


DOCUMENT_RULES = """Document rules:
- The supervisor work order must include every specialist condition in SPECIALIST_CONDITIONS, in a sensible
  order with the Decision Lead's next steps; the quality notice must include the quality conditions.
- Use readable names: machine IDs like M26 are fine, but never internal codes such as PLAN_TRANSFER_07,
  STA_B2 or LINE_C (write "Station B2", "Line C (Thermostats)", "the approved plan").
- Put each numbered step and each order on its own line ("1. ...", "- ...").
- Mention only risks that appear in the data or the conditions; do not add others.
- The plan is already approved: never list "obtain approval" as a step.
- Never mention internal field names (such as "rule-based risks", "decision data", "specialist_conditions").
- Write dates and times as "2 Oct, 17:00", never as raw timestamps; say what changes for each order.
- The customer update goes outside the plant: no document IDs, machine IDs or internal terms in it."""


class ActionDocuments(BaseModel):
    supervisor_work_order: str = Field(description="Numbered shop-floor instructions for the shift supervisor, one step per line.")
    quality_notice: str = Field(description="Notice to the quality team: inspections/holds required, with citations.")
    revised_schedule_summary: str = Field(description="What changes for each affected order, as a short list.")
    customer_message: str = Field(description="Short professional update for the account manager to send to the "
                                              "customer of the highest-priority affected order. No promises beyond the data.")


def _evidence_list(decision) -> List[Dict[str, Any]]:
    return [
        {"id": e.document_id, "title": e.title, "excerpt": e.excerpt}
        for e in decision.engineering_evidence
    ]


def _plan_label(decision) -> str:
    sp = decision.selected_plan
    return sp.description if sp else "No feasible plan"


MAX_HISTORY_TURNS = 3


def format_history(history: Optional[List[Dict[str, str]]]) -> str:
    """The last few questions and answers, so follow-ups like 'and what would that cost?' make sense."""
    turns = [t for t in (history or []) if (t.get("question") or "").strip()][-MAX_HISTORY_TURNS:]
    if not turns:
        return ""
    lines = [
        "EARLIER IN THIS CONVERSATION (oldest first; use it only to understand follow-up questions such as "
        "'that', 'it' or 'instead'; every figure in your answer must still come from the data or tool results):"
    ]
    for t in turns:
        lines.append(f"Q: {t['question'].strip()[:300]}")
        lines.append(f"A: {(t.get('answer') or '').strip()[:800]}")
    return "\n".join(lines) + "\n\n"


def answer_question(llm: Optional[GeminiService], question: str, decision, history=None) -> Dict[str, Any]:
    """Answer a question about the current decision, grounded in its data."""
    context = build_decision_context(decision, decision.robustness)
    evidence = _evidence_list(decision)
    if decision.specialist_assessments:
        context["specialist_reviews"] = [
            {"agent": a.get("title"), "stance": a.get("stance"), "view": a.get("assessment"),
             "concern": a.get("key_concern")}
            for a in decision.specialist_assessments
        ]

    if llm is not None and llm.available:
        prompt = (
            f"DECISION DATA:\n{compact_json(context)}\n\nEVIDENCE:\n{compact_json(evidence, 5000)}\n\n"
            f"{format_history(history)}QUESTION FROM PLANT MANAGER:\n{question}"
        )
        res = llm.generate_json(
            prompt, GroundedAnswer,
            system="You answer questions about a factory disruption decision for a plant manager.\n" + GROUNDING_RULES,
            temperature=0.2,
        )
        if res is not None:
            valid = {e["id"] for e in evidence}
            # Earlier answers were checked when they were given, so their figures count as known
            ungrounded = find_ungrounded_numbers(
                [res.answer], compact_json(context, 50_000) + compact_json(evidence, 50_000) + format_history(history) + question
            )
            return {
                "answer": res.answer,
                "citations": [c for c in res.citations if c in valid],
                "confidence": res.confidence,
                "source": "gemini",
                "grounding_check": {"ungrounded_numbers": ungrounded, "passed": not ungrounded},
            }

    return {
        "answer": _template_answer(question, decision),
        "citations": [],
        "confidence": "partial",
        "source": "rules",
    }


def _template_answer(question: str, decision) -> str:
    q = question.lower()
    sp = decision.selected_plan
    if sp is None:
        return "No feasible recovery plan was found, so supervisory intervention is required."
    if "overtime" in q:
        ot = next((p for p in decision.candidate_plans if p.strategy_type == "OVERTIME"), None)
        if ot is None:
            return "No overtime option was generated for this scenario."
        if not ot.is_feasible:
            return "Overtime was ruled out: " + "; ".join(ot.infeasibility_reasons)
        return (f"Overtime is feasible (extra cost ${ot.cost_impact_usd:,.2f}) but scored lower than "
                f"the recommended plan under the current priorities.")
    if "risk" in q:
        return "Main risks: " + "; ".join(decision.risks)
    return (f"Recommended: {sp.description}. Extra cost ${sp.cost_impact_usd or 0:,.2f}. "
            "Ask about a specific option (e.g. overtime) or risk for more detail.")


def generate_action_documents(
    llm: Optional[GeminiService],
    decision,
    approver: str,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """Draft the post-approval communication pack for the approved plan."""
    context = build_decision_context(decision, decision.robustness)
    # What the approver signed up to: the specialists' conditions and the Decision Lead's next steps
    conditions = _specialist_conditions(decision)
    context["specialist_conditions"] = conditions
    context["decision_lead_next_steps"] = [
        s for s in ((decision.decision_brief or {}).get("next_steps") or []) if not _APPROVAL_STEP.search(s)
    ]
    evidence = _evidence_list(decision)
    approved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    docs: Optional[Dict[str, str]] = None
    source = "rules"
    if llm is not None and llm.available:
        prompt = (
            f"APPROVED BY: {approver}\nAPPROVER NOTE: {note or 'none'}\n\n"
            f"DECISION DATA:\n{compact_json(context)}\n\nEVIDENCE:\n{compact_json(evidence, 5000)}\n\n"
            "Draft the communication pack for the RECOMMENDED PLAN that was just approved."
        )
        res = llm.generate_json(
            prompt, ActionDocuments,
            system="You draft operational documents for a manufacturing plant after a human-approved decision.\n"
                   + GROUNDING_RULES + "\n" + DOCUMENT_RULES,
            temperature=0.3,
        )
        if res is not None:
            docs = {k: readable_codes(v) for k, v in res.model_dump().items()}
            docs["supervisor_work_order"] = drop_approval_steps(docs["supervisor_work_order"])
            source = "gemini"

    if docs is None:
        docs = _template_documents(decision, approver, note)

    # Same check as every other AI text: numbers must exist in the data, the evidence or the approver's note
    ungrounded = find_ungrounded_numbers(
        list(docs.values()), compact_json(context, 50_000) + compact_json(evidence, 50_000) + (note or "") + approver
    ) if source == "gemini" else []

    return {
        "approved_by": approver,
        "approved_at": approved_at,
        "note": note,
        "plan_id": decision.selected_plan.plan_id if decision.selected_plan else None,
        "plan_description": _plan_label(decision),
        "scenario": decision.scenario_summary,
        "documents": docs,
        "source": source,
        "conditions": conditions,
        "grounding_check": {"ungrounded_numbers": ungrounded, "passed": not ungrounded},
    }


def _specialist_conditions(decision) -> List[Dict[str, str]]:
    return [
        {"specialist": a.get("title"), "condition": a.get("key_concern")}
        for a in (decision.specialist_assessments or [])
        if (a.get("key_concern") or "").strip() and a.get("key_concern").strip().lower() != "none"
    ]


_STATION_RE = re.compile(r"\bSTA_([A-Z])(\d)\b")
_LINE_RE = re.compile(r"\bLINE_([A-Z])\b")
_PLAN_RE = re.compile(r"\s*\(?\b(?:under |plan )?PLAN_[A-Z]+_\d+\b\)?")


_APPROVAL_STEP = re.compile(r"\b(?:approve|approval|sign[- ]off on the plan)\b", re.IGNORECASE)
_STEP_RE = re.compile(r"(?:^|\s)(\d{1,2})\.\s")


def drop_approval_steps(text: str) -> str:
    """The plan is already approved when the work order is written: remove 'review and approve' steps, renumber."""
    parts = [p.strip() for p in _STEP_RE.split(text or "")]
    if len(parts) < 3:  # not a numbered list
        return text
    intro, steps = parts[0], [parts[i + 1] for i in range(1, len(parts) - 1, 2)]
    kept = [s for s in steps if s and not (_APPROVAL_STEP.search(s) and not re.search(r"sign-off is complete|signed off", s, re.I))]
    if not kept:
        return text
    body = "\n".join(f"{i}. {s}" for i, s in enumerate(kept, start=1))
    return f"{intro}\n{body}".strip() if intro else body


def readable_codes(text: str) -> str:
    """Last safety net for documents that leave the plant: no internal codes."""
    from app.domains import get_pack

    lines = get_pack().get("lines", {})
    text = _STATION_RE.sub(lambda m: f"Station {m.group(1)}{m.group(2)}", text or "")
    text = _LINE_RE.sub(lambda m: lines.get(f"LINE_{m.group(1)}", f"Line {m.group(1)}"), text)
    return _PLAN_RE.sub("", text)


def _template_documents(decision, approver: str, note: Optional[str]) -> Dict[str, str]:
    sp = decision.selected_plan
    orders = decision.affected_orders
    order_lines = "\n".join(
        f"- {o['order_id']} ({o['priority']}, {o['quantity']} x {o['product_model']}): follow {'the approved plan' if sp else 'a manual plan'}"
        for o in orders
    ) or "- No orders affected."
    high = [o for o in orders if o.get("priority") == "HIGH"]
    evidence_ids = ", ".join(e.document_id for e in decision.engineering_evidence) or "none retrieved"
    return {
        "supervisor_work_order": (
            f"Approved by {approver}.\n1. Execute: {_plan_label(decision)}.\n"
            "2. Complete setup and calibration before volume run.\n"
            + "".join(f"{i}. {c['specialist']} condition: {c['condition']}\n"
                      for i, c in enumerate(_specialist_conditions(decision), start=3))
            + f"{3 + len(_specialist_conditions(decision))}. Report progress to the plant manager at shift midpoint."
            + (f"\nNote: {note}" if note else "")
        ),
        "quality_notice": (
            "Apply standard inspection requirements for the approved change "
            f"(reference documents: {evidence_ids}). Hold output until first-article results are signed off."
        ),
        "revised_schedule_summary": order_lines,
        "customer_message": (
            f"Update on order {high[0]['order_id']}: an equipment disruption occurred and a recovery plan has been "
            "approved. We will confirm the delivery status after the first production run."
            if high else "No high-priority customer orders are affected; no customer message required."
        ),
    }
