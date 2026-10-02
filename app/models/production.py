from enum import Enum
from datetime import datetime
from pydantic import BaseModel, ConfigDict

class OperationType(str, Enum):
    STAMPING = "STAMPING"
    WELDING = "WELDING"
    PAINTING = "PAINTING"
    ASSEMBLY = "ASSEMBLY"
    INSPECTION = "INSPECTION"

class ProductionSchedule(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    schedule_id: str
    order_id: str
    machine_id: str
    station_id: str
    start_time: datetime
    end_time: datetime
    planned_quantity: int
