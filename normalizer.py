"""Nigerian phone number normalization.

Turns whatever the collector types into an E.164 number plus the variant
forms used for username generation and bio matching.
"""

from __future__ import annotations

import re

import phonenumbers
from phonenumbers import carrier, geocoder

DEFAULT_REGION = "NG"


def _parse(raw: str) -> phonenumbers.PhoneNumber:
    """Parse with NG first, then let phonenumbers infer the region."""
    try:
        parsed = phonenumbers.parse(raw, DEFAULT_REGION)
        if phonenumbers.is_valid_number(parsed):
            return parsed
    except phonenumbers.NumberParseException:
        pass

    try:
        parsed = phonenumbers.parse(raw, None)
    except phonenumbers.NumberParseException as exc:
        raise ValueError(
            "That phone number could not be parsed. Send a Nigerian number "
            "such as 08031234567 or +2348031234567."
        ) from exc

    if not phonenumbers.is_valid_number(parsed):
        raise ValueError(
            "That phone number is not valid. Send a Nigerian number such as "
            "08031234567 or +2348031234567."
        )
    return parsed


def normalize_phone(raw) -> tuple[str, list[str], dict]:
    """Normalize a raw phone string.

    Returns (e164, variants, meta) where variants are deduplicated and meta
    carries carrier/region/national details. Raises ValueError on invalid
    input.
    """
    if raw is None or not str(raw).strip():
        raise ValueError(
            "A phone number is required. Send a Nigerian number such as "
            "08031234567 or +2348031234567."
        )

    parsed = _parse(str(raw).strip())

    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    national = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)
    national_digits = re.sub(r"\D", "", national)
    national_no_trunk = (
        national_digits[1:] if national_digits.startswith("0") else national_digits
    )
    country_code = parsed.country_code or 0

    raw_variants = [
        e164,
        e164.lstrip("+"),
        national_digits,
        national_no_trunk,
        f"{country_code}{national_no_trunk}",
    ]

    variants: list[str] = []
    for variant in raw_variants:
        variant = (variant or "").strip()
        if variant and variant not in variants:
            variants.append(variant)

    meta = {
        "carrier": carrier.name_for_number(parsed, "en") or "",
        "region": geocoder.description_for_number(parsed, "en") or "",
        "country_code": country_code,
        "national": national_digits,
    }

    return e164, variants, meta
