"""
Industry packs: the same engine on two plants. Both packs must produce identical numbers,
use their own vocabulary and documents, and stay isolated per viewer.
"""
import pytest
from fastapi.testclient import TestClient

from app.domains import current_domain, use_domain
from app.domains.packs import PACKS
from app.agent.orchestrator import DecisionOrchestrator
from app.knowledge.repository import get_knowledge_repository, search_engineering_knowledge
from app.prediction import alerts as alerts_mod
from app.prediction.health import get_watch_list

EXPECTED_NAMES = {
    "electronics": {"model": "NW-Speaker", "machine_type": "PICK_AND_PLACE_Unit_2", "line": "Line B (Smart speakers)"},
    "automotive": {"model": "Cruiser", "machine_type": "WELDING_Unit_2", "line": "Line B (SUV)"},
}


@pytest.mark.parametrize("domain", list(PACKS))
def test_canonical_scenario_identical_numbers_in_every_pack(domain):
    with use_domain(domain):
        resp = DecisionOrchestrator().orchestrate("M17", 8.0, "delivery cost")
    imp = resp.impact_summary
    assert imp["capacity_loss_units"] == 211.2
    assert imp["capacity_loss_percentage"] == 17.6
    assert imp["affected_order_count"] == 6
    assert imp["high_priority_affected_count"] == 3
    assert resp.selected_plan.plan_id == "PLAN_TRANSFER_07"
    assert resp.selected_plan.target_machine_id == "M26"
    assert resp.selected_plan.cost_impact_usd == 100.0
    assert resp.selected_plan.weighted_score == 0.6881
    # ...but in the pack's own vocabulary
    names = EXPECTED_NAMES[domain]
    assert imp["affected_product_models"] == [names["model"]]
    assert resp.dependency_chain["machine_type"] == names["machine_type"]
    assert resp.dependency_chain["line_name"] == names["line"]


def test_automotive_pack_is_sanitized():
    pack = PACKS["automotive"]
    assert "Stuttgart" not in pack["plant"]["location"]
    assert not any(m.startswith("Model-") for m in pack["models"])
    for doc in _docs("automotive").values():
        assert "Model-" not in doc.content


def _docs(domain):
    with use_domain(domain):
        return get_knowledge_repository().documents


def test_each_pack_has_its_own_documents_with_matching_ids():
    electronics, automotive = _docs("electronics"), _docs("automotive")
    assert len(electronics) == len(automotive) == 18
    assert set(electronics) == set(automotive)
    assert "pick-and-place" in electronics["KB-MAN-M17"].content.lower()
    assert "welding" in automotive["KB-MAN-M17"].content.lower()
    with use_domain("electronics"):
        hits = search_engineering_knowledge(query="quality inspection machine transfer first article", topic="quality")
    assert any(h.document_id == "KB-QTY-001" for h in hits)


def test_watch_list_identical_across_packs():
    lists = {}
    for domain in PACKS:
        with use_domain(domain):
            lists[domain] = [(a["machine_id"], a["risk_level"], a["hours_to_alarm"]) for a in get_watch_list(with_plans=False)]
    assert lists["electronics"] == lists["automotive"]
    assert lists["electronics"][0][0] == "M23"


def test_simulated_faults_are_per_pack():
    alerts_mod.reset_alerts()
    with use_domain("electronics"):
        alert = alerts_mod.simulate_fault()
        assert "vacuum" in alert["title"].lower()
    with use_domain("automotive"):
        assert alerts_mod.list_alerts([]) == []
    alerts_mod.reset_alerts()


def test_war_room_thread_runs_in_the_viewers_pack(monkeypatch):
    """The specialist review runs on a worker thread; it must still see the request's industry."""
    import app.agent.agent as agent_mod
    from app.agent.agent import FactoryDecisionAgent
    from app.ai.llm import GeminiService

    seen = {}

    def fake_war_room(decision, robustness):
        seen["domain"] = current_domain()
        return [], None

    monkeypatch.setattr(GeminiService, "available", property(lambda self: True))
    monkeypatch.setattr(agent_mod, "run_war_room_resilient", fake_war_room)
    monkeypatch.setattr(agent_mod, "understand_scenario", lambda *a, **k: {
        "intent": "new_scenario", "machine_id": "M17", "downtime_hours": 8.0, "downtime_range": None,
        "priorities": "delivery cost", "disallow_overtime": False, "disallow_cross_line_transfer": False,
        "disallow_all_transfers": False, "clarification_needed": None, "interpretation": {"source": "gemini"},
    })
    with use_domain("electronics"):
        FactoryDecisionAgent(api_key="mock").run("M17 down 8 hours")
    assert seen["domain"] == "electronics"


@pytest.fixture
def client():
    from app.api import app
    return TestClient(app)


def test_api_switches_industry_per_request(client):
    listed = client.get("/api/domains").json()
    assert {d["key"] for d in listed["domains"]} == set(PACKS)

    elec = client.get("/api/factory/overview", headers={"X-FDE-Domain": "electronics"}).json()
    auto = client.get("/api/factory/overview", headers={"X-FDE-Domain": "automotive"}).json()
    assert elec["plant"]["name"] == "Northwind Electronics Plant 1"
    assert auto["plant"]["name"] == "Apex Automotive Plant 1"
    assert elec["kpis"] == auto["kpis"]

    prompt = "M17 will be unavailable for 8 hours. Keep high-priority deliveries on time while minimizing cost."
    decision = client.post("/api/analyze", json={"prompt": prompt}, headers={"X-FDE-Domain": "electronics"}).json()
    assert decision["impact_summary"]["affected_product_models"] == ["NW-Speaker"]
    assert decision["selected_plan"]["target_machine_id"] == "M26"

    # Unknown packs fall back to the default instead of failing
    assert client.get("/api/health", headers={"X-FDE-Domain": "nonsense"}).json()["domain"] in PACKS
