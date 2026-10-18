"""Trusted provider response boundary, intentionally not wired to a public endpoint.

Cached input is a subset of total input, not extra billable input. Provider adapters
must normalize their response into this structure, then use configured entitlements
before execution. No provider is connected or token quota enabled in this slice.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProviderTokens:
    input_tokens: int
    output_tokens: int
    cached_tokens: int

    def __post_init__(self):
        if (
            any(
                type(value) is not int or value < 0
                for value in (self.input_tokens, self.output_tokens, self.cached_tokens)
            )
            or self.cached_tokens > self.input_tokens
        ):
            raise ValueError("Invalid provider token measurement")


class ProviderResponseMeter(Protocol):
    def measure(self, response: object) -> ProviderTokens | None:
        """Return None when the provider did not supply usage, never estimate silently."""
        ...
