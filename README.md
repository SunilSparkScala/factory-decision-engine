# Factory Decision Engine

> Before you change the factory, understand what happens next.

AI-powered manufacturing decision-support system for simulating factory operations and evaluating scenario impacts.

## Phase 1 — Manufacturing Simulation Foundation

Phase 1 provides the deterministic synthetic manufacturing environment and scenario simulation baseline.

### Directory Structure

```text
FactoryDecisionEngine/
├── README.md
├── PHASE_01_INSTRUCTIONS.md
├── requirements.txt
├── .gitignore
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── machine.py
│   │   ├── production.py
│   │   ├── order.py
│   │   ├── inventory.py
│   │   └── maintenance.py
│   ├── data/
│   │   ├── __init__.py
│   │   ├── database.py
│   │   └── generator.py
│   ├── factory/
│   │   ├── __init__.py
│   │   ├── state.py
│   │   ├── topology.py
│   │   └── dependencies.py
│   └── simulation/
│       ├── __init__.py
│       ├── scenarios.py
│       ├── impact.py
│       └── machine_failure.py
├── data/
│   ├── raw/
│   └── generated/
├── scripts/
│   ├── generate_data.py
│   └── run_simulation.py
└── tests/
    └── test_data_generation.py
```

### Installation

```bash
pip install -r requirements.txt
```

### Generate Synthetic Dataset

```bash
python scripts/generate_data.py
```

### Run Tests

```bash
python -m pytest tests/test_data_generation.py
```
