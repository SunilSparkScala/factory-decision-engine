# Factory Decision Engine: how it meets the judging criteria

Google Cloud AI Builder Cup 2026, theme *Manufacturing: Intelligent Operations & Industrial Efficiency*.
Every claim below points to code in this repository. The plant, its orders, sensor history and
engineering documents are synthetic.

| Criterion | Weight | Where to see it |
|---|---|---|
| Technical merit & Gen AI | 40% | Gemini understanding, specialist war room, ADK tool-calling agent, grounding check, Vertex AI on Cloud Run |
| Problem alignment & impact | 25% | Machine breakdowns and early warnings turned into an approved recovery plan in minutes |
| Innovation & creativity | 25% | "AI reasons, Python calculates", robustness across repair times, predictive alerts with pre-built plans, an industry-agnostic engine (plants are configuration packs) |
| User experience | 10% | Guided tour, plain-language results, one action per alert, mandatory human approval |

## 1. Technical merit & Gen AI (40%)

**Principle: AI reasons, Python calculates.** Capacity loss, affected orders, plan feasibility, cost,
energy and scores come only from the deterministic engine (`app/simulation/`, `app/recovery/`,
`app/evaluation/`). Gemini never produces an authoritative number.

| Gen AI component | What it does | Code |
|---|---|---|
| Scenario understanding | Gemini turns a plain-language report into machine, downtime range and priorities (structured output). Python validates the machine and ranges; if the machine is ambiguous it asks instead of guessing. | `app/agent/understanding.py` |
| Voice input | Gemini on Vertex AI transcribes a spoken report or question (machine IDs and numbers kept exact, machine noise and filler dropped); browser speech recognition is the fallback. Read aloud speaks the recommendation, verdict and conditions. | `/api/transcribe` in `app/api.py`, `transcribe_audio` in `app/ai/llm.py`, `app/ui/js/components/voice.js` |
| Specialist war room | Four Gemini specialists (Production, Quality, Maintenance, Energy) review the recommended plan in parallel and cite documents; a Decision Lead writes the brief and links only real knock-on effects between their concerns. Specialists are told which hard checks the engine already passed, judge only their own field and never invent a condition. Objections are detected from the specialists' own stances. | `app/agent/specialists.py` |
| Tool-calling agent | "Ask about this plan" ("What if M26 breaks down too?") goes to a Google ADK agent that calls the simulation, evaluation and knowledge-search tools itself; the UI says in plain words what it ran. The last three questions and answers go with each new one, so follow-ups work. | `app/agent/agent.py`, `app/agent/tools.py` |
| Grounding check | Any number the AI writes that does not appear in the engine output, the cited documents or the manager's own question is flagged in the UI, on answers and on all four approval documents. | `find_ungrounded_numbers` in `app/agent/specialists.py` |
| Approval documents | After a person approves the plan with the specialists' conditions, Gemini drafts the work order, quality notice, schedule summary and customer update from engine data, carrying those conditions and the Decision Lead's next steps (templates if AI is unavailable). | `app/agent/assistant.py` |

**Google Cloud.** Deployed on Cloud Run; Gemini is called through **Vertex AI** with the service's own
identity (`gemini-3.5-flash-lite`, no API key). Approved decisions are stored in **Cloud Storage**,
so the audit log survives restarts. Without Gemini the app keeps working in rule-based mode with
identical numbers, and says so on screen.

**Tests.** `python -m pytest`: 209 passed, 1 skipped (a live-key test). The suite pins the canonical
numbers below, checks the grounding rules, both configuration packs, approvals and their documents,
the voice endpoint and the UI assets.

## 2. Problem alignment & impact (25%)

When a machine stops, a plant manager has to decide quickly with incomplete information: which
orders are hit, which machines could take over, and what each option costs. The app gives that
answer, with the reasons, and leaves the decision to the person.

Canonical scenario (synthetic Northwind Electronics Plant 1):

| Metric | Value |
|---|---|
| Disruption | M17 down for 8 hours (Line B) |
| Capacity loss | 211.2 units (17.6% of the line) |
| Affected orders | 6, of which 3 high-priority |
| Recovery plans checked | 10: 7 feasible, 3 not possible (each with its reason) |
| Recommendation | `PLAN_TRANSFER_07`: move M17's work to M26, extra cost $100 |

**Early warning.** A transparent trend model on synthetic sensor history (vibration, temperature,
health) projects when each machine reaches its alarm, as a range, switching to a curve when wear is
accelerating (`app/prediction/health.py`). M23 still reports "healthy" but is predicted to reach its
alarm in about 17 hours; the engine has already built its backup plan. This is a trend
extrapolation, not a trained ML model.

## 3. Innovation & creativity (25%)

- **Numbers you can trust, reasoning you can read:** deterministic engine plus Gemini review, with a
  grounding check between them.
- **Robustness across repair times:** the decision is re-run for 1–48 hours of downtime, showing
  where the recommendation holds and where another plan wins (`app/simulation/robustness.py`).
- **Connected specialist review:** the Decision Lead links one department's condition to another
  (e.g. a quality inspection slows production's first hour) instead of just summarising.
- **Industry-agnostic engine:** a plant is a configuration pack (layout, vocabulary, engineering
  documents) in `app/domains/`. The demo runs an electronics plant; an automotive pack with the same
  layout is included and the regression suite runs on it, showing the calculations do not depend on
  the plant's vocabulary.
- **What-if in conversation:** "No overtime?" re-runs the engine with the constraint (7 → 6 feasible
  plans) without changing the base scenario.

## 4. User experience (10%)

- A 7-step guided tour on the first visit; the **?** button replays it.
- Factory Overview: machine health (each machine in exactly one group), delivery pressure, what needs
  action now, machine health trends and a plant map. Every alert has one action: *Analyze impact*.
- Results in plain words (e.g. "Delivery: best achievable", "Quality risk: medium"); raw scores stay
  in the candidates table and the details panel.
- Decision Studio reads in order: 1 Report, 2 What this affects, 3 Compare the recovery options,
  4 Recommendation, then specialist review, Ask about this plan, and Review & approve.
- Speak instead of typing (🎤) and Read aloud (🔊) for hands-busy use on the shop floor.
- Mandatory human approval: the manager approves the plan *with* the specialists' listed conditions,
  with a name and an explicit checkbox, plus a separate acknowledgement for any specialist objection.
- Engineering Evidence shows the only documents the AI may cite; Decision Log records each approved
  decision in words, with its conditions and whether the documents' numbers were checked.

## Honest limits

- All data is synthetic; fault events are triggered by a demo button (in production they would come
  from the machines, e.g. via Pub/Sub).
- The engine assumes a backup machine has free capacity and charges a flat transfer cost; it does not
  model labour skills or downstream starvation.
- The early-warning model is a trend extrapolation on synthetic data.
