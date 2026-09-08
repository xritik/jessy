"""
Structured result contract for every capability execution.

Every capability MUST return a CapabilityResult. This guarantees the
agent never fabricates success and always has a real, inspectable
outcome to reason about or show to the user.

RiskLevel lives here (not duplicated in safety.py) so both the Safety
Layer and every CapabilityResult share a single source of truth.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class RiskLevel(Enum):
    """Classification of action risk."""
    SAFE = "safe"
    CONFIRMATION_REQUIRED = "confirmation_required"
    BLOCKED = "blocked"


class CapabilityResult(BaseModel):
    """
    The universal result shape returned by every capability execution.

    success:    whether the action actually completed as intended.
    action:     the capability name that was executed (e.g. "create_file").
    data:       arbitrary structured payload on success (paths, values, etc).
    error:      a short human-readable error message on failure. Must be
                None on success and must be set (non-empty) on failure.
    risk_level: the risk classification assigned by the Safety Layer for
                this action. Defaults to SAFE for capabilities with no
                specific rule.
    """

    success: bool
    action: str
    data: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    risk_level: RiskLevel = Field(
        default=RiskLevel.SAFE, description="Risk level of the action"
    )

    @classmethod
    def ok(
        cls,
        action: str,
        data: Optional[dict[str, Any]] = None,
        risk_level: RiskLevel = RiskLevel.SAFE,
    ) -> "CapabilityResult":
        """Convenience constructor for a successful result."""
        return cls(success=True, action=action, data=data, error=None, risk_level=risk_level)

    @classmethod
    def fail(
        cls,
        action: str,
        error: str,
        risk_level: RiskLevel = RiskLevel.SAFE,
    ) -> "CapabilityResult":
        """Convenience constructor for a failed result."""
        return cls(success=False, action=action, data=None, error=error, risk_level=risk_level)


class AgentStepLog(BaseModel):
    """
    Internal record of a single step taken during an agent run.
    Used for building the final response and for future action-history
    display in the GUI (Phase 11 / ActionFeed).
    """

    step_number: int
    capability: Optional[str] = None
    arguments: Optional[dict[str, Any]] = None
    result: Optional[CapabilityResult] = None
    note: Optional[str] = Field(
        default=None,
        description="Free-text note for non-capability steps, e.g. 'model returned final answer'.",
    )
