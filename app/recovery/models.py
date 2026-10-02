from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, ConfigDict

class RecoveryStrategyType(str, Enum):
    MACHINE_TRANSFER = "MACHINE_TRANSFER"
    RESEQUENCE = "RESEQUENCE"
    OVERTIME = "OVERTIME"

class RecoveryAction(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    action_id: str
    strategy_type: RecoveryStrategyType
    target_machine_id: Optional[str] = None
    affected_order_ids: List[str] = []
    recovered_capacity_units: float = 0.0
    details: Dict[str, Any] = {}

class FeasibilityCheck(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    is_feasible: bool
    checked_constraints: List[str] = []
    infeasible_reasons: List[str] = []

class RecoveryPlan(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    plan_id: str
    strategy_type: RecoveryStrategyType
    description: str
    actions: List[RecoveryAction] = []
    affected_order_ids: List[str] = []
    recovered_capacity_units: float = 0.0
    remaining_capacity_gap_units: float = 0.0
    feasibility: FeasibilityCheck
    explanation: str
