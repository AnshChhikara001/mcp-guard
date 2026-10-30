"""Decisions: what the Gateway does with one Tool call, and which of its layers decided.

For now there are two: allow, and deny with a reason. Hold and redact come later.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Allow:
    layer: str  # the layer that let the call through, e.g. "cedar"


@dataclass(frozen=True)
class Deny:
    layer: str  # the layer that stopped the call
    reason: str  # a reason code, e.g. "not_found_or_not_yours"
    explanation: str  # the reason in plain words, for the Agent to pass on


type Decision = Allow | Deny
