"""
Conversational assistance over a finished decision:
- answer_question: grounded Q&A ("Why not overtime?", "Explain this to my CEO").
- generate_action_documents: after human approval, draft the shop-floor work order,
  quality notice, revised schedule and customer communication.

Gemini only rephrases and organizes facts from the deterministic DecisionResponse. Every
function has a deterministic template fallback so the workflow never blocks on the LLM.
"""
import logging
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


class ActionDocuments(BaseModel):
    supervisor_work_order: str = Field(description="Numbered shop-floor instructions for the shift supervisor.")
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


def answer_question(llm: Optional[GeminiService], question: str, decision) -> Dict[str, Any]:
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
            f"QUESTION FROM PLANT MANAGER:\n{question}"
        )
        res = llm.generate_json(
            prompt, GroundedAnswer,
            system="You answer questions about a factory disruption decision for a plant manager.\n" + GROUNDING_RULES,
            temperature=0.2,
        )
        if res is not None:
            valid = {e["id"] for e in evidence}
            ungrounded = find_ungrounded_numbers([res.answer], compact_json(context, 50_000) + compact_json(evidence, 50_000))
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
                   + GROUNDING_RULES,
            temperature=0.3,
        )
        if res is not None:
            docs = res.model_dump()
            source = "gemini"

    if docs is None:
        docs = _template_documents(decision, approver, note)

    return {
        "approved_by": approver,
        "approved_at": approved_at,
        "note": note,
        "plan_id": decision.selected_plan.plan_id if decision.selected_plan else None,
        "plan_description": _plan_label(decision),
        "scenario": decision.scenario_summary,
        "documents": docs,
        "source": source,
    }


def _template_documents(decision, approver: str, note: Optional[str]) -> Dict[str, str]:
    sp = decision.selected_plan
    orders = decision.affected_orders
    order_lines = "\n".join(
        f"- {o['order_id']} ({o['priority']}, {o['quantity']} x {o['product_model']}): follow {sp.plan_id if sp else 'manual plan'}"
        for o in orders
    ) or "- No orders affected."
    high = [o for o in orders if o.get("priority") == "HIGH"]
    evidence_ids = ", ".join(e.document_id for e in decision.engineering_evidence) or "none retrieved"
    return {
        "supervisor_work_order": (
            f"Approved by {approver}.\n1. Execute: {_plan_label(decision)}.\n"
            "2. Complete setup and calibration before volume run.\n"
            "3. Report progress to the plant manager at shift midpoint."
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
