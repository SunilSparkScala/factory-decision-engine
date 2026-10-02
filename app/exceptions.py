class FactoryDecisionEngineError(Exception):
    """Base exception for Factory Decision Engine."""
    pass

class UnknownMachineError(FactoryDecisionEngineError):
    """Raised when a requested machine_id is not found in the factory state."""
    pass

class UnknownStationError(FactoryDecisionEngineError):
    """Raised when a requested station_id is not found in the factory state."""
    pass

class UnknownLineError(FactoryDecisionEngineError):
    """Raised when a requested line_id is not found in the factory state."""
    pass

class UnknownOrderError(FactoryDecisionEngineError):
    """Raised when a requested order_id is not found in the factory state."""
    pass

class InvalidDowntimeError(FactoryDecisionEngineError):
    """Raised when downtime_hours is invalid (e.g., <= 0)."""
    pass

class InvalidScenarioError(FactoryDecisionEngineError):
    """Raised when an unsupported scenario type or invalid scenario definition is provided."""
    pass

class NoAlternativeCapacityError(FactoryDecisionEngineError):
    """Raised when no alternative capacity is available."""
    pass

class UnknownDocumentError(FactoryDecisionEngineError):
    """Raised when a requested knowledge document_id is not found."""
    pass
