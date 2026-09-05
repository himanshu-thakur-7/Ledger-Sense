"""Counterparty name generation and noise (spec §4.2/§4.3).

KEY4 INVARIANT: the first 4 alphanumeric characters of a counterparty's canonical
name, uppercased, must survive every noise variant this module can produce. Agent 1's
``by_key4`` block index (spec §5.2) depends on this holding for every bank record --
it is asserted as a tested invariant in ``tests/test_generator_invariants.py``, not
just hoped for.
"""

import random

_LEGAL_SUFFIXES = [
    " Inc.",
    " Inc",
    " LLC",
    " Ltd.",
    " Ltd",
    " Group",
    " Corp.",
    " Corp",
    " Co.",
    " Holdings",
    " Partners",
]

_INDUSTRY_WORDS = [
    "Logistics",
    "Systems",
    "Freight",
    "Foods",
    "Materials",
    "Analytics",
    "Robotics",
    "Energy",
    "Chemicals",
    "Textiles",
    "Media",
    "Networks",
    "Capital",
    "Building",
    "Foods",
    "Pharma",
    "Retail",
    "Transport",
    "Software",
    "Hardware",
]

_ROOT_WORDS = [
    "Acme",
    "Summit",
    "Vertex",
    "Harbor",
    "Meridian",
    "Cobalt",
    "Union",
    "Granite",
    "Falcon",
    "Beacon",
    "Cedar",
    "Atlas",
    "Orbit",
    "Pioneer",
    "Northwind",
    "Redwood",
    "Silverline",
    "Anchor",
    "Cascade",
    "Ironwood",
    "Delta",
    "Frontier",
    "Halcyon",
    "Juniper",
    "Keystone",
    "Lighthouse",
    "Monarch",
    "Nova",
    "Outpost",
    "Prairie",
]


def key4(name: str) -> str:
    """First 4 alphanumeric characters of ``name``, uppercased.

    This is the canonical block key Agent 1 will use (spec §5.2 ``by_key4``); every
    noise transform in this module is required to preserve it exactly.
    """
    alnum = [c for c in name if c.isalnum()]
    return "".join(alnum[:4]).upper()


def generate_canonical_name(rng: random.Random, index: int) -> str:
    """Build a plausible, unique-enough canonical counterparty name.

    Deterministic given ``rng``'s position in the stream -- callers must draw names
    in a fixed order for reproducibility.
    """
    root = rng.choice(_ROOT_WORDS)
    industry = rng.choice(_INDUSTRY_WORDS)
    use_suffix = rng.random() < 0.55
    suffix = rng.choice(_LEGAL_SUFFIXES) if use_suffix else ""
    # Index disambiguator keeps ~800 draws from colliding while staying human-looking;
    # dropped most of the time so most names read cleanly.
    if rng.random() < 0.15:
        name = f"{root} {industry} {index % 97}{suffix}"
    else:
        name = f"{root} {industry}{suffix}"
    return name


def _safe_min_len(name: str) -> int:
    """Shortest prefix length of ``name`` that still contains 4 alphanumeric chars."""
    seen = 0
    for i, c in enumerate(name):
        if c.isalnum():
            seen += 1
        if seen >= 4:
            return i + 1
    return len(name)


def _drop_suffix(name: str) -> str:
    for suffix in _LEGAL_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)].rstrip()
    return name


def _truncate(rng: random.Random, name: str) -> str:
    min_len = _safe_min_len(name)
    if min_len >= len(name):
        return name
    cut = rng.randint(min_len, len(name))
    return name[:cut].rstrip()


def _abbreviate(rng: random.Random, name: str) -> str:
    words = name.split(" ")
    if len(words) <= 1:
        return name
    # First word always survives intact -- it alone carries the first 4 alnum chars
    # for every canonical name this module generates (root words are >=4 letters).
    out = [words[0]]
    for w in words[1:]:
        if w and w[0].isalnum() and rng.random() < 0.7:
            out.append(w[0].upper() + ".")
        else:
            out.append(w)
    return " ".join(out)


def _uppercase(name: str) -> str:
    return name.upper()


def _lowercase_padded(name: str) -> str:
    return f"  {name.lower()}  "


_VARIANTS = ("uppercase", "drop_suffix", "truncate", "abbreviate")


def noisy_variant(rng: random.Random, name: str, kind=None) -> str:
    """Apply one noise transform to ``name``, preserving the KEY4 invariant.

    ``kind`` forces a specific variant (used by the ``malformed`` defect for its
    lowercase/padded shape); otherwise one of the baseline variants is chosen.
    """
    if kind == "malformed":
        return _lowercase_padded(name)
    if kind is None:
        kind = rng.choice(_VARIANTS)
    if kind == "uppercase":
        return _uppercase(name)
    if kind == "drop_suffix":
        return _drop_suffix(name)
    if kind == "truncate":
        return _truncate(rng, name)
    if kind == "abbreviate":
        return _abbreviate(rng, name)
    raise ValueError(f"unknown name noise variant: {kind!r}")
