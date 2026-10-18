"""
Factory Decision Engine - Decision Intelligence API Layer.
Provides a thin HTTP/REST boundary for the judge-facing decision UI.
Directs requests to FactoryDecisionAgent without duplicating simulation or optimization logic.

Endpoints are synchronous (FastAPI runs them in a worker thread) because the agent performs
blocking model and database calls. A lock serializes access to the shared conversational agent.
"""
import hashlib
import json
import os
import logging
import threading
from collections import Counter
from datetime import timedelta
from typing import Any, Dict, List, Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.agent.agent import FactoryDecisionAgent
from app.agent.assistant import generate_action_documents
from app.agent import decision_log
from app.agent.schemas import DecisionResponse
from app.ai.llm import GeminiService, model_for, vertex_enabled
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
        from app.prediction import alerts as _alerts
        token = domains.set_domain(requested)
        viewer_token = _alerts.set_viewer(headers.get(b"x-fde-viewer", b"").decode("latin-1"))
        try:
            await self.app(scope, receive, send)
        finally:
            _alerts.reset_viewer(viewer_token)
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
_approval_lock = threading.Lock()

class AnalyzeRequest(BaseModel):
    prompt: Optional[str] = Field(None, description="Natural-language operational scenario or disruption prompt")
    machine_id: Optional[str] = Field(None, description="Direct target machine ID for manual scenario entry")
    downtime_hours: Optional[float] = Field(None, description="Direct downtime hours for manual scenario entry")
    constraints: Optional[Dict[str, bool]] = Field(None, description="Explicit operational constraints e.g. disallow_overtime")
    priorities: Optional[Any] = Field(None, description="Explicit priorities or weights e.g. 'minimize cost'")
    incident_id: Optional[str] = Field(None, description="Originating incident ID if alert-driven")
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
    history: List[Dict[str, str]] = Field(
        default_factory=list, description="Earlier questions and answers about this decision ({question, answer}), oldest first"
    )

class ApproveRequest(BaseModel):
    decision: DecisionResponse = Field(..., description="Decision being approved")
    approver: str = Field(..., min_length=1, description="Name/role of the approving person")
    note: Optional[str] = Field(None, description="Optional approval note")
    idempotency_key: Optional[str] = Field(None, description="Optional stable client idempotency key")
    incident_id: Optional[str] = Field(None, description="Alert this decision responds to; an approved fault leaves the inbox")

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
    """Industry packs (same engine, different plant). The demo shows one plant; the selector
    appears only when FDE_INDUSTRY_SWITCH is on (the packs share one layout, so switching
    would show identical numbers under different names)."""
    return {
        "default": domains.DEFAULT_DOMAIN,
        "current": domains.current_domain(),
        "switchable": os.getenv("FDE_INDUSTRY_SWITCH", "").strip().lower() in ("1", "true", "yes"),
        "domains": [domains.public_pack(key) for key in PACKS],
    }

@app.post("/api/analyze", response_model=DecisionResponse)
def analyze_scenario(req: AnalyzeRequest):
    """
    Primary endpoint: Analyze a disruption scenario.
    Dispatches to FactoryDecisionAgent, maintaining numerical authority in Python.
    Supports:
    1. Natural-language prompt, optionally tagged with the alert it came from (incident_id):
       Gemini understanding, deterministic engine, robustness and specialist review
    2. Direct parameters without a prompt (machine_id, downtime_hours, constraints):
       deterministic engine only
    All paths converge on the exact same deterministic simulation and evaluation engine.
    """
    has_prompt = bool(req.prompt and req.prompt.strip())
    has_manual = bool(req.machine_id and req.downtime_hours is not None)

    if not has_prompt and not has_manual:
        raise HTTPException(status_code=400, detail="Scenario prompt cannot be empty.")

    try:
        with agent_lock:
            # No previous decision means a new conversation: never inherit another client's scenario
            if req.previous_response is None:
                agent.reset_state()

            # Direct parameter entry (API clients only): deterministic engine, no AI review.
            # When a description is also sent (e.g. from an alert), the full AI path below wins.
            if has_manual and not has_prompt:
                if req.downtime_hours <= 0:
                    raise HTTPException(status_code=400, detail="Downtime hours must be greater than zero.")
                mid = req.machine_id.strip().upper()
                res = agent.orchestrate_scenario(
                    machine_id=mid,
                    downtime_hours=float(req.downtime_hours),
                    priorities=req.priorities,
                    constraints=req.constraints,
                )
                if req.incident_id:
                    res.scenario_summary = f"[{req.incident_id}] {res.scenario_summary}"
                return res

            # Natural-language prompt path
            res = agent.run(req.prompt.strip(), previous_response=req.previous_response)
            if req.incident_id:
                res.scenario_summary = f"[{req.incident_id}] {res.scenario_summary}"
            return res
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
    return agent.ask(req.question.strip(), req.decision, history=req.history)

# Voice notes: a report is a sentence or two. The browser stops recording at 60 s; 5 MB covers
# 60 s of WAV and far more of WebM/Ogg, so anything larger is not a voice note.
MAX_AUDIO_BYTES = 5 * 1024 * 1024
AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/wav", "audio/mp4", "audio/mpeg", "audio/aac", "audio/flac"}
AUDIO_TYPE_ALIASES = {"audio/x-wav": "audio/wav", "audio/wave": "audio/wav", "audio/vnd.wave": "audio/wav",
                      "audio/mp3": "audio/mpeg", "audio/x-m4a": "audio/mp4", "audio/m4a": "audio/mp4",
                      "video/webm": "audio/webm"}
_transcriber: Optional[GeminiService] = None


@app.post("/api/transcribe")
async def transcribe_voice_note(request: Request):
    """
    Gemini listens to a recorded voice note (the raw audio is the request body, with its
    Content-Type, e.g. audio/webm from the browser's MediaRecorder) and returns the text.
    The raw body avoids a multipart dependency. Errors are clear so the UI can fall back to
    the browser's own speech recognition.
    """
    global _transcriber
    mime = request.headers.get("content-type", "").split(";")[0].strip().lower()
    mime = AUDIO_TYPE_ALIASES.get(mime, mime)
    if mime not in AUDIO_TYPES:
        raise HTTPException(status_code=415, detail="Send the recording as WebM, Ogg, WAV, MP4 or MP3 audio.")
    too_large = HTTPException(status_code=413, detail="The recording is too long. Keep voice notes under a minute.")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_AUDIO_BYTES:
        raise too_large
    audio = bytearray()
    async for chunk in request.stream():
        audio += chunk
        if len(audio) > MAX_AUDIO_BYTES:
            raise too_large
    if not audio:
        raise HTTPException(status_code=400, detail="The recording is empty.")

    if _transcriber is None:
        _transcriber = GeminiService(model=model_for("assistant"))
    if not _transcriber.available:
        raise HTTPException(status_code=503, detail="Gemini is not set up on this server, so it cannot transcribe.")
    # The Gemini call blocks, so it runs in a worker thread like the synchronous endpoints
    text = await run_in_threadpool(_transcriber.transcribe_audio, bytes(audio), mime)
    if text is None:
        raise HTTPException(status_code=502, detail="Gemini could not transcribe the recording right now.")
    return {"text": text, "source": "gemini", "model": _transcriber.last_model}


@app.post("/api/approve")
def approve_decision(req: ApproveRequest):
    """
    Record a human approval and draft the post-approval communication pack.
    Nothing is dispatched to shop-floor systems; documents are drafts for people to send.
    """
    if req.decision.selected_plan is None or not req.decision.selected_plan.is_feasible:
        raise HTTPException(
            status_code=400,
            detail="Cannot approve an infeasible plan or a decision without a selected recovery plan."
        )

    approver = req.approver.strip()
    mach_id = req.decision.impact_summary.get("machine_id")
    dt_hours = req.decision.impact_summary.get("downtime_hours")

    # Complete decision fingerprint: includes scenario, weights, loss, plan, cost, and approver
    decision_signature = {
        "scenario": req.decision.scenario_summary,
        "machine_id": mach_id,
        "downtime_hours": dt_hours,
        "capacity_loss": req.decision.impact_summary.get("capacity_loss_units"),
        "plan_id": req.decision.selected_plan.plan_id,
        "cost_impact_usd": req.decision.selected_plan.cost_impact_usd,
        "objective_weights": req.decision.objective_weights,
        "approver": approver,
        "idempotency_key": req.idempotency_key or "",
    }
    fingerprint = hashlib.sha256(json.dumps(decision_signature, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    with _approval_lock:
        entry = next((e for e in decision_log.list_entries(50)
                      if (req.idempotency_key and e.get("idempotency_key") == req.idempotency_key)
                      or e.get("fingerprint") == fingerprint), None)
        if entry is None:
            pack = generate_action_documents(agent.llm, req.decision, approver, req.note)
            entry = decision_log.record({
                **pack,
                "fingerprint": fingerprint,
                "idempotency_key": req.idempotency_key,
                "domain": domains.current_domain(),
                "machine_id": mach_id,
                "downtime_hours": dt_hours,
                "execution_mode": req.decision.execution_mode,
                "cost_impact_usd": req.decision.selected_plan.cost_impact_usd,
            })

    # The fault this plan answers is handled: it leaves the viewer's "Needs action now" list
    if req.incident_id:
        from app.prediction.alerts import resolve_fault
        resolved = resolve_fault(req.incident_id, mach_id)
        if resolved:
            entry = {**entry, "resolved_incident": {"alert_id": resolved["alert_id"], "title": resolved["title"]}}
    return entry

@app.get("/api/decisions")
def list_decisions(limit: int = 50):
    """Audit trail of approved decisions, newest first."""
    return {"decisions": decision_log.list_entries(limit)}

@app.get("/api/factory/overview")
def factory_overview():
    """Plant snapshot for the Factory Overview and Control Room."""
    from app.factory.state import get_factory_state
    from app.prediction.alerts import list_alerts

    state = get_factory_state()
    plant_clock = config.PLANT_CLOCK
    station_names = {s.station_id: s.name for s in state.stations}
    station_ops = {s.station_id: s.operation_type for s in state.stations}
    station_caps = {s.station_id: s.capacity_per_hour for s in state.stations}
    status_counts = Counter(m.status.value for m in state.machines)
    orders = state.production_orders
    due_soon = [o for o in orders if o.due_time <= plant_clock + timedelta(hours=24)]

    # Identify lines and stations with impacted machines (DOWN or DEGRADED)
    impacted_machine_ids = {m.machine_id for m in state.machines if m.status.value in ("DOWN", "DEGRADED", "MAINTENANCE")}
    impacted_line_ids = {m.line_id for m in state.machines if m.machine_id in impacted_machine_ids}

    # All alerts to attach to machines
    alerts = list_alerts(_watch_list())
    machine_alert_map = {a["machine_id"]: a["alert_id"] for a in alerts if "machine_id" in a}

    lines = []
    total_capacity = 0.0
    total_scheduled_24h_units = 0

    for line in state.production_lines:
        line_machines = [m for m in state.machines if m.line_id == line.line_id]
        line_stations = [s for s in state.stations if s.line_id == line.line_id]
        line_orders = [o for o in orders if o.assigned_line == line.line_id]
        line_due_soon = [o for o in line_orders if o.due_time <= plant_clock + timedelta(hours=24)]

        # Calculate scheduled demand in units
        units_24h = sum(o.quantity for o in line_due_soon)
        total_scheduled_24h_units += units_24h

        # Line capacity over 24h
        daily_capacity = line.capacity_per_hour * 24.0
        total_capacity += line.capacity_per_hour
        utilization_pct = round(min(100.0, (units_24h / daily_capacity * 100.0)), 1) if daily_capacity > 0 else None

        # Bottleneck station: minimum capacity_per_hour among stations on this line
        bottleneck = min(line_stations, key=lambda s: s.capacity_per_hour) if line_stations else None

        lines.append({
            "line_id": line.line_id,
            "name": line.name,
            "supported_models": line.supported_models,
            "capacity_per_hour": line.capacity_per_hour,
            "capacity_24h": daily_capacity,
            "scheduled_units_24h": units_24h,
            "utilization_pct": utilization_pct,
            "order_count": len(line_orders),
            "high_priority_orders": sum(1 for o in line_orders if o.priority.value == "HIGH"),
            "bottleneck_station": {
                "station_id": bottleneck.station_id if bottleneck else None,
                "name": bottleneck.name if bottleneck else None,
                "capacity_per_hour": bottleneck.capacity_per_hour if bottleneck else None,
            } if bottleneck else None,
            "status": "IMPACTED" if line.line_id in impacted_line_ids else "OPERATIONAL",
            "machines": [
                {
                    "machine_id": m.machine_id,
                    "type": m.machine_type,
                    "station_id": m.station_id,
                    "station": station_names.get(m.station_id, m.station_id),
                    "operation_type": station_ops.get(m.station_id, "UNKNOWN"),
                    "cycle_time_seconds": m.cycle_time_seconds,
                    "capacity_per_hour": m.capacity_per_hour,
                    "status": m.status.value,
                    "health": m.health_score,
                    "line_id": line.line_id,
                    "line_name": line.name,
                    "active_alert_id": machine_alert_map.get(m.machine_id),
                }
                for m in line_machines
            ],
        })

    total_factory_24h_cap = total_capacity * 24.0
    factory_utilization = round(min(100.0, (total_scheduled_24h_units / total_factory_24h_cap * 100.0)), 1) if total_factory_24h_cap > 0 else None

    # Line disruption exposure: all active orders on lines experiencing stoppage or degradation
    line_impacted_orders = [o for o in orders if o.assigned_line in impacted_line_ids and o.status.value in ("PLANNED", "IN_PROGRESS")]
    # Demonstrably elevated delivery risk: orders scheduled on disrupted lines AND due within the next 24 hours
    delivery_risk_orders = [o for o in line_impacted_orders if o in due_soon]

    # Maintenance events
    active_maintenance = [
        {
            "maintenance_id": me.maintenance_id,
            "machine_id": me.machine_id,
            "maintenance_type": me.maintenance_type.value,
            "status": me.status.value,
            "start_time": me.start_time.isoformat(),
            "duration_hours": me.duration_hours,
        }
        for me in state.maintenance_events
    ]

    return {
        "plant": {
            "name": state.factory.name if state.factory else "Plant",
            "location": state.factory.location if state.factory else "",
            "operating_hours": state.factory.operating_hours if state.factory else 24.0,
        },
        "data_mode": {
            "mode": "synthetic_demo",
            "label": "Synthetic Demonstration Dataset",
            "description": "Deterministic manufacturing simulation engine using reproducible SQLite shop-floor database.",
            "live_integration_status": "Simulated Plant State (No live industrial PLC connection claimed)",
            "delivery_risk_rule": "Rule-based indicator: Orders on disrupted lines due within 24h are flagged as ELEVATED delivery commitment risk (heuristic indicator, not a confirmed late-delivery prediction). Other orders on disrupted lines are monitored as line exposure.",
        },
        "plant_clock": plant_clock.isoformat(),
        "kpis": {
            "machines_total": len(state.machines),
            "machines_by_status": dict(status_counts),
            "machines_running": status_counts.get("HEALTHY", 0),
            "machines_degraded": status_counts.get("DEGRADED", 0),
            "machines_stopped": status_counts.get("DOWN", 0) + status_counts.get("MAINTENANCE", 0),
            "machines_unknown": 0,
            "orders_active": len(orders),
            "orders_high_priority": sum(1 for o in orders if o.priority.value == "HIGH"),
            "orders_due_24h": len(due_soon),
            "high_priority_due_24h": sum(1 for o in due_soon if o.priority.value == "HIGH"),
            "orders_line_exposure": len(line_impacted_orders),
            "orders_delivery_risk_elevated": len(delivery_risk_orders),
            "high_priority_delivery_risk": sum(1 for o in delivery_risk_orders if o.priority.value == "HIGH"),
            "orders_at_risk": len(delivery_risk_orders),
            "high_priority_at_risk": sum(1 for o in delivery_risk_orders if o.priority.value == "HIGH"),
            "total_capacity_per_hour": total_capacity,
            "total_scheduled_24h_units": total_scheduled_24h_units,
            "factory_utilization_pct": factory_utilization,
            "active_incidents_count": len(alerts),
            "active_incidents_urgent": sum(1 for a in alerts if a.get("severity") in ("CRITICAL", "HIGH")),
        },
        "lines": lines,
        "orders": [
            {
                "order_id": o.order_id,
                "product_model": o.product_model,
                "quantity": o.quantity,
                "priority": o.priority.value,
                "due_time": o.due_time.isoformat(),
                "assigned_line": o.assigned_line,
                "status": o.status.value,
                "delivery_risk": "ELEVATED" if o in delivery_risk_orders else ("MONITORED_LINE_EXPOSURE" if o.assigned_line in impacted_line_ids else "NOMINAL"),
                "line_impacted": o.assigned_line in impacted_line_ids,
                "is_due_24h": o in due_soon,
            }
            for o in sorted(orders, key=lambda o: (0 if o.priority.value == "HIGH" else 1 if o.priority.value == "MEDIUM" else 2, o.due_time))
        ],
        "maintenance_events": active_maintenance,
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

@app.get("/api/machines/{machine_id}")
def get_machine_detail(machine_id: str):
    """Full operational detail for a specific machine including telemetry, schedules, maintenance, and alerts."""
    from app.factory.state import get_machine, get_factory_state
    from app.prediction.health import get_machine_telemetry
    from app.prediction.alerts import list_alerts

    mid = machine_id.upper()
    try:
        machine = get_machine(mid)
    except UnknownMachineError as e:
        raise HTTPException(status_code=404, detail=str(e))

    state = get_factory_state()
    station = next((s for s in state.stations if s.station_id == machine.station_id), None)
    line = next((l for l in state.production_lines if l.line_id == machine.line_id), None)

    # Schedules and assigned orders
    schedules = [s for s in state.production_schedules if s.machine_id == mid]
    sched_order_ids = {s.order_id for s in schedules}
    orders = [o for o in state.production_orders if o.order_id in sched_order_ids or o.assigned_line == machine.line_id]

    # Maintenance events
    maints = [me for me in state.maintenance_events if me.machine_id == mid]

    # Telemetry and early-warning assessment
    telemetry_data = get_machine_telemetry(mid)

    # Associated alerts
    all_alerts = list_alerts(_watch_list())
    machine_alerts = [a for a in all_alerts if a.get("machine_id") == mid]

    return {
        "machine_id": machine.machine_id,
        "machine_type": machine.machine_type,
        "status": machine.status.value,
        "health_score": machine.health_score,
        "cycle_time_seconds": machine.cycle_time_seconds,
        "capacity_per_hour": machine.capacity_per_hour,
        "station": {
            "station_id": station.station_id if station else machine.station_id,
            "name": station.name if station else machine.station_id,
            "operation_type": station.operation_type if station else "UNKNOWN",
            "capacity_per_hour": station.capacity_per_hour if station else machine.capacity_per_hour,
        },
        "line": {
            "line_id": line.line_id if line else machine.line_id,
            "name": line.name if line else machine.line_id,
            "capacity_per_hour": line.capacity_per_hour if line else 0.0,
            "supported_models": line.supported_models if line else [],
        },
        "scheduled_orders": [
            {
                "order_id": o.order_id,
                "product_model": o.product_model,
                "quantity": o.quantity,
                "priority": o.priority.value,
                "due_time": o.due_time.isoformat(),
                "status": o.status.value,
            }
            for o in orders[:10]
        ],
        "maintenance_events": [
            {
                "maintenance_id": m.maintenance_id,
                "maintenance_type": m.maintenance_type.value,
                "status": m.status.value,
                "start_time": m.start_time.isoformat(),
                "duration_hours": m.duration_hours,
            }
            for m in maints
        ],
        "telemetry": telemetry_data.get("assessment", {}),
        "alerts": machine_alerts,
    }

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
    """Demo trigger: raise the next scripted machine fault event (visible to this viewer only)."""
    from app.prediction.alerts import simulate_fault
    return simulate_fault()


@app.post("/api/alerts/clear")
def clear_demo_faults():
    """Remove this viewer's simulated faults."""
    from app.prediction.alerts import clear_faults
    return {"removed": clear_faults()}

@app.get("/api/knowledge")
def list_knowledge_documents():
    """List all verified engineering documents and constraints for the current industry pack."""
    from app.knowledge.repository import get_knowledge_repository
    repo = get_knowledge_repository()
    return {
        "documents": [
            {
                "document_id": doc.document_id,
                "title": doc.title,
                "document_type": doc.document_type,
                "topics": doc.topics,
                "machine_ids": doc.machine_ids,
                "station_ids": doc.station_ids,
                "line_ids": doc.line_ids,
                "excerpt": doc.content[:300].strip() + ("..." if len(doc.content) > 300 else ""),
                "content": doc.content,
            }
            for doc in sorted(repo.documents.values(), key=lambda d: d.document_id)
        ]
    }

# Mount UI static directory
UI_DIR = Path(__file__).resolve().parent / "ui"
if UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")

    @app.get("/")
    def serve_index():
        return FileResponse(str(UI_DIR / "index.html"))
