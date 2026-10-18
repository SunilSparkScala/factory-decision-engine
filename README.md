# Factory Decision Engine

> Before you change the factory, understand what happens next.

**Live demo:** https://factory-decision-engine-627710168651.asia-south1.run.app (Cloud Run, Gemini on Vertex AI; no login)

An agentic decision-intelligence system for manufacturing. When something disrupts a plant (a machine
fails, a repair runs long, a machine starts degrading), a plant manager describes it in plain language
and gets the ripple effects, feasible recovery options, a recommendation reviewed by AI specialist agents,
and documents ready to send after **human approval**.

**Principle: AI reasons, Python calculates.** Every number (capacity loss, cost, energy, feasibility,
scores) comes from a deterministic simulation and optimization engine. Gemini interprets messages,
orchestrates tools, reviews plans, and explains them, but it never invents numbers. A grounding check
flags any number the AI writes that does not appear in the engine's data or the cited documents.

## What it does

| Capability | How |
|---|---|
| **Understands messy reports** | Gemini turns "the Unit 2 pick-and-place at B2 is down, maybe 6–10 h, keep it cheap, no overtime" into a validated scenario. If the machine is ambiguous, it asks which one instead of guessing. |
| **Speak instead of typing** | 🎤 on the report box, the Ask box and the Factory Overview. Gemini on Vertex AI transcribes the voice note (machine IDs kept as `M17`, numbers as digits, machine noise and filler words dropped); the browser's own speech recognition is the fallback. A spoken report replaces the box text, with "Restore previous text" under it. 🔊 **Read aloud** speaks the recommendation, its extra cost and delivery effect, the specialists' verdict and each condition. |
| **Simulates the ripple effect** | Machine → station → line → affected orders → delivery risk, from the plant data. |
| **Generates and ranks options** | Move work, reorder the queue, or overtime, each checked against hard constraints and scored on delivery, cost, quality, energy and risk. Changing priorities re-ranks instantly. **Calculation details** lists the real assumed figures behind every number (overtime rate and cap, setup costs, late-delivery penalty, energy rate). |
| **Specialist war room** | Production, Quality, Maintenance and Energy agents review the plan in parallel, then a Decision Lead writes the brief and links only real knock-on effects. Specialists are told which hard checks the engine already passed, judge only their own field and never invent a condition. The verdict is counted from the cards (e.g. "4 of 4 support, 2 with conditions"). Each source chip opens its document excerpt under the card; citations are validated against retrieved documents. |
| **Ask about this plan** | A Google ADK tool-calling agent answers "Why not overtime?", "Explain this to my director" or "What if M26 breaks down too?" (it runs new simulations, evaluations and knowledge searches itself, and shows what it did in plain words). It remembers the last three questions, so "and what would that cost?" works. Example questions adapt to the plan on screen. |
| **Robust to uncertain repairs** | Re-runs the decision for 1–48 h of downtime and shows where the plan holds and where another option wins. |
| **Predicts before it breaks** | A trend model on vibration, temperature and health flags machines heading for an alarm (including ones whose status still says "healthy") and pre-builds a backup plan. The Factory Overview shows machine health, delivery pressure, what needs action now and a plant map of where the trouble is. |
| **Human approval with conditions** | The approval window sums up the fault, cost and specialists' verdict and lists **"You approve with these conditions"**. It needs a name and a tick. Nothing is executed automatically. |
| **Closes the loop** | Approval logs the decision and drafts the supervisor work order, quality notice, schedule changes and customer update. The documents carry the specialists' conditions and the Decision Lead's next steps, and all four are grounding-checked (badge shows whether every number traces to engine data). The **Decision Log** records the situation in words, the approved conditions and the check result. |
| **Engineering Evidence** | The only documents the AI may cite (machine manuals, quality rules, SOPs, past incidents), shown formatted, with the ones used by the current plan tagged "Cited in the current plan". |

## Not tied to one industry: plant configuration packs

**The demo runs one plant: Northwind Electronics Plant 1 (Pune), a synthetic electronics factory**
(lines: Routers · Smart speakers · Thermostats; M17 is a pick-and-place machine).

The engine itself is not tied to electronics: it works for any plant built from production lines,
stations and machines (electronics, automotive, appliances…). A plant is a **configuration pack** in
`app/domains/packs.py` (plant and line names, process steps, products, fault scripts) plus its
engineering documents in `knowledge/documents/<pack>/`. Adding another factory means adding a pack,
not changing the engine. To show this, the code also ships an **automotive pack** (Apex Automotive
Plant 1, Chennai: Sedan · SUV · Compact). It uses the same layout and numbers on purpose, so the
regression suite (which runs on it) checks that every calculation is independent of the plant's
vocabulary.

The industry selector is hidden in the demo, because both packs share one layout and would show
identical numbers under different names. Set `FDE_INDUSTRY_SWITCH=true` to show it, or
`FDE_DEFAULT_DOMAIN=automotive` to run the other pack.

## Quick walkthrough for judges (3 minutes)

How the project maps to the judging criteria: [docs/HACK2SKILL_JUDGING_COMPLIANCE.md](docs/HACK2SKILL_JUDGING_COMPLIANCE.md).

1. **Open the [live demo](https://factory-decision-engine-627710168651.asia-south1.run.app).** A short
   guided tour starts on the first visit (the **?** button replays it). The app opens in light mode;
   the ☀ button switches theme for that tab.
2. **Factory Overview:** machine health (every machine in exactly one group), delivery pressure, what
   needs action now, machine health trends (M23 still reports healthy but is predicted to reach an
   alarm in about 17 hours) and a plant map of where the trouble is.
3. **Open an alert, type or speak a report** (e.g. *"M17 will be unavailable for 8 hours tomorrow. Keep
   high-priority deliveries on time while minimizing cost."*). The Decision Studio reads in order:
   - **1 Report a disruption:** Gemini states what it understood (machine, duration, priorities).
   - **2 What this affects:** **211.2 units lost (17.6%)**, **6 orders affected, 3 high-priority**.
   - **3 Compare the recovery options:** **10 plans (7 feasible, 3 not possible, each with the
     reason)**, the plant-floor map of where work can move, and whether the plan holds if the repair
     takes longer.
   - **4 Recommendation:** **move M17's work to M26 for $100** (`PLAN_TRANSFER_07`), then the
     specialist review (four Gemini agents in parallel, with sources) and the Decision Lead's brief.
     🔊 Read aloud speaks it.
4. **Change priorities or ask about this plan.** "No overtime?" re-runs the engine with overtime
   disallowed (7 → 6 feasible plans); "What if M26 breaks down too?" makes the ADK agent run a new
   simulation and say what it did; a follow-up like "and what would that cost?" keeps the context.
5. **Review & approve.** Nothing happens without a person's sign-off. The manager approves the plan
   *with the specialists' conditions*; the work order, quality notice, schedule changes and customer
   update are drafted, grounding-checked and logged in the **Decision Log**.

## Run locally

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts/generate_data.py            # builds one database per industry pack
copy .env.example .env                     # then paste your Gemini key into .env
python -m uvicorn app.api:app --env-file .env --port 8000
```

Open http://localhost:8000. Without a key the app runs in **rule-based mode**: every number and plan is
identical, and the AI features show as offline.

Run the tests with `python -m pytest` (209 tests; Gemini is mocked, so no key or network is needed).

### Gemini quotas

Each analysis uses about 6 model calls (understanding, 4 specialists plus a lead); an agentic Ask question adds a few more,
a voice note one, and approval one (the four documents). On the
**free tier**, Flash-Lite models allow about 15 requests per minute and the larger Flash models only about
20 per day. The app spreads roles across models, skips any model that is cooling down after a quota error,
caches identical repeat requests for 15 minutes, and falls back to rule-based results instead of failing.
For demos and judging, **enable billing on the API key's project** (paid tier). An analysis then costs a
fraction of a cent.

## Deploy to Google Cloud Run

On Cloud Run the app reaches **Gemini through Vertex AI** using the service's own identity, with no
API key and billing through the project. The factory database is generated during the image build.

```powershell
gcloud config set project factory-decision-engine-01
gcloud run deploy factory-decision-engine --source . --region asia-south1 --allow-unauthenticated `
  --memory 1Gi --cpu 1 --timeout 300 --max-instances 3 `
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=factory-decision-engine-01,GOOGLE_CLOUD_LOCATION=global,GEMINI_MODEL=gemini-3.5-flash-lite,FDE_LOG_BUCKET=factory-decision-engine-01-decision-log"
```

One-time project setup: enable `run`, `cloudbuild`, `artifactregistry` and `aiplatform` APIs, and grant
the default compute service account `roles/run.builder`, `roles/aiplatform.user` and
`roles/logging.logWriter`. Approved decisions are kept in a private Cloud Storage bucket
(`FDE_LOG_BUCKET`), so the audit log survives restarts and is shared by all instances; the service
account gets `roles/storage.objectUser` on that bucket only. Without the variable the log is a local file.

## Architecture

```text
Browser (vanilla JS, no build)               FastAPI (app/api.py)
 Factory Overview ────┐                       /api/analyze  /api/rerank  /api/review  /api/what-if
 Decision Studio ─────┼─── fetch ───────────▶ /api/ask      /api/transcribe (voice → Gemini)
 Engineering Evidence ┤                       /api/approve  /api/decisions /api/knowledge
 Decision Log ────────┘                       /api/factory/overview /api/watchlist /api/alerts
 🎤 voice notes · 🔊 read aloud                           │
                                      FactoryDecisionAgent (app/agent/agent.py)
     ┌──────────────────────────────┬──────────────┴───────────────┬───────────────────────────┐
 Understanding (Gemini,       Deterministic engine           ADK tool-calling agent      Specialist war room
 structured output,           simulation → recovery plans →  (simulate, evaluate,        Production ‖ Quality ‖
 validated vs registry)       feasibility → scoring →        knowledge search; Ask)      Maintenance ‖ Energy → Lead
 app/agent/understanding.py   evidence (app/simulation,      app/agent/tools.py          app/agent/specialists.py
                              recovery, evaluation)
                                     │
                              Robustness sweep (app/simulation/robustness.py)
                              Early warning (app/prediction/) · Q&A and approval docs (app/agent/assistant.py)
```

| Path | Purpose |
|---|---|
| `app/ai/llm.py` | Gemini wrapper: structured output, model routing per role, quota cooldowns, voice transcription |
| `app/agent/` | Agent pipeline, understanding, specialists, assistant (Ask with follow-up memory, approval documents), decision log |
| `app/simulation/`, `app/recovery/`, `app/evaluation/` | Deterministic engine (source of truth for numbers) |
| `app/prediction/` | Synthetic telemetry, early-warning trend model, alert inbox |
| `app/domains/` | Industry packs and per-request pack selection |
| `app/knowledge/`, `knowledge/documents/` | Engineering knowledge base (SOPs, manuals, incidents) |
| `app/ui/` | `index.html`, `style.css`, `app.js` entry, `js/views/` (overview, Decision Studio, Decision Log) and `js/components/` (plant map, guided tour, voice) |
| `tests/` | pytest suite: engine, agents, grounding, approval documents, voice endpoint, UI API (Gemini mocked) |

## Honest limits of the prototype

- The plants, their orders, telemetry and documents are **synthetic** (Northwind Electronics Plant 1
  and Apex Automotive Plant 1); names are fictional.
- Fault events are scripted ("Demo: simulate a machine fault"); in production they would stream from MES/PLC
  systems (e.g. Pub/Sub). The early-warning model is a transparent trend extrapolation, not a trained model.
- The simulation simplifies some realities (for example, it does not model labour skills).
  Assumptions, with their assumed rates, are listed with every decision.
- Voice needs microphone permission. Read aloud uses the browser's built-in voices, so how it sounds
  depends on the browser; without Gemini, transcription falls back to the browser's speech
  recognition where the browser has it (Chrome, Edge).
