"""Rule-based and phonetic name variation.

Nigerian names are orthographically noisy (Chukwu / Chuku / Chukwuka,
Olawale / Olaolu, Ijeoma / Ijeoma) but phonetically stable. Instead of a
model, this module applies a small set of tunable spelling rules and
phonetic encodings. No personal names are hardcoded here.
"""

from __future__ import annotations

import re

import jellyfish

import config

# Single-position consonant swaps. Each group is interchangeable.
CONSONANT_GROUPS = (
    ("d", "t"),
    ("b", "p"),
    ("k", "c", "q"),
    ("s", "z"),
    ("f", "v"),
    ("g", "j"),
)

# Vowel digraph swaps, applied in both directions.
VOWEL_DIGRAPHS = (
    ("u", "oo"),
    ("i", "ee"),
    ("e", "ae"),
    ("o", "au"),
)

# Suffix expansions, applied in both directions.
SUFFIX_PAIRS = (
    ("du", "dum"),
    ("chu", "chukwu"),
    ("chuku", "chukwuka"),
    ("nne", "nneka"),
    ("ola", "olami"),
)

_USERNAME_ALLOWED = re.compile(r"[^a-z0-9._]")


def split_name(full_name) -> tuple[str, str]:
    """Split a full name into (first, last).

    The last whitespace token is the surname; everything before it is the
    first name. Raises ValueError on empty input.
    """
    if full_name is None or not str(full_name).strip():
        raise ValueError("A full name is required.")
    tokens = str(full_name).strip().split()
    if len(tokens) == 1:
        return tokens[0], ""
    return " ".join(tokens[:-1]), tokens[-1]


def _phonetic_match(a: str, b: str) -> bool:
    """True when soundex, metaphone or nysiis agree for both tokens."""
    if not a or not b:
        return False
    for encoder in (jellyfish.soundex, jellyfish.metaphone, jellyfish.nysiis):
        try:
            left, right = encoder(a), encoder(b)
        except Exception:
            continue
        if left and left == right:
            return True
    return False


def _orthographic_variants(token: str) -> set[str]:
    """Apply the tunable spelling rules to one token."""
    base = (token or "").strip().lower()
    if not base:
        return set()

    variants: set[str] = set()

    for index, char in enumerate(base):
        for group in CONSONANT_GROUPS:
            if char not in group:
                continue
            for replacement in group:
                if replacement == char:
                    continue
                variants.add(base[:index] + replacement + base[index + 1 :])

    for short, long_ in VOWEL_DIGRAPHS:
        if short in base:
            variants.add(base.replace(short, long_))
        if long_ in base:
            variants.add(base.replace(long_, short))

    collapsed = re.sub(r"(.)\1+", r"\1", base)
    if collapsed != base:
        variants.add(collapsed)

    for short, long_ in SUFFIX_PAIRS:
        if short in base:
            variants.add(base.replace(short, long_))
        if long_ in base:
            variants.add(base.replace(long_, short))

    variants.discard(base)
    return variants


def _lev_ratio(a: str, b: str) -> float:
    """Normalized Levenshtein similarity in pure Python (0.0 - 1.0)."""
    a, b = (a or "").lower(), (b or "").lower()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            cost = 0 if char_a == char_b else 1
            current.append(
                min(
                    previous[j] + 1,  # deletion
                    current[j - 1] + 1,  # insertion
                    previous[j - 1] + cost,  # substitution
                )
            )
        previous = current

    distance = previous[-1]
    return 1.0 - (distance / max(len(a), len(b)))


def _rank_tokens(original: str, candidates, limit: int) -> list[str]:
    """Return [original] plus the best-scoring candidates, best first."""
    original = (original or "").strip().lower()
    if not original:
        return []

    scored: list[tuple[float, str]] = []
    seen = {original}

    for candidate in candidates or ():
        candidate = (candidate or "").strip().lower()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)

        jw = jellyfish.jaro_winkler_similarity(original, candidate)
        score = max(jw, _lev_ratio(original, candidate) * 0.9)
        phonetic = _phonetic_match(original, candidate)
        if phonetic:
            score += 0.15

        if score >= config.FUZZY_THRESHOLD or (phonetic and jw > 0.7):
            scored.append((score, candidate))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [original] + [candidate for _, candidate in scored[: max(0, limit)]]


def _clean_username(value: str) -> str:
    value = (value or "").strip().lower()
    value = _USERNAME_ALLOWED.sub("", value)
    return value.strip("._-")


def generate_usernames(full_name, phone_variants) -> list[str]:
    """Generate candidate public usernames from a name and phone variants."""
    first, last = split_name(full_name)

    first_variants = _rank_tokens(
        first, _orthographic_variants(first), config.MAX_NAME_VARIATIONS
    )
    last_variants = (
        _rank_tokens(last, _orthographic_variants(last), config.MAX_SURNAME_VARIATIONS)
        if last
        else []
    )

    usernames: list[str] = []

    def add(value: str) -> None:
        cleaned = _clean_username(value)
        if len(cleaned) >= 3 and cleaned not in usernames:
            usernames.append(cleaned)

    # Phone-derived handles go first: they are the most specific signal, and
    # the name combinations below would otherwise fill the cap.
    for variant in phone_variants or ():
        digits = re.sub(r"\D", "", str(variant))
        if len(digits) < 3:
            continue
        add(digits)
        if digits.startswith("234"):
            add(digits[3:])
        elif digits.startswith("0"):
            add(digits[1:])

    for first_var in first_variants:
        if not last_variants:
            add(first_var)
            continue
        for last_var in last_variants:
            add(first_var + last_var)
            add(first_var + "." + last_var)
            add(first_var + "_" + last_var)
            add(last_var + first_var)
            add(first_var[0] + last_var)
            add(first_var + last_var[0])
            add(last_var + first_var[0])

    return usernames[: config.MAX_USERNAMES]
