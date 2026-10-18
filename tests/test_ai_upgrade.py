"""
Tests for the AI upgrade: scenario understanding, robustness, war-room grounding,
early-warning prediction, alerts, and the new API endpoints. Gemini is always mocked.
"""
import re

import pytest
from fastapi.testclient import TestClient

from app.agent.agent import FactoryDecisionAgent
from app.agent.orchestrator import DecisionOrchestrator
from app.agent.understanding import ScenarioUnderstanding, understand_scenario
from app.agent.specialists import _consensus_line, find_ungrounded_numbers
from app.simulation.robustness import analyze_robustness
from app.prediction.health import _hours_to_threshold, get_watch_list
from app.prediction import alerts as alerts_mod

CANONICAL = "M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost."


class FakeLLM:
    """Stands in for GeminiService; returns a fixed structured result."""

    def __init__(self, result):
        self.result = result
        self.available = True
        self.calls = 0

    def generate_json(self, prompt, schema, system=None, temperature=0.0):
        self.calls += 1
        return self.result


# ---------------------------------------------------------------- understanding

def test_understanding_falls_back_to_rules_without_llm():
    res = understand_scenario(CANONICAL, None)
    assert res["machine_id"] == "M17"
    assert res["downtime_hours"] == 8.0
    assert res["interpretation"]["source"] == "rules"


def test_understanding_uses_gemini_and_computes_range_midpoint():
    llm = FakeLLM(ScenarioUnderstanding(
        intent="new_scenario", machine_id="M17", downtime_min_hours=6, downtime_max_hours=10,
        priority_focus=["cost"], disallow_overtime=True,
        interpretation_summary="M17 down 6-10 h, keep cost low, no overtime.",
    ))
    res = understand_scenario("Unit 2 welder at B2 down 6 to 10 hours, cheap, no overtime", llm)
    assert res["interpretation"]["source"] == "gemini"
    assert res["machine_id"] == "M17"
    assert res["downtime_range"] == [6.0, 10.0]
    assert res["downtime_hours"] == 8.0
    assert res["priorities"] == "cost"
    assert res["disallow_overtime"] is True
    assert res["clarification_needed"] is None


def test_understanding_rejects_invented_machine():
    llm = FakeLLM(ScenarioUnderstanding(
        intent="new_scenario", machine_id="M99", downtime_hours=3,
        interpretation_summary="M99 down 3 h.",
    ))
    res = understand_scenario("M99 broke for 3 hours", llm)
    assert res["machine_id"] is None
    assert "not in the plant registry" in res["clarification_needed"]


def test_understanding_ambiguous_machine_lists_candidates():
    llm = FakeLLM(ScenarioUnderstanding(
        intent="new_scenario", candidate_machine_ids=["M16", "M17", "M18"], downtime_hours=5,
        interpretation_summary="A Line B welder is jammed.",
    ))
    res = understand_scenario("the welding robot on line B jammed", llm)
    assert res["machine_id"] is None
    assert res["interpretation"]["candidate_machine_ids"] == ["M16", "M17", "M18"]
    assert "M16" in res["clarification_needed"]


# ---------------------------------------------------------------- robustness

def test_robustness_profile_for_canonical_scenario():
    orch = DecisionOrchestrator()
    base = orch.orchestrate("M17", 8.0, "delivery cost")
    rb = analyze_robustness(orch, "M17", 8.0, "delivery cost", base.applied_constraints,
                            base.selected_plan.plan_id, downtime_range=[6, 10])
    assert rb["selected_plan_id"] == "PLAN_TRANSFER_07"
    assert rb["holds_for_stated_range"] is True
    assert rb["segments"][0]["from_hours"] <= 1
    assert any(s["is_recommended"] for s in rb["segments"])
    # Very short outages favour overtime over a transfer
    short = next(p for p in rb["points"] if p["downtime_hours"] == 1)
    assert short["best_strategy"] == "OVERTIME"


def test_what_if_preserves_previous_constraints():
    orch = DecisionOrchestrator()
    base = orch.orchestrate("M17", 8.0, "delivery cost", constraints={"disallow_overtime": True})
    follow = orch.what_if(base, updated_downtime=10.0)
    assert follow.applied_constraints["disallow_overtime"] is True
    ot = next(p for p in follow.candidate_plans if p.strategy_type == "OVERTIME")
    assert ot.is_feasible is False


# ---------------------------------------------------------------- grounding

def test_grounding_ignores_identifiers_and_flags_invented_numbers():
    context = '{"cost": 100.0, "energy_kwh": 528.0, "doc": "KB-QTY-001", "plan": "PLAN_TRANSFER_07"}'
    texts = ["Transfer to M26 costs $100 and uses 528 kWh per KB-QTY-001 (PLAN_TRANSFER_07).",
             "It will also save 4,250 dollars."]
    assert find_ungrounded_numbers(texts, context) == ["4250"]


def test_consensus_line_counts_stances():
    line = _consensus_line([{"stance": "support"}, {"stance": "caution"}, {"stance": "caution"}, {"stance": "oppose"}])
    assert line == "4 specialists reviewed: 1 support, 2 support with conditions, 1 oppose."


# ---------------------------------------------------------------- prediction & alerts

def test_hours_to_threshold_on_linear_series():
    series = [2.0 + 0.1 * i for i in range(30)]  # +0.1 / h, ends at 4.9
    res = _hours_to_threshold(series, 7.1, 1)
    assert res["slope_per_hour"] == pytest.approx(0.1, abs=1e-6)
    assert res["hours_to_threshold"] == pytest.approx(22.0, abs=0.2)
    assert _hours_to_threshold([50.0] * 30, 7.1, 1)["hours_to_threshold"] == 0.0
    assert _hours_to_threshold([3.0] * 30, 7.1, 1)["hours_to_threshold"] is None
    assert res["model"] == "linear"


def test_accelerating_wear_uses_curve_and_is_sooner_than_a_line():
    import numpy as np
    series = list(2.0 + 0.004 * np.arange(30) ** 2)  # wear speeding up
    res = _hours_to_threshold(series, 7.1, 1)
    window = np.arange(24)
    line_slope = np.polyfit(window, series[-24:], 1)[0]
    line_eta = (7.1 - np.polyval(np.polyfit(window, series[-24:], 1), 23)) / line_slope
    assert res["model"] == "quadratic"
    assert res["hours_to_threshold"] < line_eta


def test_noisy_series_gets_a_range_around_the_estimate():
    import random
    rng = random.Random(1)
    series = [2.0 + 0.1 * i + rng.gauss(0, 0.1) for i in range(30)]
    res = _hours_to_threshold(series, 7.1, 1)
    lo, hi = res["hours_range"]
    assert lo < res["hours_to_threshold"] < hi


def test_watch_list_surfaces_hidden_risk_first():
    wl = get_watch_list()
    top = wl[0]
    assert top["machine_id"] == "M23"
    assert top["status"] == "HEALTHY"
    assert top["risk_level"] in ("HIGH", "CRITICAL")
    assert 5 < top["hours_to_alarm"] < 36
    lo, hi = top["hours_to_alarm_range"]
    assert lo <= top["hours_to_alarm"] <= hi
    assert top["prebuilt_plan"]["plan_id"] is not None
    assert all(a["risk_level"] != "NORMAL" for a in wl)


def test_simulated_faults_cycle_and_carry_analysis_prompt():
    alerts_mod.reset_alerts()
    first = alerts_mod.simulate_fault()
    second = alerts_mod.simulate_fault()
    assert first["machine_id"] == "M17" and second["machine_id"] != "M17"
    assert "6 to 10 hours" in first["analysis_prompt"]
    third = alerts_mod.simulate_fault()
    fourth = alerts_mod.simulate_fault()
    # Worst first (HIGH before MEDIUM), newest first within the same severity
    listed = [a["alert_id"] for a in alerts_mod.list_alerts([])]
    assert [first["severity"], second["severity"], third["severity"], fourth["severity"]] == ["HIGH", "MEDIUM", "HIGH", "MEDIUM"]
    assert listed[:4] == [third["alert_id"], first["alert_id"], fourth["alert_id"], second["alert_id"]]
    alerts_mod.reset_alerts()


def test_demo_faults_never_break_another_alerts_backup_machine():
    """The demo must not show a machine as broken while another alert sends work to it."""
    from app.domains import PACKS, use_domain

    for domain in PACKS:
        with use_domain(domain):
            alerts_mod.reset_alerts()
            faults = [alerts_mod.simulate_fault() for _ in PACKS[domain]["faults"]]
            faulted = {f["machine_id"] for f in faults}
            for a in alerts_mod.list_alerts(get_watch_list()):
                target = re.search(r"alternative machine (M\d+)", a.get("recommended_action") or "")
                assert not (target and target.group(1) in faulted), f"{domain}: {a['machine_id']}'s backup {target.group(1)} is a demo fault"
            alerts_mod.reset_alerts()


# ---------------------------------------------------------------- agent routing

def test_question_intent_answers_without_rerunning(monkeypatch):
    agent = FactoryDecisionAgent(api_key=None)
    base = agent.run(CANONICAL)
    import app.agent.agent as agent_mod
    monkeypatch.setattr(agent_mod, "understand_scenario", lambda *a, **k: {
        "intent": "question", "interpretation": {"source": "gemini"}, "machine_id": None,
    })
    res = agent.run("why not overtime?", previous_response=base)
    assert res.selected_plan.plan_id == base.selected_plan.plan_id
    assert res.assistant_message and "overtime" in res.assistant_message.lower()


def test_live_pipeline_attaches_war_room_and_interpretation(monkeypatch):
    import app.agent.agent as agent_mod
    from app.ai.llm import GeminiService

    agent = FactoryDecisionAgent(api_key="mock-key")
    monkeypatch.setattr(GeminiService, "available", property(lambda self: True))
    monkeypatch.setattr(agent_mod, "understand_scenario", lambda *a, **k: {
        "intent": "new_scenario", "machine_id": "M17", "downtime_hours": 8.0, "downtime_range": [6.0, 10.0],
        "priorities": "delivery cost", "disallow_overtime": False, "disallow_cross_line_transfer": False,
        "disallow_all_transfers": False, "clarification_needed": None,
        "interpretation": {"source": "gemini", "summary": "M17 down 6-10 h"},
    })
    monkeypatch.setattr(agent.runner, "run", lambda *a, **k: iter(()))
    assessments = [{"agent": "quality", "title": "Quality", "stance": "caution", "headline": "FAI first",
                    "assessment": "Needs FAI.", "key_concern": "FAI", "citations": ["KB-QTY-001"]}]
    brief = {"headline": "Move work to M26", "summary": "s", "why": [], "watch_outs": [], "next_steps": [],
             "specialist_consensus": "1 specialists reviewed: 1 support with conditions."}
    monkeypatch.setattr(agent_mod, "run_war_room_resilient", lambda d, r: (assessments, brief))

    res = agent.run(CANONICAL)
    assert res.execution_mode == "live_gemini"
    assert res.ai_components == {"understanding": "gemini", "war_room": "gemini"}
    assert res.specialist_assessments == assessments
    assert res.decision_brief["headline"] == "Move work to M26"
    assert res.robustness["downtime_range"] == [6.0, 10.0]
    assert res.selected_plan.plan_id == "PLAN_TRANSFER_07"
    # Complete live results are cached: a fresh repeat costs no model calls
    monkeypatch.setattr(agent_mod, "understand_scenario", lambda *a, **k: pytest.fail("cache miss"))
    agent.reset_state()
    again = agent.run(CANONICAL)
    assert again.selected_plan.plan_id == res.selected_plan.plan_id


# ---------------------------------------------------------------- API

@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.agent import decision_log
    monkeypatch.setattr(decision_log, "LOG_PATH", tmp_path / "decision_log.json")
    monkeypatch.setattr(decision_log, "_entries", None)
    monkeypatch.delenv("FDE_LOG_BUCKET", raising=False)
    from app.api import app
    return TestClient(app)


def test_api_overview_and_watchlist(client):
    ov = client.get("/api/factory/overview").json()
    assert ov["kpis"]["machines_total"] == 36
    assert len(ov["lines"]) == 3
    wl = client.get("/api/watchlist").json()["machines"]
    assert wl[0]["machine_id"] == "M23"


def test_api_alerts_simulate_and_list(client):
    alerts_mod.reset_alerts()
    created = client.post("/api/alerts/simulate").json()
    listed = client.get("/api/alerts").json()["alerts"]
    assert listed[0]["alert_id"] == created["alert_id"]
    assert any(a["kind"] == "predictive" and a["machine_id"] == "M23" for a in listed)
    alerts_mod.reset_alerts()


def test_api_rerank_ask_approve_and_log(client):
    decision = client.post("/api/analyze", json={"prompt": CANONICAL}).json()

    reranked = client.post("/api/rerank", json={"decision": decision, "priorities": "cost"}).json()
    assert reranked["objective_weights"]["cost"] > reranked["objective_weights"]["delivery"]
    assert reranked["robustness"] is not None
    assert reranked["ai_components"]["war_room"] == "pending"

    answer = client.post("/api/ask", json={"question": "Why not overtime?", "decision": decision}).json()
    assert answer["source"] == "rules" and answer["answer"]

    entry = client.post("/api/approve", json={"decision": decision, "approver": "Test Manager"}).json()
    assert entry["decision_id"].startswith("DEC-")
    assert set(entry["documents"]) == {"supervisor_work_order", "quality_notice", "revised_schedule_summary", "customer_message"}
    log = client.get("/api/decisions").json()["decisions"]
    assert log[0]["decision_id"] == entry["decision_id"]


def test_api_review_skipped_without_llm(client):
    decision = client.post("/api/analyze", json={"prompt": CANONICAL}).json()
    reviewed = client.post("/api/review", json={"decision": decision}).json()
    assert reviewed["ai_components"]["war_room"] == "skipped"


def test_api_approve_rejects_decision_without_plan(client):
    decision = client.post("/api/analyze", json={"prompt": "something broke"}).json()
    res = client.post("/api/approve", json={"decision": decision, "approver": "X"})
    assert res.status_code == 400


# ---------------------------------------------------------------- batch 1: honesty fixes

def test_robustness_summary_reports_every_range():
    """#14: a plan that wins, loses, then wins again must be described as separate ranges."""
    orch = DecisionOrchestrator()
    base = orch.orchestrate("M17", 8.0, "balanced all objectives")
    rb = analyze_robustness(orch, "M17", 8.0, "balanced all objectives", base.applied_constraints,
                            base.selected_plan.plan_id)
    rec_segments = [s for s in rb["segments"] if s["is_recommended"]]
    assert len(rb["best_ranges"]) == len(rec_segments)
    if len(rb["best_ranges"]) > 1:
        # Each range is named, so the summary can't claim one continuous span
        for lo, hi in rb["best_ranges"]:
            assert (f"{lo:g} h" in rb["summary"]) or (f"{lo:g}–{hi:g} h" in rb["summary"])
    alt = [s for s in rb["segments"] if not s["is_recommended"] and s["plan_id"]]
    if alt:
        assert "is better" in rb["summary"]


def test_war_room_objections_and_dependencies(monkeypatch):
    """#13: objections come from stances (not wording); dependency links are validated."""
    import app.agent.specialists as sp

    def fake_engine(model, context_json, evidence):
        view = lambda stance, concern: {"stance": stance, "headline": f"{stance} view", "assessment": "ok",
                                         "key_concern": concern, "citations": []}
        return {
            "production_view": view("support", None),
            "quality_view": view("caution", "First-part inspection required."),
            "maintenance_view": view("oppose", "M26 is due for service; do not load it."),
            "energy_view": view("support", None),
            "decision_brief": {
                "headline": "Review M26 before moving work", "summary": "Maintenance objects.",
                "why": ["a"], "watch_outs": ["b"], "specialist_consensus": "x", "next_steps": ["c"],
                "dependencies": [
                    {"from_agent": "quality", "to_agent": "production", "note": "Inspection slows the first run."},
                    {"from_agent": "energy", "to_agent": "energy", "note": "self-link must be dropped"},
                ],
                "conflicts": ["Maintenance opposes while Production supports."],
            },
        }

    monkeypatch.setattr(sp, "_run_parallel", fake_engine)
    decision = DecisionOrchestrator().orchestrate("M17", 8.0, "delivery cost")
    assessments, brief = sp.run_war_room(decision, None, "test-model")

    assert brief["objections"] == [{"agent": "maintenance", "title": "Maintenance",
                                    "reason": "M26 is due for service; do not load it."}]
    assert [(d["from_agent"], d["to_agent"]) for d in brief["dependencies"]] == [("quality", "production")]
    assert brief["conflicts"] == ["Maintenance opposes while Production supports."]
    assert brief["specialist_consensus"] == "4 specialists reviewed: 2 support, 1 support with conditions, 1 oppose."


def test_war_room_context_includes_runner_up_plans():
    from app.agent.specialists import build_decision_context

    decision = DecisionOrchestrator().orchestrate("M17", 8.0, "delivery cost")
    ctx = build_decision_context(decision)
    runners = ctx["runner_up_plans"]
    assert len(runners) == 2
    assert all(r["plan_id"] != decision.selected_plan.plan_id for r in runners)
    others = sorted((p for p in decision.feasible_alternatives if p.plan_id != decision.selected_plan.plan_id),
                    key=lambda p: -(p.weighted_score or 0))
    assert [r["plan_id"] for r in runners] == [p.plan_id for p in others[:2]]


def test_alert_analysis_uses_the_full_pipeline_not_the_direct_path(client):
    """An alert sends its report plus machine/downtime; the report must win, so Gemini, robustness
    and the specialists run (offline here, so the rule-based parser stands in for Gemini)."""
    body = {"prompt": "M17 will be down for 8 hours. Protect high-priority deliveries.",
            "machine_id": "M17", "downtime_hours": 8, "incident_id": "ALT-P-M17"}
    d = client.post("/api/analyze", json=body).json()
    assert d["scenario_interpretation"] is not None
    assert d["robustness"] is not None
    assert d["scenario_summary"].startswith("[ALT-P-M17]")
    direct = client.post("/api/analyze", json={"machine_id": "M17", "downtime_hours": 8}).json()
    assert direct["scenario_interpretation"] is None


def test_alert_numbers_come_from_the_engine():
    """The numbers on an alert match the engine's analysis of the same outage."""
    alerts_mod.reset_alerts()
    fault = alerts_mod.simulate_fault()  # first scripted fault: M17, 6-10 h repair
    assert fault["machine_id"] == "M17"
    assert (fault["affected_orders_count"], fault["high_priority_orders_count"], fault["loss_estimate_units"]) == (6, 3, 211.2)
    assert "M26" in fault["recommended_action"]
    alerts_mod.reset_alerts()

    wl = get_watch_list()
    pred = alerts_mod.predictive_alerts(wl)[0]
    plan = wl[0]["prebuilt_plan"]
    assert pred["machine_id"] == "M23"
    assert pred["affected_orders_count"] == plan["orders_at_risk"]
    assert pred["high_priority_orders_count"] == plan["high_priority_at_risk"]
    assert pred["loss_estimate_units"] == plan["capacity_loss_units"]
    assert pred["hours_to_alarm"] == wl[0]["hours_to_alarm"]


def test_repeated_fault_on_same_machine_is_listed_once():
    alerts_mod.reset_alerts()
    for _ in range(9):  # more presses than there are scripted faults
        alerts_mod.simulate_fault()
    faults = [a for a in alerts_mod.list_alerts([]) if a["kind"] == "fault"]
    machines = [a["machine_id"] for a in faults]
    assert len(machines) == len(set(machines))
    alerts_mod.reset_alerts()


def test_demo_faults_belong_to_the_viewer_who_raised_them(client):
    alerts_mod.reset_alerts()
    a, b = {"X-FDE-Viewer": "judge-a"}, {"X-FDE-Viewer": "judge-b"}
    fault = client.post("/api/alerts/simulate", headers=a).json()
    kinds = lambda h: [(x["kind"], x["machine_id"]) for x in client.get("/api/alerts", headers=h).json()["alerts"]]
    assert ("fault", fault["machine_id"]) in kinds(a)
    assert all(k == "predictive" for k, _ in kinds(b))  # judge B sees only the shared prediction
    # judge B's first demo fault is the same first scripted fault, independent of judge A
    assert client.post("/api/alerts/simulate", headers=b).json()["machine_id"] == fault["machine_id"]
    alerts_mod.reset_alerts()


def test_clear_demo_faults_removes_only_this_viewers_faults(client):
    alerts_mod.reset_alerts()
    a, b = {"X-FDE-Viewer": "judge-a"}, {"X-FDE-Viewer": "judge-b"}
    client.post("/api/alerts/simulate", headers=a)
    client.post("/api/alerts/simulate", headers=b)
    assert client.post("/api/alerts/clear", headers=a).json()["removed"] == 1
    assert not [x for x in client.get("/api/alerts", headers=a).json()["alerts"] if x["kind"] == "fault"]
    assert [x for x in client.get("/api/alerts", headers=b).json()["alerts"] if x["kind"] == "fault"]
    alerts_mod.reset_alerts()


def test_approving_the_plan_for_a_fault_resolves_it(client):
    alerts_mod.reset_alerts()
    h = {"X-FDE-Viewer": "judge-a"}
    fault = client.post("/api/alerts/simulate", headers=h).json()  # M17, 6-10 h
    decision = client.post("/api/analyze", headers=h, json={"prompt": fault["analysis_prompt"], "incident_id": fault["alert_id"]}).json()
    entry = client.post("/api/approve", headers=h, json={"decision": decision, "approver": "Test", "incident_id": fault["alert_id"]}).json()
    assert entry["resolved_incident"]["alert_id"] == fault["alert_id"]
    assert not [x for x in client.get("/api/alerts", headers=h).json()["alerts"] if x["kind"] == "fault"]
    # a prediction is never removed by approving a plan for it
    pred = next(x for x in client.get("/api/alerts", headers=h).json()["alerts"] if x["kind"] == "predictive")
    d2 = client.post("/api/analyze", headers=h, json={"prompt": pred["analysis_prompt"], "incident_id": pred["alert_id"]}).json()
    e2 = client.post("/api/approve", headers=h, json={"decision": d2, "approver": "Test", "incident_id": pred["alert_id"]}).json()
    assert "resolved_incident" not in e2
    assert any(x["kind"] == "predictive" for x in client.get("/api/alerts", headers=h).json()["alerts"])
    alerts_mod.reset_alerts()


def test_trend_graph_shows_the_signal_that_drives_the_warning():
    """M03 is flagged by its falling health score, so its graph must be the health score, not vibration."""
    by_id = {a["machine_id"]: a for a in get_watch_list()}
    m03, m23 = by_id["M03"], by_id["M23"]
    assert m03["driver_signal"] == m03["sparkline_signal"] == "Health score"
    assert m03["sparkline"][-1] < m03["sparkline"][0] and m03["sparkline_threshold"] < min(m03["sparkline"])
    assert m23["sparkline_signal"] == "Vibration" and m23["sparkline_threshold"] > max(m23["sparkline"])


def test_cause_borrowed_from_an_old_incident_is_flagged():
    """A specialist may not present a past incident's cause as the cause of today's breakdown."""
    from app.agent.specialists import find_unsupported_causes

    facts = "Unscheduled downtime on M17 for 8.0 hours. M17 will be down for 8 hours; capacity loss 211.2 units."
    borrowed = "M17 had 8.0 hours of unscheduled downtime due to a placement head vacuum fault."
    assert find_unsupported_causes([borrowed], facts) == ["due to a placement head vacuum fault"]
    # "triggered by" in a sentence that is not about the breakdown is not a cause claim
    assert find_unsupported_causes(["Extra cost is triggered by keeping the work on M23 with overtime."], facts) == []
    # A clearly labelled precedent is allowed
    precedent = "A past precedent on M17 involved an 8.0-hour downtime due to a placement head vacuum fault."
    assert find_unsupported_causes([precedent], facts) == []
    # Generic wording about the known situation passes
    assert find_unsupported_causes(["Orders slip because of the capacity loss on M17."], facts) == []
    # A cause the manager actually reported may be repeated
    assert find_unsupported_causes([borrowed], facts + " Reported: placement head vacuum fault on M17.") == []


def test_specialists_see_the_reported_problem():
    from app.agent.specialists import build_decision_context

    decision = DecisionOrchestrator().orchestrate(machine_id="M17", downtime_hours=8.0, priorities="delivery cost")
    assert "cause is not stated" in build_decision_context(decision)["reported_problem"]
    decision.scenario_interpretation = {"summary": "M17 placement head lost vacuum; down about 8 hours."}
    assert build_decision_context(decision)["reported_problem"].startswith("M17 placement head lost vacuum")


def test_grounding_reads_numbers_next_to_dashes_in_documents():
    """A document says "15–20%"; an agent quoting "15 to 20 percent" is grounded."""
    import json

    evidence = {"maintenance": [{"id": "KB-MNT-002", "excerpt": "Speed Derating: Reduce cycle rate by 15–20%."}]}
    text = "Derate speed by 15 to 20 percent."
    assert find_ungrounded_numbers([text], json.dumps(evidence, ensure_ascii=False)) == []


def test_ai_reads_readable_machine_and_line_names():
    """Raw codes like PICK_AND_PLACE_Unit_2 or LINE_C leaked into the AI's text; it now reads plain names."""
    import json

    from app.agent.specialists import build_decision_context
    from app.agent.understanding import build_machine_catalog
    from app.domains import machine_label, use_domain

    assert machine_label("PICK_AND_PLACE_Unit_2") in ("Pick-and-place Unit 2", "Welding Unit 2")
    with use_domain("electronics"):
        m17 = next(m for m in build_machine_catalog() if m["id"] == "M17")
        assert m17["type"] == "Pick-and-place Unit 2" and m17["line"] == "Line B (Smart speakers)"
        decision = DecisionOrchestrator().orchestrate(machine_id="M17", downtime_hours=8.0, priorities="delivery cost")
        text = json.dumps(build_decision_context(decision))
    assert "PICK_AND_PLACE_Unit" not in text and '"LINE_C"' not in text
    assert "Line C (Thermostats)" in text


def test_specialists_are_told_what_the_engine_verified():
    """'Line C (Thermostats)' made a specialist object that speakers can't be built there; they now get the facts."""
    from app.agent.specialists import build_decision_context
    from app.domains import use_domain

    with use_domain("electronics"):
        decision = DecisionOrchestrator().orchestrate(machine_id="M17", downtime_hours=8.0, priorities="delivery cost")
        ctx = build_decision_context(decision)
    assert decision.selected_plan.target_machine_id == "M26"
    assert "NW-Speaker" in ctx["recommended_plan"]["target_line_builds"]
    assert "Line C (Thermostats) is qualified to build NW-Speaker" in ctx["engine_verified"]
