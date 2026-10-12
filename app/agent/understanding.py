"""
Natural-language scenario understanding.

Gemini converts a free-form operator message ("the welding robot on line B jammed, out till
lunch, don't let the Toyota order slip") into a structured scenario. Python then validates
every field against the factory database, so an invented machine or an absurd duration can
never reach the simulation engine. When Gemini is unavailable the deterministic regex
ScenarioParser is used instead.
"""
import logging
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.ai.llm import GeminiService
from app.domains import get_pack
from app.agent.orchestrator import ScenarioParser

logger = logging.getLogger(__name__)

PRIORITY_DIMENSIONS = ["delivery", "cost", "quality", "energy", "risk"]
MAX_DOWNTIME_HOURS = 168.0
DEFAULT_DOWNTIME_HOURS = 8.0


class ScenarioUnderstanding(BaseModel):
    """Structured interpretation of an operator message, produced by Gemini."""

    intent: Literal["new_scenario", "follow_up", "question", "not_a_scenario"] = Field(
        description="new_scenario: a disruption to analyze. follow_up: changes a parameter of the scenario already "
        "on screen (e.g. 'what if it is 12 hours', 'no overtime', 'prioritize cost'). question: asks about the "
        "current recommendation without changing it (e.g. 'why not overtime?', 'what is the biggest risk?'). "
        "not_a_scenario: anything else."
    )
    machine_id: Optional[str] = Field(None, description="Exact machine ID from the catalog, only if unambiguous.")
    candidate_machine_ids: List[str] = Field(
        default_factory=list, description="If the message fits several machines, list them all."
    )
    downtime_hours: Optional[float] = Field(None, description="Best estimate of downtime in hours.")
    downtime_min_hours: Optional[float] = Field(None, description="Lower bound if a range or vague time was given.")
    downtime_max_hours: Optional[float] = Field(None, description="Upper bound if a range or vague time was given.")
    priority_focus: List[Literal["delivery", "cost", "quality", "energy", "risk"]] = Field(
        default_factory=list, description="Objectives the user explicitly cares about, most important first."
    )
    disallow_overtime: bool = False
    disallow_cross_line_transfer: bool = False
    disallow_all_transfers: bool = False
    clarification_question: Optional[str] = Field(
        None, description="A short question to ask if the machine cannot be identified unambiguously."
    )
    interpretation_summary: str = Field(description="One plain-English sentence restating the scenario.")
    assumptions: List[str] = Field(default_factory=list, description="Anything inferred rather than stated.")


UNDERSTANDING_SYSTEM = """You interpret messages from plant managers and technicians at the plant named in PLANT.
Convert the message into the structured scenario schema.

Rules:
- machine_id must be copied exactly from the MACHINE CATALOG. Never invent an ID.
- If the description fits more than one machine (e.g. "the welding robot on line B" matches several),
  leave machine_id empty, list candidate_machine_ids, and write a short clarification_question.
- Convert time expressions to hours. A shift is 8 hours. "Out till lunch" from a morning start is about 5 hours.
  "The rest of the day" is about 8 hours. If the user gives a range ("4 to 6 hours", "maybe 6-10 h"), fill
  downtime_min_hours and downtime_max_hours and set downtime_hours to the midpoint.
- If no duration is stated, leave all downtime fields empty.
- priority_focus: only objectives the user actually expressed. "Don't let customer orders slip" -> delivery.
  "Keep it cheap" -> cost. "No defects" -> quality. "Save power" -> energy. "Play it safe" -> risk.
- Constraint flags only when explicitly requested ("no overtime", "keep work on this line", "no transfers").
- If CURRENT SCENARIO is provided and the message modifies it ("what if it's 12 hours", "avoid overtime",
  "prioritize cost instead"), set intent to follow_up and fill only the fields that change.
- If CURRENT SCENARIO is provided and the message only asks about it ("why not overtime?", "explain this to my
  boss"), set intent to question.
- Record inferences (e.g. "Assumed 'lunch' means about 5 hours") in assumptions.
"""


def build_machine_catalog() -> List[Dict[str, Any]]:
    """Compact machine catalog used to ground Gemini's machine identification."""
    from app.factory.state import get_factory_state

    state = get_factory_state()
    station_names = {s.station_id: s.name for s in state.stations}
    line_names = {l.line_id: l.name for l in state.production_lines}
    return [
        {
            "id": m.machine_id,
            "type": m.machine_type,
            "station": station_names.get(m.station_id, m.station_id),
            "line": f"{m.line_id} ({line_names.get(m.line_id, '')})",
            "status": m.status.value,
            "health": m.health_score,
        }
        for m in state.machines
    ]


def _priorities_text(focus: List[str]) -> Optional[str]:
    dims = [d for d in focus if d in PRIORITY_DIMENSIONS]
    return " ".join(dict.fromkeys(dims)) if dims else None


def _rules_interpretation(prompt: str) -> Dict[str, Any]:
    parsed = ScenarioParser.parse(prompt)
    parsed["intent"] = "new_scenario" if parsed.get("machine_id") else "unknown"
    parsed["downtime_range"] = None
    parsed["interpretation"] = {
        "source": "rules",
        "summary": (
            f"{parsed['machine_id']} unavailable for {parsed['downtime_hours']:g} h"
            if parsed.get("machine_id")
            else "No machine ID found in the message."
        ),
        "machine_id": parsed.get("machine_id"),
        "downtime_hours": parsed.get("downtime_hours"),
        "downtime_range": None,
        "priority_focus": [],
        "constraints": {
            k: parsed.get(k, False)
            for k in ("disallow_overtime", "disallow_cross_line_transfer", "disallow_all_transfers")
        },
        "assumptions": [],
        "candidate_machine_ids": [],
    }
    return parsed


def understand_scenario(
    prompt: str,
    llm: Optional[GeminiService],
    current_scenario: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Interpret an operator message. Returns a dict compatible with ScenarioParser.parse output,
    plus 'intent', 'downtime_range' and an 'interpretation' block for the UI.
    """
    if llm is None or not llm.available:
        return _rules_interpretation(prompt)

    try:
        catalog = build_machine_catalog()
    except Exception as e:
        logger.warning(f"Machine catalog unavailable for understanding: {e}")
        return _rules_interpretation(prompt)

    valid_ids = {m["id"] for m in catalog}
    catalog_lines = "\n".join(
        f"{m['id']}: {m['type']} | {m['station']} | {m['line']} | {m['status']} (health {m['health']})"
        for m in catalog
    )
    context = f"CURRENT SCENARIO: {current_scenario}\n" if current_scenario else ""
    pack = get_pack()
    vocabulary = "; ".join(f"{op['code']} = {op['noun']} ({op['display']})" for op in pack["operations"])
    request = (
        f"PLANT: {pack['plant']['name']} ({pack['label']})\nMACHINE TYPES: {vocabulary}\n\n"
        f"MACHINE CATALOG:\n{catalog_lines}\n\n{context}MESSAGE:\n{prompt}"
    )

    result = llm.generate_json(request, ScenarioUnderstanding, system=UNDERSTANDING_SYSTEM)
    if result is None:
        return _rules_interpretation(prompt)

    assumptions = list(result.assumptions)
    clarification = result.clarification_question

    machine_id = (result.machine_id or "").upper().strip() or None
    candidates = [c.upper() for c in result.candidate_machine_ids if c.upper() in valid_ids]
    if machine_id and machine_id not in valid_ids:
        clarification = (
            f"Machine '{machine_id}' is not in the plant registry. "
            "Which machine is affected? (e.g. M17 on Line B)"
        )
        machine_id = None
    if not machine_id and result.intent == "new_scenario" and not clarification:
        if candidates:
            clarification = f"Which machine is affected: {', '.join(candidates)}?"
        else:
            clarification = "Which machine is affected? Please name it (e.g. M17) or describe its station and line."

    lo, hi = result.downtime_min_hours, result.downtime_max_hours
    downtime_range = None
    if lo is not None and hi is not None and 0 < lo <= hi:
        downtime_range = [round(min(lo, MAX_DOWNTIME_HOURS), 2), round(min(hi, MAX_DOWNTIME_HOURS), 2)]

    downtime = result.downtime_hours
    if downtime is None and downtime_range:
        downtime = sum(downtime_range) / 2
    if downtime is None or downtime <= 0:
        if result.intent == "new_scenario":
            assumptions.append(f"No duration given; assumed {DEFAULT_DOWNTIME_HOURS:g} h (one shift).")
            downtime = DEFAULT_DOWNTIME_HOURS
        else:
            downtime = None
    if downtime is not None:
        downtime = round(min(float(downtime), MAX_DOWNTIME_HOURS), 2)

    constraints = {
        "disallow_overtime": result.disallow_overtime,
        "disallow_cross_line_transfer": result.disallow_cross_line_transfer,
        "disallow_all_transfers": result.disallow_all_transfers,
    }
    priorities = _priorities_text(result.priority_focus)

    return {
        "intent": result.intent,
        "machine_id": machine_id,
        "downtime_hours": downtime if downtime is not None else DEFAULT_DOWNTIME_HOURS,
        "downtime_explicit": downtime is not None,
        "downtime_range": downtime_range,
        "priorities": priorities,
        **constraints,
        "clarification_needed": clarification if not machine_id else None,
        "interpretation": {
            "source": "gemini",
            "summary": result.interpretation_summary,
            "machine_id": machine_id,
            "downtime_hours": downtime,
            "downtime_range": downtime_range,
            "priority_focus": list(dict.fromkeys(result.priority_focus)),
            "constraints": constraints,
            "assumptions": assumptions,
            "candidate_machine_ids": candidates,
        },
    }
