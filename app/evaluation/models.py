import math
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, ConfigDict, field_validator
from app.recovery.models import RecoveryPlan

class ObjectiveWeights(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    delivery_weight: float = 0.40
    cost_weight: float = 0.20
    quality_weight: float = 0.20
    energy_weight: float = 0.10
    risk_weight: float = 0.10

    @field_validator("delivery_weight", "cost_weight", "quality_weight", "energy_weight", "risk_weight")
    @classmethod
    def validate_single_weight(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v):
            raise ValueError("Objective weight must be a finite number.")
        if v < 0:
            raise ValueError(f"Objective weight must be non-negative, got {v}")
        return v

    def validate_weights_sum(self) -> None:
        total = self.delivery_weight + self.cost_weight + self.quality_weight + self.energy_weight + self.risk_weight
        if total <= 0:
            raise ValueError("Sum of objective weights must be greater than zero.")

    def normalized_weights(self) -> Dict[str, float]:
        self.validate_weights_sum()
        total = self.delivery_weight + self.cost_weight + self.quality_weight + self.energy_weight + self.risk_weight
        return {
            "delivery": round(self.delivery_weight / total, 4),
            "cost": round(self.cost_weight / total, 4),
            "quality": round(self.quality_weight / total, 4),
            "energy": round(self.energy_weight / total, 4),
            "risk": round(self.risk_weight / total, 4),
        }

class PlanEvaluationMetrics(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    delivery_impact_score: float
    cost_impact_usd: float
    quality_penalty_score: float
    energy_consumption_kwh: float
    risk_impact_score: float
    overtime_hours: float
    recovered_capacity_units: float
    remaining_capacity_gap_units: float

class NormalizedMetrics(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    delivery: float
    cost: float
    quality: float
    energy: float
    risk: float

class EvaluatedRecoveryPlan(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    plan: RecoveryPlan
    raw_metrics: PlanEvaluationMetrics
    normalized_scores: NormalizedMetrics
    weighted_objective_score: float
    is_feasible: bool
    assumptions: List[str] = []

class OptimizationResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    selected_plan: Optional[EvaluatedRecoveryPlan]
    evaluated_plans: List[EvaluatedRecoveryPlan]
    weights_used: ObjectiveWeights
    comparison_summary: str
