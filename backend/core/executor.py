"""
Executor — runs a single capability safely and returns a structured
CapabilityResult. This is the ONLY place capabilities are actually
invoked.

The Safety Layer sits here, between lookup and execution:
    1. Does the capability exist?
    2. Do the provided arguments match its signature?
    3. Is the action safe, confirmation-required, or blocked?
    4. Only then is the real function called.

BLOCKED actions never run. CONFIRMATION_REQUIRED actions only run when
the caller passes confirmed=True (i.e. the user has already agreed),
otherwise a result describing what needs confirmation is returned
instead of executing anything.

Every code path returns a CapabilityResult — this method never raises.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from core.registry import CapabilityRegistry
from core.result import CapabilityResult, RiskLevel
from core.safety import SafetyLayer

logger = logging.getLogger("jessy.executor")


class Executor:
    """Executes capabilities looked up from a CapabilityRegistry."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        confirmed: bool = False,
    ) -> CapabilityResult:
        """
        Execute the named capability with the given arguments.

        confirmed: set True when the caller (agent/GUI) has already
                   obtained explicit user confirmation for an action
                   that the Safety Layer marked as CONFIRMATION_REQUIRED.

        Always returns a CapabilityResult — never raises. Any failure
        (missing capability, bad arguments, blocked/unconfirmed action,
        runtime exception inside the capability) is captured and
        returned as a failed result.
        """
        if not self.registry.exists(name):
            logger.warning("Capability not found: %s", name)
            return CapabilityResult.fail(
                action=name,
                error=f"Capability '{name}' does not exist.",
            )

        capability = self.registry.get(name)

        # Validate that provided arguments match the function signature
        # before calling it, so we fail with a clear message instead of
        # a raw TypeError.
        try:
            sig = inspect.signature(capability.function)
            sig.bind_partial(**arguments)
        except TypeError as exc:
            logger.warning("Invalid arguments for %s: %s", name, exc)
            return CapabilityResult.fail(
                action=name,
                error=f"Invalid arguments for '{name}': {exc}",
            )

# --- Safety Layer check --------------------------------------
        safety_result = SafetyLayer.validate(name, arguments)
        risk_level: RiskLevel = safety_result["risk_level"]
        logger.info(
            "Safety check for %s: risk_level=%s, allowed=%s",
            name, risk_level, safety_result["allowed"],
        )

        if risk_level == RiskLevel.BLOCKED:
            logger.warning("Blocked capability call: %s args=%s", name, arguments)
            return CapabilityResult.fail(
                action=name,
                error=safety_result["message"],
                risk_level=RiskLevel.BLOCKED,
            )

        if risk_level == RiskLevel.CONFIRMATION_REQUIRED and not confirmed:
            logger.info("Capability '%s' requires confirmation; not executed.", name)
            return CapabilityResult.fail(
                action=name,
                error=safety_result["message"],
                risk_level=RiskLevel.CONFIRMATION_REQUIRED,
            )
# ---------------------------------------------------------------

        logger.info("Executing capability: %s args=%s (risk=%s)", name, arguments, risk_level)

        try:
            result = capability.function(**arguments)
        except Exception as exc:
            logger.exception("Capability '%s' raised an exception", name)
            return CapabilityResult.fail(
                action=name,
                error=f"{type(exc).__name__}: {exc}",
                risk_level=risk_level,
            )

        # Capabilities may return a CapabilityResult directly, or a plain
        # dict of data (in which case we wrap it as a success).
        if isinstance(result, CapabilityResult):
            return result

        if isinstance(result, dict):
            return CapabilityResult.ok(action=name, data=result, risk_level=risk_level)

        # Anything else (e.g. a plain string) is wrapped as success data.
        return CapabilityResult.ok(action=name, data={"result": result}, risk_level=risk_level)
