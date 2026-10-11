"""
Factory Decision Engine - Decision Intelligence API Layer.
Provides a thin HTTP/REST boundary for the judge-facing decision UI.
Directs requests to FactoryDecisionAgent without duplicating simulation or optimization logic.

Endpoints are synchronous (FastAPI runs them in a worker thread) because the agent performs
blocking model and database calls. A lock serializes access to the shared conversational agent.
"""
import logging
import threading
from collections import Counter
from datetime import timedelta
from typing import Any, Dict, Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.agent.agent import FactoryDecisionAgent
from app.agent.assistant import generate_action_documents
from app.agent import decision_log
from app.agent.schemas import DecisionResponse
from app.ai.llm import model_for, vertex_enabled
from app.config import config
from app import domains
from app.domains.packs import PACKS
from app.exceptions import UnknownMachineError, InvalidDowntimeError

logger = logging.getLogger("factory_decision_engine.api")

# google-genai's async clients are closed after the ADK runner's private event loop has already
# ended; the resulting "Event loop is closed" tracebacks are harmless shutdown noise.
logging.getLogger("asyncio").addFilter(
    lambda record: not (record.exc_info and "Event loop is closed" in str(record.exc_info[1]))
)

app = FastAPI(
    title="Factory Decision Engine API",
    description="Judge-Facing Decision Intelligence API for Factory Disruption Analysis",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class DomainMiddleware:
    """
    Pure ASGI middleware: read the viewer's industry pack from the `X-FDE-Domain` header (or a
    `domain` query parameter) and set it for this request, so concurrent viewers can use
    different industries. Pure ASGI (not BaseHTTPMiddleware) keeps the ContextVar visible to
    the endpoint and its worker thread.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        requested = headers.get(b"x-fde-domain", b"").decode("latin-1")
        if not requested:
            from urllib.parse import parse_qs
            requested = (parse_qs(scope.get("query_string", b"").decode("latin-1")).get("domain") or [""])[0]
        token = domains.set_domain(requested)
        try:
            await self.app(scope, receive, send)
        finally:
            domains.reset_domain(token)


app.add_middleware(DomainMiddleware)

@app.middleware("http")
async def revalidate_ui_assets(request, call_next):
    """Make browsers revalidate the UI on every load (cheap 304 via ETag) so updates are never stale."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

# Instantiate the central decision agent (preserves immutability of factory database)
agent = FactoryDecisionAgent()
agent_lock = threading.Lock()

class AnalyzeRequest(BaseModel):
    prompt: str = Field(..., description="Natural-language operational scenario or disruption prompt")
    previous_response: Optional[DecisionResponse] = Field(None, description="Previous DecisionResponse object for follow-up sensitivity context")

class WhatIfRequest(BaseModel):
    previous_response: Optional[DecisionResponse] = Field(None, description="Previous DecisionResponse object")
    follow_up_prompt: Optional[str] = Field(None, description="What-if sensitivity question or constraint change")
    updated_downtime: Optional[float] = Field(None, description="Direct downtime parameter override")
    updated_priorities: Optional[Any] = Field(None, description="Direct priority parameter override")
    updated_machine_id: Optional[str] = Field(None, description="Direct machine ID parameter override")

class AskRequest(BaseModel):
    question: str = Field(..., description="Question about the current decision")
    decision: DecisionResponse = Field(..., description="Decision the question refers to")

class ApproveRequest(BaseModel):
    decision: DecisionResponse = Field(..., description="Decision being approved")
    approver: str = Field(..., min_length=1, description="Name/role of the approving person")
    note: Optional[str] = Field(None, description="Optional approval note")

@app.get("/api/health")
def health_check():
    """Health check endpoint indicating model readiness and execution mode."""
    return {
        "status": "healthy",
        "service": "Factory Decision Engine",
        "model": agent.model_name,
        "models": {
            "decision_agent": agent.model_name,
            "understanding_and_assistant": model_for("assistant"),
            "specialist_war_room": model_for("specialists"),
        },
        "live_gemini_available": agent.live_ai,
        "gemini_provider": ("vertex_ai" if vertex_enabled() else "gemini_api") if agent.live_ai else None,
        "default_mode": "live_gemini" if agent.live_ai else "deterministic_fallback",
        "human_approval_policy": "MANDATORY",
        "domain": domains.current_domain(),
        "default_domain": domains.DEFAULT_DOMAIN,
    }

@app.get("/api/domains")
def list_domains():
    """Industry packs the viewer can switch between (same engine, different plant)."""
    return {
        "default": domains.DEFAULT_DOMAIN,
        "current": domains.current_domain(),
        "domains": [domains.public_pack(key) for key in PACKS],
    }

@app.post("/api/analyze", response_model=DecisionResponse)
def analyze_scenario(req: AnalyzeRequest):
    """
    Primary endpoint: Analyze a disruption scenario.
    Dispatches to FactoryDecisionAgent, maintaining numerical authority in Python.
    """
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Scenario prompt cannot be empty.")

    try:
        with agent_lock:
            # No previous decision means a new conversation: never inherit another client's scenario
            if req.previous_response is None:
                agent.reset_state()
            return agent.run(req.prompt.strip(), previous_response=req.previous_response)
    except UnknownMachineError as e:
        logger.warning(f"Unknown machine error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except InvalidDowntimeError as e:
        logger.warning(f"Invalid downtime error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error in scenario analysis: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal decision engine error: {str(e)}")

@app.post("/api/what-if", response_model=DecisionResponse)
def analyze_what_if(req: WhatIfRequest):
    """
    What-if sensitivity analysis endpoint.
    Takes previous decision and follow-up prompt or parameter overrides.
    Re-runs deterministic simulation & optimization without mutating factory database.
    """
    prev_resp = req.previous_response or agent.last_response
    if prev_resp is None:
        raise HTTPException(
            status_code=400,
            detail="No prior scenario available for what-if sensitivity analysis. Please run an initial scenario first."
        )
    has_overrides = any(
        v is not None for v in (req.updated_downtime, req.updated_priorities, req.updated_machine_id)
    )
    try:
        with agent_lock:
            if req.follow_up_prompt and not has_overrides and agent.llm_available:
                return agent.follow_up(prev_resp, req.follow_up_prompt)
            return agent.what_if(
                previous_response=prev_resp,
                follow_up_prompt=req.follow_up_prompt,
                updated_downtime=req.updated_downtime,
                updated_priorities=req.updated_priorities,
                updated_machine_id=req.updated_machine_id,
            )
    except UnknownMachineError as e:
        logger.warning(f"Unknown machine in what-if: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except InvalidDowntimeError as e:
        logger.warning(f"Invalid downtime in what-if: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error during what-if analysis: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"What-if engine error: {str(e)}")

class RerankRequest(BaseModel):
    decision: DecisionResponse = Field(..., description="Decision to re-rank")
    priorities: str = Field(..., description="Priority intent, e.g. 'cost', 'delivery', 'balanced'")

class ReviewRequest(BaseModel):
    decision: DecisionResponse = Field(..., description="Decision for the specialist war room to review")

@app.post("/api/rerank", response_model=DecisionResponse)
def rerank_decision(req: RerankRequest):
    """Instantly re-rank options under new priorities (deterministic, no LLM calls)."""
    try:
        with agent_lock:
            return agent.rerank(req.decision, req.priorities)
    except Exception as e:
        logger.error(f"Re-rank failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Re-rank error: {str(e)}")

@app.post("/api/review", response_model=DecisionResponse)
def review_decision(req: ReviewRequest):
    """Run the specialist war room on an existing decision."""
    return agent.review(req.decision)

@app.post("/api/ask")
def ask_about_decision(req: AskRequest):
    """Grounded Q&A about a decision ('Why not overtime?', 'Explain this to my CEO')."""
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    return agent.ask(req.question.strip(), req.decision)

@app.post("/api/approve")
def approve_decision(req: ApproveRequest):
    """
    Record a human approval and draft the post-approval communication pack.
    Nothing is dispatched to shop-floor systems; documents are drafts for people to send.
    """
    if req.decision.selected_plan is None:
        raise HTTPException(status_code=400, detail="This decision has no feasible plan to approve.")
    pack = generate_action_documents(agent.llm, req.decision, req.approver.strip(), req.note)
    entry = decision_log.record({
        **pack,
        "domain": domains.current_domain(),
        "machine_id": req.decision.impact_summary.get("machine_id"),
        "downtime_hours": req.decision.impact_summary.get("downtime_hours"),
        "execution_mode": req.decision.execution_mode,
        "cost_impact_usd": req.decision.selected_plan.cost_impact_usd,
    })
    return entry

@app.get("/api/decisions")
def list_decisions(limit: int = 50):
    """Audit trail of approved decisions, newest first."""
    return {"decisions": decision_log.list_entries(limit)}

@app.get("/api/factory/overview")
def factory_overview():
    """Plant snapshot for the Control Room view."""
    from app.factory.state import get_factory_state

    state = get_factory_state()
    plant_clock = config.PLANT_CLOCK
    station_names = {s.station_id: s.name for s in state.stations}
    status_counts = Counter(m.status.value for m in state.machines)
    orders = state.production_orders
    due_soon = [o for o in orders if o.due_time <= plant_clock + timedelta(hours=24)]

    lines = []
    for line in state.production_lines:
        machines = [m for m in state.machines if m.line_id == line.line_id]
        line_orders = [o for o in orders if o.assigned_line == line.line_id]
        lines.append({
            "line_id": line.line_id,
            "name": line.name,
            "supported_models": line.supported_models,
            "capacity_per_hour": line.capacity_per_hour,
            "order_count": len(line_orders),
            "high_priority_orders": sum(1 for o in line_orders if o.priority.value == "HIGH"),
            "machines": [
                {
                    "machine_id": m.machine_id,
                    "type": m.machine_type,
                    "station": station_names.get(m.station_id, m.station_id),
                    "status": m.status.value,
                    "health": m.health_score,
                }
                for m in machines
            ],
        })

    return {
        "plant": {"name": state.factory.name if state.factory else "Plant", "location": state.factory.location if state.factory else ""},
        "plant_clock": plant_clock.isoformat(),
        "kpis": {
            "machines_total": len(state.machines),
            "machines_by_status": dict(status_counts),
            "orders_active": len(orders),
            "orders_high_priority": sum(1 for o in orders if o.priority.value == "HIGH"),
            "orders_due_24h": len(due_soon),
            "high_priority_due_24h": sum(1 for o in due_soon if o.priority.value == "HIGH"),
        },
        "lines": lines,
    }

_watch_list_cache: Dict[str, list] = {}

def _watch_list() -> list:
    """Telemetry and plant data are immutable during a run, so each pack's watch list is computed once."""
    domain = domains.current_domain()
    if domain not in _watch_list_cache:
        from app.prediction.health import get_watch_list
        _watch_list_cache[domain] = get_watch_list()
    return _watch_list_cache[domain]

@app.get("/api/watchlist")
def watch_list():
    """Machines ranked by predicted risk, with pre-built backup plans for high-risk ones."""
    return {"machines": _watch_list()}

@app.get("/api/machines/{machine_id}/telemetry")
def machine_telemetry(machine_id: str):
    from app.prediction.health import get_machine_telemetry
    try:
        return get_machine_telemetry(machine_id.upper())
    except UnknownMachineError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.get("/api/alerts")
def list_alerts():
    """Alert inbox: fault events plus predictive early warnings."""
    from app.prediction.alerts import list_alerts as _list_alerts
    return {"alerts": _list_alerts(_watch_list())}

@app.post("/api/alerts/simulate")
def simulate_alert():
    """Demo trigger: raise the next scripted machine fault event."""
    from app.prediction.alerts import simulate_fault
    return simulate_fault()

# Mount UI static directory
UI_DIR = Path(__file__).resolve().parent / "ui"
if UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")

    @app.get("/")
    def serve_index():
        return FileResponse(str(UI_DIR / "index.html"))
