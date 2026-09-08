"""
Centralized safety validator for all capabilities.

Sits between the Agent/Executor and actual capability execution.
Prevents dangerous actions from running and flags others as requiring
user confirmation before they proceed.

RiskLevel is imported from core.result so there is a single shared
definition used by both the Safety Layer and every CapabilityResult.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

import logging

from core.result import RiskLevel

logger = logging.getLogger("jessy.safety")


class SafetyLayer:
    """
    Centralized safety validator for all capabilities.
    Prevents dangerous actions and requires confirmation where needed.
    """

    # Define risk rules based on action name and arguments.
    #
    # NOTE: Keys must exactly match the capability name as registered in
    # CapabilityRegistry (i.e. the string passed to Executor.execute()).
    # A mismatch here silently defaults the action to SAFE — see
    # delete_path, which was previously missing because the rule was
    # written as "delete_file" while the real capability is "delete_path".
    #
    # Where the exact registered name wasn't yet confirmed, both the
    # likely naming variants are included below as a safety net. Run a
    # full dump of registry.list_names() and remove whichever alias
    # turns out to be unused, to keep this table clean.
    RISK_RULES: Dict[str, Callable[[Dict[str, Any]], RiskLevel]] = {
        # Filesystem - destructive
        "delete_path": lambda args: RiskLevel.CONFIRMATION_REQUIRED,   # CONFIRMED real name
        "delete_file": lambda args: RiskLevel.CONFIRMATION_REQUIRED,   # alias, in case still used elsewhere
        "delete_folder": lambda args: RiskLevel.CONFIRMATION_REQUIRED, # alias, in case still used elsewhere

        "move_path": lambda args: RiskLevel.CONFIRMATION_REQUIRED,
        "move_file": lambda args: RiskLevel.CONFIRMATION_REQUIRED,
        "move_folder": lambda args: RiskLevel.CONFIRMATION_REQUIRED,

        "copy_path": lambda args: RiskLevel.CONFIRMATION_REQUIRED,
        "copy_file": lambda args: RiskLevel.CONFIRMATION_REQUIRED,
        "copy_folder": lambda args: RiskLevel.CONFIRMATION_REQUIRED,

        "overwrite_file": lambda args: RiskLevel.CONFIRMATION_REQUIRED,
        "write_file": lambda args: RiskLevel.CONFIRMATION_REQUIRED,  # UNVERIFIED alias — confirm real name

        # Process - termination (UNVERIFIED — confirm real registered name)
        "stop_process": lambda args: RiskLevel.CONFIRMATION_REQUIRED,
        "kill_process": lambda args: RiskLevel.CONFIRMATION_REQUIRED,
        "terminate_process": lambda args: RiskLevel.CONFIRMATION_REQUIRED,
        "end_process": lambda args: RiskLevel.CONFIRMATION_REQUIRED,  # alias

        # System - shutdown/restart (UNVERIFIED — confirm real registered name)
        "shutdown_system": lambda args: RiskLevel.BLOCKED,
        "restart_system": lambda args: RiskLevel.BLOCKED,
        "logoff_user": lambda args: RiskLevel.BLOCKED,
        "reboot_system": lambda args: RiskLevel.BLOCKED,  # alias
        "sign_out": lambda args: RiskLevel.BLOCKED,       # alias

        # Terminal - destructive commands
        "run_command": lambda args: RiskLevel.CONFIRMATION_REQUIRED if any(
            cmd in str(args.get("command", "")).lower()
            for cmd in ["rm -rf", "del /s/q", "format", "dd", "rd /s", "shutdown", "diskpart"]
        ) else RiskLevel.SAFE,
        "execute_command": lambda args: RiskLevel.CONFIRMATION_REQUIRED if any(
            cmd in str(args.get("command", "")).lower()
            for cmd in ["rm -rf", "del /s/q", "format", "dd", "rd /s", "shutdown", "diskpart"]
        ) else RiskLevel.SAFE,  # alias

        # Browser - navigation to unsafe sites
        "navigate_browser": lambda args: RiskLevel.CONFIRMATION_REQUIRED if any(
            site in str(args.get("url", ""))
            for site in ["malware.com", "phishing.net", "download.exe"]
        ) else RiskLevel.SAFE,
        "open_url": lambda args: RiskLevel.CONFIRMATION_REQUIRED if any(
            site in str(args.get("url", ""))
            for site in ["malware.com", "phishing.net", "download.exe"]
        ) else RiskLevel.SAFE,  # alias

        # Keyboard/Mouse - repeated spam
        "type_text": lambda args: RiskLevel.CONFIRMATION_REQUIRED if len(str(args.get("text", ""))) > 1000 else RiskLevel.SAFE,
        "press_key": lambda args: RiskLevel.CONFIRMATION_REQUIRED if args.get("count", 1) > 50 else RiskLevel.SAFE,

        # Window management - focus changes
        "switch_window": lambda args: RiskLevel.SAFE,
        "minimize_window": lambda args: RiskLevel.SAFE,
        "maximize_window": lambda args: RiskLevel.SAFE,
        "close_window": lambda args: RiskLevel.CONFIRMATION_REQUIRED,

        # Editor - file operations
        "open_file_in_vscode": lambda args: RiskLevel.SAFE,
        "save_file_in_vscode": lambda args: RiskLevel.SAFE,
        "create_file_visual_in_vscode": lambda args: RiskLevel.SAFE,
    }

    @staticmethod
    def validate(action: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate an action against risk rules.

        Returns:
            {
                "allowed": bool,
                "risk_level": RiskLevel,
                "message": Optional[str]
            }
        """
        rule = SafetyLayer.RISK_RULES.get(action)

        if rule is None:
            # No specific rule for this action - assume safe.
            # NOTE: this is a silent default. If a destructive capability
            # is added later without a matching RISK_RULES entry, it will
            # execute immediately with no confirmation. Log at DEBUG so
            # unmapped actions are at least visible in logs.
            logger.debug("No safety rule for action '%s' — defaulting to SAFE.", action)
            return {"allowed": True, "risk_level": RiskLevel.SAFE, "message": None}

        risk_level = rule(args) if callable(rule) else rule

        if risk_level == RiskLevel.BLOCKED:
            message = f"Action '{action}' is blocked due to security policy."
            logger.warning("Blocked action: %s args=%s", action, args)
            return {"allowed": False, "risk_level": RiskLevel.BLOCKED, "message": message}

        if risk_level == RiskLevel.CONFIRMATION_REQUIRED:
            message = f"Action '{action}' may cause irreversible changes. Please confirm."
            logger.info("Confirmation required for action: %s args=%s", action, args)
            return {"allowed": False, "risk_level": RiskLevel.CONFIRMATION_REQUIRED, "message": message}

        return {"allowed": True, "risk_level": RiskLevel.SAFE, "message": None}

    @staticmethod
    def get_risk_label(risk_level: RiskLevel) -> str:
        """Human-readable label for risk level."""
        return {
            RiskLevel.SAFE: "Safe",
            RiskLevel.CONFIRMATION_REQUIRED: "Requires Confirmation",
            RiskLevel.BLOCKED: "Blocked",
        }.get(risk_level, "Unknown")

    @staticmethod
    def is_dangerous(action: str, args: Dict[str, Any]) -> bool:
        """Check if action is considered dangerous."""
        result = SafetyLayer.validate(action, args)
        return result["risk_level"] != RiskLevel.SAFE
