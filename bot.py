"""SkipTrace V1 Telegram bot.

Headless, long-polling, synchronous telebot handlers around an async
search pipeline. No web server, no webhook, no LLM calls.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import sys

import telebot
from telebot.apihelper import ApiTelegramException
from telebot.types import ReplyKeyboardMarkup, ReplyKeyboardRemove

import config
import db
import formatter
import names
import normalizer
import scorer
import searcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("skiptrace.bot")

# Placeholder keeps imports safe; main() refuses to poll without a real token.
bot = telebot.TeleBot(config.BOT_TOKEN or "0:" + "A" * 35, parse_mode="HTML")

STATE: dict[int, dict] = {}

GENDER_CHOICES = {
    "male": "Male",
    "female": "Female",
    "prefer not to say": "Prefer not to say",
}

WELCOME_TEXT = (
    "👋 <b>SkipTrace V1</b>\n"
    "Public-profile lookup for loan collectors.\n\n"
    "Send /trace to begin:\n"
    "1. Debtor's full name\n"
    "2. Phone number\n"
    "3. Gender\n"
    "4. Company (optional)\n\n"
    "Only public profiles are searched. The bot never messages or calls "
    "anyone.\n"
    "/cancel stops the current trace."
)

TEXT_LIMIT = 4000


def _new_state() -> dict:
    return {
        "step": "idle",
        "name": "",
        "phone": "",
        "gender": "",
        "company": "",
        "username": "",
    }


def _reset(uid: int) -> None:
    STATE.pop(uid, None)


def _gender_keyboard() -> ReplyKeyboardMarkup:
    markup = ReplyKeyboardMarkup(
        row_width=3, one_time_keyboard=True, resize_keyboard=True
    )
    markup.add("Male", "Female", "Prefer not to say")
    return markup


def _truncate(text: str) -> str:
    if len(text) <= TEXT_LIMIT:
        return text
    cut = text[:TEXT_LIMIT]
    cut = cut.rsplit("\n", 1)[0]
    return cut + "\n…"


def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _send_html(chat_id: int, text: str) -> None:
    """Send HTML, falling back to plain text if Telegram rejects the markup."""
    text = _truncate(text)
    try:
        bot.send_message(chat_id, text, parse_mode="HTML")
    except ApiTelegramException:
        logger.warning("HTML send rejected; retrying as plain text")
        bot.send_message(chat_id, _strip_html(text))


@bot.message_handler(commands=["start"])
def cmd_start(message):
    _reset(message.from_user.id)
    bot.send_message(message.chat.id, WELCOME_TEXT, parse_mode="HTML")


@bot.message_handler(commands=["cancel"])
def cmd_cancel(message):
    _reset(message.from_user.id)
    bot.send_message(
        message.chat.id,
        "Trace cancelled. Send /trace to start again.",
        reply_markup=ReplyKeyboardRemove(),
    )


@bot.message_handler(commands=["trace"])
def cmd_trace(message):
    STATE[message.from_user.id] = _new_state()
    STATE[message.from_user.id]["step"] = "name"
    bot.send_message(
        message.chat.id,
        "Step 1/4 — Send the debtor's <b>full name</b> (first and surname).",
        parse_mode="HTML",
    )


@bot.message_handler(commands=["skip"])
def cmd_skip(message):
    uid = message.from_user.id
    state = STATE.get(uid)
    if not state or state.get("step") != "company":
        bot.send_message(
            message.chat.id,
            "/skip is only valid at the company step. Send /trace to start a search.",
        )
        return
    state["company"] = ""
    state["step"] = "idle"
    _run_and_reply(message, state)


@bot.message_handler(content_types=["text"])
def handle_text(message):
    uid = message.from_user.id
    state = STATE.get(uid)
    text = (message.text or "").strip()

    if text.startswith("/"):
        bot.send_message(
            message.chat.id,
            "Unknown command. Send /trace to start a search or /cancel to stop.",
        )
        return

    if not state or state.get("step") in (None, "idle"):
        bot.send_message(
            message.chat.id, "Send /trace to start a new search."
        )
        return

    step = state.get("step")

    if step == "name":
        if len(text) < 2:
            bot.send_message(
                message.chat.id,
                "The name must be at least 2 characters. Send the debtor's full name.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        state["name"] = text
        state["step"] = "phone"
        bot.send_message(
            message.chat.id,
            "Step 2/4 — Send the debtor's <b>phone number</b> "
            "(e.g. 08031234567 or +2348031234567).",
            parse_mode="HTML",
        )
        return

    if step == "phone":
        try:
            e164, _variants, _meta = normalizer.normalize_phone(text)
        except ValueError as exc:
            bot.send_message(message.chat.id, f"⚠️ {html.escape(str(exc))}", parse_mode="HTML")
            return
        state["phone"] = e164
        state["step"] = "gender"
        bot.send_message(
            message.chat.id,
            "Step 3/4 — Select the debtor's <b>gender</b>.",
            parse_mode="HTML",
            reply_markup=_gender_keyboard(),
        )
        return

    if step == "gender":
        choice = GENDER_CHOICES.get(text.lower())
        if not choice:
            bot.send_message(
                message.chat.id,
                "Please choose one of the three options below.",
                reply_markup=_gender_keyboard(),
            )
            return
        state["gender"] = choice
        state["step"] = "company"
        bot.send_message(
            message.chat.id,
            "Step 4/4 — Send the <b>company</b> (optional) or /skip to continue.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if step == "company":
        state["company"] = text
        state["step"] = "idle"
        _run_and_reply(message, state)
        return

    _reset(uid)
    bot.send_message(message.chat.id, "Send /trace to start a new search.")


async def run_search(uid, user_input) -> tuple[str, list[dict]]:
    """Run the full lookup pipeline and return (dossier_text, matches)."""
    name = user_input.get("name") or ""
    gender = user_input.get("gender") or ""
    company = user_input.get("company") or ""
    telegram_username = user_input.get("username") or "unknown"

    e164, phone_variants, meta = normalizer.normalize_phone(user_input.get("phone"))
    usernames = names.generate_usernames(name, phone_variants)
    hits = await searcher.search_usernames(usernames)

    # V1 limitation: Maigret confirms profile existence, but bio fields are
    # not reliably extracted. Display name falls back to the handle.
    profiles_by_url = {
        hit["url"]: {
            "display_name": hit.get("username", ""),
            "bio": "",
            "location": "",
            "gender": "",
        }
        for hit in hits
        if hit.get("url")
    }

    input_ctx = {
        "name": name,
        "gender": gender,
        "company": company,
        "phone_e164": e164,
        "phone_variants": phone_variants,
        "meta": meta,
    }

    matches = scorer.rank_hits(hits, profiles_by_url, input_ctx)

    try:
        search_id = db.log_search(
            telegram_id=uid,
            telegram_username=telegram_username,
            full_name=name,
            phone=e164,
            gender=gender,
            company=company,
            variations_count=len(usernames),
            matches_count=len(matches),
        )
        db.log_matches(search_id, matches)
    except Exception:  # noqa: BLE001 - audit log must never break a search
        logger.warning("Audit log write failed", exc_info=True)

    dossier = formatter.format_dossier(
        input_ctx, matches, len(usernames), telegram_username
    )
    return dossier, matches


def _run_and_reply(message, state) -> None:
    """Reply immediately, then run the async pipeline from a sync handler."""
    uid = message.from_user.id
    chat_id = message.chat.id
    payload = {
        "name": state.get("name") or "",
        "phone": state.get("phone") or "",
        "gender": state.get("gender") or "",
        "company": state.get("company") or "",
        "username": getattr(message.from_user, "username", "") or "",
    }

    bot.send_message(
        chat_id, "🔎 Searching public profiles… this can take a few minutes."
    )

    try:
        dossier, _matches = asyncio.run(run_search(uid, payload))
    except ValueError as exc:
        bot.send_message(chat_id, f"⚠️ {html.escape(str(exc))}", parse_mode="HTML")
        return
    except Exception:  # noqa: BLE001 - report, do not crash the poller
        logger.exception("Search pipeline failed for telegram_id=%s", uid)
        bot.send_message(
            chat_id, "⚠️ The search failed. Please try again with /trace."
        )
        return

    _send_html(chat_id, dossier)


def main() -> None:
    if not config.BOT_TOKEN:
        print(
            "ERROR: BOT_TOKEN is not set.\n"
            "Export the token from @BotFather first, for example:\n"
            "  export BOT_TOKEN='123456789:AA...'\n"
            "then run: python bot.py",
            file=sys.stderr,
        )
        raise SystemExit(1)

    db.init_db()
    logger.info("SkipTrace V1 starting (audit log: %s)", config.DB_PATH)
    logger.info("Long polling started")
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=30)
    except KeyboardInterrupt:
        logger.info("Stopped by user")


if __name__ == "__main__":
    main()
