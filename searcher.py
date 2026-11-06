"""Async username search across public profiles.

Maigret is the primary engine: it checks a bundled database of public
profile URLs. When Maigret is missing or its site database cannot be
loaded, a small curated HTTP fallback keeps the bot working. Both paths
only touch public profile pages; no login-walled endpoints are used.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os

import aiohttp

import config

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Curated fallback sites only. Each URL is a public profile page.
FALLBACK_SITES = (
    {
        "site": "Instagram",
        "url": "https://www.instagram.com/{username}/",
        "tags": ["social"],
    },
    {
        "site": "TikTok",
        "url": "https://www.tiktok.com/@{username}",
        "tags": ["social"],
    },
    {
        "site": "Facebook",
        "url": "https://www.facebook.com/{username}",
        "tags": ["social"],
    },
    {
        "site": "LinkedIn",
        "url": "https://www.linkedin.com/in/{username}",
        "tags": ["social"],
    },
    {
        "site": "GitHub",
        "url": "https://github.com/{username}",
        "tags": ["social", "misc"],
    },
    {
        "site": "Nairaland",
        "url": "https://www.nairaland.com/{username}",
        "tags": ["forum"],
    },
)

_maigret_search = None
_maigret_sites = None
_maigret_unavailable = False


def _load_maigret():
    """Lazily import Maigret and load its bundled site database.

    Returns (search_coroutine, site_dict) or None when Maigret cannot be
    used. A failure is remembered so later calls skip straight to the HTTP
    fallback instead of retrying an import that will not succeed.
    """
    global _maigret_search, _maigret_sites, _maigret_unavailable

    if _maigret_search is not None:
        return _maigret_search, _maigret_sites
    if _maigret_unavailable:
        return None

    try:
        import maigret
        from maigret import search as maigret_search
        from maigret.sites import MaigretDatabase

        data_path = os.path.join(
            os.path.dirname(os.path.abspath(maigret.__file__)),
            "resources",
            "data.json",
        )
        if not os.path.exists(data_path):
            raise RuntimeError(f"Maigret site database not found at {data_path}")

        database = MaigretDatabase()
        loader = getattr(database, "load_from_file", None) or getattr(
            database, "load_from_path", None
        )
        if loader is None:
            raise RuntimeError("MaigretDatabase exposes no loader")
        loaded = loader(data_path)
        if loaded is not None:
            database = loaded

        raw_sites = getattr(database, "sites", None) or {}
        if isinstance(raw_sites, dict):
            all_sites = dict(raw_sites)
            for name, site in all_sites.items():
                if not getattr(site, "name", ""):
                    site.name = name
        else:
            all_sites = {
                getattr(site, "name", ""): site
                for site in raw_sites
                if getattr(site, "name", "")
            }

        wanted_tags = set(config.MAIGRET_TAGS)
        tagged_sites = {
            name: site
            for name, site in all_sites.items()
            if wanted_tags & set(getattr(site, "tags", []) or [])
        }

        if not tagged_sites and not all_sites:
            raise RuntimeError("Maigret site database is empty")

        _maigret_search = maigret_search
        _maigret_sites = tagged_sites or all_sites
        return _maigret_search, _maigret_sites
    except Exception as exc:  # noqa: BLE001 - degrade instead of crashing
        logger.warning(
            "Maigret unavailable (%s); using curated HTTP fallback", exc
        )
        _maigret_unavailable = True
        return None


def _status_text(value) -> str:
    status = value.get("status") if isinstance(value, dict) else getattr(value, "status", None)
    status = getattr(status, "status", status)
    status = getattr(status, "name", status)
    return str(status or "").lower()


def _iter_results(results):
    if hasattr(results, "items"):
        for site_name, value in results.items():
            yield site_name, value
    elif isinstance(results, (list, tuple, set)):
        for value in results:
            yield None, value


def _maigret_hits(username: str, results) -> dict:
    hits: dict[str, dict] = {}

    for site_name, value in _iter_results(results):
        if value is None:
            continue

        status = _status_text(value)
        if "claimed" not in status and "found" not in status:
            continue

        site = value.get("site") if isinstance(value, dict) else getattr(value, "site", None)
        name = site_name or getattr(site, "name", None) or ""
        url = None
        if isinstance(value, dict):
            url = value.get("url") or value.get("url_user") or value.get("url_main")
        else:
            url = (
                getattr(value, "url_user", None)
                or getattr(value, "url", None)
                or getattr(value, "url_main", None)
            )
        if not name or not url:
            continue

        tags = getattr(site, "tags", None) if site is not None else None
        if tags is None and isinstance(value, dict):
            tags = value.get("tags")
        if isinstance(tags, str):
            tags = [tags]

        hits[name] = {
            "site": name,
            "url": url,
            "username": username,
            "tags": list(tags or []),
            "source": "maigret",
        }

    return hits


async def _maigret_search_one(username: str) -> dict:
    """Search one username with Maigret; return site_name -> hit dict."""
    loaded = _load_maigret()
    if loaded is None:
        return {}
    search_func, sites = loaded

    options = {
        "logger": logger,
        "timeout": config.MAIGRET_TIMEOUT,
        "max_connections": config.MAIGRET_MAX_CONNECTIONS,
        "no_progressbar": True,
    }

    try:
        params = inspect.signature(search_func).parameters
        has_kwargs = any(
            param.kind is inspect.Parameter.VAR_KEYWORD
            for param in params.values()
        )
        if not has_kwargs:
            options = {key: value for key, value in options.items() if key in params}
        results = search_func(username, sites, **options)
        if inspect.isawaitable(results):
            results = await results
    except Exception as exc:  # noqa: BLE001 - one bad username must not stop the batch
        logger.warning("Maigret lookup failed for %s: %s", username, exc)
        return {}

    return _maigret_hits(username, results)


async def _http_fallback_one(username: str) -> dict:
    """Check the curated public sites directly over HTTP."""
    hits: dict[str, dict] = {}
    timeout = aiohttp.ClientTimeout(total=config.MAIGRET_TIMEOUT)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.8",
    }

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        for site in FALLBACK_SITES:
            url = site["url"].format(username=username)
            try:
                async with session.get(url, allow_redirects=True, ssl=False) as response:
                    if response.status != 200:
                        continue
                    body = await response.text(errors="ignore")
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.debug("Fallback lookup failed for %s: %s", url, exc)
                continue
            except Exception as exc:  # noqa: BLE001 - public sites are flaky
                logger.debug("Fallback lookup error for %s: %s", url, exc)
                continue

            if username.lower() in body.lower():
                hits[site["site"]] = {
                    "site": site["site"],
                    "url": url,
                    "username": username,
                    "tags": list(site["tags"]),
                    "source": "http",
                }

    return hits


async def search_usernames(usernames) -> list[dict]:
    """Search a list of usernames in batches and return flat hit dicts."""
    unique: list[str] = []
    for username in usernames or ():
        cleaned = str(username or "").strip().lower()
        if cleaned and cleaned not in unique:
            unique.append(cleaned)

    hits: list[dict] = []
    seen_urls: set[str] = set()
    batch_size = max(1, config.MAIGRET_MAX_CONNECTIONS // 2)

    for start in range(0, len(unique), batch_size):
        batch = unique[start : start + batch_size]
        use_fallback = _load_maigret() is None

        if use_fallback:
            tasks = [_http_fallback_one(username) for username in batch]
        else:
            tasks = [_maigret_search_one(username) for username in batch]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for username, result in zip(batch, results):
            if isinstance(result, Exception):
                logger.warning("Lookup failed for %s: %s", username, result)
                continue
            for hit in result.values():
                url = hit.get("url")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                hits.append(hit)

        if start + batch_size < len(unique):
            await asyncio.sleep(config.BATCH_SLEEP_SECONDS)

    return hits
