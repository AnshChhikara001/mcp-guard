"""Decisions: what the Gateway does with one Tool call, and which of its layers decided.

For now there are three: allow, deny with a reason, and redact. Hold comes later.
"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Allow:
    layer: str  # the layer that let the call through, e.g. "cedar"


@dataclass(frozen=True)
class Deny:
    layer: str  # the layer that stopped the call
    reason: str  # a reason code, e.g. "not_found_or_not_yours"
    explanation: str  # the reason in plain words, for the Agent to pass on


@dataclass(frozen=True)
class Redact:
    """An allowed call whose result held PII, which the Agent got as Pseudonyms instead."""

    layer: str  # the layer that replaced it
    counts: Mapping[str, int]  # how many pieces of each type, e.g. {"PHONE": 1}; never values


REDACT_REASON = "pii_in_result"
REDACT_EXPLANATION = (
    "The result held personal data, which the Agent got as Pseudonyms like <PHONE_1>."
)

type Decision = Allow | Deny | Redact


def fields_of(decision: Decision) -> dict[str, str | None]:
    """The Decision as plain fields: decision (allow, deny or redact), reason, explanation and
    layer.

    An allowed call has no reason or explanation.
    """
    match decision:
        case Allow(layer=layer):
            return {"decision": "allow", "reason": None, "explanation": None, "layer": layer}
        case Deny(layer=layer, reason=reason, explanation=explanation):
            return {
                "decision": "deny",
                "reason": reason,
                "explanation": explanation,
                "layer": layer,
            }
        case Redact(layer=layer):
            return {
                "decision": "redact",
                "reason": REDACT_REASON,
                "explanation": REDACT_EXPLANATION,
                "layer": layer,
            }
