"""Permission engine - decide whether a tool call may run.

Two layers combine to a final decision:

1. **Explicit rules** (:meth:`decide`) - resolved first, in order:
     yolo/bypass -> deny rules -> allow rules -> session grants ->
     per-tool config -> the tool's own default -> the global default.
2. **Mode overlay** (:meth:`apply_mode`) - the session-wide posture
   (default / auto / plan / bypass) refines an ``ask``/``allow`` outcome
   using whether the specific call is read-only or destructive.

An explicit ``deny`` always wins; ``bypass``/yolo always allows.
"""

from __future__ import annotations

from kavi.config.schema import PermissionConfig, PermissionDecision
from kavi.permissions.rules import parse_rules


class PermissionEngine:
    def __init__(self, config: PermissionConfig) -> None:
        self.config = config
        self._allow = parse_rules(config.allow)
        self._deny = parse_rules(config.deny)
        # Grants added at runtime when the user picks "always allow".
        self._session_allow: list[str] = []
        self._session_allow_subjects: dict[str, set[str]] = {}

    # -- runtime state -----------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        """Change the active permission mode for the rest of the session."""
        self.config.mode = mode  # type: ignore[assignment]
        # Keep the legacy flag consistent so other code paths agree.
        self.config.yolo = mode == "bypass"

    def grant_session(self, tool_name: str, subject: str | None = None) -> None:
        """Remember an approval for the rest of the session."""
        if subject:
            self._session_allow_subjects.setdefault(tool_name, set()).add(subject)
        else:
            if tool_name not in self._session_allow:
                self._session_allow.append(tool_name)

    def _session_allows(self, tool_name: str, subject: str) -> bool:
        if tool_name in self._session_allow:
            return True
        return subject in self._session_allow_subjects.get(tool_name, set())

    # -- decisions ---------------------------------------------------------------

    def decide(
        self,
        tool_name: str,
        subject: str,
        tool_default: PermissionDecision,
    ) -> PermissionDecision:
        if self.config.effective_mode() == "bypass":
            return "allow"

        for rule in self._deny:
            if rule.matches(tool_name, subject):
                return "deny"
        for rule in self._allow:
            if rule.matches(tool_name, subject):
                return "allow"

        if self._session_allows(tool_name, subject):
            return "allow"

        if tool_name in self.config.tools:
            return self.config.tools[tool_name]

        # Tool's own default takes precedence over the global default only when it is
        # more permissive-appropriate (read-only tools opt into "allow").
        if tool_default == "allow":
            return "allow"

        return self.config.default

    def apply_mode(
        self,
        decision: PermissionDecision,
        *,
        is_read_only: bool,
        is_destructive: bool,
    ) -> PermissionDecision:
        """Refine a rule-based decision with the session-wide mode.

        Never loosens an explicit ``deny`` and never fires when bypass already
        allowed everything.
        """
        if decision == "deny":
            return "deny"
        mode = self.config.effective_mode()

        if mode == "bypass":
            return "allow"

        if is_read_only:
            # Read-only tools are always safe to run regardless of mode.
            return "allow" if decision != "deny" else "deny"

        if mode == "plan":
            # Strictly read-only: refuse any mutating/exec tool.
            return "deny"

        if mode == "auto":
            # Accept edits automatically, but still confirm destructive actions.
            return "ask" if is_destructive else "allow"

        # default mode: leave the rule-based decision as-is.
        return decision

