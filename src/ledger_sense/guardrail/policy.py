"""Loading and validating the guardrail policy book (spec §8.1).

The policy book is a small JSON document, checked in at ``policy_book.json``
next to this module. It is *JSON-overridable*: pass ``--policy-book`` on the
CLI (or ``path=`` here) to swap in a different file wholesale. We do not
merge an override on top of the default -- the override must be a complete,
valid policy book, so a run's ``policy_applied.json`` always reflects exactly
one document, never a silently-merged hybrid.
"""

import json
from pathlib import Path

DEFAULT_POLICY_PATH = Path(__file__).with_name("policy_book.json")

_REQUIRED_KEYS = {"policy_version", "denied_parties", "dual_control_threshold", "required_approvals"}


class PolicyError(ValueError):
    """The policy book on disk is missing a required field or malformed."""


def load_policy(path=None) -> dict:
    """Load and validate a policy book. ``path`` defaults to the bundled default."""
    source = Path(path) if path is not None else DEFAULT_POLICY_PATH
    with source.open("r", encoding="utf-8") as fh:
        policy = json.load(fh)
    missing = _REQUIRED_KEYS - policy.keys()
    if missing:
        raise PolicyError(f"policy book {source} missing required keys: {sorted(missing)}")
    if not isinstance(policy["policy_version"], str) or not policy["policy_version"]:
        raise PolicyError(f"policy book {source}: policy_version must be a non-empty string")
    if not isinstance(policy["denied_parties"], list):
        raise PolicyError(f"policy book {source}: denied_parties must be a list")
    for entry in policy["denied_parties"]:
        if "name" not in entry:
            raise PolicyError(f"policy book {source}: each denied_parties entry needs a 'name'")
    # dual_control_threshold is kept as the exact string from JSON; the caller
    # converts it to Decimal (never float) at the point of use.
    if not isinstance(policy["dual_control_threshold"], str):
        raise PolicyError(f"policy book {source}: dual_control_threshold must be a decimal string")
    policy.setdefault("source_path", str(source))
    return policy
