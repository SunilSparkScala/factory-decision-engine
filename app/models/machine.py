from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict

class MachineStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    MAINTENANCE = "MAINTENANCE"
    DOWN = "DOWN"

class Factory(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    factory_id: str
    name: str
    location: str
    operating_hours: float = 24.0

class ProductionLine(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    line_id: str
    name: str
    capacity_per_hour: float
    supported_models: List[str]
    status: str = "OPERATIONAL"

class Station(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    station_id: str
    line_id: str
    name: str
    operation_type: str
    capacity_per_hour: float

class Machine(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    machine_id: str
    line_id: str
    station_id: str
    machine_type: str
    cycle_time_seconds: float
    capacity_per_hour: float
    status: MachineStatus = MachineStatus.HEALTHY
    health_score: float = Field(default=100.0, ge=0.0, le=100.0)
