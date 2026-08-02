"""Extended-thinking budget helpers.

Thinking mode is provider-dependent. This module centralises how Kavi decides whether to
request thinking and how large a budget to allocate for a turn.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ThinkingPolicy:
    enabled: bool
    budget_tokens: int

    def effective_budget(self, max_tokens: int) -> int:
        # Budget must leave room for the visible answer.
        return max(1024, min(self.budget_tokens, max_tokens - 1024))
