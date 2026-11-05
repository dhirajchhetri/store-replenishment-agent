"""Errors raised by the replenishment flow."""


class ReplenishmentError(Exception):
    """Base error for this package."""


class NotReadyError(ReplenishmentError):
    """A run was requested before data or a model was available."""


class PlanValidationError(ReplenishmentError):
    """A pick list did not match the rule engine."""


class ToolBudgetExceeded(ReplenishmentError):
    """The planner asked for more tool calls than the run allows."""


class ConflictError(ReplenishmentError):
    """The pick list is not in the state the action requires."""
