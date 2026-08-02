"""Token and cost accounting.

Prices are approximate USD per 1M tokens and are matched by model-name substring so that
new dated snapshots still resolve. Unknown models simply report $0 while still tracking
tokens.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kavi.log import get_logger
from kavi.messages import Usage

logger = get_logger(__name__)

# (input_per_mtok, output_per_mtok)
_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-3-7-sonnet": (3.0, 15.0),
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-3-5-haiku": (0.8, 4.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4o": (2.5, 10.0),
    "gpt-4.1": (2.0, 8.0),
    "o1": (15.0, 60.0),
    "o3": (2.0, 8.0),
}


class CostLimitExceeded(Exception):
    """Raised when the session cost exceeds max_cost_usd."""
    pass


class CostWarning(Exception):
    """Returned when the session cost crosses warn_cost_usd."""
    pass


def _price_for(model: str) -> tuple[float, float]:
    for key, price in _PRICES.items():
        if key in model:
            return price
    return (0.0, 0.0)


def cost_for(model: str, usage: Usage) -> float:
    inp, out = _price_for(model)
    return (usage.input_tokens / 1_000_000) * inp + (usage.output_tokens / 1_000_000) * out


@dataclass
class CostTracker:
    """Accumulates usage and cost across a session."""

    total: Usage = field(default_factory=Usage)
    total_cost_usd: float = 0.0
    api_calls: int = 0
    max_cost_usd: float | None = None
    warn_cost_usd: float | None = None
    _warned: bool = False

    def record(self, model: str, usage: Usage) -> float | CostWarning:
        self.total = self.total + usage
        call_cost = cost_for(model, usage)
        self.total_cost_usd += call_cost
        self.api_calls += 1

        if self.max_cost_usd is not None and self.total_cost_usd >= self.max_cost_usd:
            logger.warning(
                "cost limit exceeded: $%.4f >= $%.4f", self.total_cost_usd, self.max_cost_usd
            )
            raise CostLimitExceeded(
                f"Session cost (${self.total_cost_usd:.4f}) exceeded the "
                f"maximum limit of ${self.max_cost_usd:.4f}."
            )

        if (
            self.warn_cost_usd is not None
            and not self._warned
            and self.total_cost_usd >= self.warn_cost_usd
        ):
            self._warned = True
            logger.info(
                "cost warning threshold reached: $%.4f >= $%.4f",
                self.total_cost_usd,
                self.warn_cost_usd,
            )
            return CostWarning(
                f"Warning: Session cost (${self.total_cost_usd:.4f}) has exceeded the "
                f"warning threshold of ${self.warn_cost_usd:.4f}."
            )

        return call_cost

    def summary(self) -> str:
        return (
            f"API calls: {self.api_calls}\n"
            f"Input tokens: {self.total.input_tokens:,}\n"
            f"Output tokens: {self.total.output_tokens:,}\n"
            f"Cache read: {self.total.cache_read_tokens:,}\n"
            f"Total cost: ${self.total_cost_usd:.4f}"
        )

    def status(self) -> str:
        tok = self.total.input_tokens + self.total.output_tokens
        return f"${self.total_cost_usd:.4f} | {tok:,} tok"
