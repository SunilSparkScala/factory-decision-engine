import os
from pathlib import Path
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
GENERATED_DATA_DIR = DATA_DIR / "generated"
DB_PATH = GENERATED_DATA_DIR / "factory.db"

class Config(BaseModel):
    OPERATING_HOURS_PER_DAY: float = 24.0
    DELIVERY_RISK_HIGH_HOURS: float = 8.0
    MAX_OVERTIME_HOURS_PER_MACHINE: float = 12.0
    
    # Synthetic Evaluation Cost & Energy Constants
    OVERTIME_COST_PER_HOUR: float = 50.0
    TRANSFER_BASE_COST: float = 100.0
    RESEQUENCE_BASE_COST: float = 30.0
    UNRECOVERED_GAP_COST_PER_UNIT: float = 25.0
    ENERGY_KWH_PER_UNIT: float = 2.5
    ENERGY_COST_PER_KWH: float = 0.15
    
    DEFAULT_UTILIZATION: float = 0.85
    RANDOM_SEED: int = 42
    
    # Dataset size parameters
    NUM_LINES: int = 3
    NUM_STATIONS: int = 12
    NUM_MACHINES: int = 36
    NUM_ORDERS: int = 50
    NUM_MATERIALS: int = 25
    NUM_SUPPLIERS: int = 15

    DB_PATH: Path = DB_PATH
    GENERATED_DATA_DIR: Path = GENERATED_DATA_DIR

config = Config()
