"""
Executor — runs a single capability safely and returns a structured
CapabilityResult. This is the ONLY place capabilities are actually
invoked.

The Safety Layer (Phase 7) will sit between the Agent and the Executor.
For now, the executor performs existence + basic argument validation
and catches all exceptions so a bad capability can never crash the
agent loop.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from core.registry import CapabilityRegistry
from core.result import CapabilityResult

logger = logging.getLogger("jessy.executor")


class Executor:
    """Executes capabilities looked up from a CapabilityRegistry."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def execute(self, name: str, arguments: dict[str, Any]) -> CapabilityResult:
        """
        Execute the named capability with the given arguments.

        Always returns a CapabilityResult — never raises. Any failure
        (missing capability, bad arguments, runtime exception inside
        the capability) is captured and returned as a failed result.
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

        logger.info("Executing capability: %s args=%s", name, arguments)

        try:
            result = capability.function(**arguments)
        except Exception as exc:
            logger.exception("Capability '%s' raised an exception", name)
            return CapabilityResult.fail(
                action=name,
                error=f"{type(exc).__name__}: {exc}",
            )

        # Capabilities may return a CapabilityResult directly, or a plain
        # dict of data (in which case we wrap it as a success).
        if isinstance(result, CapabilityResult):
            return result

        if isinstance(result, dict):
            return CapabilityResult.ok(action=name, data=result)

        # Anything else (e.g. a plain string) is wrapped as success data.
        return CapabilityResult.ok(action=name, data={"result": result})
