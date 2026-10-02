import os
import json
import pytest
from app.data import get_connection
from app.agent import (
    FactoryDecisionAgent,
    DecisionResponse,
    EvidenceItem,
    CandidatePlanSummary,
    ALL_AGENT_TOOLS,
    get_factory_state,
    get_machine_status,
    simulate_machine_failure,
    generate_recovery_plans,
    evaluate_recovery_plans,
    search_engineering_knowledge,
    map_user_priorities_to_weights,
    AGENT_SYSTEM_INSTRUCTION,
)

def test_agent_package_imports():
    assert FactoryDecisionAgent is not None
    assert DecisionResponse is not None
    assert EvidenceItem is not None
    assert CandidatePlanSummary is not None
    assert len(ALL_AGENT_TOOLS) == 6
    assert "Apex Automotive Plant 1" in AGENT_SYSTEM_INSTRUCTION

def test_tool_functions_call_existing_deterministic_engines():
    # 1. get_factory_state
    fs = get_factory_state()
    assert fs["success"] is True
    assert fs["total_machines"] > 0

    # 2. get_machine_status
    ms = get_machine_status("M17")
    assert ms["success"] is True
    assert ms["machine_id"] == "M17"

    # 3. simulate_machine_failure
    sim = simulate_machine_failure("M17", 8.0)
    assert sim["success"] is True
    assert sim["machine_id"] == "M17"
    assert sim["capacity_loss_units"] > 0

    # 4. generate_recovery_plans
    rec = generate_recovery_plans("M17", 8.0)
    assert rec["success"] is True
    assert rec["total_plans_generated"] > 0

    # 5. evaluate_recovery_plans
    ev = evaluate_recovery_plans("M17", 8.0, weights="minimize cost")
    assert ev["success"] is True
    assert ev["selected_plan"] is not None

    # 6. search_engineering_knowledge
    kb = search_engineering_knowledge(query="quality inspection machine transfer", limit=3)
    assert kb["success"] is True
    assert kb["count"] > 0

def test_tool_outputs_are_json_serializable():
    tools_outputs = [
        get_factory_state(),
        get_machine_status("M17"),
        simulate_machine_failure("M17", 8.0),
        generate_recovery_plans("M17", 8.0),
        evaluate_recovery_plans("M17", 8.0),
        search_engineering_knowledge(query="welding manual", limit=2),
    ]
    for output in tools_outputs:
        serialized = json.dumps(output)
        assert isinstance(serialized, str)
        assert len(serialized) > 0

def test_machine_failure_scenario_representation():
    agent = FactoryDecisionAgent()
    resp = agent.orchestrate_scenario(
        machine_id="M17",
        downtime_hours=8.0,
        priorities="protect high-priority deliveries while minimizing cost",
    )
    assert isinstance(resp, DecisionResponse)
    assert "M17" in resp.scenario_summary
    assert resp.impact_summary["machine_id"] == "M17"
    assert resp.impact_summary["downtime_hours"] == 8.0
    assert resp.selected_plan is not None
    assert len(resp.candidate_plans) > 0
    assert resp.human_approval_required is True

def test_objective_priorities_map_correctly():
    # 1. Delivery only
    w_deliv = map_user_priorities_to_weights("protect high-priority deliveries on time").normalized_weights()
    assert w_deliv["delivery"] == 0.65
    assert w_deliv["delivery"] > w_deliv["cost"]

    # 2. Cost only
    w_cost = map_user_priorities_to_weights("minimize cost and stay within budget").normalized_weights()
    assert w_cost["cost"] == 0.65
    assert w_cost["cost"] > w_cost["delivery"]

    # 3. Delivery + Cost blended
    w_deliv_cost = map_user_priorities_to_weights("keep high priority deliveries on time while minimizing cost").normalized_weights()
    assert w_deliv_cost["delivery"] == 0.40
    assert w_deliv_cost["cost"] == 0.40
    assert w_deliv_cost["delivery"] > w_deliv_cost["quality"]
    assert w_deliv_cost["cost"] > w_deliv_cost["quality"]

    # 4. Delivery + Energy blended
    w_deliv_energy = map_user_priorities_to_weights("protect customer deliveries on time and minimize green energy").normalized_weights()
    assert w_deliv_energy["delivery"] == 0.40
    assert w_deliv_energy["energy"] == 0.40
    assert w_deliv_energy["delivery"] > w_deliv_energy["cost"]

    # 5. Quality + Delivery blended
    w_qual_deliv = map_user_priorities_to_weights("ensure strict quality tolerance and protect on-time delivery").normalized_weights()
    assert w_qual_deliv["quality"] == 0.40
    assert w_qual_deliv["delivery"] == 0.40
    assert w_qual_deliv["quality"] > w_qual_deliv["cost"]

    # 6. All objectives balanced
    w_all = map_user_priorities_to_weights("balance all objectives equally across production").normalized_weights()
    assert w_all["delivery"] == 0.20
    assert w_all["cost"] == 0.20
    assert w_all["quality"] == 0.20
    assert w_all["energy"] == 0.20
    assert w_all["risk"] == 0.20

    # 7. Explicit dictionary
    w_dict = map_user_priorities_to_weights({"delivery": 0.5, "cost": 0.5, "quality": 0.0, "energy": 0.0, "risk": 0.0}).normalized_weights()
    assert w_dict["delivery"] == 0.5
    assert w_dict["cost"] == 0.5

def test_knowledge_evidence_preserved_in_decision():
    agent = FactoryDecisionAgent()
    resp = agent.run("M17 will be unavailable for 8 hours. Minimize cost.")
    assert len(resp.engineering_evidence) > 0
    doc_ids = [e.document_id for e in resp.engineering_evidence]
    assert any("KB-MAN-M17" in did or "KB-INC-001" in did or "KB-QTY-001" in did for did in doc_ids)

    # Check evidence formatting
    for ev in resp.engineering_evidence:
        assert ev.document_id.startswith("KB-")
        assert len(ev.title) > 0
        assert len(ev.excerpt) > 0

    # Rationale includes traceable citation or grounding
    assert any(ev.document_id in resp.rationale for ev in resp.engineering_evidence) or "According to [" in resp.rationale

def test_missing_evidence_handled_explicitly():
    agent = FactoryDecisionAgent()
    # Artificial scenario with empty evidence
    resp = agent.orchestrate_scenario("M01", 1.0)
    # Filter out evidence to test fallback message
    resp.engineering_evidence = []
    briefing = resp.to_markdown_explanation()
    assert "No specific engineering document retrieved" in briefing

def test_unknown_machine_handled_cleanly():
    agent = FactoryDecisionAgent()
    resp = agent.orchestrate_scenario("M999", 8.0)
    assert resp.selected_plan is None
    assert "Unknown machine" in resp.rationale or "Unknown machine" in str(resp.impact_summary)
    assert resp.human_approval_required is True

def test_no_feasible_recovery_handled_cleanly(monkeypatch):
    agent = FactoryDecisionAgent()
    monkeypatch.setattr(
        "app.agent.agent.evaluate_recovery_plans",
        lambda *args, **kwargs: {
            "success": True,
            "has_feasible_plan": False,
            "selected_plan": None,
            "applied_weights": {"delivery": 0.5, "cost": 0.5},
            "candidates": [
                {
                    "plan_id": "PLAN_OVERTIME_01",
                    "strategy_type": "OVERTIME",
                    "is_feasible": False,
                    "infeasibility_reasons": ["Overtime exceeds maximum allowed threshold of 12.0 hours."],
                }
            ],
        },
    )
    resp = agent.orchestrate_scenario("M17", 8.0)
    assert resp.selected_plan is None
    assert "None of the candidate recovery plans met feasibility criteria" in resp.rationale
    assert resp.human_approval_required is True

def test_what_if_sensitivity_analysis():
    agent = FactoryDecisionAgent()
    resp1 = agent.run("M17 will be unavailable for 4 hours. Protect deliveries.")
    resp2 = agent.what_if(resp1, updated_downtime=12.0)

    assert resp1.impact_summary["downtime_hours"] == 4.0
    assert resp2.impact_summary["downtime_hours"] == 12.0
    assert resp2.impact_summary["capacity_loss_units"] > resp1.impact_summary["capacity_loss_units"]

def test_database_remains_unmutated_after_agent_run():
    import hashlib
    from app.config import config

    hash_before = hashlib.sha256(open(config.DB_PATH, "rb").read()).hexdigest()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, health_score FROM machines WHERE machine_id = 'M17'")
    row_before = cursor.fetchone()
    status_before, health_before = row_before["status"], row_before["health_score"]
    conn.close()

    agent = FactoryDecisionAgent()
    resp = agent.run("M17 will be unavailable for 8 hours. Keep high priority deliveries on time while minimizing cost.")

    # Verify execution mode is fallback when no API key is set
    assert resp.execution_mode == "deterministic_fallback"
    assert resp.human_approval_required is True
    assert resp.selected_plan is not None

    conn2 = get_connection()
    cursor2 = conn2.cursor()
    cursor2.execute("SELECT status, health_score FROM machines WHERE machine_id = 'M17'")
    row_after = cursor2.fetchone()
    assert row_after["status"] == status_before
    assert row_after["health_score"] == health_before
    conn2.close()

    hash_after = hashlib.sha256(open(config.DB_PATH, "rb").read()).hexdigest()
    assert hash_after == hash_before

def test_fallback_mode_provenance_when_no_api_key(monkeypatch):
    """Verify FactoryDecisionAgent with no API key produces clear deterministic fallback provenance."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    agent = FactoryDecisionAgent(api_key=None)
    resp = agent.run("M17 will be unavailable for 8 hours. Keep high priority deliveries on time while minimizing cost.")

    assert resp.execution_mode == "deterministic_fallback"
    assert resp.fallback_reason is not None
    assert "No GEMINI_API_KEY configured" in resp.fallback_reason
    assert resp.selected_plan is not None
    assert len(resp.candidate_plans) > 0
    assert len(resp.engineering_evidence) > 0
    assert resp.human_approval_required is True

def test_fallback_when_live_adk_runner_fails(monkeypatch):
    """Verify FactoryDecisionAgent falls back safely with documented reason if live runner execution fails."""
    agent = FactoryDecisionAgent(api_key="mock-key-for-failure-test")

    def failing_run(*args, **kwargs):
        raise RuntimeError("Quota exceeded (429 RESOURCE_EXHAUSTED)")

    monkeypatch.setattr(agent.runner, "run", failing_run)

    resp = agent.run("M17 will be unavailable for 8 hours. Minimize cost.")
    assert resp.execution_mode == "deterministic_fallback"
    assert resp.fallback_reason is not None
    assert "Live Gemini ADK execution failed" in resp.fallback_reason
    assert "Quota exceeded" in resp.fallback_reason
    assert resp.selected_plan is not None
    assert resp.human_approval_required is True

def test_offline_mock_adk_tool_loop(monkeypatch):
    """
    Demonstrate offline Gemini/ADK tool loop:
    Gemini tool call -> Python tool -> tool result -> final response
    without requiring a real API key.
    """
    from google.adk.events import Event
    from google.genai import types

    agent = FactoryDecisionAgent(api_key="mock-test-key")

    def mock_adk_event_generator(*args, **kwargs):
        # 1. Gemini requests simulate_machine_failure
        yield Event(message=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(
                name="simulate_machine_failure",
                args={"machine_id": "M17", "downtime_hours": 8.0},
            ))],
        ))
        # 2. ADK receives Python tool response
        sim_data = simulate_machine_failure("M17", 8.0)
        yield Event(content=types.Content(
            role="user",
            parts=[types.Part(function_response=types.FunctionResponse(
                name="simulate_machine_failure",
                response={"result": sim_data},
            ))],
        ))
        # 3. Gemini requests evaluate_recovery_plans
        yield Event(message=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(
                name="evaluate_recovery_plans",
                args={"machine_id": "M17", "downtime_hours": 8.0, "weights": "keep high priority deliveries on time while minimizing cost"},
            ))],
        ))
        # 4. ADK receives Python tool response
        eval_data = evaluate_recovery_plans("M17", 8.0, weights="keep high priority deliveries on time while minimizing cost")
        yield Event(content=types.Content(
            role="user",
            parts=[types.Part(function_response=types.FunctionResponse(
                name="evaluate_recovery_plans",
                response={"result": eval_data},
            ))],
        ))
        # 5. Gemini requests search_engineering_knowledge
        yield Event(message=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(
                name="search_engineering_knowledge",
                args={"machine_id": "M17", "topic": "quality"},
            ))],
        ))
        # 6. ADK receives Python tool response
        kb_data = search_engineering_knowledge(machine_id="M17", topic="quality")
        yield Event(content=types.Content(
            role="user",
            parts=[types.Part(function_response=types.FunctionResponse(
                name="search_engineering_knowledge",
                response={"result": kb_data},
            ))],
        ))
        # 7. Gemini produces final response text based on the tool results
        yield Event(message=types.Content(
            role="model",
            parts=[types.Part(text="Based on deterministic simulation and evaluation, transferring M17 workload to M26 satisfies customer due dates while adhering to quality standards.")],
        ))

    monkeypatch.setattr(agent.runner, "run", mock_adk_event_generator)

    resp = agent.run("M17 will be unavailable for 8 hours. Keep high priority deliveries on time while minimizing cost.")

    # Verify execution provenance
    assert resp.execution_mode == "live_gemini"
    assert resp.fallback_reason is None

    # Verify deterministic numerical authority preserved
    assert resp.selected_plan is not None
    assert resp.selected_plan.weighted_score is not None
    assert resp.selected_plan.cost_impact_usd is not None
    assert resp.selected_plan.delivery_delay_score is not None

    # Verify impact and candidate plans
    assert resp.impact_summary["machine_id"] == "M17"
    assert resp.impact_summary["downtime_hours"] == 8.0
    assert len(resp.candidate_plans) > 0

    # Verify engineering evidence retrieved and preserved
    assert len(resp.engineering_evidence) > 0
    assert any("KB-" in e.document_id for e in resp.engineering_evidence)

    # Verify model rationale was captured
    assert "transferring M17 workload" in resp.rationale
    assert resp.human_approval_required is True

@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("GEMINI_API_KEY"), reason="Requires live GEMINI_API_KEY credential")
def test_live_gemini_tool_loop():
    """
    Live integration test against Google Gemini API and Google ADK Runner.
    Verifies that the live agent starts, invokes at least one Python tool, receives tool results,
    and returns a structured DecisionResponse.
    """
    agent = FactoryDecisionAgent()
    resp = agent.run("M17 will be unavailable for 8 hours. Keep high priority deliveries on time while minimizing cost.")

    assert resp.execution_mode == "live_gemini"
    assert resp.selected_plan is not None
    assert isinstance(resp, DecisionResponse)
    assert resp.impact_summary["machine_id"] == "M17"
    assert resp.impact_summary["downtime_hours"] == 8.0
    assert resp.human_approval_required is True
    assert len(resp.candidate_plans) > 0
    assert len(resp.rationale) > 0


def test_conversational_what_if_via_agent_run():
    """
    Verify agent.run() handles follow-up what-if queries without machine ID
    by automatically applying them to the active base scenario.
    """
    agent = FactoryDecisionAgent()

    # Step 1: Run base scenario
    r1 = agent.run("Machine M17 will be unavailable for 8 hours. Maintain high-priority deliveries while minimizing cost.")
    assert r1.impact_summary["machine_id"] == "M17"
    assert r1.impact_summary["downtime_hours"] == 8.0
    assert r1.impact_summary["capacity_loss_units"] == 211.2
    assert len(r1.feasible_alternatives) == 7

    # Step 2: Follow-up what-if without machine ID
    r2 = agent.run("What if overtime is not allowed?")
    assert r2.clarification_needed is None
    assert r2.impact_summary["machine_id"] == "M17"
    assert r2.impact_summary["downtime_hours"] == 8.0
    assert r2.impact_summary["capacity_loss_units"] == 211.2
    assert len(r2.feasible_alternatives) == 6
    assert len(r2.infeasible_alternatives) == 4

    # Overtime plan is infeasible
    ot = next(p for p in r2.candidate_plans if "OVERTIME" in p.strategy_type)
    assert ot.is_feasible is False

    # Step 3: When reset, running what-if without base scenario returns clarification
    agent.reset_state()
    r3 = agent.run("What if overtime is not allowed?")
    assert r3.clarification_needed is not None
    assert r3.selected_plan is None


