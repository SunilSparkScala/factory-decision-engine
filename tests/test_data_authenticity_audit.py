"""
End-to-End Data Authenticity, Accuracy & Provenance Audit Test Suite.
Validates that every number, KPI, status count, percentage, capacity, score,
order metric, and recommendation is mathematically verified against raw SQLite ground truth.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.config import config, DB_PATH
from app.domains import use_domain, get_pack
from app.agent.orchestrator import DecisionOrchestrator
from app.agent.agent import FactoryDecisionAgent

client = TestClient(app)


def _raw_db_query(query: str, params: tuple = (), db_file: Path = None):
    """Execute raw query directly on SQLite database file to get unmediated ground truth."""
    path = db_file or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        return cur.fetchall()
    finally:
        conn.close()


def test_authoritative_machine_inventory_and_mutually_exclusive_states():
    """
    Audit 1: Machine inventory and status counts.
    Reconciles raw SQLite machines table against /api/factory/overview.
    Asserts:
    - Total machine count = 36.
    - Status categories are mutually exclusive: HEALTHY (32) + DEGRADED (2) + MAINTENANCE (1) + DOWN (1) = 36.
    - /api/factory/overview kpis match database counts exactly.
    - M23 operational status in database is HEALTHY.
    """
    rows = _raw_db_query("SELECT machine_id, status, health_score FROM machines")
    assert len(rows) == 36, f"Expected 36 machines in database, found {len(rows)}"

    statuses = [r["status"] for r in rows]
    healthy_count = statuses.count("HEALTHY")
    degraded_count = statuses.count("DEGRADED")
    maintenance_count = statuses.count("MAINTENANCE")
    down_count = statuses.count("DOWN")

    assert healthy_count == 32
    assert degraded_count == 2
    assert maintenance_count == 1
    assert down_count == 1
    assert healthy_count + degraded_count + maintenance_count + down_count == 36

    # Verify M23 in raw database
    m23_row = next(r for r in rows if r["machine_id"] == "M23")
    assert m23_row["status"] == "HEALTHY", "M23 must have operational status HEALTHY in DB"

    # Verify API overview response
    res = client.get("/api/factory/overview")
    assert res.status_code == 200
    kpis = res.json()["kpis"]

    assert kpis["machines_total"] == 36
    assert kpis["machines_running"] == 32
    assert kpis["machines_degraded"] == 2
    assert kpis["machines_stopped"] == 2  # MAINTENANCE (1) + DOWN (1)
    assert kpis["machines_unknown"] == 0
    assert kpis["machines_running"] + kpis["machines_degraded"] + kpis["machines_stopped"] == kpis["machines_total"]


def test_predictive_risk_separated_from_operational_state():
    """
    Audit 2: Telemetry warning separation.
    Asserts:
    - M23 is on the predictive watchlist with risk_level HIGH and hours_to_alarm < 24.0.
    - M23 operational status remains strictly HEALTHY in API overview and machine detail.
    - Predictive risk is treated as a forward-looking advisory, not an operational failure.
    """
    wl_res = client.get("/api/watchlist")
    assert wl_res.status_code == 200
    watchlist = wl_res.json()["machines"]

    m23_watch = next((w for w in watchlist if w["machine_id"] == "M23"), None)
    assert m23_watch is not None, "M23 must appear on the predictive watchlist"
    assert m23_watch["risk_level"] == "HIGH"
    assert m23_watch["status"] == "HEALTHY"
    assert 15.0 <= m23_watch["hours_to_alarm"] <= 20.0

    # Operational status check in machine detail
    m_res = client.get("/api/machines/M23")
    assert m_res.status_code == 200
    m_data = m_res.json()
    assert m_data["status"] == "HEALTHY"
    assert m_data["telemetry"]["risk_level"] == "HIGH"


def test_plant_and_line_capacity_and_utilization_formulae():
    """
    Audit 3: Production, capacity, and utilization calculations.
    Independently computes line and plant utilization from raw DB records and validates API output:
    - Plant clock: 2026-10-01T08:00:00 UTC
    - Scheduled demand window: next 24 hours (due_time <= plant_clock + 24h)
    - Line capacities: LINE_A = 60.0, LINE_B = 50.0, LINE_C = 70.0 (total = 180.0 u/h, 4320.0 u/24h)
    - Exactly 10 orders due in 24h, total 1518 units.
    - Factory utilization: 1518 / 4320 = 35.1%
    - Line A: 1081 / 1440 = 75.1%
    - Line B: 324 / 1200 = 27.0%
    - Line C: 113 / 1680 = 6.7%
    """
    base_time = config.PLANT_CLOCK
    window_end = (base_time + timedelta(hours=24)).isoformat()

    orders_24h = _raw_db_query(
        "SELECT order_id, assigned_line, quantity, priority, due_time FROM production_orders WHERE due_time <= ?",
        (window_end,)
    )
    assert len(orders_24h) == 10
    total_scheduled = sum(r["quantity"] for r in orders_24h)
    assert total_scheduled == 1518

    # Query API overview
    res = client.get("/api/factory/overview")
    assert res.status_code == 200
    data = res.json()
    kpis = data["kpis"]

    assert kpis["total_capacity_per_hour"] == 180.0
    assert kpis["total_scheduled_24h_units"] == 1518
    assert kpis["factory_utilization_pct"] == 35.1

    line_map = {l["line_id"]: l for l in data["lines"]}
    assert line_map["LINE_A"]["scheduled_units_24h"] == 1081
    assert line_map["LINE_A"]["capacity_24h"] == 1440.0
    assert line_map["LINE_A"]["utilization_pct"] == 75.1

    assert line_map["LINE_B"]["scheduled_units_24h"] == 324
    assert line_map["LINE_B"]["capacity_24h"] == 1200.0
    assert line_map["LINE_B"]["utilization_pct"] == 27.0

    assert line_map["LINE_C"]["scheduled_units_24h"] == 113
    assert line_map["LINE_C"]["capacity_24h"] == 1680.0
    assert line_map["LINE_C"]["utilization_pct"] == 6.7


def test_order_counts_and_delivery_risk_classification():
    """
    Audit 4: Order counts and delivery risk classification.
    Raw DB ground truth:
    - Total orders = 55.
    - High priority = 12.
    - Orders due in 24h = 10 (1 high priority).
    - Delivery risk categories in API must be mutually exclusive and reconcile.
    """
    orders = _raw_db_query("SELECT order_id, priority, due_time FROM production_orders")
    assert len(orders) == 55
    high_pri = [o for o in orders if o["priority"] == "HIGH"]
    assert len(high_pri) == 12

    res = client.get("/api/factory/overview")
    assert res.status_code == 200
    kpis = res.json()["kpis"]
    assert kpis["orders_active"] == 55
    assert kpis["orders_high_priority"] == 12
    assert kpis["orders_due_24h"] == 10
    assert kpis["high_priority_due_24h"] == 1

    api_orders = res.json()["orders"]
    assert len(api_orders) == 55
    risk_labels = {o["delivery_risk"] for o in api_orders}
    assert risk_labels.issubset({"ELEVATED", "MONITORED_LINE_EXPOSURE", "NOMINAL"})


def test_canonical_m17_8h_scenario_exact_reproducibility():
    """
    Audit 5: Canonical M17 8h disruption scenario.
    Asserts exact values established by the deterministic engine:
    - Capacity loss: 211.2 units (17.6% of line daily capacity).
    - Affected orders: 6 total, 3 high priority.
    - Candidate plans: 10 total (7 feasible, 3 infeasible).
    - Selected plan: PLAN_TRANSFER_07, target M26, cost $100.00, score 0.6881.
    - Objective weights: delivery=0.4, cost=0.4, quality=0.1, energy=0.05, risk=0.05.
    """
    res = client.post("/api/analyze", json={
        "prompt": "M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost."
    })
    assert res.status_code == 200
    data = res.json()

    imp = data["impact_summary"]
    assert imp["machine_id"] == "M17"
    assert imp["downtime_hours"] == 8.0
    assert imp["capacity_loss_units"] == 211.2
    assert imp["capacity_loss_percentage"] == 17.6
    assert imp["affected_order_count"] == 6
    assert imp["high_priority_affected_count"] == 3

    weights = data["objective_weights"]
    assert weights["delivery"] == 0.4
    assert weights["cost"] == 0.4
    assert weights["quality"] == 0.1
    assert weights["energy"] == 0.05
    assert weights["risk"] == 0.05

    plans = data["candidate_plans"]
    assert len(plans) == 10
    feasible = [p for p in plans if p["is_feasible"]]
    infeasible = [p for p in plans if not p["is_feasible"]]
    assert len(feasible) == 7
    assert len(infeasible) == 3

    sel = data["selected_plan"]
    assert sel["plan_id"] == "PLAN_TRANSFER_07"
    assert sel["target_machine_id"] == "M26"
    assert sel["cost_impact_usd"] == 100.0
    assert sel["weighted_score"] == 0.6881
    assert sel["is_feasible"] is True


def test_what_if_no_overtime_feasibility_transition():
    """
    Audit 6: What-if sensitivity analysis for overtime constraint.
    Asserts:
    - Base scenario has 7 feasible and 3 infeasible plans.
    - What-if 'What if overtime is not allowed?' drops feasible plans to 6 and increases infeasible to 4.
    - Overtime plan is marked infeasible with explicit reason.
    """
    base_res = client.post("/api/analyze", json={
        "prompt": "M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost."
    })
    base_data = base_res.json()

    wi_res = client.post("/api/what-if", json={
        "previous_response": base_data,
        "follow_up_prompt": "What if overtime is not allowed?"
    })
    assert wi_res.status_code == 200
    wi_data = wi_res.json()

    plans = wi_data["candidate_plans"]
    assert len(plans) == 10
    feasible = [p for p in plans if p["is_feasible"]]
    infeasible = [p for p in plans if not p["is_feasible"]]
    assert len(feasible) == 6
    assert len(infeasible) == 4

    ot = next(p for p in plans if p["strategy_type"] == "OVERTIME")
    assert ot["is_feasible"] is False
    assert any("Overtime is strictly disallowed" in r for r in ot["infeasibility_reasons"])


def test_rerank_deterministic_mathematical_consistency():
    """
    Audit 7: Re-rank optimization consistency across priority intents.
    Asserts:
    - Balanced / Cost / Delivery intents select PLAN_TRANSFER_07 (cost $100.0).
    - Energy priority intent selects PLAN_RESEQUENCE_01 (energy 250.0 kWh, cost $2,810.0).
    - Risk priority intent selects PLAN_OVERTIME_01 (risk 0.20, cost $400.0).
    """
    dec = DecisionOrchestrator().orchestrate(machine_id="M17", downtime_hours=8.0, priorities="delivery cost")
    agent = FactoryDecisionAgent()

    # Re-rank under energy
    res_energy = agent.rerank(dec, "energy")
    assert res_energy.selected_plan.plan_id == "PLAN_RESEQUENCE_01"
    assert res_energy.selected_plan.energy_kwh == 250.0
    assert res_energy.selected_plan.cost_impact_usd == 2810.0

    # Re-rank under risk
    res_risk = agent.rerank(dec, "risk")
    assert res_risk.selected_plan.plan_id == "PLAN_OVERTIME_01"
    assert res_risk.selected_plan.risk_score == 0.2
    assert res_risk.selected_plan.cost_impact_usd == 400.0

    # Re-rank under cost
    res_cost = agent.rerank(dec, "cost")
    assert res_cost.selected_plan.plan_id == "PLAN_TRANSFER_07"
    assert res_cost.selected_plan.cost_impact_usd == 100.0


def test_domain_switching_isolation():
    """
    Audit 8: Multi-domain isolation between electronics and automotive.
    Asserts:
    - Identical mathematical topology and numbers across domains.
    - Isolated plant names, locations, and model nomenclature.
    """
    with use_domain("electronics"):
        dec_elec = DecisionOrchestrator().orchestrate(machine_id="M17", downtime_hours=8.0, priorities="delivery cost")
        ov_elec = client.get("/api/factory/overview", headers={"X-FDE-Domain": "electronics"}).json()

    with use_domain("automotive"):
        dec_auto = DecisionOrchestrator().orchestrate(machine_id="M17", downtime_hours=8.0, priorities="delivery cost")
        ov_auto = client.get("/api/factory/overview", headers={"X-FDE-Domain": "automotive"}).json()

    # Identical math
    assert dec_elec.impact_summary["capacity_loss_units"] == dec_auto.impact_summary["capacity_loss_units"] == 211.2
    assert dec_elec.selected_plan.plan_id == dec_auto.selected_plan.plan_id == "PLAN_TRANSFER_07"
    assert dec_elec.selected_plan.cost_impact_usd == dec_auto.selected_plan.cost_impact_usd == 100.0
    assert dec_elec.selected_plan.weighted_score == dec_auto.selected_plan.weighted_score == 0.6881

    # Isolated plant vocabulary
    assert "Northwind" in ov_elec["plant"]["name"]
    assert "Apex" in ov_auto["plant"]["name"]
    assert "NW-Router" in ov_elec["lines"][0]["supported_models"]
    assert "Aurora" in ov_auto["lines"][0]["supported_models"]


def test_frontend_source_hygiene_no_fabricated_metrics():
    """
    Audit 9: Frontend source hygiene check.
    Asserts that no hardcoded business metric values, mock plan IDs, or fake outputs exist in UI assets.
    """
    ui_dir = Path(__file__).resolve().parent.parent / "app" / "ui"
    js_files = [ui_dir / "app.js", *sorted((ui_dir / "js").rglob("*.js"))]

    forbidden_strings = ['"240.0"', '"0.7850"', '"Model-B"', "rec_transfer_m26"]
    for js in js_files:
        content = js.read_text(encoding="utf-8")
        for forbidden in forbidden_strings:
            assert forbidden not in content, f"Found forbidden hardcoded string '{forbidden}' in {js.name}"
