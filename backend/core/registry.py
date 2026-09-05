"""
CapabilityRegistry — the single source of truth for what JESSY can do.

Capabilities are plain Python callables. They know nothing about Groq.
The registry's job is to:
  - store capabilities under a unique name
  - store a JSON-schema description of their arguments (for Groq)
  - retrieve capabilities by name for execution
  - generate the full "tools" array Groq needs for tool-calling
  - validate that a requested capability actually exists

This class has NO knowledge of filesystem, Windows, browser, etc.
Those are registered into it from capabilities/*.py in later steps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger("jessy.registry")


@dataclass
class RegisteredCapability:
    """Internal storage record for one capability."""

    name: str
    function: Callable[..., Any]
    description: str
    parameters: dict[str, Any]
    risk: str = "safe"  # "safe" | "confirm" | "blocked" — used fully in Phase 7


class CapabilityRegistry:
    """Holds all capabilities JESSY can execute."""

    def __init__(self) -> None:
        self._capabilities: dict[str, RegisteredCapability] = {}

    def register(
        self,
        name: str,
        function: Callable[..., Any],
        description: str,
        parameters: dict[str, Any],
        risk: str = "safe",
    ) -> None:
        """
        Register a new capability.

        Raises ValueError if a capability with the same name already exists,
        to prevent silent overwrites.
        """
        if name in self._capabilities:
            raise ValueError(f"Capability '{name}' is already registered.")

        self._capabilities[name] = RegisteredCapability(
            name=name,
            function=function,
            description=description,
            parameters=parameters,
            risk=risk,
        )
        logger.info("Registered capability: %s (risk=%s)", name, risk)

    def unregister(self, name: str) -> None:
        """Remove a capability by name. No-op if it doesn't exist."""
        if name in self._capabilities:
            del self._capabilities[name]
            logger.info("Unregistered capability: %s", name)

    def get(self, name: str) -> RegisteredCapability:
        """
        Retrieve a registered capability by name.
        Raises KeyError if it does not exist.
        """
        if name not in self._capabilities:
            raise KeyError(f"Capability '{name}' is not registered.")
        return self._capabilities[name]

    def exists(self, name: str) -> bool:
        """Check whether a capability name is registered."""
        return name in self._capabilities

    def all(self) -> list[RegisteredCapability]:
        """Return all registered capabilities."""
        return list(self._capabilities.values())

    def to_groq_tools(self) -> list[dict[str, Any]]:
        """
        Build the `tools` array in the exact shape Groq's chat completion
        API expects for function/tool calling.
        """
        tools = []
        for cap in self._capabilities.values():
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": cap.name,
                        "description": cap.description,
                        "parameters": cap.parameters,
                    },
                }
            )
        return tools


# Single shared registry instance for the whole backend process.
registry = CapabilityRegistry()
