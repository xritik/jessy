"""
The JESSY Agent Loop.

This is the heart of JESSY described in the master architecture:

    user message -> Groq -> tool_calls? -> Executor -> result
        -> back to Groq -> repeat until Groq gives a final answer
        or MAX_AGENT_STEPS is reached.

The agent never executes model-generated code. It only ever calls
capabilities that exist in the CapabilityRegistry, through the
Executor.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Optional

from core.executor import Executor
from core.groq_client import GroqClient
from core.registry import CapabilityRegistry
from core.result import AgentStepLog, CapabilityResult

logger = logging.getLogger("jessy.agent")

SYSTEM_PROMPT = """You are JESSY, a personal AI computer assistant that can \
control the user's Windows computer through a set of tools (capabilities). \

Behave like a calm, confident, slightly witty computer operator — not a \
generic chatbot. Be concise and natural in your final answers.

Formatting rules:
- Use only plain, standard ASCII punctuation: straight quotes ('), regular \
hyphens (-), and periods. Do NOT use curly quotes, em dashes, or other \
typographic/unicode punctuation.
- Keep responses in plain, simple sentences suitable for both text display \
and being spoken aloud by a text-to-speech engine later.

Rules:
- Only use the tools provided to you. Never claim to have done something \
you did not actually do with a tool.
- If a tool result indicates failure, acknowledge it honestly and decide \
whether to try a reasonable alternative or ask the user for clarification.
- If a request is ambiguous, ask ONE sharp clarifying question instead of \
guessing.
- Once you have enough information and no more tools are needed, respond \
with a final, natural-language answer summarizing what actually happened.
"""



class Agent:
    """Runs the multi-step tool-calling loop for a single user request."""

    def __init__(
        self,
        groq_client: GroqClient,
        registry: CapabilityRegistry,
        executor: Executor,
    ) -> None:
        self.groq_client = groq_client
        self.registry = registry
        self.executor = executor
        self.max_steps = int(os.getenv("MAX_AGENT_STEPS", "12"))

    def run(
        self,
        user_message: str,
        history: Optional[list[dict[str, Any]]] = None,
        on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
    ) -> tuple[str, list[AgentStepLog]]:
        """
        Run the agent loop for a single user message.

        history: prior conversation turns (role/content dicts), excluding
                 the system prompt — kept short by the caller (Phase 8
                 Memory/Context will manage this properly).
        on_event: optional callback(event_type, payload) for real-time
                  GUI events (wired to WebSocket in Phase 10). Safe to
                  leave as None for now.

        Returns (final_response_text, step_logs).
        """

        def emit(event_type: str, payload: dict[str, Any]) -> None:
            if on_event:
                try:
                    on_event(event_type, payload)
                except Exception:
                    logger.exception("on_event callback failed for %s", event_type)

        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        tools = self.registry.to_groq_tools()
        step_logs: list[AgentStepLog] = []

        emit("agent_started", {"message": user_message})

        for step in range(1, self.max_steps + 1):
            emit("agent_thinking", {"step": step})

            response = self.groq_client.chat(messages=messages, tools=tools or None)
            choice = response.choices[0].message

            # Groq wants to call one or more tools.
            if getattr(choice, "tool_calls", None):
                messages.append(choice)

                for tool_call in choice.tool_calls:
                    cap_name = tool_call.function.name
                    try:
                        cap_args = json.loads(tool_call.function.arguments or "{}")
                    except json.JSONDecodeError:
                        cap_args = {}

                    emit(
                        "capability_started",
                        {"capability": cap_name, "arguments": cap_args},
                    )

                    result: CapabilityResult = self.executor.execute(cap_name, cap_args)

                    if result.success:
                        emit(
                            "capability_completed",
                            {"capability": cap_name, "data": result.data},
                        )
                    else:
                        emit(
                            "capability_failed",
                            {"capability": cap_name, "error": result.error},
                        )

                    step_logs.append(
                        AgentStepLog(
                            step_number=step,
                            capability=cap_name,
                            arguments=cap_args,
                            result=result,
                        )
                    )

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": cap_name,
                            "content": result.model_dump_json(),
                        }
                    )

                # Continue the loop so Groq can see the tool results and
                # decide the next action or produce a final answer.
                continue

            # No tool calls: Groq has produced a final answer.
            final_text = choice.content or ""
            step_logs.append(
                AgentStepLog(step_number=step, note="final_answer")
            )
            emit("agent_completed", {"response": final_text})
            return final_text, step_logs

        # Loop exhausted without a final answer.
        logger.warning("Agent reached MAX_AGENT_STEPS (%s) without finishing.", self.max_steps)
        emit("agent_error", {"error": "max_steps_reached"})
        fallback = (
            "I've hit my step limit working on this. Here's where things stand — "
            "let me know if you'd like me to continue."
        )
        return fallback, step_logs
