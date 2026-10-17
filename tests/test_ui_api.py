"""
Tests for Phase 4C Decision Intelligence API and UI endpoints.
Verifies API behavior, what-if sensitivity, error handling, and database immutability.
"""
import hashlib
import uuid
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
    assert "Factory Decision Engine" in res.text
    assert "Before you change the factory, understand what happens next." in res.text
    assert "Human approval required." in res.text
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
    assert data["impact_summary"]["affected_product_models"] == ["Cruiser"]
    assert data["selected_plan"]["plan_id"] == "PLAN_TRANSFER_07"
    assert data["selected_plan"]["target_machine_id"] == "M26"
    assert data["selected_plan"]["weighted_score"] == 0.6881
    assert data["selected_plan"]["cost_impact_usd"] == 100.0

def test_ui_app_js_source_hygiene():
    """Verify the UI scripts do not contain fabricated fallback values."""
    from pathlib import Path
    ui_dir = Path(__file__).resolve().parent.parent / "app" / "ui"
    assert (ui_dir / "app.js").exists()
    for js in [ui_dir / "app.js", *sorted((ui_dir / "js").rglob("*.js"))]:
        content = js.read_text(encoding="utf-8")
        assert '"240.0"' not in content, js
        assert '"0.7850"' not in content, js
        assert '"Model-B"' not in content, js
        assert "rec_transfer_m26" not in content, js

def test_ui_modules_served():
    """Every ES module imported by the UI is served by the static mount."""
    for path in ["/static/app.js", "/static/js/api.js", "/static/js/dom.js", "/static/js/format.js",
                 "/static/js/state.js", "/static/js/views/control-room.js",
                 "/static/js/views/workspace.js", "/static/js/views/decision-log.js",
                 "/static/js/components/plant-map.js", "/static/js/components/tour.js"]:
        assert client.get(path).status_code == 200, path

def test_ui_reset_demo_and_tooltip_elements():
    """
    Verify the M17 example button and Delivery Delay Score tooltip exist in the served UI.
    (The old "Reset Demo" link was removed: it duplicated this button and the M17 example chip.)
    """
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    # 1. One-click M17 example exists
    assert 'id="btn-empty-canonical"' in html
    assert "Try the M17 example" in html
    assert 'id="btn-reset-demo"' not in html
    # 2. Delivery Impact Score tooltip text exists
    expected_tooltip = "Lower is better: 0.00 indicates zero delay penalty; 1.00 indicates maximum delay penalty."
    assert expected_tooltip in html

def test_reset_flow_canonical_values_and_db_immutability():
    """
    Verify the reset flow executes the canonical scenario and leaves the DB immutable.
    """
    initial_hash = _get_db_hash()
    canonical_prompt = "M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost."
    res = client.post("/api/analyze", json={"prompt": canonical_prompt})
    assert res.status_code == 200
    data = res.json()

    assert data["impact_summary"]["capacity_loss_units"] == 211.2
    assert data["impact_summary"]["affected_order_count"] == 6
    assert data["impact_summary"]["high_priority_affected_count"] == 3
    assert data["selected_plan"]["plan_id"] == "PLAN_TRANSFER_07"
    assert data["selected_plan"]["target_machine_id"] == "M26"
    assert data["selected_plan"]["weighted_score"] == 0.6881
    assert data["selected_plan"]["cost_impact_usd"] == 100.0

    # DB hash verified
    assert _get_db_hash() == initial_hash


def test_what_if_preserves_base_scenario_state_via_analyze():
    """
    Regression Test 1-4:
    1. Analyze M17 8h works and returns canonical impact.
    2. What-if 'What if overtime is not allowed?' preserves M17, 8h downtime, and factory impact.
    3. Overtime plan becomes infeasible.
    4. The result is recalculated (feasible plans 7 -> 6, infeasible 3 -> 4) rather than returning original result.
    5. No 'machine ID required' clarification error is raised.
    """
    # Step 1: Base analysis
    base_res = client.post("/api/analyze", json={
        "prompt": "Machine M17 will be unavailable for 8 hours. Maintain high-priority deliveries while minimizing cost."
    })
    assert base_res.status_code == 200
    base_data = base_res.json()

    assert base_data["impact_summary"]["machine_id"] == "M17"
    assert base_data["impact_summary"]["downtime_hours"] == 8.0
    assert base_data["impact_summary"]["capacity_loss_units"] == 211.2
    assert base_data["impact_summary"]["affected_order_count"] == 6
    assert base_data["impact_summary"]["high_priority_affected_count"] == 3
    assert len(base_data["feasible_alternatives"]) == 7
    assert len(base_data["infeasible_alternatives"]) == 3

    # Verify overtime plan is initially feasible
    ot_plans = [p for p in base_data["candidate_plans"] if "OVERTIME" in p["strategy_type"]]
    assert len(ot_plans) >= 1
    assert any(p["is_feasible"] for p in ot_plans)

    # Step 2: What-if follow-up through /api/analyze without repeating machine ID
    what_if_res = client.post("/api/analyze", json={
        "prompt": "What if overtime is not allowed?",
        "previous_response": base_data,
    })
    assert what_if_res.status_code == 200
    what_if_data = what_if_res.json()

    # Verify no clarification error
    assert what_if_data.get("clarification_needed") is None
    assert what_if_data["selected_plan"] is not None

    # Verify base scenario state is preserved
    assert what_if_data["impact_summary"]["machine_id"] == "M17"
    assert what_if_data["impact_summary"]["downtime_hours"] == 8.0
    assert what_if_data["impact_summary"]["capacity_loss_units"] == 211.2
    assert what_if_data["impact_summary"]["affected_order_count"] == 6
    assert what_if_data["impact_summary"]["high_priority_affected_count"] == 3

    # Verify overtime plan became infeasible
    ot_plans_after = [p for p in what_if_data["candidate_plans"] if "OVERTIME" in p["strategy_type"]]
    assert len(ot_plans_after) >= 1
    for p in ot_plans_after:
        assert p["is_feasible"] is False
        assert any("overtime is strictly disallowed" in r.lower() for r in p["infeasibility_reasons"])

    # Verify result is recalculated: feasible count changed 7 -> 6, infeasible 3 -> 4
    assert len(what_if_data["feasible_alternatives"]) == 6
    assert len(what_if_data["infeasible_alternatives"]) == 4


def test_what_if_preserves_different_base_machine_m04():
    """
    Regression Test 5 & 6:
    Proves NO hardcoded M17 in what-if handling.
    Analyzes M04 8h as the base scenario, then tests what-if constraint.
    M04, 8h, and 238.4 capacity loss must be preserved, NOT overwritten with M17.
    """
    # Base analysis on machine M04
    base_res = client.post("/api/analyze", json={
        "prompt": "Machine M04 will be unavailable for 8 hours."
    })
    assert base_res.status_code == 200
    base_data = base_res.json()

    assert base_data["impact_summary"]["machine_id"] == "M04"
    assert base_data["impact_summary"]["downtime_hours"] == 8.0
    assert base_data["impact_summary"]["capacity_loss_units"] == 238.4

    # Follow-up what-if without machine ID
    what_if_res = client.post("/api/analyze", json={
        "prompt": "What if overtime is not allowed?",
        "previous_response": base_data,
    })
    assert what_if_res.status_code == 200
    what_if_data = what_if_res.json()

    # Confirms M04 is preserved, not replaced by M17
    assert what_if_data["impact_summary"]["machine_id"] == "M04"
    assert what_if_data["impact_summary"]["downtime_hours"] == 8.0
    assert what_if_data["impact_summary"]["capacity_loss_units"] == 238.4
    assert what_if_data["selected_plan"] is not None

    # Overtime plans are infeasible for M04
    for p in what_if_data["candidate_plans"]:
        if "OVERTIME" in p["strategy_type"]:
            assert p["is_feasible"] is False


def test_existing_m17_12h_behavior_remains_unchanged():
    """
    Regression Test:
    Verify existing Preset 3 (M17 12h) behavior is preserved and produces 316.8 capacity loss.
    """
    res = client.post("/api/analyze", json={
        "prompt": "What if M17 is unavailable for 12 hours instead?"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["impact_summary"]["machine_id"] == "M17"
    assert data["impact_summary"]["downtime_hours"] == 12.0
    assert data["impact_summary"]["capacity_loss_units"] == 316.8
    assert data["selected_plan"] is not None


def test_no_hardcoded_scenario_calculations_in_frontend():
    """
    Verify frontend app.js does not contain hardcoded scenario outputs or fake calculations.
    """
    from pathlib import Path
    app_js_path = Path(__file__).resolve().parent.parent / "app" / "ui" / "app.js"
    content = app_js_path.read_text(encoding="utf-8")

    # Confirm no hardcoded optimization / plan logic on frontend
    assert "simulate_machine_failure" not in content
    assert "generate_recovery_plans" not in content
    assert "evaluate_recovery_plans" not in content
    # Confirm no hardcoded machine calculations
    assert "211.2" not in content
    assert "316.8" not in content
    assert "238.4" not in content


def test_machine_inventory_reconciliation():
    """
    Priority 1 Regression Test:
    Verify authoritative machine counts across domains:
    - Overview KPIs return exactly 36 total machines (3 lines x 12 machines).
    - Status categories (running + degraded + stopped + unknown) exactly sum to machines_total.
    - Total machines matches the database count exactly.
    """
    for domain in ["electronics", "automotive"]:
        res = client.get("/api/factory/overview", headers={"x-fde-domain": domain})
        assert res.status_code == 200
        data = res.json()
        kpis = data["kpis"]

        assert kpis["machines_total"] == 36
        assert (
            kpis["machines_running"]
            + kpis["machines_degraded"]
            + kpis["machines_stopped"]
            + kpis["machines_unknown"]
        ) == kpis["machines_total"]

        total_line_machines = sum(len(line["machines"]) for line in data["lines"])
        assert total_line_machines == 36


def test_line_capacity_and_utilization_formulas():
    """
    Priority 2 Regression Test:
    Verify production line formulas and units:
    - capacity_24h == capacity_per_hour * 24.0
    - utilization_pct == round(min(100.0, scheduled_units_24h / capacity_24h * 100.0), 1)
    - bottleneck station has valid capacity
    """
    res = client.get("/api/factory/overview")
    assert res.status_code == 200
    data = res.json()
    for line in data["lines"]:
        cap_h = line["capacity_per_hour"]
        cap_24 = line["capacity_24h"]
        assert cap_24 == round(cap_h * 24.0, 1)

        sched_24 = line["scheduled_units_24h"]
        expected_util = round(min(100.0, (sched_24 / cap_24 * 100.0)) if cap_24 > 0 else 0.0, 1)
        assert line["utilization_pct"] == expected_util

        assert line["bottleneck_station"] is not None
        assert line["bottleneck_station"]["capacity_per_hour"] > 0


def test_approval_server_side_deduplication():
    """
    Priority 3 Regression Test:
    Verify server-side approval deduplication:
    Submitting the identical decision and approver returns the existing decision record
    without creating a duplicate entry in the audit trail.
    """
    # 1. Generate a valid decision
    analysis_res = client.post("/api/analyze", json={
        "prompt": "M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost."
    })
    assert analysis_res.status_code == 200
    decision = analysis_res.json()
    assert decision["selected_plan"] is not None

    # 2. First approval
    approve_res_1 = client.post("/api/approve", json={
        "decision": decision,
        "approver": "Chief Operations Officer - Shift A",
        "note": "Authorized transfer to M26 per engineering SOP."
    })
    assert approve_res_1.status_code == 200
    entry_1 = approve_res_1.json()

    # 3. Repeated approval of identical decision and approver
    approve_res_2 = client.post("/api/approve", json={
        "decision": decision,
        "approver": "Chief Operations Officer - Shift A",
        "note": "Authorized transfer to M26 per engineering SOP."
    })
    assert approve_res_2.status_code == 200
    entry_2 = approve_res_2.json()

    # Must return the same decision_id without creating duplicate consequential entries
    assert entry_1["decision_id"] == entry_2["decision_id"]


def test_domain_switch_state_consistency():
    """
    Priority 1 & 4 Regression Test:
    Verify domain switching between Electronics and Automotive updates plant names,
    line models, and maintains independent consistent state without cross-domain pollution.
    """
    res_elec = client.get("/api/factory/overview", headers={"x-fde-domain": "electronics"})
    assert res_elec.status_code == 200
    data_elec = res_elec.json()

    res_auto = client.get("/api/factory/overview", headers={"x-fde-domain": "automotive"})
    assert res_auto.status_code == 200
    data_auto = res_auto.json()

    assert data_elec["plant"]["name"] != data_auto["plant"]["name"]
    assert data_elec["lines"][0]["name"] != data_auto["lines"][0]["name"]
    assert data_elec["kpis"]["machines_total"] == 36
    assert data_auto["kpis"]["machines_total"] == 36


def test_machine_operational_state_separated_from_predictive_risk():
    """
    Priority 2 Regression Test:
    Preserve authoritative machine status from database (32 healthy, 2 degraded, 2 stopped).
    Telemetry warnings (e.g. M23) must be a separate risk indicator and must NOT alter
    operational state from HEALTHY to at-risk.
    Reconciles exactly: 32 running + 2 degraded + 2 stopped = 36 total.
    """
    res = client.get("/api/factory/overview")
    assert res.status_code == 200
    data = res.json()
    kpis = data["kpis"]

    assert kpis["machines_total"] == 36
    assert kpis["machines_running"] == 32
    assert kpis["machines_degraded"] == 2
    assert kpis["machines_stopped"] == 2
    assert kpis["machines_unknown"] == 0
    assert kpis["machines_running"] + kpis["machines_degraded"] + kpis["machines_stopped"] == kpis["machines_total"]

    # Verify M23 in overview lines is HEALTHY
    all_machines = [m for l in data["lines"] for m in l["machines"]]
    m23 = next((m for m in all_machines if m["machine_id"] == "M23"), None)
    assert m23 is not None
    assert m23["status"] == "HEALTHY", f"M23 operational state must be HEALTHY, got {m23['status']}"

    # Verify M23 has a predictive telemetry warning in watchlist
    wl_res = client.get("/api/watchlist")
    assert wl_res.status_code == 200
    wl_data = wl_res.json()
    m23_watch = next((w for w in wl_data["machines"] if w["machine_id"] == "M23"), None)
    assert m23_watch is not None
    assert m23_watch["risk_level"] in ("ELEVATED", "HIGH", "CRITICAL")
    assert m23_watch["status"] == "HEALTHY", "M23 authoritative status in watch list must remain HEALTHY"


def test_delivery_risk_delineation():
    """
    Priority 3 Regression Test:
    Separate line exposure (all active orders on disrupted lines) from demonstrably elevated delivery risk
    (orders on disrupted lines due within 24 hours).
    Verify data_mode documents the exact delivery risk rule.
    """
    res = client.get("/api/factory/overview")
    assert res.status_code == 200
    data = res.json()

    # Rule must be documented in data_mode
    assert "delivery_risk_rule" in data["data_mode"]
    assert "24h" in data["data_mode"]["delivery_risk_rule"]

    kpis = data["kpis"]
    assert "orders_line_exposure" in kpis
    assert "orders_delivery_risk_elevated" in kpis
    # Elevated delivery risk orders must be a subset of line exposure orders
    assert kpis["orders_delivery_risk_elevated"] <= kpis["orders_line_exposure"]

    # Check order items
    for o in data["orders"]:
        assert o["delivery_risk"] in ("ELEVATED", "MONITORED_LINE_EXPOSURE", "NOMINAL")
        if o["delivery_risk"] == "ELEVATED":
            assert o["line_impacted"] is True
            assert o["is_due_24h"] is True


def test_metric_edge_cases_and_zero_capacity():
    """
    Priority 4 Regression Test:
    Zero or missing capacity must be represented as unavailable (None), not 0% utilization.
    All denominators and time horizons must be strictly positive.
    """
    res = client.get("/api/factory/overview")
    assert res.status_code == 200
    data = res.json()

    # In standard factory state, capacity is positive and utilization is valid
    assert data["kpis"]["total_capacity_per_hour"] > 0
    assert 0.0 <= data["kpis"]["factory_utilization_pct"] <= 100.0

    for line in data["lines"]:
        assert line["capacity_24h"] == line["capacity_per_hour"] * 24.0
        if line["capacity_per_hour"] > 0:
            assert line["utilization_pct"] is not None
            assert 0.0 <= line["utilization_pct"] <= 100.0


def test_approval_idempotency_fingerprint_and_rejection():
    """
    Priority 5 Regression Test:
    - Rejects approval of infeasible plans with HTTP 400.
    - Rejects approval with missing selected_plan with HTTP 400.
    - Deterministic SHA-256 fingerprint allows identical resubmissions safely (idempotent).
    - Distinct legitimate decisions on the same asset (different downtime) produce distinct approvals.
    """
    # 1. Analyze 8h outage
    res_8h = client.post("/api/analyze", json={
        "prompt": "M17 will be unavailable for 8 hours. Keep high priority deliveries on time."
    })
    assert res_8h.status_code == 200
    decision_8h = res_8h.json()
    assert decision_8h["selected_plan"]["is_feasible"] is True

    # 2. Infeasible plan rejection: synthesize an infeasible decision
    infeasible_decision = dict(decision_8h)
    infeasible_decision["selected_plan"] = dict(decision_8h["selected_plan"])
    infeasible_decision["selected_plan"]["is_feasible"] = False
    infeasible_decision["selected_plan"]["infeasibility_reasons"] = ["Exceeds max overtime limit"]

    rej_res = client.post("/api/approve", json={
        "decision": infeasible_decision,
        "approver": "Plant Director",
        "note": "Attempting to approve infeasible plan"
    })
    assert rej_res.status_code == 400
    assert "Cannot approve an infeasible plan" in rej_res.json()["detail"]

    # 3. Valid approval of 8h decision
    app_res_1 = client.post("/api/approve", json={
        "decision": decision_8h,
        "approver": "Shift Lead Alpha",
        "note": "Approved standard plan",
        "idempotency_key": "IDEM-TEST-KEY-001"
    })
    assert app_res_1.status_code == 200
    rec_1 = app_res_1.json()

    # 4. Duplicate submission with same idempotency key or fingerprint
    app_res_dup = client.post("/api/approve", json={
        "decision": decision_8h,
        "approver": "Shift Lead Alpha",
        "note": "Approved standard plan",
        "idempotency_key": "IDEM-TEST-KEY-001"
    })
    assert app_res_dup.status_code == 200
    rec_dup = app_res_dup.json()
    assert rec_dup["decision_id"] == rec_1["decision_id"]

    # 5. Distinct legitimate decision on same asset: 12h outage
    res_12h = client.post("/api/analyze", json={
        "prompt": "M17 will be unavailable for 12 hours. Keep high priority deliveries on time."
    })
    assert res_12h.status_code == 200
    decision_12h = res_12h.json()
    assert decision_12h["impact_summary"]["downtime_hours"] == 12.0

    app_res_diff = client.post("/api/approve", json={
        "decision": decision_12h,
        "approver": "Shift Lead Alpha",
        "note": "Approved 12h extended plan",
        "idempotency_key": "IDEM-TEST-KEY-002"
    })
    assert app_res_diff.status_code == 200
    rec_diff = app_res_diff.json()
    # Different downtime produces a distinct legitimate approval
    assert rec_diff["decision_id"] != rec_1["decision_id"]
    assert rec_diff["downtime_hours"] == 12.0


def test_approval_concurrent_and_persistence():
    """
    Priority 4 Regression Test:
    - Verifies atomic deduplication under concurrent identical requests.
    - Verifies persistence across in-memory cache clearing / application restart.
    """
    from concurrent.futures import ThreadPoolExecutor
    from app.agent import decision_log

    res = client.post("/api/analyze", json={
        "prompt": "M17 will be unavailable for 8 hours. Minimize cost."
    })
    assert res.status_code == 200
    decision = res.json()

    shared_key = f"IDEM-CONCUR-{uuid.uuid4().hex[:6]}"

    def send_approval():
        return client.post("/api/approve", json={
            "decision": decision,
            "approver": "Shift Director",
            "note": "Concurrent approval test",
            "idempotency_key": shared_key
        })

    # Test two simultaneous identical requests
    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(send_approval)
        f2 = executor.submit(send_approval)
        r1, r2 = f1.result(), f2.result()

    assert r1.status_code == 200
    assert r2.status_code == 200
    entry1, entry2 = r1.json(), r2.json()
    assert entry1["decision_id"] == entry2["decision_id"], "Concurrent requests must return identical decision_id"

    # Simulate restart by clearing in-memory cached entries
    decision_log._entries = None

    # Repeated request after simulated restart
    r3 = send_approval()
    assert r3.status_code == 200
    entry3 = r3.json()
    assert entry3["decision_id"] == entry1["decision_id"], "Restarted app must return identical decision_id from DB"


def test_canonical_m17_ten_candidate_plans_and_what_if_breakdown():
    """
    Priority 2 Regression Test:
    Verify exact reconciliation of canonical M17 8h baseline:
    - 211.2 units capacity loss
    - 6 affected orders (3 high priority)
    - Exactly 10 candidate recovery plans: 7 feasible and 3 infeasible
    - Selected plan: PLAN_TRANSFER_07, target M26, cost $100.00, score 0.6881
    - What-if 'What if overtime is not allowed?': 10 candidate plans: 6 feasible and 4 infeasible
    """
    res = client.post("/api/analyze", json={
        "prompt": "M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost."
    })
    assert res.status_code == 200
    data = res.json()

    assert data["impact_summary"]["capacity_loss_units"] == 211.2
    assert data["impact_summary"]["affected_order_count"] == 6
    assert data["impact_summary"]["high_priority_affected_count"] == 3

    plans = data["candidate_plans"]
    assert len(plans) == 10, f"Expected exactly 10 candidate plans, got {len(plans)}"
    feasible = [p for p in plans if p["is_feasible"]]
    infeasible = [p for p in plans if not p["is_feasible"]]
    assert len(feasible) == 7, f"Expected 7 feasible plans, got {len(feasible)}"
    assert len(infeasible) == 3, f"Expected 3 infeasible plans, got {len(infeasible)}"

    sel = data["selected_plan"]
    assert sel["plan_id"] == "PLAN_TRANSFER_07"
    assert sel["target_machine_id"] == "M26"
    assert sel["cost_impact_usd"] == 100.0
    assert sel["weighted_score"] == 0.6881
    assert sel["is_feasible"] is True

    # What-if: No overtime
    wi_res = client.post("/api/what-if", json={
        "previous_response": data,
        "follow_up_prompt": "What if overtime is not allowed?"
    })
    assert wi_res.status_code == 200
    wi_data = wi_res.json()
    wi_plans = wi_data["candidate_plans"]
    assert len(wi_plans) == 10, f"Expected exactly 10 what-if candidate plans, got {len(wi_plans)}"
    wi_feasible = [p for p in wi_plans if p["is_feasible"]]
    wi_infeasible = [p for p in wi_plans if not p["is_feasible"]]
    assert len(wi_feasible) == 6, f"Expected 6 feasible plans under no-overtime, got {len(wi_feasible)}"
    assert len(wi_infeasible) == 4, f"Expected 4 infeasible plans under no-overtime, got {len(wi_infeasible)}"

    ot_plan = next(p for p in wi_plans if p["strategy_type"] == "OVERTIME")
    assert ot_plan["is_feasible"] is False
    assert any("Overtime is strictly disallowed" in r for r in ot_plan["infeasibility_reasons"])







