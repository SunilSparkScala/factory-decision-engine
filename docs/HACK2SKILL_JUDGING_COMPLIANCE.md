# Hack2Skill AI Builder Cup 2026 — Judging Criteria & Compliance Audit

This document provides a thorough audit of **Factory Decision Engine** against the official judging criteria for the **Hack2Skill AI Builder Cup** (Manufacturing Theme: Intelligent Operations & Industrial Efficiency).

---

## 1. Executive Summary & Evaluation Matrix

| Criterion | Weight | Current evidence in repository | Gap | Implemented change | Verification evidence |
|---|---|---|---|---|---|
| **Technical Merit & GenAI Implementation** | **40%** | Google GenAI SDK (`google-genai`), `gemini-2.5-flash` model, ADK tool-calling agent in `app/agent/tools.py` orchestrating `simulate_machine_failure`, `generate_recovery_plans`, `evaluate_recovery_plans`, `retrieve_evidence`. Strict separation: AI reasons/interprets; Python optimizer calculates. | Cloud Run deployment documentation needed explicit project setup and verification steps; fallback execution mode needed clear attribution in UI and API headers. | Documented Cloud Run & Vertex AI deployment pipeline in `Dockerfile` and `README.md`; surfaced deterministic vs live execution modes in `/api/health`. | `pytest` test suite: 165 passed, 1 skipped (live API key test). Verified `test_agent.py`, `test_ai_upgrade.py`, `test_decision_orchestration.py`. |
| **Problem Alignment & Impact** | **25%** | Real industrial downtime recovery workflow. Canonical scenario: M17 failure (8h outage, 211.2 units capacity loss, 6 affected orders, 3 high-priority orders). | Hero video previously showed electronics pick-and-place without full automotive framing, and headline was generic. | Replaced footage with authentic automotive robotics chassis assembly video (`hero_automotive.mp4`, Engin Altundağ, Pexels); updated headline to *"When a machine stops, production decisions can’t wait."* with transparent impact metrics. | Verified canonical baseline values via `tests/test_ui_api.py::test_api_canonical_scenario_exact_authoritative_values` and `test_reset_flow_canonical_values_and_db_immutability`. |
| **Innovation & Creativity** | **25%** | Deterministic multi-objective Pareto scoring (delivery, cost, quality, energy, operational risk). Hard constraint validation (feasible vs. infeasible plans). Specialist War Room (Production, Quality, Maintenance, Energy). Knowledge base evidence retrieval. Interactive What-If analysis. | What-if sensitivity needed clear, explicit documentation of how constraint shifts alter candidate feasibility without modifying the base scenario. | Documented and verified What-If no-overtime workflow where overtime plans transition to infeasible (`Overtime is strictly disallowed`) while preserving baseline M17 8h parameters. | `tests/test_ui_api.py::test_api_what_if_disallow_overtime` and `test_what_if_preserves_base_scenario_state_via_analyze`. |
| **User Experience & Solution Design** | **10%** | Non-overlapping split hero layout with dedicated machine-vision monitor on left and decision narrative on right; high-density industrial Decision Studio workspace; step-by-step guided flow; WCAG AAA text contrast; zero decorative AI hallucinations. | Hero video previously sat full-bleed behind text causing readability overlap. | Implemented split-screen hero (`.landing-hero-grid`) with live video monitor on the left and narrative/CTAs on the right; added live telemetry ticker and capability status pills. | Browser visual inspection in Chromium (`1536x730`), verified zero text overlap, smooth autoplay, and responsive layout. |

---

## 2. Criterion A: Technical Merit & GenAI Implementation (40%)

### Architecture Overview
Factory Decision Engine adheres to the strict core principle: **AI Reasons, Python Calculates**.

```
                        ┌──────────────────────────────────────────────┐
                        │      FastAPI Gateway (app/api.py)           │
                        └──────────────────────┬───────────────────────┘
                                               │
                        ┌──────────────────────▼───────────────────────┐
                        │   FactoryDecisionAgent (app/agent/agent.py)  │
                        └──────────────┬───────────────┬───────────────┘
                                       │               │
        ┌──────────────────────────────▼───┐       ┌───▼───────────────────────────┐
        │   Gemini 2.5 Flash / ADK Tools   │       │     Deterministic Engine      │
        │   - Natural Language Scenario    │       │     - Topology Graph (SQLite) │
        │   - Specialist War Room Reviews  │       │     - Capacity Math (Loss)    │
        │   - Grounded Evidence Briefing   │       │     - Pareto Multi-Objective  │
        │   - Grounding Check (Data Audit) │       │     - Hard Constraint Checks  │
        └──────────────────────────────────┘       └───────────────────────────────┘
```

1. **Deterministic Authority**:
   - Capacity loss, order allocation, machine speeds, and transfer costs are calculated by `app/simulation/` and `app/evaluation/`.
   - SQLite factory database (`data/factory.db`) is SHA256 hashed and verified immutable in tests.
2. **GenAI Orchestration**:
   - `google-genai` SDK orchestrates the workflow.
   - When a plant manager inputs an ambiguous scenario (e.g., *"M17 or M26 is down"*), Gemini identifies the ambiguity and prompts for clarification with candidate buttons rather than guessing.
   - When an ungrounded number is detected in AI text that does not exist in the engine data, the Grounding Check flags it (`rec-grounding`).
3. **Resilience & Fallback**:
   - If `GEMINI_API_KEY` is not provided or quota limits are exceeded, the engine seamlessly switches to deterministic rule-based analysis. No endpoint crashes; identical mathematical scores are produced.

---

## 3. Criterion B: Problem Alignment & Impact (25%)

### The Manufacturing Reality
Industrial downtime costs automotive and discrete manufacturing plants between $20,000 and $50,000 per hour. When a critical machine stops, supervisors are forced to make snap decisions without visibility into:
1. Which customer orders will miss SLA deadlines.
2. Which alternative machines have compatible tooling and available capacity.
3. The true cost of emergency overtime vs. rerouting.

### Canonical Baseline Verification
The system is anchored by a rigorously verified canonical test scenario:

| Metric | Canonical Value | Meaning |
|---|---|---|
| **Disrupted Unit** | Machine `M17` (Line B) | Automotive Welding / Electronics SMT |
| **Outage Window** | 8.0 Hours | Scheduled shift downtime |
| **Capacity Loss** | 211.2 Units (17.6%) | Direct lost output from station |
| **Affected Orders** | 6 Orders | Orders scheduled during outage |
| **High-Priority Exposed** | 3 Orders | Critical client deliveries threatened |
| **Feasible Plans** | 7 Plans | Reroute, queue buffer, overtime, shift rebalance |
| **Infeasible Plans** | 3 Plans | Blocked by capacity overload or maintenance conflict |
| **Recommended Plan** | `PLAN_TRANSFER_07` | Work transferred to compatible machine `M26` |
| **Transfer Cost** | $100.00 USD | Setup and tooling changeover fee |
| **Weighted Decision Score** | **0.6881** | Pareto optimum across delivery, cost, quality, risk & energy |

---

## 4. Criterion C: Innovation & Creativity (25%)

### Key Differentiators
1. **Explainable Infeasibility**:
   Unlike black-box optimization, Factory Decision Engine explicitly surfaces *why* alternatives fail (e.g. *"Station STA_A1 already at 100% capacity"*, *"Tooling profile incompatible"*).
2. **Interactive What-If Sensitivity**:
   Operators can test constraints on the fly:
   - *"What if overtime is not allowed?"* -> Disables overtime recovery plans without altering the base scenario (feasible plans shift 7 -> 6; overtime plan marked infeasible with reason *"Overtime is strictly disallowed"*).
3. **Multi-Agent Specialist War Room**:
   Four independent domain agents assess each proposed plan:
   - **Production Agent**: Evaluates line rate and throughput.
   - **Quality Agent**: Audits error margins and tolerance variance.
   - **Maintenance Agent**: Flags thermal and mechanical wear implications.
   - **Energy Agent**: Assesses peak kWh consumption and carbon impact.
4. **Knowledge-Grounded Evidence**:
   Recommendations cite authentic engineering standards from `knowledge/documents/` (e.g., *ISO 13849 Machine Safety*, *Work Instruction WI-702*).

---

## 5. Criterion D: User Experience & Solution Design (10%)

### Professional Industrial UX Design
1. **Split Hero Showcase (Landing)**:
   - Left: Authentic automotive robotics body assembly video monitor (`hero_automotive.mp4`, 1080p, 60fps feed simulation) with laser vision scan beam and machine reticles.
   - Right: High-contrast narrative with headline *"When a machine stops, production decisions can’t wait."*, primary CTA *"Enter Decision Studio"*, and live shop-floor marquee ticker.
   - Zero overlap between video and copy.
2. **Decision Studio (Operational Workspace)**:
   - Five-stage guided workflow:
     1. **What Happened**: Scenario input with quick canonical presets.
     2. **Impact**: Numerical loss metrics and plant line topology map.
     3. **Options**: Ranked candidate cards and comprehensive comparison table.
     4. **Recommendation**: Multi-objective figure cards, AI briefing, and War Room consensus.
     5. **Human Approval**: Mandatory human authorization modal with supervisor dispatch packet.
3. **Accessibility & Design System**:
   - WCAG AAA contrast ratio on all core text.
   - Responsive breakpoints for desktop workstations (1440x900, 1280x800) and mobile viewports.

---

## 6. Verification & Automated Test Evidence

### Full Pytest Suite Run
```
============================= test session starts =============================
platform win32 -- Python 3.14.3, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\genAI\FactoryDecisionEngine
configfile: pytest.ini
collected 166 items

tests\test_agent.py ..............s.........                             [ 14%]
tests\test_ai_upgrade.py .......................                         [ 28%]
tests\test_data_generation.py ....                                       [ 30%]
tests\test_decision_log.py ...                                           [ 32%]
tests\test_decision_orchestration.py ................                    [ 42%]
tests\test_domains.py ........                                           [ 46%]
tests\test_evaluation_optimization.py ..........                         [ 53%]
tests\test_factory_state.py .............                                [ 60%]
tests\test_knowledge_base.py .......................                     [ 74%]
tests\test_machine_failure.py ..........                                 [ 80%]
tests\test_recovery_plans.py ...............                             [ 89%]
tests\test_ui_api.py .................                                   [100%]

================= 165 passed, 1 skipped, 2 warnings in 36.46s =================
```
