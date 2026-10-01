"""
The JESSY Agent Loop.

This is the heart of JESSY described in the master architecture:

    user message -> Groq -> tool_calls? -> Executor -> result
        -> back to Groq -> repeat until Groq gives a final answer
        or MAX_AGENT_STEPS is reached.

The agent never executes model-generated code. It only ever calls
capabilities that exist in the CapabilityRegistry, through the
Executor.

Model split:
    - Tool-routing / argument-extraction steps use
      groq_client.tool_model.
    - Final natural-language response uses groq_client.model.

Confirmation handling:
    - CONFIRMATION_REQUIRED actions are stored per session.
    - The next clear yes/no reply is handled directly without another
      model round-trip.
    - "yes,", "yes.", "Yes!" etc. are normalized before matching.

Placeholder guard:
    - Prevents unresolved template values such as "<username>"
      from reaching filesystem capabilities.

False-completion guard:
    - Prevents the final model from claiming that a file was written,
      created, or modified when no mutating capability actually
      succeeded.

Working context:
    - Tracks the current file/folder/project/application/browser/tab.
    - Updated after capability execution.
    - Injected into the model when useful.

Action history:
    - Every capability attempt is recorded in core/action_log.py.

Voice state:
    - run() sets the session to THINKING and always returns it to IDLE.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable, Optional

from agent.context import WorkingContext, get_context
from core.executor import Executor
from core.groq_client import GroqClient
from core.registry import CapabilityRegistry
from core.result import AgentStepLog, CapabilityResult, RiskLevel
from core import action_log
from voice.state import VoiceState, set_state as set_voice_state


logger = logging.getLogger("jessy.agent")


_OVERWRITE_CAPABLE_CAPABILITIES = {"write_file"}


SYSTEM_PROMPT = """You are JARVIS, a personal AI computer assistant that can \
control the user's Windows computer through a set of tools (capabilities).

Behave like a calm, confident, slightly witty computer operator - not a \
generic chatbot. Be concise and natural in your final answers.

User details:
- The user's name is Ritik.
- The user's favorite song is "Bepanah Pyaar". If asked to play "my \
favorite song" or similar, use this song.

Formatting rules:
- Use only plain, standard ASCII punctuation: straight quotes ('), regular \
hyphens (-), and periods. Do NOT use curly quotes, em dashes, or other \
typographic/unicode punctuation.
- Keep responses in plain, simple sentences suitable for both text display \
and being spoken aloud by a text-to-speech engine later.

Browser behavior:
- To open a browser application itself with no specific page in mind \
(e.g. "open chrome", "open edge", "open a new chrome window"), use \
open_application with the browser's name.
- To open a URL or a new tab (e.g. "open a new tab in chrome", "go to \
youtube.com"), use open_url. Pass browser="chrome" or browser="edge" \
whenever the user names one, so tab-vs-window is handled correctly. Only \
set new_window=true if the user explicitly wants a separate new window \
rather than a tab.
- If no specific site is mentioned for open_url, default to google.com.
- To switch/cycle to the next or previous browser tab, use \
switch_tab_direction with browser set to the named browser. Do NOT use \
hotkey for this - hotkey sends input to whatever window currently has \
OS focus, which may not be the browser at all.
- To navigate the CURRENTLY ACTIVE tab to a new page after switching tabs \
or otherwise, use navigate_active_tab with the same browser specified.

Editor and file behavior:
- By default, when the user asks to create a NEW file or NEW folder, do \
it VISIBLY: use create_folder_visual for new folders and \
create_file_visual_in_vscode for new files.
- Only use silent write_file/create_directory tools when the user explicitly \
asks for background or silent execution.
- If the user does not specify a folder/location for a new file or \
folder, default to the Desktop ('~/Desktop').
- create_folder_visual and create_file_visual_in_vscode already open the \
correct window themselves. Do not call open_application or open_folder \
separately beforehand.
- write_file only writes bytes to disk and never opens the file as a \
visible tab. If you use write_file for a VS Code task, call \
open_file_in_vscode with that same path immediately after write_file succeeds.
- If the user asks to open a folder in VS Code, use the appropriate \
capability to launch VS Code on that folder.
- NEVER invent or type out a full literal filesystem path yourself, and \
NEVER include placeholder tokens such as "<username>", "<user>", "<path>", \
etc. in any tool argument. Use "~" or "~/Desktop" when appropriate.
- If a previous tool result supplied an exact path, reuse that exact path.
- If the user's message describes content being added or written in a way \
that is ambiguous between "please do this now" and "I already did this \
myself", and no tool call in this conversation has actually written that \
content yet, treat it as an instruction to perform the write now.

Focus and window targeting:
- The generic hotkey and press_key tools send input to whatever window \
currently has OS-level focus.
- Before using them for a named application, prefer a dedicated \
focus-safe capability when one exists.
- Only fall back to hotkey/press_key/type_text for generic actions on \
whatever window the user is actively using.

Confirmation behavior:
- Some actions require explicit user confirmation.
- If a tool result says confirmation is required, do NOT call another tool \
to ask for confirmation. Simply return a short confirmation question.
- The system handles the user's yes/no reply.
- Never call a tool name that was not explicitly provided.

Rules:
- Only use the tools provided to you.
- Never claim to have done something you did not actually do.
- If a tool result indicates failure, acknowledge it honestly.
- If a request is ambiguous, ask ONE sharp clarifying question.
- Once enough information is available and no more tools are needed, \
return a concise natural-language answer.
"""


_pending_confirmations: dict[str, dict[str, Any]] = {}


_AFFIRMATIVE_PATTERN = re.compile(
    r"^(y|yes|yeah|yep|yup|sure|ok|okay|confirm|confirmed|"
    r"go ahead|do it|proceed|please do|affirmative)$",
    re.IGNORECASE,
)

_NEGATIVE_PATTERN = re.compile(
    r"^(n|no|nope|nah|cancel|stop|don't|do not|never ?mind)$",
    re.IGNORECASE,
)


_EDGE_PUNCT_RE = re.compile(r"^[\s.,!?;:]+|[\s.,!?;:]+$")


_PLACEHOLDER_RE = re.compile(r"<[^<>\s]+>")


_MUTATING_CAPABILITIES = {
    "write_file",
    "append_to_file",
    "create_file_visual_in_vscode",
    "create_folder_visual",
    "create_directory",
}


_FALSE_COMPLETION_PHRASES = (
    "i've added",
    "i have added",
    "i added",
    "i've written",
    "i have written",
    "i wrote",
    "has been added",
    "has been written",
    "has been created",
    "i've created",
    "i have created",
    "i created",
)


def _normalize_reply(text: str) -> str:
    """Normalize whitespace and punctuation around yes/no replies."""
    return _EDGE_PUNCT_RE.sub("", text.strip())


def _find_placeholder(value: Any) -> Optional[str]:
    """Find an unresolved <placeholder> recursively."""
    if isinstance(value, str):
        match = _PLACEHOLDER_RE.search(value)
        return match.group(0) if match else None

    if isinstance(value, dict):
        for item in value.values():
            found = _find_placeholder(item)
            if found:
                return found

    if isinstance(value, list):
        for item in value:
            found = _find_placeholder(item)
            if found:
                return found

    return None


class Agent:
    """Runs the multi-step tool-calling loop for one user request."""

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

    @staticmethod
    def _describe_confirmed_result(
        cap_name: str,
        cap_args: dict[str, Any],
        result: CapabilityResult,
    ) -> str:
        pretty_name = cap_name.replace("_", " ")

        if not result.success:
            return f"I tried to {pretty_name} but it failed: {result.error}"

        target = cap_args.get("path") or (result.data or {}).get("path")

        if target:
            return f"Done - {pretty_name} completed on '{target}'."

        return f"Done - {pretty_name} completed successfully."

    @staticmethod
    def _describe_confirmation_request(
        cap_name: str,
        cap_args: dict[str, Any],
    ) -> str:
        if cap_name == "call_contact":
            contact = cap_args.get("name", "this contact")
            return f"Do you want me to call {contact}?"

        pretty_name = cap_name.replace("_", " ")
        target = cap_args.get("path") or cap_args.get("source")

        if target:
            return (
                f"Do you want me to {pretty_name} on "
                f"'{target}'? This may be irreversible."
            )

        return (
            f"Do you want me to {pretty_name}? "
            f"This may be irreversible."
        )

    def run(
        self,
        user_message: str,
        history: Optional[list[dict[str, Any]]] = None,
        on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
        session_id: str = "default",
    ) -> tuple[str, list[AgentStepLog]]:

        def emit(event_type: str, payload: dict[str, Any]) -> None:
            if on_event:
                try:
                    on_event(event_type, payload)
                except Exception:
                    logger.exception(
                        "on_event callback failed for %s",
                        event_type,
                    )

        working_context: WorkingContext = get_context(session_id)

        set_voice_state(session_id, VoiceState.THINKING)

        try:
            # ---------------------------------------------------------
            # Pending confirmation
            # ---------------------------------------------------------
            pending = _pending_confirmations.get(session_id)
            normalized_message = _normalize_reply(user_message)

            logger.info(
                "run() session=%s message=%r normalized=%r pending=%s",
                session_id,
                user_message,
                normalized_message,
                pending,
            )

            if pending is not None:

                # -----------------------------------------------------
                # YES
                # -----------------------------------------------------
                if _AFFIRMATIVE_PATTERN.match(normalized_message):
                    _pending_confirmations.pop(session_id, None)

                    cap_name = pending["capability"]
                    cap_args = dict(pending["arguments"])

                    if cap_name in _OVERWRITE_CAPABLE_CAPABILITIES:
                        cap_args["overwrite"] = True

                    emit(
                        "capability_started",
                        {
                            "capability": cap_name,
                            "arguments": cap_args,
                        },
                    )

                    result = self.executor.execute(
                        cap_name,
                        cap_args,
                        confirmed=True,
                    )

                    if result.success:
                        emit(
                            "capability_completed",
                            {
                                "capability": cap_name,
                                "data": result.data,
                            },
                        )
                    else:
                        emit(
                            "capability_failed",
                            {
                                "capability": cap_name,
                                "error": result.error,
                            },
                        )

                    working_context.update_from_result(
                        cap_name,
                        cap_args,
                        result,
                    )

                    action_log.record(
                        cap_name,
                        cap_args,
                        result,
                        session_id=session_id,
                    )

                    final_text = self._describe_confirmed_result(
                        cap_name,
                        cap_args,
                        result,
                    )

                    step_log = AgentStepLog(
                        step_number=1,
                        capability=cap_name,
                        arguments=cap_args,
                        result=result,
                    )

                    emit(
                        "agent_completed",
                        {"response": final_text},
                    )

                    return final_text, [step_log]

                # -----------------------------------------------------
                # NO
                # -----------------------------------------------------
                if _NEGATIVE_PATTERN.match(normalized_message):
                    _pending_confirmations.pop(session_id, None)

                    action_log.record(
                        pending["capability"],
                        pending["arguments"],
                        result=None,
                        session_id=session_id,
                        status_override="cancelled",
                    )

                    final_text = (
                        "Okay, I won't do that. "
                        "Let me know if you'd like something else."
                    )

                    emit(
                        "agent_completed",
                        {"response": final_text},
                    )

                    return final_text, [
                        AgentStepLog(
                            step_number=1,
                            note="confirmation_declined",
                        )
                    ]

                # Not yes/no. Treat it as a new request.
                _pending_confirmations.pop(session_id, None)

            # ---------------------------------------------------------
            # Build model messages
            # ---------------------------------------------------------
            messages: list[dict[str, Any]] = [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                }
            ]

            context_summary = working_context.as_prompt_context()

            if context_summary:
                messages.append(
                    {
                        "role": "system",
                        "content": context_summary,
                    }
                )

            if history:
                messages.extend(history)

            messages.append(
                {
                    "role": "user",
                    "content": user_message,
                }
            )

            # IMPORTANT:
            # CapabilityRegistry exposes to_groq_tools(), not
            # openai_tools().
            tools = self.registry.to_groq_tools()

            step_logs: list[AgentStepLog] = []

            emit(
                "agent_started",
                {"message": user_message},
            )

            # ---------------------------------------------------------
            # Agent loop
            # ---------------------------------------------------------
            for step in range(1, self.max_steps + 1):

                emit(
                    "agent_thinking",
                    {"step": step},
                )

                # Tool-routing model.
                response = self.groq_client.chat(
                    messages=messages,
                    tools=tools or None,
                    model=self.groq_client.tool_model,
                )

                choice = response.choices[0].message

                # -----------------------------------------------------
                # Tool calls
                # -----------------------------------------------------
                if getattr(choice, "tool_calls", None):

                    messages.append(choice)

                    for tool_call in choice.tool_calls:

                        cap_name = tool_call.function.name

                        try:
                            cap_args = json.loads(
                                tool_call.function.arguments or "{}"
                            )
                        except json.JSONDecodeError:
                            cap_args = {}

                        emit(
                            "capability_started",
                            {
                                "capability": cap_name,
                                "arguments": cap_args,
                            },
                        )

                        # ---------------------------------------------
                        # Placeholder protection
                        # ---------------------------------------------
                        placeholder = _find_placeholder(cap_args)

                        if placeholder:
                            result = CapabilityResult(
                                success=False,
                                action=cap_name,
                                error=(
                                    "Argument contains an unresolved "
                                    f"placeholder '{placeholder}'. "
                                    "Use '~' for the home directory, "
                                    "or the exact path from a previous "
                                    "tool result, instead of a template value."
                                ),
                            )

                            logger.warning(
                                "Blocked tool call %s for session %s: "
                                "placeholder %s found in arguments %s",
                                cap_name,
                                session_id,
                                placeholder,
                                cap_args,
                            )

                        else:
                            result = self.executor.execute(
                                cap_name,
                                cap_args,
                            )

                        # ---------------------------------------------
                        # Capability result events
                        # ---------------------------------------------
                        if result.success:
                            emit(
                                "capability_completed",
                                {
                                    "capability": cap_name,
                                    "data": result.data,
                                },
                            )
                        else:
                            emit(
                                "capability_failed",
                                {
                                    "capability": cap_name,
                                    "error": result.error,
                                },
                            )

                            # -----------------------------------------
                            # Confirmation required
                            # -----------------------------------------
                            if (
                                result.risk_level
                                == RiskLevel.CONFIRMATION_REQUIRED
                            ):
                                _pending_confirmations[session_id] = {
                                    "capability": cap_name,
                                    "arguments": cap_args,
                                }

                                logger.info(
                                    "Stored pending confirmation for "
                                    "session %s: %s %s",
                                    session_id,
                                    cap_name,
                                    cap_args,
                                )

                                working_context.update_from_result(
                                    cap_name,
                                    cap_args,
                                    result,
                                )

                                action_log.record(
                                    cap_name,
                                    cap_args,
                                    result,
                                    session_id=session_id,
                                    status_override="proposed",
                                )

                                step_logs.append(
                                    AgentStepLog(
                                        step_number=step,
                                        capability=cap_name,
                                        arguments=cap_args,
                                        result=result,
                                    )
                                )

                                confirmation_question = (
                                    self._describe_confirmation_request(
                                        cap_name,
                                        cap_args,
                                    )
                                )

                                emit(
                                    "agent_completed",
                                    {
                                        "response": confirmation_question,
                                    },
                                )

                                return (
                                    confirmation_question,
                                    step_logs,
                                )

                        # ---------------------------------------------
                        # Working context + action history
                        # ---------------------------------------------
                        working_context.update_from_result(
                            cap_name,
                            cap_args,
                            result,
                        )

                        action_log.record(
                            cap_name,
                            cap_args,
                            result,
                            session_id=session_id,
                        )

                        step_logs.append(
                            AgentStepLog(
                                step_number=step,
                                capability=cap_name,
                                arguments=cap_args,
                                result=result,
                            )
                        )

                        # ---------------------------------------------
                        # Return tool result to Groq
                        # ---------------------------------------------
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "name": cap_name,
                                "content": result.model_dump_json(),
                            }
                        )

                    # Groq sees the tool result and decides the next step.
                    continue

                # -----------------------------------------------------
                # No tool calls - generate final answer
                # -----------------------------------------------------
                try:
                    final_response = self.groq_client.chat(
                        messages=messages,
                        model=self.groq_client.model,
                    )

                    final_text = (
                        final_response.choices[0].message.content or ""
                    )

                except Exception:
                    logger.exception(
                        "Final answer generation failed; "
                        "falling back to a safe summary."
                    )

                    final_text = (
                        "I finished taking the actions above, "
                        "but ran into an issue generating a summary. "
                        "Let me know if you'd like more detail."
                    )

                # -----------------------------------------------------
                # False-completion guard
                # -----------------------------------------------------
                executed_mutation_this_turn = any(
                    log.capability in _MUTATING_CAPABILITIES
                    and log.result is not None
                    and log.result.success
                    for log in step_logs
                )

                if (
                    not executed_mutation_this_turn
                    and any(
                        phrase in final_text.lower()
                        for phrase in _FALSE_COMPLETION_PHRASES
                    )
                ):
                    logger.warning(
                        "Suppressed a likely false-completion claim "
                        "for session %s: %r",
                        session_id,
                        final_text,
                    )

                    final_text = (
                        "I haven't actually done that yet - no file was "
                        "written or created in this step. "
                        "Want me to go ahead and do it now?"
                    )

                step_logs.append(
                    AgentStepLog(
                        step_number=step,
                        note="final_answer",
                    )
                )

                emit(
                    "agent_completed",
                    {"response": final_text},
                )

                return final_text, step_logs

            # ---------------------------------------------------------
            # Max steps reached
            # ---------------------------------------------------------
            logger.warning(
                "Agent reached MAX_AGENT_STEPS (%s) without finishing.",
                self.max_steps,
            )

            emit(
                "agent_error",
                {"error": "max_steps_reached"},
            )

            fallback = (
                "I've hit my step limit working on this. "
                "Here's where things stand - let me know if you'd like "
                "me to continue."
            )

            return fallback, step_logs

        finally:
            # Always return the session to IDLE.
            set_voice_state(
                session_id,
                VoiceState.IDLE,
            )