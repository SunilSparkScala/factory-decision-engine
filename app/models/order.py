from enum import Enum
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict

class OrderPriority(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class OrderStatus(str, Enum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    DELAYED = "DELAYED"
    CANCELLED = "CANCELLED"

class ProductionOrder(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    order_id: str
    product_model: str
    quantity: int
    priority: OrderPriority
    due_time: datetime
    assigned_line: str
    status: OrderStatus = OrderStatus.PLANNED
