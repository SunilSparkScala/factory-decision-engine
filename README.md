# Factory Decision Engine

> Before you change the factory, understand what happens next.

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
| **Understands messy reports** | Gemini turns "the Unit 2 welder at B2 is down, maybe 6–10 h, keep it cheap, no overtime" into a validated scenario. If the machine is ambiguous, it asks which one instead of guessing. |
| **Simulates the ripple effect** | Machine → station → line → affected orders → delivery risk, from the plant data. |
| **Generates and ranks options** | Move work, reorder the queue, or overtime, each checked against hard constraints and scored on delivery, cost, quality, energy and risk. Changing priorities re-ranks instantly. |
| **Tool-calling agent in Ask** | Open questions ("what if M26 also breaks?") go to a Google ADK agent that runs new simulations, evaluations and knowledge searches itself; its tool trace is shown under the answer. |
| **Specialist war room** | Production, Quality, Maintenance and Energy agents review the plan in parallel, then a Decision Lead writes the brief and maps how their concerns connect. Any objection is surfaced before approval. Citations are validated against retrieved documents. |
| **Robust to uncertain repairs** | Re-runs the decision for 1–48 h of downtime and shows where the plan holds and where another option wins. |
| **Predicts before it breaks** | A trend model on vibration, temperature and health flags machines heading for an alarm (including ones whose status still says "healthy") and pre-builds a backup plan. The Control Room shows machine health, delivery pressure and a plant map of where the trouble is. |
| **Ask about the decision** | "Why not overtime?" or "Explain this to my director": grounded answers with citations. |
| **Closes the loop** | Approval records an audit entry and drafts the supervisor work order, quality notice, schedule changes and customer update. Nothing is executed automatically. |

## One engine, any factory: industry packs

The same engine runs two synthetic plants, and each viewer can switch with the **Industry**
selector in the header (their choice is per browser, so viewers don't affect each other):

| Pack | Plant | Lines | Example machine |
|---|---|---|---|
| **Electronics** (default) | Northwind Electronics Plant 1, Pune | Routers · Smart speakers · Thermostats | M17 = pick-and-place machine |
| **Automotive** | Apex Automotive Plant 1, Chennai | Sedan · SUV · Compact | M17 = welding robot |

Both packs share one structure and seed, so every calculation is identical; only the vocabulary,
the 18 engineering documents (`knowledge/documents/<pack>/`) and the examples differ. Packs are
defined in `app/domains/packs.py`; the API reads the pack from the `X-FDE-Domain` header.

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

Run the tests with `python -m pytest` (Gemini is mocked; no key or network needed).

### Gemini quotas

Each analysis uses about 6 model calls (understanding, 4 specialists plus a lead); an agentic Ask question adds a few more. On the
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
Browser (vanilla JS, no build)          FastAPI (app/api.py)
 Control Room ─┐                         /api/analyze  /api/rerank  /api/review
 Decide ───────┼──── fetch ────────────▶ /api/ask      /api/approve /api/decisions
 Decision Log ─┘                         /api/factory/overview /api/watchlist /api/alerts
                                                     │
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
| `app/ai/llm.py` | Gemini wrapper: structured output, model routing per role, quota cooldowns |
| `app/agent/` | Agent pipeline, understanding, specialists, assistant, decision log |
| `app/simulation/`, `app/recovery/`, `app/evaluation/` | Deterministic engine (source of truth for numbers) |
| `app/prediction/` | Synthetic telemetry, early-warning trend model, alert inbox |
| `app/domains/` | Industry packs and per-request pack selection |
| `app/knowledge/`, `knowledge/documents/` | Engineering knowledge base (SOPs, manuals, incidents) |
| `app/ui/` | `index.html`, `style.css`, `app.js` entry, `js/` modules (views and helpers) |

## Honest limits of the prototype

- The plants, their orders, telemetry and documents are **synthetic** (Northwind Electronics Plant 1
  and Apex Automotive Plant 1); names are fictional.
- Fault events are scripted ("Demo: simulate a machine fault"); in production they would stream from MES/PLC
  systems (e.g. Pub/Sub). The early-warning model is a transparent trend extrapolation, not a trained model.
- The simulation simplifies some realities (for example, it does not model labour skills).
  Assumptions are listed with every decision.
