"""
Factory Decision Engine - Decision Intelligence API Layer.
Provides a thin HTTP/REST boundary for the judge-facing decision UI.
Directs requests to FactoryDecisionAgent without duplicating simulation or optimization logic.
"""
import os
import logging
from typing import Dict, Any, Optional
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.agent.agent import FactoryDecisionAgent
from app.agent.schemas import DecisionResponse
from app.exceptions import UnknownMachineError, InvalidDowntimeError

logger = logging.getLogger("factory_decision_engine.api")

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

# Instantiate the central decision agent (preserves immutability of factory database)
agent = FactoryDecisionAgent()

class AnalyzeRequest(BaseModel):
    prompt: str = Field(..., description="Natural-language operational scenario or disruption prompt")

class WhatIfRequest(BaseModel):
    previous_response: DecisionResponse = Field(..., description="Previous DecisionResponse object")
    follow_up_prompt: Optional[str] = Field(None, description="What-if sensitivity question or constraint change")
    updated_downtime: Optional[float] = Field(None, description="Direct downtime parameter override")
    updated_priorities: Optional[Any] = Field(None, description="Direct priority parameter override")
    updated_machine_id: Optional[str] = Field(None, description="Direct machine ID parameter override")

@app.get("/api/health")
async def health_check():
    """Health check endpoint indicating model readiness and execution mode."""
    return {
        "status": "healthy",
        "service": "Factory Decision Engine",
        "model": agent.model_name,
        "live_gemini_available": bool(agent.api_key),
        "default_mode": "live_gemini" if agent.api_key else "deterministic_fallback",
        "human_approval_policy": "MANDATORY",
    }

@app.post("/api/analyze", response_model=DecisionResponse)
async def analyze_scenario(req: AnalyzeRequest):
    """
    Primary endpoint: Analyze a disruption scenario.
    Dispatches to FactoryDecisionAgent, maintaining numerical authority in Python.
    """
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Scenario prompt cannot be empty.")

    try:
        decision = agent.run(req.prompt.strip())
        return decision
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
async def analyze_what_if(req: WhatIfRequest):
    """
    What-if sensitivity analysis endpoint.
    Takes previous decision and follow-up prompt or parameter overrides.
    Re-runs deterministic simulation & optimization without mutating factory database.
    """
    try:
        updated_decision = agent.what_if(
            previous_response=req.previous_response,
            follow_up_prompt=req.follow_up_prompt,
            updated_downtime=req.updated_downtime,
            updated_priorities=req.updated_priorities,
            updated_machine_id=req.updated_machine_id,
        )
        return updated_decision
    except UnknownMachineError as e:
        logger.warning(f"Unknown machine in what-if: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except InvalidDowntimeError as e:
        logger.warning(f"Invalid downtime in what-if: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error during what-if analysis: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"What-if engine error: {str(e)}")

# Mount UI static directory
UI_DIR = Path(__file__).resolve().parent / "ui"
if UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")

    @app.get("/")
    async def serve_index():
        return FileResponse(str(UI_DIR / "index.html"))
