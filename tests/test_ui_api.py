"""
Tests for Phase 4C Decision Intelligence API and UI endpoints.
Verifies API behavior, what-if sensitivity, error handling, and database immutability.
"""
import hashlib
import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.config import DB_PATH
from app.agent.schemas import DecisionResponse

client = TestClient(app)

def _get_db_hash() -> str:
    """Compute SHA256 hash of the factory SQLite database."""
    with open(DB_PATH, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

def test_api_health():
    """Verify GET /api/health endpoint reports service status."""
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["service"] == "Factory Decision Engine"
    assert data["human_approval_policy"] == "MANDATORY"
    assert "default_mode" in data

def test_api_analyze_canonical_m17_scenario():
    """
    Verify POST /api/analyze handles the canonical M17 8-hour prompt.
    Checks:
    - Dependency chain present and dynamic
    - Impact summary with numerical semantics
    - Feasible and infeasible alternatives separated
    - Dominant recommendation present
    - Grounded engineering evidence attached
    - Human approval required = True
    """
    initial_hash = _get_db_hash()
    prompt = "M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost."
    res = client.post("/api/analyze", json={"prompt": prompt})
    assert res.status_code == 200
    data = res.json()

    # Parse into DecisionResponse model
    decision = DecisionResponse(**data)
    assert decision.human_approval_required is True
    assert decision.impact_summary["machine_id"] == "M17"
    assert decision.impact_summary["downtime_hours"] == 8.0
    assert decision.impact_summary["capacity_loss_units"] > 0

    # Dependency Chain
    dc = decision.dependency_chain
    assert dc["machine_id"] == "M17"
    assert dc["station_id"] == "STA_B2"
    assert dc["line_id"] == "LINE_B"

    # Selected Plan
    assert decision.selected_plan is not None
    assert decision.selected_plan.is_feasible is True
    assert decision.selected_plan.target_machine_id == "M26" or "transfer" in decision.selected_plan.strategy_type.lower()

    # Feasible vs. Infeasible
    assert len(decision.feasible_alternatives) > 0
    assert len(decision.infeasible_alternatives) > 0
    for inf in decision.infeasible_alternatives:
        assert inf.is_feasible is False
        assert len(inf.infeasibility_reasons) > 0

    # Grounded Evidence
    assert len(decision.engineering_evidence) > 0
    for ev in decision.engineering_evidence:
        assert ev.document_id is not None
        assert ev.evidence_category is not None

    # DB Immutability
    assert _get_db_hash() == initial_hash

def test_api_analyze_empty_prompt_returns_400():
    """Verify empty prompt is rejected with 400 Bad Request."""
    res = client.post("/api/analyze", json={"prompt": "   "})
    assert res.status_code == 400
    assert "empty" in res.json()["detail"].lower()

def test_api_analyze_unknown_machine_fails_cleanly():
    """Verify unknown machine returns clean structured response without crashing."""
    res = client.post("/api/analyze", json={"prompt": "Machine M999 will be unavailable for 8 hours."})
    assert res.status_code == 200
    data = res.json()
    assert data["selected_plan"] is None
    assert data["clarification_needed"] is not None
    assert "M999" in data["rationale"] or "Unknown machine" in data["rationale"]

def test_api_what_if_disallow_overtime():
    """
    Verify POST /api/what-if enforces 'overtime is not allowed' constraint.
    """
    initial_hash = _get_db_hash()
    base_res = client.post("/api/analyze", json={
        "prompt": "M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost."
    })
    assert base_res.status_code == 200
    base_data = base_res.json()

    # Post what-if
    what_if_res = client.post("/api/what-if", json={
        "previous_response": base_data,
        "follow_up_prompt": "What if overtime is not allowed?",
    })
    assert what_if_res.status_code == 200
    what_if_data = what_if_res.json()

    # Overtime plans must be marked infeasible
    for p in what_if_data["candidate_plans"]:
        if p["strategy_type"] == "OVERTIME":
            assert p["is_feasible"] is False
            assert any("Overtime is strictly disallowed" in r for r in p["infeasibility_reasons"])

    assert _get_db_hash() == initial_hash

def test_api_what_if_downtime_extension():
    """
    Verify POST /api/what-if extends downtime from 8h to 12h and recomputes impact.
    """
    base_res = client.post("/api/analyze", json={
        "prompt": "M17 will be unavailable for 8 hours."
    })
    base_data = base_res.json()

    what_if_res = client.post("/api/what-if", json={
        "previous_response": base_data,
        "follow_up_prompt": "What if M17 is unavailable for 12 hours instead?",
    })
    assert what_if_res.status_code == 200
    what_if_data = what_if_res.json()

    assert what_if_data["impact_summary"]["downtime_hours"] == 12.0
    assert what_if_data["impact_summary"]["capacity_loss_units"] > base_data["impact_summary"]["capacity_loss_units"]

def test_api_what_if_priority_shift():
    """
    Verify POST /api/what-if shifts optimizer weights when user asks 'What if cost is the priority?'.
    """
    base_res = client.post("/api/analyze", json={
        "prompt": "M17 will be unavailable for 8 hours. Keep high priority deliveries on time."
    })
    base_data = base_res.json()
    assert base_data["objective_weights"]["delivery"] == 0.65

    what_if_res = client.post("/api/what-if", json={
        "previous_response": base_data,
        "follow_up_prompt": "What if cost is the priority?",
    })
    assert what_if_res.status_code == 200
    what_if_data = what_if_res.json()

    assert what_if_data["objective_weights"]["cost"] == 0.65
    assert what_if_data["objective_weights"]["delivery"] < what_if_data["objective_weights"]["cost"]

def test_ui_index_html_served():
    """Verify GET / serves index.html with neutral placeholders."""
    res = client.get("/")
    assert res.status_code == 200
    assert "FACTORY DECISION ENGINE" in res.text
    assert "Before you change the factory, understand what happens next." in res.text
    assert "HUMAN APPROVAL REQUIRED: YES" in res.text
    assert "PLAN_--" in res.text
    assert "Waiting for analysis..." in res.text
    assert "rec_transfer_m26" not in res.text
    assert "240.0" not in res.text
    assert "0.7850" not in res.text
    assert "Model-B" not in res.text

def test_api_canonical_scenario_exact_authoritative_values():
    """
    Verify POST /api/analyze returns exact authoritative ground truth from Phase 4B:
    - capacity_loss_units: 211.2
    - capacity_loss_percentage: 17.6
    - affected_order_count: 6
    - high_priority_affected_count: 3
    - selected_plan_id: PLAN_TRANSFER_07
    - target_machine_id: M26
    - weighted_score: 0.6881
    - cost: $100.00
    """
    res = client.post("/api/analyze", json={
        "prompt": "M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost."
    })
    assert res.status_code == 200
    data = res.json()
    assert data["impact_summary"]["machine_id"] == "M17"
    assert data["impact_summary"]["capacity_loss_units"] == 211.2
    assert data["impact_summary"]["capacity_loss_percentage"] == 17.6
    assert data["impact_summary"]["affected_order_count"] == 6
    assert data["impact_summary"]["high_priority_affected_count"] == 3
    assert data["impact_summary"]["affected_product_models"] == ["Model-C"]
    assert data["selected_plan"]["plan_id"] == "PLAN_TRANSFER_07"
    assert data["selected_plan"]["target_machine_id"] == "M26"
    assert data["selected_plan"]["weighted_score"] == 0.6881
    assert data["selected_plan"]["cost_impact_usd"] == 100.0

def test_ui_app_js_source_hygiene():
    """Verify app.js does not contain fabricated fallback values."""
    from pathlib import Path
    app_js_path = Path(__file__).resolve().parent.parent / "app" / "ui" / "app.js"
    assert app_js_path.exists()
    content = app_js_path.read_text(encoding="utf-8")
    assert '"240.0"' not in content
    assert '"0.7850"' not in content
    assert '"Model-B"' not in content
    assert "rec_transfer_m26" not in content

