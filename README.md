# SkipTrace V1

SkipTrace V1 is a headless Python Telegram bot for Nigerian loan collectors. A
collector sends a debtor's full name, phone number, gender and (optionally)
company; the bot normalizes the phone number, derives plausible public
usernames from rule-based and phonetic name variants, checks public social
profiles (Facebook, Instagram, TikTok, LinkedIn and a few Nigerian sites)
through Maigret, ranks every hit with a deterministic, explainable confidence
score, and replies with an HTML dossier the collector can act on. It runs with
long polling, stores an audit log in SQLite, and never calls or messages the
debtor.

## Why name variation is rule-based, not an LLM

Nigerian names are orthographically noisy but phonetically stable: a surname
that appears as "Chukwu", "Chuku" or "Chukwuka" on three platforms is still the
same identity to a human ear, and the same is true of "Olawale"/"Olaolu",
"Ijeoma"/"Ijeoma", "Bello"/"Belo" and the many d/t, b/p, k/c/q and s/z
spellings in circulation. A deterministic rule set (consonant-group swaps,
vowel-digraph swaps, doubled-letter collapse, suffix expansions, plus soundex,
metaphone and nysiis agreement) covers that noise cheaply and identically on
every run. Deterministic scoring is auditable: every point in a dossier traces
back to a named rule, which matters when a collector has to justify an action.
An LLM would hallucinate handles that do not exist, vary between identical
inputs, and blur the audit trail. LLM-based semantic disambiguation (deciding
which of several plausible profiles is the debtor) is deliberately reserved for
V2.

## Setup

```bash
pip install -r requirements.txt
export BOT_TOKEN=123456789:your_botfather_token
python bot.py
```

`DB_PATH` is optional and defaults to `skiptrace.db`. Create the bot and get its
token from [@BotFather](https://t.me/BotFather). Then open a chat with the bot,
send `/start`, and send `/trace` to run a search. `/cancel` resets the
conversation, and `/skip` is accepted at the company step.

A `.env` file is not read automatically; export the variables in your shell or
inject them from your platform's secret store.

## Dependency pins

`maigret==0.6.6` is pinned rather than `0.4.4` because `0.4.4` hard-pins
`aiohttp==3.8.1`, which conflicts with the required `aiohttp==3.9.5` and has no
Python 3.12 wheel, so the pair cannot be installed on `python:3.12-slim` at all.
`aiohttp==3.14.5` is the client version Maigret 0.6.6 requires and the bot only
uses its standard client API. Every other pin is unchanged. Maigret also pulls
its optional reporting dependencies (including Flask and its web report
module); `bot.py` never imports or runs any of them, so the bot remains a
headless long-polling process.

## Deploy notes

Deploy as a worker process, not a web service: there is no HTTP port, no
webhook and no inbound traffic.

- Use a Python Docker image. The included `Dockerfile` is based on
  `python:3.12-slim`, installs `gcc` and `libffi-dev` for the compiled
  dependencies, and installs the pinned requirements.
- Set the `BOT_TOKEN` environment variable in the platform's secret settings.
  Never bake it into the image or commit it.
- Start command: `python bot.py`.
- Make sure the filesystem holding `DB_PATH` persists if you want the audit log
  to survive restarts. A single instance is enough; Telegram long polling does
  not support multiple pollers on one token.

## Security and hard rules

- Public profiles only. The bot follows the same public URLs a browser would,
  and no login-walled endpoints are used.
- No BVN, NIN or leaked-database lookups. The bot has no such data source and
  no credential handling beyond `BOT_TOKEN`.
- No auto-calling and no auto-messaging. The dossier only reports links; any
  contact is a human decision made outside the bot.
- No LLM API calls in V1. Scoring is deterministic and reproducible.
- Secrets come only from environment variables. `.env`, `*.db` and
  `__pycache__` are git-ignored, and `.env.example` contains placeholders only.
- Each search is written to a local SQLite audit log with the collector's
  Telegram ID, the subject details and the reported matches.

## Limitations

Maigret confirms that a profile exists at a URL, but bio fields are not
reliably extracted in V1. The scorer therefore works from the name similarity
between the input and the handle, the handle itself, phone digits appearing in
the handle or profile text, cross-platform username reuse, and any location or
company evidence available. Gender and location signals are wired into the
scorer but frequently receive no data in V1. Treat a dossier as a set of leads
to verify manually, not as proof of identity; the bot intentionally stops short
of contacting anyone.
