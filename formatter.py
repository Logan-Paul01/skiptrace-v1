"""Telegram HTML dossier formatting."""

from __future__ import annotations

import html
from datetime import datetime, timezone

MAX_MATCHES = 10
BIO_LIMIT = 200
MEDALS = ("🥇", "🥈", "🥉")


def _mask_phone(phone) -> str:
    value = str(phone or "").strip()
    if len(value) <= 7:
        return value
    return f"{value[:4]}•••{value[-3:]}"


def _clip(value, limit: int = BIO_LIMIT) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _next_action(matches: list[dict]) -> str:
    if not matches:
        return "try different spelling or check CAC"

    reasons = " ".join(matches[0].get("reasons") or []).lower()
    if "phone number appears" in reasons:
        return "verify before calling"
    if "2+ platforms" in reasons:
        return "high identity confidence"
    if "login wall" in reasons:
        return "try manual LinkedIn search"
    return "manually review the top match before acting"


def format_dossier(input_ctx, matches, variations_count, telegram_username) -> str:
    """Render the dossier as Telegram-safe HTML."""
    input_ctx = input_ctx or {}
    matches = list(matches or [])[:MAX_MATCHES]

    name = html.escape(str(input_ctx.get("name") or "Unknown"))
    phone = html.escape(_mask_phone(input_ctx.get("phone_e164")))
    gender = html.escape(str(input_ctx.get("gender") or "Not stated"))
    company = html.escape(str(input_ctx.get("company") or "").strip() or "—")
    searched_by = html.escape(str(telegram_username or "unknown").lstrip("@"))
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "🔎 <b>SKIPTRACE DOSSIER</b>",
        f"<b>Subject:</b> {name} · {phone} · {gender} · {company}",
        f"<b>Audit:</b> searched by @{searched_by} · {timestamp}",
        f"<b>Variations tested:</b> {int(variations_count or 0)} usernames",
        "",
    ]

    if not matches:
        lines.append("No public profiles cleared the confidence threshold.")
        lines.append("")

    for index, match in enumerate(matches):
        medal = MEDALS[index] if index < len(MEDALS) else "▪️"
        site = html.escape(str(match.get("site") or "Unknown site"))
        score = int(match.get("score") or 0)
        display_name = html.escape(str(match.get("display_name") or match.get("username") or ""))
        url = html.escape(str(match.get("url") or ""), quote=True)

        lines.append(f"{medal} <b>{site} — {score}/100</b>")
        if display_name:
            lines.append(display_name)

        bio = _clip(match.get("bio"))
        if bio:
            lines.append(f"“{html.escape(bio)}”")

        if url:
            link_text = html.escape(str(match.get("url") or ""))
            lines.append(f'<a href="{url}">{link_text}</a>')

        reasons = [html.escape(str(reason)) for reason in (match.get("reasons") or [])]
        if reasons:
            lines.append("✔ " + " · ".join(reasons))

        lines.append("")

    lines.append(f"<b>Suggested next action:</b> {html.escape(_next_action(matches))}")
    return "\n".join(lines)
