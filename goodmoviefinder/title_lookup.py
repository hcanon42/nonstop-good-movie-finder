"""Alternate film titles from German Wikipedia (for German cinema listings)."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from goodmoviefinder.config import USER_AGENT
from goodmoviefinder.matching import normalize_title_chars

_WIKI_API = "https://de.wikipedia.org/w/api.php"
_CACHE: dict[tuple[str, int | None], str | None] = {}

_GERMAN_TITLE_HINT = re.compile(r"[äöüßÄÖÜ]")
_WIKI_EN_SUFFIX_RE = re.compile(
    r"\s*\((?:disambiguation|film|anime|manga|roman|\d{4}\s+film)\)\s*$",
    re.IGNORECASE,
)

_GERMAN_PHRASES = (
    " aus dem ",
    " aus der ",
    " kleiner ",
    " kleine ",
    " große ",
    " großer ",
    " und die ",
    " der ",
    " die ",
    " das ",
)


def likely_german_release_title(title: str) -> bool:
    """True when a title is probably a German cinema name, not an English one."""
    title = normalize_title_chars(title).strip()
    if not title:
        return False
    if _GERMAN_TITLE_HINT.search(title):
        return True
    padded = f" {title.casefold()} "
    return any(phrase in padded for phrase in _GERMAN_PHRASES)


def _wiki_get(params: dict[str, str]) -> dict[str, object] | None:
    query = urllib.parse.urlencode({**params, "format": "json"})
    request = urllib.request.Request(
        f"{_WIKI_API}?{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_disambiguation_title(title: str) -> bool:
    return "(disambiguation)" in title.casefold()


def _normalize_english_wikipedia_title(title: str) -> str:
    title = normalize_title_chars(title).strip()
    while True:
        stripped = _WIKI_EN_SUFFIX_RE.sub("", title).strip()
        if stripped == title:
            break
        title = stripped
    return title


def _english_langlink(page_title: str) -> str | None:
    parsed = _wiki_get(
        {
            "action": "query",
            "prop": "langlinks",
            "titles": page_title,
            "lllang": "en",
        }
    )
    if not parsed:
        return None
    query = parsed.get("query")
    if not isinstance(query, dict):
        return None
    pages = query.get("pages")
    if not isinstance(pages, dict):
        return None
    for page in pages.values():
        if not isinstance(page, dict) or page.get("missing"):
            continue
        links = page.get("langlinks")
        if not isinstance(links, list):
            continue
        for link in links:
            if not isinstance(link, dict):
                continue
            if link.get("lang") == "en":
                name = link.get("*")
                if isinstance(name, str) and name.strip():
                    english = _normalize_english_wikipedia_title(name)
                    if not english or _is_disambiguation_title(english):
                        return None
                    return english
    return None


def _search_hits(title: str, year: int | None) -> list[str]:
    search = title
    if year is not None:
        search = f"{title} {year}"
    parsed = _wiki_get(
        {
            "action": "query",
            "list": "search",
            "srsearch": search,
            "srlimit": "6",
        }
    )
    if not parsed:
        return []
    query = parsed.get("query")
    if not isinstance(query, dict):
        return []
    search_block = query.get("search")
    if not isinstance(search_block, list):
        return []
    hits: list[str] = []
    for item in search_block:
        if not isinstance(item, dict):
            continue
        hit_title = item.get("title")
        if isinstance(hit_title, str) and hit_title.strip():
            hits.append(hit_title.strip())
    return hits


def _pick_search_page(hits: list[str], year: int | None) -> list[str]:
    if year is None:
        return hits
    year_text = str(year)
    with_year = [hit for hit in hits if year_text in hit]
    without_noise = [
        hit
        for hit in (with_year or hits)
        if not re.search(r"\b(Manga|Roman|Computerspiel|Videospiel)\b", hit, re.I)
    ]
    preferred = [hit for hit in without_noise if re.search(r"\((Anime|Film|\d{4})\)", hit, re.I)]
    return preferred or without_noise or with_year or hits


def english_title_from_german_wikipedia(
    title: str,
    year: int | None = None,
) -> str | None:
    """English title linked from German Wikipedia, when this looks like a German name."""
    title = normalize_title_chars(title).strip()
    if not title or not likely_german_release_title(title):
        return None
    cache_key = (title.casefold(), year)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    found: str | None = None
    for page_title in _pick_search_page(_search_hits(title, year), year):
        found = _english_langlink(page_title)
        if found:
            break
    if not found:
        found = _english_langlink(title)

    _CACHE[cache_key] = found
    return found
