"""SQLite audit log for searches and reported matches."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import config


@contextmanager
def _connect():
    """Open a connection, commit on success, roll back on failure."""
    connection = sqlite3.connect(config.DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                telegram_username TEXT,
                full_name TEXT,
                phone TEXT,
                gender TEXT,
                company TEXT,
                variations_count INTEGER,
                matches_count INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                search_id INTEGER,
                site TEXT,
                url TEXT,
                display_name TEXT,
                score INTEGER,
                reasons TEXT,
                FOREIGN KEY (search_id) REFERENCES searches (id)
            )
            """
        )


def log_search(
    telegram_id,
    telegram_username,
    full_name,
    phone,
    gender,
    company,
    variations_count,
    matches_count,
) -> int:
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO searches (
                telegram_id, telegram_username, full_name, phone, gender,
                company, variations_count, matches_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                telegram_username,
                full_name,
                phone,
                gender,
                company,
                variations_count,
                matches_count,
            ),
        )
        return int(cursor.lastrowid)


def log_matches(search_id: int, matches) -> None:
    rows = [
        (
            search_id,
            match.get("site"),
            match.get("url"),
            match.get("display_name") or match.get("username"),
            int(match.get("score") or 0),
            " · ".join(match.get("reasons") or []),
        )
        for match in (matches or [])
    ]
    if not rows:
        return

    with _connect() as connection:
        connection.executemany(
            """
            INSERT INTO matches (search_id, site, url, display_name, score, reasons)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
