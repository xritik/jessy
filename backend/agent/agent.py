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
    - Tool-routing / argument-extraction steps (deciding which
      capability to call and with what arguments) use
      groq_client.tool_model — a smaller/faster model, since this is
      a simpler decision task and happens on every step of the loop.
    - Once the model decides no more tools are needed, a final
      dedicated call is made using groq_client.model (the larger,
      higher-quality model) with tools disabled, purely to produce
      the natural-language answer shown to the user.

Confirmation handling:
    - When the Safety Layer marks an action CONFIRMATION_REQUIRED, the
      Executor refuses to run it and returns a message explaining that.
      Rather than letting the model attempt to phrase that question
      itself (which was producing garbled, sometimes duplicated text
      across multiple loop steps before it settled), the Agent now
      short-circuits: as soon as a CONFIRMATION_REQUIRED result comes
      back, it stores the pending confirmation and returns a
      deterministic, code-generated question immediately — no further
      model calls happen in that turn.
    - The Agent itself remembers "there is a pending confirmation for
      capability X with arguments Y" per session_id. If the very next
      user message is a clear yes/no, the Agent resolves it directly by
      calling the Executor with confirmed=True/False — no model
      round-trip needed.

      IMPORTANT: user replies are messy ("yes,", "yes.", "yes ", "Yes!").
      We normalize (strip trailing punctuation/whitespace) before matching
      against the yes/no patterns below. Skipping normalization was the
      cause of a real bug: a message like "yes, " (trailing comma) failed
      to match the old regex, silently dropped the pending confirmation,
      and fell through to a full model call that just re-asked the same
      question in different words — looking like a "double confirmation"
      even though only one was ever intended.

    - A separate, related bug: confirmed=True on the Executor only
      bypasses the SafetyLayer's risk check. It does NOT satisfy a
      capability's OWN internal guard (e.g. write_file's "File already
      exists — set overwrite=true to replace it"). If the original tool
      call never included overwrite=True (because the model didn't add
      it), a confirmed "yes" would still fail on that internal guard.
      We now inject overwrite=True automatically for write_file when
      resolving a confirmed "yes", since the user's confirmation IS the
      overwrite consent. This is only done for capabilities verified to
      accept an `overwrite` keyword (currently just write_file) — move_path
      /copy_path/delete_path do not accept that kwarg and would fail
      Executor's signature check if we injected it there too.

Placeholder guard:
    - Occasionally the tool-routing model hallucinates a literal template
      token (e.g. "C:\\Users\\<username>\\Desktop\\aaa.txt") instead of a
      real path. Left unchecked, this reaches the Executor and blows up
      as a cryptic OSError deep in file-system code. We detect an
      unresolved "<...>" placeholder in tool-call arguments before
      executing and turn it into a clear, actionable tool-result error
      instead, so the model can self-correct (or the user gets an
      understandable message) rather than a raw WinError.

False-completion guard:
    - The final-answer model occasionally describes an edit/write as
      already done (e.g. "I've added the code") even when no mutating
      capability actually succeeded in this turn — usually because the
      tool-routing model misread an ambiguous instruction like "just
      wrote there X" as narration about something already done, rather
      than an instruction to do it now, and skipped calling any tool at
      all. We add a system-prompt rule for this ambiguity, plus a code-
      level backstop that checks step_logs before letting such a claim
      through in the final answer.

Working context and action history (Phase 8):
    - Each session has a WorkingContext (agent/context.py) tracking the
      "current file / folder / project / application / browser / tab"
      JESSY has touched, updated after every capability call. A short
      plain-text summary of it is injected as an extra system message
      before the user's message — but only when there's something worth
      mentioning — so references like "open it" or "run it" resolve
      correctly without repeating full paths every turn.
    - Every capability attempt (executed, failed, proposed for
      confirmation, or cancelled by the user) is also recorded into the
      global, cross-session action log (core/action_log.py), which
      backs the GET /actions endpoint and, later, the GUI's Action Feed.
      This never affects what's sent to Groq — it's purely an audit
      trail for the user/GUI.

IMPORTANT — process model requirement:
    `_pending_confirmations` below is a plain in-memory dict living at
    module scope. It is only reliable if this app runs as a SINGLE
    process with no auto-reload. If uvicorn is started with
    `--workers > 1`, each worker process gets its own independent copy
    of this dict, and a pending confirmation stored by one worker will
    be invisible to a later request that happens to land on a
    different worker. Likewise, `--reload` restarting the process
    between two requests wipes this dict. Run with a single worker and
    no reload for this design to behave correctly, or move this state
    to a shared store (e.g. Redis) if you need multiple workers. The
    same applies to agent/context.py's per-session store.
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

logger = logging.getLogger("jessy.agent")

# Capabilities confirmed (by checking their actual function signatures
# in capabilities/filesystem.py) to accept an `overwrite` keyword
# argument. Only these get overwrite=True injected automatically when
# a pending confirmation is resolved with "yes" — injecting it into a
# capability that doesn't accept that kwarg would fail Executor's
# signature check (sig.bind_partial) with an "Invalid arguments" error.
_OVERWRITE_CAPABLE_CAPABILITIES = {"write_file"}

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
- To switch/cycle to the next or previous browser tab (e.g. "switch the \
chrome tab to the right/left", "go to the next tab"), use \
switch_tab_direction with browser set to the named browser. Do NOT use \
hotkey for this — hotkey sends input to whatever window currently has \
OS focus, which may not be the browser at all.
- To navigate the CURRENTLY ACTIVE tab to a new page after switching tabs \
or otherwise (e.g. "search canva.com there"), use navigate_active_tab \
with the same browser specified, so it acts on the correct window.

Editor and file behavior:
- By default, when the user asks to create a NEW file or NEW folder, do \
it VISIBLY: use create_folder_visual for new folders and \
create_file_visual_in_vscode for new files, so the user actually watches \
Explorer/VS Code perform the action on screen. Only use the silent \
write_file/create_directory tools when the user explicitly asks for it \
to happen in the background, silently, or without opening any window.
- If the user does not specify a folder/location for a new file or \
folder, default to the Desktop ('~/Desktop').
- create_folder_visual and create_file_visual_in_vscode already open the \
correct window (Explorer or VS Code) themselves as part of the visible \
flow. Do not call open_application or open_folder separately beforehand.
- write_file only writes bytes to disk and never opens the file as a \
visible tab, even if VS Code is already open. If you use write_file \
(because the user explicitly wanted it silent) for a task that involves \
VS Code, you MUST call open_file_in_vscode with that same path \
immediately after write_file succeeds, so the user actually sees the \
file open, not just present in the Explorer sidebar.
- If the user asks to open a folder in VS Code, use open_application or \
whatever capability launches VS Code on that folder first. Do not assume \
opening the folder also opens or focuses any particular file inside it.
- NEVER invent or type out a full literal filesystem path yourself,and \
NEVER include placeholder tokens such as "<username>", "<user>", \
"<path>", etc. in any tool argument. If you need the user's home \
directory or Desktop, use "~" or "~/Desktop" and let the system resolve \
it. If you need the path to a file you already created or found earlier \
in this conversation, reuse the exact path string a previous tool result \
gave you — do not retype or guess it.
- If the user's message describes content being added or written in a \
way that is ambiguous between "please do this now" and "I already did \
this myself" (e.g. "just wrote there multiplication code"), and no tool \
call in this conversation has actually written that content yet, treat \
it as an instruction to perform the write now using the appropriate \
tool (write_file or append_to_file). Never describe content as already \
added, written, or present in a file unless a tool call you made in \
this conversation actually did that.

Focus and window targeting:
- The generic hotkey and press_key tools send input to whatever window \
currently has OS-level focus, NOT necessarily the application named in \
the user's request. Before using hotkey/press_key to act on a specific \
named application, make sure a dedicated, focus-safe tool for that action \
exists and is preferred (e.g. switch_tab_direction for browser tabs). \
Only fall back to hotkey/press_key/type_text for generic actions on \
whatever window the user is already actively using right now.

Confirmation behavior:
- Some actions (e.g. deleting or overwriting files) require the user's \
explicit confirmation before they run. If a tool result tells you an \
action requires confirmation, do NOT call any tool to ask for that \
confirmation — there is no such tool. Simply respond with a short, \
plain-text question asking the user to confirm (e.g. "Do you want me to \
permanently delete this file?"). The system will handle the user's yes/no \
reply on its own; you do not need to re-issue the original tool call \
yourself once they confirm.
- Never call a tool name that was not explicitly given to you in your \
tools list. If you are unsure whether a tool exists, do not call it —
respond in plain text instead.

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

# In-memory pending-confirmation store, keyed by session_id. Holds the
# capability name and arguments that are awaiting a yes/no reply from
# the user. Intentionally simple (not persisted) — if the process
# restarts, any pending confirmation is lost, which is an acceptable
# tradeoff for a local dev assistant.
#
# See the module docstring above: this only works correctly with a
# single-process, no-reload uvicorn setup.
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

# Any run of whitespace and/or common sentence punctuation at the very
# start/end of a reply gets stripped before matching against the
# yes/no patterns above. This is what fixes replies like "yes,", "yes.",
# "Yes!!", "  no...", etc. — previously only a lone trailing "." or "!"
# was tolerated, so a plain trailing comma made the whole match fail.
_EDGE_PUNCT_RE = re.compile(r"^[\s.,!?;:]+|[\s.,!?;:]+$")

# Matches an unresolved template-style placeholder such as "<username>",
# "<user>", "<path>" inside a string argument.
_PLACEHOLDER_RE = re.compile(r"<[^<>\s]+>")

# Capabilities that actually mutate file/content state. Used by the
# false-completion guard below: if the final answer claims something
# was written/added/created but none of these succeeded this turn, the
# claim is almost certainly false and gets replaced.
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
    """Strip leading/trailing whitespace and common sentence punctuation
    so short yes/no replies match reliably regardless of stray commas,
    periods, or exclamation marks."""
    return _EDGE_PUNCT_RE.sub("", text.strip())


def _find_placeholder(value: Any) -> Optional[str]:
    """Recursively search tool-call arguments for an unresolved
    placeholder token like "<username>". Returns the first one found,
    or None if the arguments look concrete."""
    if isinstance(value, str):
        match = _PLACEHOLDER_RE.search(value)
        return match.group(0) if match else None
    if isinstance(value, dict):
        for v in value.values():
            found = _find_placeholder(v)
            if found:
                return found
    if isinstance(value, list):
        for v in value:
            found = _find_placeholder(v)
            if found:
                return found
    return None


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

    @staticmethod
    def _describe_confirmed_result(
        cap_name: str, cap_args: dict[str, Any], result: CapabilityResult
    ) -> str:
        """Build a short, plain-text summary after a confirmed action runs,
        without needing another model call."""
        pretty_name = cap_name.replace("_", " ")

        if not result.success:
            return f"I tried to {pretty_name} but it failed: {result.error}"

        target = cap_args.get("path") or (result.data or {}).get("path")
        if target:
            return f"Done — {pretty_name} completed on '{target}'."
        return f"Done — {pretty_name} completed successfully."

    @staticmethod
    def _describe_confirmation_request(cap_name: str, cap_args: dict[str, Any]) -> str:
        """Build a short, deterministic plain-text confirmation question,
        without needing a model call. Keeping this in code (rather than
        letting the model phrase it) avoids the garbled/duplicated text
        that showed up when the loop ran extra steps before settling on
        a final answer."""
        pretty_name = cap_name.replace("_", " ")
        target = cap_args.get("path") or cap_args.get("source")
        if target:
            return f"Do you want me to {pretty_name} on '{target}'? This may be irreversible."
        return f"Do you want me to {pretty_name}? This may be irreversible."

    def run(
        self,
        user_message: str,
        history: Optional[list[dict[str, Any]]] = None,
        on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
        session_id: str = "default",
    ) -> tuple[str, list[AgentStepLog]]:
        """
        Run the agent loop for a single user message.

        history: prior conversation turns (role/content dicts), excluding
                 the system prompt — kept short by core/memory.py.
        on_event: optional callback(event_type, payload) for real-time
                  GUI events (wired to WebSocket in Phase 10). Safe to
                  leave as None for now.
        session_id: identifies the conversation, used to track a pending
                    confirmation and working context across turns.

        Returns (final_response_text, step_logs).
        """

        def emit(event_type: str, payload: dict[str, Any]) -> None:
            if on_event:
                try:
                    on_event(event_type, payload)
                except Exception:
                    logger.exception("on_event callback failed for %s", event_type)

        # Phase 8: this session's Working Context — updated after every
        # capability call below, used to build a short summary that gets
        # injected into the messages sent to Groq.
        working_context: WorkingContext = get_context(session_id)

        # --- Resolve a pending confirmation directly, with no model call ---
        pending = _pending_confirmations.get(session_id)
        normalized_message = _normalize_reply(user_message)

        logger.info(
            "run() session=%s message=%r normalized=%r pending=%s",
            session_id, user_message, normalized_message, pending,
        )

        if pending is not None:
            if _AFFIRMATIVE_PATTERN.match(normalized_message):
                _pending_confirmations.pop(session_id, None)
                cap_name = pending["capability"]
                cap_args = dict(pending["arguments"])

                # A user's "yes" to a confirmation-required action also
                # satisfies that capability's own internal overwrite
                # guard (e.g. write_file's "file already exists" check),
                # which confirmed=True on the Executor does NOT bypass on
                # its own — that flag only skips the SafetyLayer risk
                # check. Only inject it for capabilities verified to
                # accept this kwarg, to avoid an "Invalid arguments" error
                # from the Executor's signature check.
                if cap_name in _OVERWRITE_CAPABLE_CAPABILITIES:
                    cap_args["overwrite"] = True

                emit("capability_started", {"capability": cap_name, "arguments": cap_args})
                result = self.executor.execute(cap_name, cap_args, confirmed=True)

                if result.success:
                    emit("capability_completed", {"capability": cap_name, "data": result.data})
                else:
                    emit("capability_failed", {"capability": cap_name, "error": result.error})

                # Phase 8: reflect this confirmed action in working context
                # and the global action log, same as any other execution.
                working_context.update_from_result(cap_name, cap_args, result)
                action_log.record(cap_name, cap_args, result, session_id=session_id)

                final_text = self._describe_confirmed_result(cap_name, cap_args, result)
                step_log = AgentStepLog(
                    step_number=1, capability=cap_name, arguments=cap_args, result=result
                )
                emit("agent_completed", {"response": final_text})
                return final_text, [step_log]

            if _NEGATIVE_PATTERN.match(normalized_message):
                _pending_confirmations.pop(session_id, None)

                # Phase 8: record that the user declined, so the action
                # feed shows this was proposed and then cancelled rather
                # than just silently vanishing.
                action_log.record(
                    pending["capability"],
                    pending["arguments"],
                    result=None,
                    session_id=session_id,
                    status_override="cancelled",
                )

                final_text = "Okay, I won't do that. Let me know if you'd like something else."
                emit("agent_completed", {"response": final_text})
                return final_text, [AgentStepLog(step_number=1, note="confirmation_declined")]

            # Not a recognizable yes/no — drop the stale pending
            # confirmation and fall through to handle this as a normal,
            # unrelated new message.
            _pending_confirmations.pop(session_id, None)

        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Phase 8: inject a short working-context summary, but only if
        # there's actually something worth mentioning (keeps brand-new
        # sessions free of empty boilerplate).
        context_summary = working_context.as_prompt_context()
        if context_summary:
            messages.append({"role": "system", "content": context_summary})

        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        tools = self.registry.to_groq_tools()
        step_logs: list[AgentStepLog] = []

        emit("agent_started", {"message": user_message})

        for step in range(1, self.max_steps + 1):
            emit("agent_thinking", {"step": step})

            # Tool-routing step: use the smaller/faster model since this
            # is just deciding which capability (if any) to call next.
            response = self.groq_client.chat(
                messages=messages,
                tools=tools or None,
                model=self.groq_client.tool_model,
            )
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

                    # Guard against the model hallucinating a literal,
                    # unresolved placeholder (e.g. "<username>") inside an
                    # argument such as a filesystem path. Executing that
                    # directly produces a cryptic OSError deep in the
                    # Executor; catching it here gives a clear, actionable
                    # error instead and avoids ever hitting the filesystem.
                    placeholder = _find_placeholder(cap_args)
                    if placeholder:
                        result = CapabilityResult(
                            success=False,
                            action=cap_name,
                            error=(
                                f"Argument contains an unresolved placeholder "
                                f"'{placeholder}'. Use '~' for the home "
                                f"directory, or the exact path from a previous "
                                f"tool result, instead of a template value."
                            ),
                        )
                        logger.warning(
                            "Blocked tool call %s for session %s: placeholder "
                            "%s found in arguments %s",
                            cap_name, session_id, placeholder, cap_args,
                        )
                    else:
                        result = self.executor.execute(cap_name, cap_args)

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

                        # If this failure is specifically because the
                        # action needs user confirmation, stop the loop
                        # here and now. We store the pending confirmation
                        # for this session and return a deterministic,
                        # code-generated question immediately — we do NOT
                        # let the model see this tool result and try to
                        # phrase its own question. That extra model
                        # round-trip was the source of garbled/duplicated
                        # confirmation text (the model sometimes needed
                        # 2-4 more steps to "settle" on a final answer,
                        # occasionally leaking scratchpad-style text like
                        # "User hasn't responded yet" into the reply).
                        if result.risk_level == RiskLevel.CONFIRMATION_REQUIRED:
                            _pending_confirmations[session_id] = {
                                "capability": cap_name,
                                "arguments": cap_args,
                            }
                            logger.info(
                                "Stored pending confirmation for session %s: %s %s",
                                session_id, cap_name, cap_args,
                            )

                            # Phase 8: log this as "proposed" (not executed,
                            # not failed) and record the attempt in working
                            # context's recent_actions, without touching any
                            # "current X" slot since nothing actually ran.
                            working_context.update_from_result(cap_name, cap_args, result)
                            action_log.record(
                                cap_name, cap_args, result,
                                session_id=session_id, status_override="proposed",
                            )

                            step_logs.append(
                                AgentStepLog(
                                    step_number=step,
                                    capability=cap_name,
                                    arguments=cap_args,
                                    result=result,
                                )
                            )

                            confirmation_question = self._describe_confirmation_request(
                                cap_name, cap_args
                            )
                            emit("agent_completed", {"response": confirmation_question})
                            return confirmation_question, step_logs

                    # Phase 8: reflect this call in working context and the
                    # global action log. Runs for every non-confirmation-
                    # required outcome above (both success and failure).
                    working_context.update_from_result(cap_name, cap_args, result)
                    action_log.record(cap_name, cap_args, result, session_id=session_id)

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

            # No tool calls: the tool-routing model decided it's done.
            # Make one dedicated call with the higher-quality model,
            # explicitly declaring the tools but forcing tool_choice="none"
            # so the model is not confused by prior tool_call history in
            # the conversation and reliably produces plain text.
            try:
                final_response = self.groq_client.chat(
                    messages=messages,
                    model=self.groq_client.model,
                    # Deliberately omit `tools` here. Passing tool_choice="none" was
                    # meant to forbid tool calls on this final step, but gpt-oss-120b
                    # on Groq doesn't reliably respect that constraint — it can still
                    # emit a tool call, which Groq then rejects with a 400
                    # "Tool choice is none, but model called a tool" error. Not
                    # sending any tool definitions at all removes the possibility
                    # entirely: with nothing to call, the model can only answer in
                    # plain text.
                )
                final_text = final_response.choices[0].message.content or ""
            except Exception:
                logger.exception("Final answer generation failed; falling back to a safe summary.")
                final_text = (
                    "I finished taking the actions above, but ran into an issue "
                    "generating a summary. Let me know if you'd like more detail."
                )

            # False-completion guard: if no mutating capability actually
            # succeeded during this turn, but the final answer claims
            # something was written/added/created, the claim is almost
            # certainly a hallucination (usually caused by the tool-
            # routing model misreading an ambiguous instruction as
            # narration about something already done, and skipping the
            # tool call entirely). Replace it with an honest offer to do
            # it now instead of letting a false claim reach the user.
            executed_mutation_this_turn = any(
                log.capability in _MUTATING_CAPABILITIES
                and log.result is not None
                and log.result.success
                for log in step_logs
            )
            if not executed_mutation_this_turn and any(
                phrase in final_text.lower() for phrase in _FALSE_COMPLETION_PHRASES
            ):
                logger.warning(
                    "Suppressed a likely false-completion claim in final answer "
                    "for session %s (no mutating capability succeeded this turn): %r",
                    session_id, final_text,
                )
                final_text = (
                    "I haven't actually done that yet — no file was written or "
                    "created in this step. Want me to go ahead and do it now?"
                )

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
