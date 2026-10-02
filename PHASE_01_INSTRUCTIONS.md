# Factory Decision Engine

## Phase 1 — Manufacturing Simulation Foundation

### Project Codename

**Factory Decision Engine**

### Tagline

> Before you change the factory, understand what happens next.

---

# 1. Your Role

You are the primary AI software engineer for this project.

Build the project incrementally and maintain production-quality engineering practices.

For this phase, **DO NOT build the AI agent, Gemini integration, RAG, frontend, cloud deployment, or advanced ML**.

The only goal of Phase 1 is to create a reliable synthetic manufacturing environment and deterministic scenario engine.

---

# 2. Product Context

Factory Decision Engine is an AI-powered manufacturing decision-support system.

The eventual system will allow a manufacturing engineer or plant manager to ask questions such as:

> "Machine M17 will be unavailable for 8 hours tomorrow. How should we adapt production while protecting high-priority deliveries and minimizing cost?"

The eventual system will:

1. Understand the manufacturing scenario.
2. Identify affected factory operations.
3. Simulate the consequences.
4. Generate feasible alternatives.
5. Compare alternatives.
6. Optimize according to user priorities.
7. Explain the recommendation using GenAI.
8. Provide evidence.
9. Keep a human in the approval loop.

However, **Phase 1 only implements the deterministic manufacturing foundation**.

---

# 3. Phase 1 Goal

At the end of Phase 1, the application must be able to execute:

```python
scenario = {
    "machine_id": "M17",
    "downtime_hours": 8
}

result = simulate_scenario(scenario)
```

and produce a reliable result such as:

```text
Machine M17 unavailable for 8 hours.

Capacity loss: 18%
Affected orders: 3
High-priority orders affected: 1
Delivery risk: HIGH

Available alternatives:
1. Shift eligible production to Line B
2. Resequence production orders
3. Use overtime capacity
```

The numbers above are examples only. Calculate actual values from the generated data.

---

# 4. Important Engineering Principle

Do NOT hardcode the final answer.

The simulator must calculate results from the underlying manufacturing data.

For example, do not write:

```python
capacity_loss = 0.18
```

Instead calculate capacity loss from:

* machine capacity
* downtime duration
* production schedule
* cycle time
* operating hours

Similarly, do not hardcode affected orders.

Determine them from the dependency relationships in the generated factory data.

---

# 5. Technology Constraints

Use simple, free/local technologies for Phase 1.

## Required

* Python 3.11+
* SQLite
* Pandas
* NumPy
* Pydantic
* pytest
* Git

## Optional

* SQLAlchemy
* NetworkX
* Faker

## Do NOT use yet

* Gemini
* Google ADK
* Vertex AI
* BigQuery
* Cloud Run
* Firebase
* LangChain
* paid APIs
* external databases
* frontend frameworks
* vector databases

Everything in Phase 1 must run locally.

---

# 6. Project Structure

Create the following structure:

```text
factory-decision-engine/
│
├── README.md
├── PHASE_01_INSTRUCTIONS.md
├── requirements.txt
├── .gitignore
│
├── app/
│   ├── __init__.py
│   │
│   ├── config.py
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── machine.py
│   │   ├── production.py
│   │   ├── order.py
│   │   ├── inventory.py
│   │   └── maintenance.py
│   │
│   ├── data/
│   │   ├── __init__.py
│   │   ├── database.py
│   │   └── generator.py
│   │
│   ├── factory/
│   │   ├── __init__.py
│   │   ├── state.py
│   │   ├── topology.py
│   │   └── dependencies.py
│   │
│   └── simulation/
│       ├── __init__.py
│       ├── scenarios.py
│       ├── impact.py
│       └── machine_failure.py
│
├── data/
│   ├── raw/
│   └── generated/
│
├── tests/
│   ├── test_data_generation.py
│   ├── test_factory_state.py
│   ├── test_dependencies.py
│   └── test_machine_failure.py
│
└── scripts/
    ├── generate_data.py
    └── run_simulation.py
```

You may adjust the structure if there is a strong engineering reason, but keep the same separation of concerns.

---

# 7. Synthetic Factory

Create a fictional automotive manufacturing plant.

Do NOT use any confidential Mercedes-Benz data.

The factory should be realistic but entirely synthetic.

## Factory topology

Create:

```text
Factory
│
├── Line A
│   ├── Station A1
│   ├── Station A2
│   ├── Station A3
│   └── Station A4
│
├── Line B
│   ├── Station B1
│   ├── Station B2
│   └── Station B3
│
└── Line C
    ├── Station C1
    ├── Station C2
    └── Station C3
```

Create approximately:

* 3 production lines
* 12 stations
* 30–50 machines
* 50–100 production orders
* 20–30 materials
* 10–20 suppliers

Keep the initial dataset small enough for fast local development.

---

# 8. Manufacturing Entities

The initial system must represent:

## Factory

```text
factory_id
name
location
operating_hours
```

## Production Line

```text
line_id
name
capacity_per_hour
supported_models
status
```

## Station

```text
station_id
line_id
name
operation_type
capacity_per_hour
```

## Machine

```text
machine_id
line_id
station_id
machine_type
cycle_time_seconds
capacity_per_hour
status
health_score
```

Machine statuses:

```text
HEALTHY
DEGRADED
MAINTENANCE
DOWN
```

## Production Order

```text
order_id
product_model
quantity
priority
due_time
assigned_line
status
```

Priority:

```text
HIGH
MEDIUM
LOW
```

## Production Schedule

```text
schedule_id
order_id
machine_id
station_id
start_time
end_time
planned_quantity
```

## Material

```text
material_id
name
available_quantity
safety_stock
```

## Supplier

```text
supplier_id
material_id
lead_time_hours
status
```

## Maintenance Event

```text
maintenance_id
machine_id
start_time
duration_hours
maintenance_type
status
```

---

# 9. Generate Realistic Data

Create a deterministic data generator.

Running:

```bash
python scripts/generate_data.py
```

should generate the entire synthetic factory.

Use a fixed random seed.

For example:

```python
SEED = 42
```

This is important so that tests and demos are reproducible.

The generator should produce internally consistent data.

For example:

* An order assigned to Line A should only use compatible stations.
* A machine must belong to an existing station.
* A scheduled operation must use the correct machine.
* Due dates should be after production start times.
* Material quantities should support at least some planned production.
* Machine capacities should be plausible.

---

# 10. Data Generation Rules

Avoid completely random data.

Use manufacturing relationships.

For example:

```text
Model-X
  ↓
requires:
  Welding
  Assembly
  Inspection
```

and:

```text
Station A3
  ↓
supports:
  Welding
```

Then machines at A3 inherit the station's operation capability.

Production orders should depend on compatible operations.

This will allow us to calculate downstream impact later.

---

# 11. Factory Dependency Graph

Create an explicit dependency model.

Example:

```text
Machine M17
     ↓
Station A3
     ↓
Operation Welding
     ↓
Production Order O1047
     ↓
Delivery Commitment
```

The dependency engine should be able to answer:

```python
get_machine_dependencies("M17")
```

and return affected:

* station
* operations
* production schedules
* orders
* delivery commitments

Use NetworkX if useful, but do not introduce unnecessary complexity.

---

# 12. Factory State Engine

Implement:

```python
get_factory_state()
```

It should provide a consistent snapshot of the current factory.

Example:

```json
{
  "production": {
    "planned_units": 1200,
    "completed_units": 840
  },
  "machines": {
    "total": 40,
    "healthy": 35,
    "degraded": 3,
    "maintenance": 1,
    "down": 1
  },
  "orders": {
    "total": 20,
    "high_priority": 5
  },
  "quality": {
    "first_pass_yield": 0.978
  }
}
```

Do not calculate values using hardcoded constants.

---

# 13. Scenario Model

Create a generic scenario model.

Example:

```python
Scenario(
    scenario_type="MACHINE_UNAVAILABILITY",
    machine_id="M17",
    start_time=...,
    duration_hours=8
)
```

The scenario system must eventually support:

```text
MACHINE_UNAVAILABILITY
SUPPLIER_DELAY
QUALITY_DEGRADATION
ENERGY_CONSTRAINT
DEMAND_CHANGE
```

But Phase 1 only needs:

```text
MACHINE_UNAVAILABILITY
```

Design the model so the others can be added later without rewriting the simulator.

---

# 14. Machine Failure Simulation

Implement:

```python
simulate_machine_failure(
    machine_id: str,
    downtime_hours: float
)
```

The simulation should:

1. Identify the machine.
2. Determine its station.
3. Determine dependent operations.
4. Find affected production schedules.
5. Calculate lost production capacity.
6. Identify affected orders.
7. Identify high-priority affected orders.
8. Calculate delivery risk.
9. Find potentially available alternative capacity.

---

# 15. Simulation Output

Return a structured result.

Example:

```json
{
  "scenario": {
    "type": "MACHINE_UNAVAILABILITY",
    "machine_id": "M17",
    "duration_hours": 8
  },
  "impact": {
    "capacity_loss_units": 143,
    "capacity_loss_percentage": 18.2,
    "affected_orders": [
      "O1047",
      "O1048",
      "O1051"
    ],
    "high_priority_orders_affected": [
      "O1047"
    ],
    "delivery_risk": "HIGH"
  },
  "alternatives": []
}
```

Do not implement alternative optimization yet unless it is required for basic impact analysis.

---

# 16. Delivery Risk

For Phase 1 use a simple deterministic rule.

For example:

```text
LOW:
No committed order is expected to miss its due time.

MEDIUM:
A delivery buffer is significantly reduced.

HIGH:
At least one high-priority order is expected to miss its due time.
```

The exact thresholds should be configurable.

Do not use an LLM for this.

---

# 17. Capacity Calculation

Capacity must account for:

```text
machine capacity
×
available operating hours
×
machine utilization
```

For a downtime event:

```text
lost_capacity =
    machine_capacity_per_hour
    × downtime_hours
```

If production schedules are already allocated, calculate actual affected scheduled quantity as well.

Keep the calculation transparent.

---

# 18. Alternative Capacity Discovery

Implement:

```python
find_alternative_capacity(
    affected_operation,
    required_quantity
)
```

It should find compatible stations/machines that:

* support the same operation
* are operational
* have available capacity
* are not under maintenance
* do not violate known constraints

Return candidates with:

```text
machine_id
available_capacity
utilization
compatibility
```

Do not optimize yet.

---

# 19. CLI Demo

Create:

```bash
python scripts/run_simulation.py
```

Expected output:

```text
==================================================
FACTORY DECISION ENGINE
Phase 1 — Scenario Simulator
==================================================

Scenario:
Machine M17 unavailable for 8 hours.

--------------------------------------------------
FACTORY IMPACT
--------------------------------------------------

Machine: M17
Station: A3
Line: A

Estimated capacity loss: 18.2%

Affected orders:
- O1047 [HIGH]
- O1048 [MEDIUM]
- O1051 [LOW]

High-priority orders affected: 1

Delivery risk: HIGH

--------------------------------------------------
ALTERNATIVE CAPACITY
--------------------------------------------------

Candidate 1:
Machine B7
Available capacity: 120 units

Candidate 2:
Machine C4
Available capacity: 90 units

--------------------------------------------------
Simulation completed.
==================================================
```

The actual values must come from generated data.

---

# 20. Testing Requirements

Write unit tests for:

## Data generation

Verify:

* Correct number of machines
* Correct number of lines
* Valid machine relationships
* Valid orders
* Reproducible generation

## Factory state

Verify:

* Counts are correct
* Machine statuses are correct
* Orders are correctly classified

## Dependencies

Verify:

```text
M17 → A3 → affected orders
```

## Machine failure

Test:

```text
8 hours downtime
```

and verify:

* affected orders
* capacity loss
* delivery risk
* alternative machines

## Edge cases

Test:

* Unknown machine ID
* Zero downtime
* Negative downtime
* Machine already down
* Machine under maintenance
* Downtime greater than operating hours
* No alternative capacity
* No affected orders

---

# 21. Error Handling

Use explicit exceptions.

Examples:

```python
UnknownMachineError
InvalidScenarioError
NoAlternativeCapacityError
InvalidDowntimeError
```

Do not silently return incorrect values.

---

# 22. Configuration

Put configurable parameters in:

```text
app/config.py
```

Examples:

```text
OPERATING_HOURS_PER_DAY
DELIVERY_RISK_THRESHOLD
DEFAULT_UTILIZATION
RANDOM_SEED
DATASET_SIZE
```

Avoid magic numbers throughout the code.

---

# 23. Logging

Add useful structured logging.

For example:

```text
INFO  Loading factory state
INFO  Simulating machine failure: M17
INFO  Identified 3 affected orders
INFO  Capacity loss calculated: 18.2%
INFO  Found 2 alternative machines
```

Avoid excessive logging.

---

# 24. README

Create a useful README containing:

1. Project purpose
2. Architecture
3. Setup
4. How to generate data
5. How to run simulation
6. How to run tests
7. Example output
8. Phase 1 limitations
9. Future phases

---

# 25. Important Design Rules

## Rule 1 — No hardcoded demo answer

The M17 scenario must be derived from the generated data.

## Rule 2 — Deterministic first

Phase 1 must work without AI.

## Rule 3 — Separate business logic

Keep simulation logic independent from CLI/UI.

## Rule 4 — Test everything important

Especially capacity and dependency calculations.

## Rule 5 — Keep APIs clean

Functions should later be callable as AI tools.

For example:

```python
get_factory_state()
get_machine_status()
get_affected_orders()
simulate_machine_failure()
find_alternative_capacity()
```

These functions will eventually become tools exposed to the Gemini/ADK agent.

## Rule 6 — Do not over-engineer

This is a hackathon prototype.

Prefer:

```text
simple + correct + extensible
```

over:

```text
complex + impressive-looking + fragile
```

---

# 26. Future Compatibility

The code created in Phase 1 will later be used by:

```text
Phase 2:
Alternative generation

Phase 3:
OR-Tools optimization

Phase 4:
Engineering knowledge / RAG

Phase 5:
Gemini + ADK agent

Phase 6:
Web UI

Phase 7:
Google Cloud deployment

Phase 8:
Predictive ML

Phase 9:
Additional scenarios
```

Therefore, design the Phase 1 APIs so they can become agent tools later.

---

# 27. Phase 1 Definition of Done

Phase 1 is complete ONLY when all of the following work:

### Data

* [ ] Synthetic automotive factory generated
* [ ] 3 production lines
* [ ] 12 stations
* [ ] 30+ machines
* [ ] 50+ production orders
* [ ] Materials and suppliers
* [ ] Maintenance data
* [ ] Production schedules

### Factory model

* [ ] Machine relationships
* [ ] Station relationships
* [ ] Production dependencies
* [ ] Factory state

### Simulation

* [ ] Machine downtime scenario
* [ ] Capacity loss calculation
* [ ] Affected order identification
* [ ] High-priority order identification
* [ ] Delivery risk calculation
* [ ] Alternative capacity discovery

### Engineering

* [ ] Unit tests
* [ ] Error handling
* [ ] Logging
* [ ] README
* [ ] Reproducible data generation

### Demo

This command must work:

```bash
python scripts/run_simulation.py
```

and produce a meaningful manufacturing impact analysis.

---

# 28. What NOT to Implement in Phase 1

Do NOT implement:

* Gemini
* Gemini API calls
* Google ADK
* RAG
* Vector databases
* Cloud deployment
* BigQuery
* Firebase
* React
* Next.js
* Agent orchestration
* Multi-agent system
* Predictive ML
* Production optimization
* Authentication
* Real-time streaming

Those belong to later phases.

---

# 29. Development Method

Work incrementally.

Before writing large amounts of code:

1. Inspect the repository.
2. Create the project structure.
3. Implement data models.
4. Implement data generation.
5. Run tests.
6. Implement factory state.
7. Run tests.
8. Implement dependency graph.
9. Run tests.
10. Implement machine failure simulation.
11. Run tests.
12. Implement CLI.
13. Run the complete test suite.
14. Update README.

After each meaningful step, verify that existing tests still pass.

Do not rewrite working components unnecessarily.

---

# 30. First Task

Start ONLY with the following:

### Task A

Create the project structure.

### Task B

Create the Python environment requirements.

### Task C

Implement the manufacturing data models.

### Task D

Implement the synthetic factory data generator.

### Task E

Generate the first dataset.

### Task F

Write tests validating the generated dataset.

### Task G

Run the tests.

Do NOT proceed to simulation until these tasks are working.

At the end, report:

```text
Phase 1A completed.

Files created:
...

Dataset generated:
...

Tests:
X passed / X failed

Next recommended task:
Factory State Engine
```

Do not implement future phases unless explicitly instructed.
