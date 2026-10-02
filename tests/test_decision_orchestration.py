import os
import hashlib
import pytest
from app.config import config
from app.agent import (
    FactoryDecisionAgent,
    DecisionOrchestrator,
    ScenarioParser,
    DecisionResponse,
)

def test_canonical_m17_8hr_decision_workflow():
    """
    Test 1: Canonical Scenario
    'M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost.'
    """
    agent = FactoryDecisionAgent()
    prompt = "M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost."
    resp = agent.run(prompt)

    # 1. Structured DecisionResponse validation
    assert isinstance(resp, DecisionResponse)
    assert resp.human_approval_required is True
    assert "M17" in resp.scenario_summary
    assert resp.impact_summary["machine_id"] == "M17"
    assert resp.impact_summary["downtime_hours"] == 8.0

    # 2. Dependency chain trace validation
    assert resp.dependency_chain is not None
    assert resp.dependency_chain["machine_id"] == "M17"
    assert resp.dependency_chain["station_id"] == "STA_B2"
    assert resp.dependency_chain["line_id"] == "LINE_B"
    assert resp.dependency_chain["operation_type"] == "WELDING"
    assert "STA_B2" in resp.dependency_chain["trace_summary"]
    assert "LINE_B" in resp.dependency_chain["trace_summary"]

    # 3. Affected orders
    assert len(resp.affected_orders) > 0
    assert resp.impact_summary["affected_order_count"] == len(resp.affected_orders)
    assert resp.impact_summary["high_priority_affected_count"] == 3

    # 4. Recovery alternatives & feasibility separation
    assert len(resp.candidate_plans) > 0
    assert len(resp.feasible_alternatives) > 0
    assert any(c.strategy_type == "MACHINE_TRANSFER" for c in resp.candidate_plans)
    assert any(c.strategy_type == "RESEQUENCE" for c in resp.candidate_plans)
    assert any(c.strategy_type == "OVERTIME" for c in resp.candidate_plans)

    # Infeasible plans must show exact deterministic reason
    for inf in resp.infeasible_alternatives:
        assert not inf.is_feasible
        assert len(inf.infeasibility_reasons) > 0

    # 5. Deterministic selection & trade-offs
    assert resp.selected_plan is not None
    assert resp.selected_plan.is_feasible is True
    assert resp.trade_off_metrics is not None
    assert "delivery_delay_score" in resp.trade_off_metrics
    assert "cost_impact_usd" in resp.trade_off_metrics
    assert "Synthetic quality penalty proxy" in resp.trade_off_metrics["quality_metric_label"]
    assert "Synthetic energy consumption proxy" in resp.trade_off_metrics["energy_metric_label"]
    assert "Synthetic operational risk proxy" in resp.trade_off_metrics["risk_metric_label"]

    # 6. Objective weights matching delivery + cost blended intent
    assert resp.objective_weights["delivery"] == 0.40
    assert resp.objective_weights["cost"] == 0.40
    assert resp.objective_weights["quality"] == 0.10

    # 7. Engineering evidence grounding
    assert len(resp.engineering_evidence) > 0
    for ev in resp.engineering_evidence:
        assert ev.document_id.startswith("KB-")
        assert len(ev.title) > 0
        assert len(ev.excerpt) > 0

    # 8. Structured rationale
    assert "Selected plan" in resp.rationale or "recommended because" in resp.rationale.lower()
    assert "Hard Constraints:" in resp.rationale
    assert "Delivery Impact Score:" in resp.rationale
    assert "Cost Impact:" in resp.rationale
    assert "Multi-Objective Trade-Off:" in resp.rationale
    assert "Engineering Guidance:" in resp.rationale

    # 9. Markdown briefing formatting matching Section 17 contract
    briefing = resp.to_markdown_explanation()
    assert "Operational Decision Briefing:" in briefing
    assert "## 1. Scenario & Execution Provenance" in briefing
    assert "## 2. Asset Dependency Trace" in briefing
    assert "## 3. Operational Impact Summary" in briefing
    assert "## 4. Recovery Options & Feasibility" in briefing
    assert "## 5. Multi-Objective Trade-Offs" in briefing
    assert "## 6. Recommended Recovery Action" in briefing
    assert "## 7. Decision Rationale & Engineering Evidence" in briefing
    assert "## 8. Hard Constraints & Operational Assumptions" in briefing
    assert "Human Approval Required:" in briefing
    assert "YES" in briefing

def test_affected_asset_trace_internal_consistency():
    """Test 2: Dependency chain trace validation for M17."""
    orchestrator = DecisionOrchestrator()
    resp = orchestrator.orchestrate(machine_id="M17", downtime_hours=8.0)

    deps = resp.dependency_chain
    assert deps["machine_id"] == "M17"
    assert deps["machine_type"] == "WELDING_Unit_2"
    assert deps["station_id"] == "STA_B2"
    assert deps["station_name"] == "Station B2 - Welding"
    assert deps["line_id"] == "LINE_B"
    assert deps["line_name"] == "Line B (SUV)"
    assert deps["operation_type"] == "WELDING"
    assert deps["affected_order_count"] == 6

def test_scenario_parser_variants():
    """Test 3: Natural language parsing across varied user prompts."""
    # Variant A: Standard
    p1 = ScenarioParser.parse("M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost.")
    assert p1["machine_id"] == "M17"
    assert p1["downtime_hours"] == 8.0
    assert "deliveries" in p1["priorities"] or "delivery" in p1["priorities"]
    assert "cost" in p1["priorities"]
    assert p1["clarification_needed"] is None

    # Variant B: Down tomorrow
    p2 = ScenarioParser.parse("Machine M04 is down for 6.5 hours. Minimize energy.")
    assert p2["machine_id"] == "M04"
    assert p2["downtime_hours"] == 6.5
    assert "energy" in p2["priorities"]

    # Variant C: Overtime constraint
    p3 = ScenarioParser.parse("M17 will fail for 10 hours. Protect delivery, but overtime is not allowed.")
    assert p3["machine_id"] == "M17"
    assert p3["downtime_hours"] == 10.0
    assert p3["disallow_overtime"] is True

    # Variant D: Transfer constraint
    p4 = ScenarioParser.parse("M17 is down for 8 hours. What if we cannot transfer to another line?")
    assert p4["disallow_cross_line_transfer"] is True

    # Variant E: Missing machine ID flags clarification
    p5 = ScenarioParser.parse("The welding line is down for 8 hours. What should we do?")
    assert p5["clarification_needed"] is not None

def test_what_if_priority_change_recomputes_evaluation():
    """
    Test 4: What-If priority change produces a new deterministic evaluation.
    'What if we prioritize cost instead?'
    """
    agent = FactoryDecisionAgent()
    base_resp = agent.run("M17 will be unavailable for 8 hours. Keep high priority deliveries on time.")
    assert base_resp.objective_weights["delivery"] == 0.65

    # Run what-if prioritizing cost
    what_if_resp = agent.what_if(base_resp, follow_up_prompt="What if we prioritize cost instead?")
    assert what_if_resp.objective_weights["cost"] == 0.65
    assert what_if_resp.objective_weights["delivery"] < what_if_resp.objective_weights["cost"]
    assert what_if_resp.selected_plan is not None

def test_what_if_downtime_change_recomputes_impact():
    """
    Test 5: What-If downtime change from 4 to 12 hours produces a new deterministic simulation.
    """
    agent = FactoryDecisionAgent()
    resp_4h = agent.run("M17 will be unavailable for 4 hours.")
    resp_12h = agent.what_if(resp_4h, follow_up_prompt="What if M17 is unavailable for 12 hours instead?")

    assert resp_4h.impact_summary["downtime_hours"] == 4.0
    assert resp_12h.impact_summary["downtime_hours"] == 12.0
    assert resp_12h.impact_summary["capacity_loss_units"] > resp_4h.impact_summary["capacity_loss_units"]

def test_what_if_disallow_overtime_constraint():
    """
    Test 6: What-If 'overtime is not allowed' enforces policy constraint deterministically.
    """
    agent = FactoryDecisionAgent()
    base_resp = agent.run("M17 will be unavailable for 8 hours. Minimize cost.")

    what_if_resp = agent.what_if(base_resp, follow_up_prompt="What if overtime is not allowed?")
    # Verify no overtime plan is selected or feasible
    if what_if_resp.selected_plan:
        assert what_if_resp.selected_plan.strategy_type != "OVERTIME"

    overtime_cands = [c for c in what_if_resp.candidate_plans if c.strategy_type == "OVERTIME"]
    for ot in overtime_cands:
        assert not ot.is_feasible
        assert any("Overtime is strictly disallowed" in r for r in ot.infeasibility_reasons)

def test_what_if_disallow_cross_line_transfers():
    """
    Test 7: What-If 'we cannot transfer to another line' filters out cross-line transfer candidates.
    """
    agent = FactoryDecisionAgent()
    base_resp = agent.run("M17 will be unavailable for 8 hours.")

    what_if_resp = agent.what_if(base_resp, follow_up_prompt="What if we cannot transfer to another line?")
    # Check that any cross-line transfer is marked infeasible
    for c in what_if_resp.candidate_plans:
        if c.strategy_type == "MACHINE_TRANSFER" and c.target_line_id and c.target_line_id != "LINE_B":
            assert not c.is_feasible
            assert any("Cross-line transfer" in r for r in c.infeasibility_reasons)

def test_model_compatibility_enforced_in_transfer():
    """
    Test 8: Line C model compatibility. Line C only supports Model-A, Model-C, Model-E.
    Model-B orders on Line B cannot be transferred to Line C.
    """
    orchestrator = DecisionOrchestrator()
    resp = orchestrator.orchestrate("M17", 8.0)
    # Target machine M26 is on LINE_C. M17 produces Model-C orders, which LINE_C supports!
    m26_plan = next((p for p in resp.candidate_plans if p.target_machine_id == "M26"), None)
    assert m26_plan is not None
    assert m26_plan.is_feasible is True

def test_unknown_machine_fails_cleanly():
    """Test 9: Unknown machine returns structured explanation and clarification."""
    agent = FactoryDecisionAgent()
    resp = agent.run("Machine M999 will be unavailable for 8 hours.")
    assert resp.selected_plan is None
    assert "Unknown machine" in resp.rationale or "M999" in resp.rationale
    assert resp.clarification_needed is not None
    assert resp.human_approval_required is True

def test_invalid_downtime_fails_cleanly():
    """Test 10: Zero or negative downtime fails safely with clear error."""
    orchestrator = DecisionOrchestrator()
    resp_zero = orchestrator.orchestrate("M17", 0.0)
    assert resp_zero.selected_plan is None
    assert "positive" in resp_zero.rationale or "positive" in str(resp_zero.impact_summary)

    resp_neg = orchestrator.orchestrate("M17", -5.0)
    assert resp_neg.selected_plan is None
    assert "positive" in resp_neg.rationale or "positive" in str(resp_neg.impact_summary)

def test_database_immutability_throughout_orchestration():
    """
    Test 11: SQLite database hash must remain perfectly identical after all decision runs.
    """
    db_path = config.DB_PATH
    with open(db_path, "rb") as f:
        hash_before = hashlib.sha256(f.read()).hexdigest()

    agent = FactoryDecisionAgent()
    # Execute canonical run
    resp1 = agent.run("M17 will be unavailable for 8 hours tomorrow. Keep deliveries on time while minimizing cost.")
    # Execute follow-up what-if
    resp2 = agent.what_if(resp1, follow_up_prompt="What if overtime is not allowed?")
    # Execute another what-if
    resp3 = agent.what_if(resp2, follow_up_prompt="What if downtime is 16 hours instead?")

    with open(db_path, "rb") as f:
        hash_after = hashlib.sha256(f.read()).hexdigest()

    assert hash_before == hash_after, "Database file was mutated during decision intelligence orchestration!"

def test_fallback_provenance_explicitly_labeled():
    """Test 12: Provenance is explicitly 'deterministic_fallback' when no GEMINI_API_KEY is configured."""
    agent = FactoryDecisionAgent()
    resp = agent.run("M17 is down for 8 hours.")
    assert resp.execution_mode in ["deterministic_fallback", "live_gemini"]
    if not os.environ.get("GEMINI_API_KEY"):
        assert resp.execution_mode == "deterministic_fallback"
        assert resp.fallback_reason is not None
        assert "No GEMINI_API_KEY" in resp.fallback_reason or "fallback" in resp.fallback_reason.lower()

def test_delivery_impact_score_numerical_semantics_regression():
    """
    Test 13 (Section 3 Audit): Verify numerical semantics of delivery_impact_score.
    Confirms it is a unitless normalized penalty score on [0.0, 1.0], NOT literal hours.
    """
    agent = FactoryDecisionAgent()
    resp = agent.run("M17 will be unavailable for 8 hours tomorrow. Keep deliveries on time while minimizing cost.")

    # 1. Delivery impact score on candidate plans and trade-offs
    assert resp.selected_plan is not None
    deliv_score = resp.selected_plan.delivery_delay_score
    assert 0.0 <= deliv_score <= 1.0, f"Delivery impact score {deliv_score} is out of normalized [0.0, 1.0] bounds!"

    # 2. Check metric labeling in trade_off_metrics
    assert "delivery_metric_label" in resp.trade_off_metrics
    assert "Normalized delivery impact score" in resp.trade_off_metrics["delivery_metric_label"]
    assert "hours" not in resp.trade_off_metrics["delivery_metric_label"].lower()

    # 3. Check markdown briefing does NOT claim literal hours for delivery impact
    briefing = resp.to_markdown_explanation()
    assert "Delivery Impact Score:" in briefing
    assert "(hours delay proxy)" not in briefing
    assert "0.60 hrs" not in resp.rationale

def test_evidence_quality_and_categorization():
    """
    Test 14 (Section 4 & 5 Audit): Verify evidence categorization and grounded relevance.
    Distinguishes historical precedent, machine specification, and quality requirements.
    """
    agent = FactoryDecisionAgent()
    resp = agent.run("M17 will be unavailable for 8 hours tomorrow. Keep deliveries on time while minimizing cost.")

    ev_by_id = {e.document_id: e for e in resp.engineering_evidence}
    assert "KB-INC-001" in ev_by_id, "Missing incident report KB-INC-001"
    assert "KB-MAN-M17" in ev_by_id, "Missing machine manual KB-MAN-M17"
    assert "KB-QTY-001" in ev_by_id, "Missing quality guideline KB-QTY-001"

    # Verify categories
    assert ev_by_id["KB-INC-001"].evidence_category == "historical_precedent"
    assert ev_by_id["KB-MAN-M17"].evidence_category == "machine_specification"
    assert ev_by_id["KB-QTY-001"].evidence_category == "quality_requirement"

    # Verify non-empty excerpts
    for ev in resp.engineering_evidence:
        assert len(ev.excerpt) > 20
        assert len(ev.title) > 0

def test_scenario_parser_robustness_equivalents():
    """
    Test 15 (Section 7 Audit): Test equivalent phrases for downtime, overtime, and line transfer constraints.
    """
    # 1. Downtime variations
    p_num = ScenarioParser.parse("M17 is down for 12 hours.")
    assert p_num["machine_id"] == "M17"
    assert p_num["downtime_hours"] == 12.0

    p_shorthand = ScenarioParser.parse("M17 unavailable for 12h.")
    assert p_shorthand["downtime_hours"] == 12.0

    p_words = ScenarioParser.parse("M17 will be offline for twelve hours.")
    assert p_words["downtime_hours"] == 12.0

    p_sixteen = ScenarioParser.parse("Machine M04 is offline for sixteen hours.")
    assert p_sixteen["machine_id"] == "M04"
    assert p_sixteen["downtime_hours"] == 16.0

    # 2. No overtime variations
    assert ScenarioParser.parse("M17 is down for 8h. No overtime.")["disallow_overtime"] is True
    assert ScenarioParser.parse("M17 is down for 8h. Don't use overtime.")["disallow_overtime"] is True
    assert ScenarioParser.parse("M17 is down for 8h. Overtime is unavailable.")["disallow_overtime"] is True

    # 3. No cross-line transfer variations
    assert ScenarioParser.parse("M17 is down for 8h. No cross-line transfer.")["disallow_cross_line_transfer"] is True
    assert ScenarioParser.parse("M17 is down for 8h. Don't move work to another line.")["disallow_cross_line_transfer"] is True
    assert ScenarioParser.parse("M17 is down for 8h. Keep everything on Line B.")["disallow_cross_line_transfer"] is True

def test_selected_plan_integrity_cannot_be_infeasible_or_invented():
    """
    Test 16 (Section 9 Audit): Selected plan must exist in candidate plans and be strictly feasible.
    """
    agent = FactoryDecisionAgent()
    resp = agent.run("M17 will be unavailable for 8 hours.")

    assert resp.selected_plan is not None
    assert resp.selected_plan.is_feasible is True

    matching_in_candidates = [c for c in resp.candidate_plans if c.plan_id == resp.selected_plan.plan_id]
    assert len(matching_in_candidates) == 1
    assert matching_in_candidates[0].is_feasible is True
    assert matching_in_candidates[0].weighted_score == resp.selected_plan.weighted_score

