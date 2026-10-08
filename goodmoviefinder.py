#!/usr/bin/env python3
"""
Fetch Nonstop Kino program listings, look up Letterboxd ratings, sort best → worst.
"""

from __future__ import annotations

import argparse
import calendar
import html
import http.cookiejar
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
GENERATED_HTML = SCRIPT_DIR / "generated" / "program-ranked.html"
DEFAULT_CACHE = SCRIPT_DIR / "cache" / "ratings.json"
DEFAULT_PROGRAM_URL = (
    "https://nonstopkino.at/en/program/?weekday=all&time=all&location=wien"
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
CACHE_VERSION = 4
NONSTOP_LINK_QUERY = "weekday=all&time=all&location=wien"
DEFAULT_LETTERBOXD_USER = "hcanon"
PROFILE_CACHE_MAX_AGE = 6 * 60 * 60  # seconds
LETTERBOXD_SLUG_RE = re.compile(r'data-item-slug="([^"]+)"')
# Letterboxd encodes a viewer's score as rated-N, where N is half-stars (rated-7 = 3.5).
LETTERBOXD_USER_RATING_RE = re.compile(r'class="rating[^"]*\brated-(\d+)')
LETTERBOXD_NEXT_PAGE_RE = re.compile(
    r'<a class="next" href="([^"]+)"[^>]*>\s*Older\s*</a>',
    re.IGNORECASE,
)
PROGRAM_ARTICLE_RE = re.compile(
    r'<article class="event".*?</article>',
    re.IGNORECASE | re.DOTALL,
)
PRIMARY_LANGUAGE_RE = re.compile(
    r"Primary Language</span></h3>\s*"
    r'<div class="text-sluglist">\s*'
    r'<a href="/films/language/[^"]+"[^>]*>\s*([^<]+?)\s*</a>',
    re.IGNORECASE,
)
# Films with one language use this heading instead of "Primary Language".
SINGLE_LANGUAGE_RE = re.compile(
    r"<h3><span>Language</span></h3>\s*"
    r'<div class="text-sluglist">\s*'
    r'<a href="/films/language/[^"]+"[^>]*>\s*([^<]+?)\s*</a>',
    re.IGNORECASE,
)
LANGUAGE_PARSER = 2
# Bump to re-check cached Letterboxd pages against Nonstop director, runtime, and cast.
MATCHER_VERSION = 3
# Runtimes within this many minutes count as the same cut.
DURATION_CLOSE_MINUTES = 5
# A larger gap means a different film (a short versus a feature, for example).
DURATION_CONFLICT_MINUTES = 20
# Bump to re-read original titles for films already cached with a Letterboxd page.
TITLE_PARSER = 1
UNDERSTOOD_PRIMARY_LANGUAGES = frozenset({"english", "french"})
UNDERSTOOD_AUDIO_VERSIONS = frozenset({"OV", "OmdU", "OmeU"})
ENGLISH_SUBTITLE_VERSIONS = frozenset({"OmeU"})
VERSION_ORDER = ("OV", "OmdU", "OmeU", "DF")
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_MONTHS_SHORT = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def nonstop_display_url(url: str) -> str:
    """Nonstop links in output include default program filters."""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{NONSTOP_LINK_QUERY}"


def letterboxd_slug_from_url(url: str | None) -> str | None:
    if not url:
        return None
    path = urllib.parse.urlparse(url).path.rstrip("/")
    parts = path.split("/film/")
    if len(parts) < 2 or not parts[-1]:
        return None
    return parts[-1].split("/")[0] or None


def letterboxd_profile_url(user: str, section: str, page: int = 1) -> str:
    base = f"https://letterboxd.com/{user}/{section}/"
    if page <= 1:
        return base
    return f"{base}page/{page}/"


def parse_letterboxd_profile_slugs(html_text: str) -> set[str]:
    return set(LETTERBOXD_SLUG_RE.findall(html_text))


def parse_letterboxd_watched_ratings(html_text: str) -> dict[str, float | None]:
    """Map each film slug on a watched page to that viewer's rating."""
    ratings: dict[str, float | None] = {}
    for chunk in html_text.split('<li class="griditem">')[1:]:
        end = chunk.find("</li>")
        item = chunk if end < 0 else chunk[:end]
        slug_match = LETTERBOXD_SLUG_RE.search(item)
        if not slug_match:
            continue
        rated = LETTERBOXD_USER_RATING_RE.search(item)
        ratings[slug_match.group(1)] = int(rated.group(1)) / 2 if rated else None
    return ratings


def _iter_letterboxd_profile_pages(user: str, section: str, delay: float):
    page = 1
    opener = make_cookie_opener()
    profile_delay = max(delay, 1.0)

    while page <= 50:
        url = letterboxd_profile_url(user, section, page)
        html_text = None
        for attempt in range(4):
            try:
                html_text = fetch(url, opener=opener)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in {403, 429, 503} and attempt < 3:
                    time.sleep(profile_delay * (attempt + 2))
                    continue
                raise RuntimeError(f"Failed to fetch {url}: HTTP {exc.code}") from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"Failed to fetch {url}: {exc.reason}") from exc

        if html_text is None:
            raise RuntimeError(f"Failed to fetch {url}")

        yield html_text
        if not LETTERBOXD_NEXT_PAGE_RE.search(html_text):
            break
        page += 1
        time.sleep(profile_delay)


def fetch_letterboxd_profile_slugs(
    user: str,
    section: str,
    delay: float,
) -> set[str]:
    slugs: set[str] = set()
    for html_text in _iter_letterboxd_profile_pages(user, section, delay):
        slugs |= parse_letterboxd_profile_slugs(html_text)
    return slugs


def fetch_letterboxd_watched_ratings(
    user: str,
    delay: float,
) -> dict[str, float | None]:
    ratings: dict[str, float | None] = {}
    for html_text in _iter_letterboxd_profile_pages(user, "films", delay):
        ratings.update(parse_letterboxd_watched_ratings(html_text))
    return ratings


def load_profile_cache(
    cache: dict[str, Any],
    user: str,
) -> tuple[dict[str, float | None], set[str]] | None:
    profile = cache.get("profile", {}).get(user)
    if not profile:
        return None
    fetched_at = profile.get("fetched_at", 0)
    if time.time() - fetched_at > PROFILE_CACHE_MAX_AGE:
        return None
    raw_ratings = profile.get("watched_ratings")
    if not isinstance(raw_ratings, dict):
        return None
    watched = {
        slug: None if value is None else float(value)
        for slug, value in raw_ratings.items()
    }
    watchlist = set(profile.get("watchlist", []))
    return watched, watchlist


def save_profile_cache(
    cache: dict[str, Any],
    user: str,
    watched_ratings: dict[str, float | None],
    watchlist: set[str],
) -> None:
    cache.setdefault("profile", {})[user] = {
        "watched": sorted(watched_ratings),
        "watched_ratings": watched_ratings,
        "watchlist": sorted(watchlist),
        "fetched_at": int(time.time()),
    }


@dataclass
class Screening:
    weekday: str
    time: str
    language: str
    venue_slug: str
    venue_name: str
    city: str


@dataclass
class ProgramFilm:
    title: str
    url: str
    versions: frozenset[str]
    poster_url: str | None
    screenings: list[Screening]


@dataclass
class NonstopFilmPage:
    title: str | None
    year: int | None
    director: str | None
    synopsis: str | None
    cinema_sites: dict[str, str]
    cast: list[str] = field(default_factory=list)
    duration_minutes: int | None = None


@dataclass
class Movie:
    title: str
    nonstop_url: str
    letterboxd_url: str | None = None
    letterboxd_title: str | None = None
    original_title: str | None = None
    original_language: str | None = None
    titles_known: bool = False
    rating: float | None = None
    year: int | None = None
    directors: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    primary_language: str | None = None
    primary_language_known: bool = False
    versions: list[str] = field(default_factory=list)
    poster_url: str | None = None
    synopsis: str | None = None
    cinema_sites: dict[str, str] = field(default_factory=dict)
    details_known: bool = False
    screenings: list[Screening] = field(default_factory=list)
    note: str | None = None
    user_rating: float | None = None
    # False when a cached Letterboxd link was stored by an older matcher.
    match_current: bool = True


def _request_headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }


def make_cookie_opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def fetch(
    url: str,
    timeout: float = 45.0,
    opener: urllib.request.OpenerDirector | None = None,
) -> str:
    req = urllib.request.Request(url, headers=_request_headers())
    if opener is not None:
        resp_cm = opener.open(req, timeout=timeout)
    else:
        resp_cm = urllib.request.urlopen(req, timeout=timeout)
    with resp_cm as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def fetch_status(url: str, timeout: float = 45.0) -> tuple[int, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        body = resp.read().decode(charset, errors="replace")
        return resp.status, resp.geturl(), body


def normalize_title_chars(title: str) -> str:
    title = html.unescape(title).strip()
    for uchar, replacement in (
        ("\u2019", "'"),
        ("\u2018", "'"),
        ("\u201c", '"'),
        ("\u201d", '"'),
    ):
        title = title.replace(uchar, replacement)
    return title


def parenthetical_segments(title: str) -> list[str]:
    title = normalize_title_chars(title)
    segments: list[str] = []
    for match in re.finditer(r"\(([^)]+)\)", title):
        inner = match.group(1).strip()
        if inner:
            segments.append(inner)
    return segments


def slugify(title: str) -> str:
    title = normalize_title_chars(title)
    title = unicodedata.normalize("NFKD", title)
    title = title.encode("ascii", "ignore").decode("ascii")
    title = title.lower().replace("&", "and")
    title = re.sub(r"[^a-z0-9]+", "-", title)
    return title.strip("-")


def search_titles_from_display_title(title: str) -> list[str]:
    title = normalize_title_chars(title)
    titles: list[str] = []
    for inner in parenthetical_segments(title):
        if re.search(r"[A-Za-z]", inner):
            titles.append(inner)
    stripped = re.sub(r"\s*\([^)]+\)\s*", " ", title).strip()
    if stripped:
        titles.append(stripped)
    if not titles:
        titles.append(title)
    seen: set[str] = set()
    out: list[str] = []
    for t in titles:
        key = t.casefold()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def parenthetical_search_queries(
    display_title: str,
    nonstop_page_title: str | None,
) -> list[str]:
    """Alternate titles in parentheses, for Letterboxd search when slugs fail."""
    seen: set[str] = set()
    queries: list[str] = []
    for title in (display_title, nonstop_page_title):
        if not title:
            continue
        for inner in parenthetical_segments(title):
            key = inner.casefold()
            if key not in seen:
                seen.add(key)
                queries.append(inner)
    return queries


_TITLE_PIECE_RE = re.compile(r"\s+(?:[–—]|-)\s+|\s*:\s*")
_YEAR_OR_RANGE_RE = re.compile(r"^\d{4}(?:\s*[–—-]\s*\d{4})?$")


def _usable_search_query(text: str) -> bool:
    text = text.strip()
    if len(text) < 2:
        return False
    return _YEAR_OR_RANGE_RE.match(text) is None


def flexible_search_queries(
    display_title: str,
    nonstop_page_title: str | None,
) -> list[str]:
    """Titles to try with Letterboxd autocomplete after slug guesses miss.

    Order: full titles, parenthetical alternates, then pieces split on
    spaced dashes and colons. Year ranges and tiny fragments are skipped.
    """
    seen: set[str] = set()
    queries: list[str] = []

    def add(raw: str | None) -> None:
        if not raw:
            return
        text = normalize_title_chars(raw).strip()
        if not _usable_search_query(text):
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        queries.append(text)

    titles = [display_title]
    if nonstop_page_title and nonstop_page_title != display_title:
        titles.append(nonstop_page_title)
    for title in titles:
        add(title)
    for inner in parenthetical_search_queries(display_title, nonstop_page_title):
        add(inner)
    for title in titles:
        normalized = normalize_title_chars(title)
        for piece in _TITLE_PIECE_RE.split(normalized):
            add(piece)
    return queries


def _name_fold(name: str) -> str:
    name = normalize_title_chars(name)
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z]+", " ", name).strip().casefold()


def _surnames(name: str) -> set[str]:
    folded = _name_fold(name)
    if not folded:
        return set()
    return {folded.split()[-1]}


def director_overlaps(
    nonstop_director: str | None,
    letterboxd_directors: list[str],
) -> bool:
    """True when a Nonstop credit shares a surname with a Letterboxd director."""
    if not nonstop_director or not letterboxd_directors:
        return False
    left: set[str] = set()
    for chunk in re.split(r"\s*(?:,|&|\band\b)\s*", nonstop_director):
        left |= _surnames(chunk)
    right: set[str] = set()
    for name in letterboxd_directors:
        right |= _surnames(name)
    left.discard("")
    return bool(left & right)


def _name_tokens(name: str) -> list[str]:
    return [token for token in _name_fold(name).split() if len(token) > 1]


def people_overlap_count(left: list[str], right: list[str]) -> int:
    """How many people appear on both credit lists.

    A shared surname counts when at least one other name token also matches,
    so two different people who only share a last name are not treated as one.
    """
    used: set[int] = set()
    count = 0
    for name in left:
        tokens = _name_tokens(name)
        if not tokens:
            continue
        surname = tokens[-1]
        for index, other in enumerate(right):
            if index in used:
                continue
            other_tokens = _name_tokens(other)
            if not other_tokens or other_tokens[-1] != surname:
                continue
            if len(tokens) >= 2 and len(other_tokens) >= 2:
                if not (set(tokens[:-1]) & set(other_tokens[:-1])):
                    continue
            used.add(index)
            count += 1
            break
    return count


def parse_iso_duration_minutes(value: str) -> int | None:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value.strip())
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    if hours == 0 and minutes == 0 and seconds == 0:
        return None
    total = hours * 60 + minutes
    if seconds >= 30:
        total += 1
    return total


def duration_relation(left: int | None, right: int | None) -> str:
    """'close', 'conflict', or 'unknown'."""
    if left is None or right is None:
        return "unknown"
    delta = abs(left - right)
    if delta <= DURATION_CLOSE_MINUTES:
        return "close"
    if delta >= DURATION_CONFLICT_MINUTES:
        return "conflict"
    return "unknown"


def _director_status(
    nonstop_director: str | None,
    letterboxd_directors: list[str],
) -> bool | None:
    if not nonstop_director or not letterboxd_directors:
        return None
    return director_overlaps(nonstop_director, letterboxd_directors)


def film_identity_confirms(
    nonstop_year: int | None,
    nonstop_director: str | None,
    nonstop_cast: list[str],
    nonstop_duration: int | None,
    letterboxd_year: int | None,
    letterboxd_directors: list[str],
    letterboxd_actors: list[str],
    letterboxd_duration: int | None,
) -> bool:
    """True when director, runtime, or cast show this is the Nonstop film."""
    director = _director_status(nonstop_director, letterboxd_directors)
    cast_count = (
        people_overlap_count(nonstop_cast, letterboxd_actors)
        if nonstop_cast and letterboxd_actors
        else 0
    )
    duration = duration_relation(nonstop_duration, letterboxd_duration)
    year_delta = (
        abs(nonstop_year - letterboxd_year)
        if nonstop_year is not None and letterboxd_year is not None
        else None
    )
    if director is False and cast_count < 2:
        return False
    if cast_count >= 2 and duration != "conflict":
        return True
    if cast_count >= 1 and duration == "close" and director is not False:
        return True
    if director is True and duration == "close":
        return True
    if (
        director is True
        and duration != "conflict"
        and year_delta is not None
        and year_delta <= 1
    ):
        return True
    if (
        director is None
        and not nonstop_director
        and not nonstop_cast
        and duration != "conflict"
        and year_delta == 0
    ):
        return True
    if (
        director is None
        and duration == "close"
        and year_delta is not None
        and year_delta <= 1
    ):
        return True
    return False


def film_identity_conflicts(
    nonstop_year: int | None,
    nonstop_director: str | None,
    nonstop_cast: list[str],
    nonstop_duration: int | None,
    letterboxd_year: int | None,
    letterboxd_directors: list[str],
    letterboxd_actors: list[str],
    letterboxd_duration: int | None,
) -> bool:
    """True when this Letterboxd film is a different movie with a similar title."""
    if film_identity_confirms(
        nonstop_year,
        nonstop_director,
        nonstop_cast,
        nonstop_duration,
        letterboxd_year,
        letterboxd_directors,
        letterboxd_actors,
        letterboxd_duration,
    ):
        return False
    director = _director_status(nonstop_director, letterboxd_directors)
    cast_count = (
        people_overlap_count(nonstop_cast, letterboxd_actors)
        if nonstop_cast and letterboxd_actors
        else 0
    )
    duration = duration_relation(nonstop_duration, letterboxd_duration)
    year_delta = (
        abs(nonstop_year - letterboxd_year)
        if nonstop_year is not None and letterboxd_year is not None
        else None
    )
    if director is False:
        return True
    if duration == "conflict" and cast_count == 0:
        return True
    if nonstop_cast and letterboxd_actors and cast_count == 0 and director is not True:
        return True
    # Same director credit, but a distant year and no runtime or cast support.
    if (
        director is True
        and year_delta is not None
        and year_delta > 1
        and duration != "close"
        and cast_count == 0
        and (nonstop_duration is not None or bool(nonstop_cast))
    ):
        return True
    return False


def letterboxd_autocomplete_slugs(
    query: str,
    delay: float,
    limit: int = 5,
) -> list[str]:
    query = query.strip()
    if not query:
        return []
    url = (
        "https://letterboxd.com/s/autocompletefilm?q="
        + urllib.parse.quote(query)
    )
    try:
        body = fetch(url)
    except urllib.error.URLError:
        return []
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return []
    slugs: list[str] = []
    for item in payload.get("data") or []:
        if item.get("type") != "film":
            continue
        slug = item.get("slug")
        if not slug:
            continue
        slugs.append(slug)
        if len(slugs) >= limit:
            break
    time.sleep(delay)
    return slugs


def slug_from_nonstop_url(nonstop_url: str) -> str:
    path = urllib.parse.urlparse(nonstop_url).path.rstrip("/")
    slug = path.split("/")[-1]
    slug = re.sub(r"-\d+$", "", slug)
    return slug


def ordered_versions(codes: set[str]) -> list[str]:
    known = [code for code in VERSION_ORDER if code in codes]
    extra = sorted(codes - set(VERSION_ORDER))
    return known + extra


def _plain_chunk(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", "", fragment)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _parse_clock_time(article: str) -> str:
    normal = re.search(
        r'<div class="normal">\s*(.*?)\s*</div>',
        article,
        re.IGNORECASE | re.DOTALL,
    )
    blob = normal.group(1) if normal else ""
    match = re.search(r"(\d{1,2}:\d{2})", blob)
    return match.group(1) if match else ""


def _parse_venue_city(article: str) -> tuple[str, str]:
    match = re.search(
        r'<div class="location">\s*(.*?)\s*</div>',
        article,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return "", ""
    parts = re.split(r"<br\s*/?>", match.group(1), maxsplit=1, flags=re.IGNORECASE)
    venue = _plain_chunk(parts[0])
    city = _plain_chunk(parts[1]) if len(parts) > 1 else ""
    return venue, city


def _parse_poster_url(article: str) -> str | None:
    match = re.search(r'<img[^>]+src="([^"]+)"', article, re.IGNORECASE)
    if not match:
        return None
    url = html.unescape(match.group(1)).strip()
    return url or None


def parse_screening(article: str) -> Screening | None:
    weekday_match = re.search(r'data-weekday="(\d{4}-\d{2}-\d{2})"', article)
    if not weekday_match:
        return None
    venue_match = re.search(r'data-venue="([^"]*)"', article)
    language_match = re.search(r'data-language="([^"]*)"', article)
    venue_slug = venue_match.group(1).strip() if venue_match else ""
    language = language_match.group(1).strip() if language_match else ""
    venue_name, city = _parse_venue_city(article)
    if not venue_name and venue_slug:
        venue_name = venue_slug.replace("-", " ").title()
    return Screening(
        weekday=weekday_match.group(1),
        time=_parse_clock_time(article),
        language=language,
        venue_slug=venue_slug,
        venue_name=venue_name,
        city=city,
    )


def parse_program(html_text: str) -> list[ProgramFilm]:
    films: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for article in PROGRAM_ARTICLE_RE.findall(html_text):
        url_match = re.search(
            r'href="(https://nonstopkino\.at/en/movies/[^"]+)"',
            article,
        )
        if not url_match:
            continue
        url = url_match.group(1)
        title_match = re.search(r"<h3>\s*(.*?)\s*</h3>", article, re.DOTALL)
        title = url
        if title_match:
            title = re.sub(r"\s+", " ", html.unescape(title_match.group(1))).strip()
        if url not in films:
            films[url] = {
                "title": title,
                "versions": set(),
                "poster_url": _parse_poster_url(article),
                "screenings": [],
                "seen": set(),
            }
            order.append(url)
        entry = films[url]
        screening = parse_screening(article)
        if screening is None:
            continue
        if screening.language:
            entry["versions"].add(screening.language)
        key = (
            screening.weekday,
            screening.time,
            screening.venue_slug,
            screening.language,
        )
        if key not in entry["seen"]:
            entry["seen"].add(key)
            entry["screenings"].append(screening)
        if entry["poster_url"] is None:
            entry["poster_url"] = _parse_poster_url(article)
    programs: list[ProgramFilm] = []
    for url in order:
        entry = films[url]
        screenings = sorted(
            entry["screenings"],
            key=lambda s: (s.weekday, s.time, s.venue_name, s.language),
        )
        programs.append(
            ProgramFilm(
                title=entry["title"],
                url=url,
                versions=frozenset(entry["versions"]),
                poster_url=entry["poster_url"],
                screenings=screenings,
            )
        )
    return programs


def parse_synopsis(html_text: str) -> str | None:
    chunks = re.findall(
        r'<p class="description">\s*(.*?)\s*</p>',
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    best = ""
    for chunk in chunks:
        text = re.sub(r"<br\s*/?>", "\n", chunk, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) > len(best):
            best = text
    return best or None


def parse_cinema_sites(html_text: str) -> dict[str, str]:
    sites: dict[str, str] = {}
    for article in PROGRAM_ARTICLE_RE.findall(html_text):
        venue_match = re.search(r'data-venue="([^"]*)"', article)
        if not venue_match:
            continue
        venue = venue_match.group(1).strip()
        if not venue or venue in sites:
            continue
        for tag in re.findall(r"<a\b[^>]*>", article, re.IGNORECASE):
            class_match = re.search(r'class="([^"]*)"', tag)
            href_match = re.search(r'href="([^"]+)"', tag)
            if not class_match or not href_match:
                continue
            classes = class_match.group(1).split()
            if "button" not in classes or "ics" in classes:
                continue
            url = html.unescape(href_match.group(1)).strip()
            if url:
                sites[venue] = url
                break
    return sites


def _meta_class_value(html_text: str, class_name: str) -> str | None:
    match = re.search(
        rf'class="{class_name}".*?class="value">(.*?)</span>',
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    text = re.sub(r"<[^>]+>", " ", match.group(1))
    text = normalize_title_chars(re.sub(r"\s+", " ", html.unescape(text)).strip())
    return text or None


def _parse_nonstop_cast(html_text: str) -> list[str]:
    raw = _meta_class_value(html_text, "cast")
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_nonstop_duration(html_text: str) -> int | None:
    raw = _meta_class_value(html_text, "duration")
    if not raw:
        return None
    match = re.search(r"(\d+)", raw)
    if not match:
        return None
    return int(match.group(1))


def parse_nonstop_movie_page(html_text: str) -> NonstopFilmPage:
    h1 = re.search(r"<h1>\s*(.*?)\s*</h1>", html_text, re.IGNORECASE | re.DOTALL)
    title = None
    if h1:
        title = normalize_title_chars(re.sub(r"\s+", " ", h1.group(1)).strip())

    year_match = re.search(
        r'class="releaseYear".*?class="value">\s*(\d{4})\s*<',
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    year = int(year_match.group(1)) if year_match else None

    return NonstopFilmPage(
        title=title,
        year=year,
        director=_meta_class_value(html_text, "director"),
        synopsis=parse_synopsis(html_text),
        cinema_sites=parse_cinema_sites(html_text),
        cast=_parse_nonstop_cast(html_text),
        duration_minutes=_parse_nonstop_duration(html_text),
    )


def parse_primary_language(html_text: str) -> str | None:
    match = PRIMARY_LANGUAGE_RE.search(html_text) or SINGLE_LANGUAGE_RE.search(html_text)
    if not match:
        return None
    name = html.unescape(match.group(1)).strip()
    return name or None


_ORIGINAL_NAME_RE = re.compile(
    r"<h2\b([^>]*\boriginalname\b[^>]*)>\s*"
    r'<em\b[^>]*\bquoted-creative-work-title\b[^>]*>\s*(.*?)\s*</em>',
    re.IGNORECASE | re.DOTALL,
)


def parse_original_title(html_text: str) -> tuple[str | None, str | None]:
    """Letterboxd's original title and its language code, when the page shows one."""
    match = _ORIGINAL_NAME_RE.search(html_text)
    if not match:
        return None, None
    lang_match = re.search(r'\blang="([^"]+)"', match.group(1), re.IGNORECASE)
    language = lang_match.group(1).strip() if lang_match else None
    title = re.sub(r"<[^>]+>", "", match.group(2))
    title = re.sub(r"\s+", " ", html.unescape(title)).strip()
    return (title or None), (language or None)


def film_title(movie: Movie) -> str:
    """English Letterboxd title, or the French original for French and Quebec films."""
    language = (movie.original_language or "").casefold()
    if language.startswith("fr") and movie.original_title:
        return movie.original_title
    return movie.letterboxd_title or movie.title


def _parse_letterboxd_actors_and_runtime(
    html_text: str,
) -> tuple[list[str], int | None]:
    duration = None
    duration_match = re.search(
        r'"duration":"(PT(?:\d+H)?(?:\d+M)?(?:\d+S)?)"',
        html_text,
    )
    if duration_match:
        duration = parse_iso_duration_minutes(duration_match.group(1))
    actors: list[str] = []
    actor_match = re.search(
        r'"actor":\s*\[(.*?)\]\s*,\s*"dateCreated"',
        html_text,
        re.DOTALL,
    )
    if actor_match:
        actors = [
            html.unescape(name)
            for name in re.findall(r'"name":"([^"]+)"', actor_match.group(1))
        ]
    return actors, duration


def parse_letterboxd_film(
    html_text: str,
) -> tuple[
    str | None,
    int | None,
    float | None,
    list[str],
    list[str],
    str | None,
    str | None,
    str | None,
    list[str],
    int | None,
]:
    name = None
    year = None
    rating = None
    genres: list[str] = []
    directors: list[str] = []
    primary_language = parse_primary_language(html_text)
    original_title, original_language = parse_original_title(html_text)
    actors, duration_minutes = _parse_letterboxd_actors_and_runtime(html_text)

    name_match = re.search(r'"name":"([^"]+)","genre"', html_text)
    if name_match:
        name = html.unescape(name_match.group(1))

    year_match = re.search(r'"dateCreated":"(\d{4})', html_text)
    if year_match:
        year = int(year_match.group(1))

    director_block = re.search(
        r'"director":\s*(\[.*?\])\s*,\s*"description"',
        html_text,
    )
    if director_block:
        directors = [
            html.unescape(n)
            for n in re.findall(r'"name":"([^"]+)"', director_block.group(1))
        ]

    genre_block = re.search(r'"genre":(\[[^\]]+\])', html_text)
    if genre_block:
        genres = [html.unescape(g) for g in re.findall(r'"([^"]+)"', genre_block.group(1))]

    rating_match = re.search(
        r'"aggregateRating":\{[^}]*"ratingValue":([0-9.]+)',
        html_text,
    )
    if rating_match:
        rating = float(rating_match.group(1))
    else:
        twitter = re.search(
            r'name="twitter:data2"\s+content="([0-9.]+)\s+out of 5"',
            html_text,
        )
        if twitter:
            rating = float(twitter.group(1))

    return (
        name,
        year,
        rating,
        genres,
        directors,
        primary_language,
        original_title,
        original_language,
        actors,
        duration_minutes,
    )


def apply_nonstop_metadata(
    movie: Movie,
    release_year: int | None,
    nonstop_director: str | None,
) -> None:
    if release_year is not None and movie.year is None:
        movie.year = release_year
    if nonstop_director and not movie.directors:
        movie.directors = [nonstop_director]


def apply_letterboxd_candidate(
    movie: Movie,
    url: str,
    lb_title: str | None,
    lb_year: int | None,
    rating: float | None,
    genres: list[str],
    directors: list[str],
    primary_language: str | None,
    original_title: str | None,
    original_language: str | None,
) -> None:
    movie.letterboxd_url = url
    movie.letterboxd_title = lb_title
    movie.original_title = original_title
    movie.original_language = original_language
    movie.titles_known = True
    if lb_year is not None:
        movie.year = lb_year
    movie.rating = rating
    if genres:
        movie.genres = genres
    if directors:
        movie.directors = directors
    movie.primary_language = primary_language
    movie.primary_language_known = True


def letterboxd_slug_candidates(
    display_title: str,
    nonstop_url: str,
    nonstop_page_title: str | None,
    release_year: int | None,
) -> list[str]:
    slugs: list[str] = []
    url_slug = slug_from_nonstop_url(nonstop_url)
    if url_slug:
        slugs.append(url_slug)

    for title in search_titles_from_display_title(display_title):
        slugs.append(slugify(title))

    if nonstop_page_title:
        slugs.append(slugify(nonstop_page_title))

    if release_year:
        for base in list(slugs):
            slugs.append(f"{base}-{release_year}")

    seen: set[str] = set()
    ordered: list[str] = []
    for slug in slugs:
        if slug and slug not in seen:
            seen.add(slug)
            ordered.append(slug)
    return ordered


def collision_slug_candidates(
    display_title: str,
    nonstop_url: str,
    nonstop_page_title: str | None,
    release_year: int | None,
) -> list[str]:
    """Year and article variants for when the obvious slug is a different film.

    Letterboxd disambiguates duplicate titles with a year (`obsession-2025`)
    and a numeric suffix (`obsession-2026-1`). Off-by-one years are included
    because Nonstop's release year often differs from Letterboxd's.
    """
    if release_year is None:
        return []
    bases = letterboxd_slug_candidates(
        display_title, nonstop_url, nonstop_page_title, None
    )
    extended: list[str] = []
    for base in bases:
        extended.append(base)
        if not base.startswith("the-"):
            extended.append(f"the-{base}")
    slugs: list[str] = []
    for delta in (0, -1, 1, -2, 2):
        year = release_year + delta
        for base in extended:
            slugs.append(f"{base}-{year}")
    for delta in (0, -1, 1):
        year = release_year + delta
        for base in extended:
            for suffix in (1, 2, 3):
                slugs.append(f"{base}-{year}-{suffix}")
    return slugs


LetterboxdCandidate = tuple[
    str,
    str | None,
    int | None,
    float | None,
    list[str],
    list[str],
    str | None,
    str | None,
    str | None,
    list[str],
    int | None,
]


def _letterboxd_fetch_candidate(
    slug: str,
    delay: float,
) -> tuple[LetterboxdCandidate | None, str | None]:
    """Fetch a film page by slug. Returns (candidate, fatal_error_note)."""
    url = f"https://letterboxd.com/film/{slug}/"
    try:
        status, final_url, body = fetch_status(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, None
        return None, f"Letterboxd error: {exc}"
    except urllib.error.URLError as exc:
        return None, f"Network error: {exc.reason}"

    if status != 200 or "/film/" not in final_url:
        return None, None

    (
        lb_title,
        lb_year,
        rating,
        genres,
        directors,
        primary_language,
        original_title,
        original_language,
        actors,
        duration_minutes,
    ) = parse_letterboxd_film(body)
    candidate = (
        final_url.rstrip("/") + "/",
        lb_title,
        lb_year,
        rating,
        genres,
        directors,
        primary_language,
        original_title,
        original_language,
        actors,
        duration_minutes,
    )
    time.sleep(delay)
    return candidate, None


def resolve_letterboxd(
    display_title: str,
    nonstop_url: str,
    nonstop_page_title: str | None,
    release_year: int | None,
    nonstop_director: str | None,
    delay: float,
    nonstop_cast: list[str] | None = None,
    nonstop_duration: int | None = None,
) -> Movie:
    cast = list(nonstop_cast or [])
    movie = Movie(
        title=display_title,
        nonstop_url=nonstop_url,
        year=release_year,
        primary_language_known=True,
        titles_known=True,
    )
    candidates: list[LetterboxdCandidate] = []
    tried_slugs: set[str] = set()

    def _identity(item: LetterboxdCandidate) -> tuple[bool, bool]:
        _, _, lb_year, _, _, directors, _, _, _, actors, duration = item
        confirms = film_identity_confirms(
            release_year,
            nonstop_director,
            cast,
            nonstop_duration,
            lb_year,
            directors,
            actors,
            duration,
        )
        conflicts = film_identity_conflicts(
            release_year,
            nonstop_director,
            cast,
            nonstop_duration,
            lb_year,
            directors,
            actors,
            duration,
        )
        return confirms, conflicts

    def _apply(item: LetterboxdCandidate) -> None:
        (
            url,
            lb_title,
            lb_year,
            rating,
            genres,
            directors,
            primary_language,
            original_title,
            original_language,
            _actors,
            _duration,
        ) = item
        apply_letterboxd_candidate(
            movie,
            url,
            lb_title,
            lb_year,
            rating,
            genres,
            directors,
            primary_language,
            original_title,
            original_language,
        )

    def _finish() -> Movie:
        if movie.note and not str(movie.note).startswith("Year mismatch"):
            return movie
        if release_year and movie.year and movie.year != release_year:
            movie.note = (
                f"Year mismatch (Nonstop {release_year}, Letterboxd {movie.year})"
            )
        return movie

    def consider_slug(slug: str) -> bool:
        """Fetch one slug. True means stop: identity confirmed, or Letterboxd failed."""
        if not slug or slug in tried_slugs:
            return False
        tried_slugs.add(slug)
        candidate, error = _letterboxd_fetch_candidate(slug, delay)
        if error:
            movie.note = error
            return True
        if candidate is None:
            return False
        confirms, conflicts = _identity(candidate)
        if conflicts:
            candidates.append(candidate)
            return False
        candidates.append(candidate)
        if confirms:
            _apply(candidate)
            return True
        return False

    def _search_queries() -> None:
        for query in flexible_search_queries(display_title, nonstop_page_title):
            for slug in letterboxd_autocomplete_slugs(query, delay, limit=8):
                if consider_slug(slug):
                    return

    for slug in letterboxd_slug_candidates(
        display_title, nonstop_url, nonstop_page_title, release_year
    ):
        if consider_slug(slug):
            return _finish()

    confirmed = [item for item in candidates if _identity(item)[0]]
    conflicting = [item for item in candidates if _identity(item)[1]]
    has_identity = bool(nonstop_director or cast or nonstop_duration is not None)
    if not confirmed and has_identity and (conflicting or not candidates):
        for slug in collision_slug_candidates(
            display_title, nonstop_url, nonstop_page_title, release_year
        ):
            if consider_slug(slug):
                return _finish()
        if not any(_identity(item)[0] for item in candidates):
            _search_queries()
            if movie.letterboxd_url:
                return _finish()
    elif not candidates:
        _search_queries()
        if movie.letterboxd_url:
            return _finish()

    if movie.note and not movie.letterboxd_url and "error" in movie.note.casefold():
        return movie

    usable = [item for item in candidates if not _identity(item)[1]]
    confirmed = [item for item in usable if _identity(item)[0]]
    pool = confirmed or usable
    if not pool:
        movie.note = "No Letterboxd match"
        movie.year = release_year
        return movie

    def score(item: LetterboxdCandidate) -> tuple[int, int, int, int, int, int]:
        _, _, lb_year, rating, _, directors, _, _, _, actors, duration = item
        director_match = 1 if director_overlaps(nonstop_director, directors) else 0
        cast_count = people_overlap_count(cast, actors) if cast and actors else 0
        duration_close = (
            1 if duration_relation(nonstop_duration, duration) == "close" else 0
        )
        year_match = 1 if release_year and lb_year == release_year else 0
        year_close = (
            1
            if release_year is not None
            and lb_year is not None
            and abs(release_year - lb_year) <= 1
            else 0
        )
        has_rating = 1 if rating is not None else 0
        return (
            director_match,
            cast_count,
            duration_close,
            year_match,
            year_close,
            has_rating,
        )

    _apply(max(pool, key=score))
    return _finish()


def load_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": CACHE_VERSION, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": CACHE_VERSION, "entries": {}}
    if data.get("version") != CACHE_VERSION:
        return {"version": CACHE_VERSION, "entries": {}}
    return data


def save_cache(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def cache_get(cache: dict[str, Any], nonstop_url: str) -> Movie | None:
    entry = cache.get("entries", {}).get(nonstop_url)
    if not entry:
        return None
    if not entry.get("letterboxd_url") and entry.get("matcher") != MATCHER_VERSION:
        return None
    language_known = (
        entry.get("language_parser") == LANGUAGE_PARSER
        and "primary_language" in entry
    )
    titles_known = entry.get("title_parser") == TITLE_PARSER or not entry.get(
        "letterboxd_url"
    )
    details_known = "synopsis" in entry and "cinema_sites" in entry
    cinema_sites = entry.get("cinema_sites") if details_known else {}
    return Movie(
        title=entry["title"],
        nonstop_url=nonstop_url,
        letterboxd_url=entry.get("letterboxd_url"),
        letterboxd_title=entry.get("letterboxd_title"),
        original_title=entry.get("original_title") if titles_known else None,
        original_language=entry.get("original_language") if titles_known else None,
        titles_known=titles_known,
        rating=entry.get("rating"),
        year=entry.get("year"),
        directors=entry.get("directors") or [],
        genres=entry.get("genres") or [],
        primary_language=entry.get("primary_language") if language_known else None,
        primary_language_known=language_known,
        synopsis=entry.get("synopsis") if details_known else None,
        cinema_sites=dict(cinema_sites or {}),
        details_known=details_known,
        note=entry.get("note"),
        match_current=entry.get("matcher") == MATCHER_VERSION,
    )


def cached_match_should_reresolve(page: NonstopFilmPage, movie: Movie) -> bool:
    """True when a stored Letterboxd link disagrees with the Nonstop credits.

    Year-mismatch notes are checked again too: the same director name can
    belong to a different film, and runtime or cast decides those.
    """
    if movie.note and movie.note.startswith("Year mismatch"):
        return True
    if (
        page.director
        and movie.directors
        and not director_overlaps(page.director, movie.directors)
    ):
        return True
    return False


def cache_put(cache: dict[str, Any], movie: Movie) -> None:
    entry: dict[str, Any] = {
        "title": movie.title,
        "letterboxd_url": movie.letterboxd_url,
        "letterboxd_title": movie.letterboxd_title,
        "rating": movie.rating,
        "year": movie.year,
        "directors": movie.directors,
        "genres": movie.genres,
        "note": movie.note,
        "matcher": MATCHER_VERSION,
    }
    if movie.titles_known:
        entry["original_title"] = movie.original_title
        entry["original_language"] = movie.original_language
        entry["title_parser"] = TITLE_PARSER
    if movie.primary_language_known:
        entry["primary_language"] = movie.primary_language
        entry["language_parser"] = LANGUAGE_PARSER
    if movie.details_known:
        entry["synopsis"] = movie.synopsis
        entry["cinema_sites"] = movie.cinema_sites
    cache.setdefault("entries", {})[movie.nonstop_url] = entry


def backfill_letterboxd_details(movie: Movie, delay: float) -> bool:
    """Fill primary language and original title for a cached Letterboxd film."""
    if movie.primary_language_known and movie.titles_known:
        return False
    if not movie.letterboxd_url:
        if not movie.primary_language_known:
            movie.primary_language = None
            movie.primary_language_known = True
        if not movie.titles_known:
            movie.original_title = None
            movie.original_language = None
            movie.titles_known = True
        return True
    try:
        body = fetch(movie.letterboxd_url)
    except urllib.error.URLError as exc:
        print(
            f"  Letterboxd lookup failed ({exc.reason}); keeping film.",
            file=sys.stderr,
        )
        return False
    if not movie.primary_language_known:
        movie.primary_language = parse_primary_language(body)
        movie.primary_language_known = True
    if not movie.titles_known:
        movie.original_title, movie.original_language = parse_original_title(body)
        movie.titles_known = True
    time.sleep(delay)
    return True


def backfill_nonstop_details(movie: Movie, delay: float) -> None:
    """Fill synopsis and cinema websites for a cached film."""
    if movie.details_known:
        return
    try:
        body = fetch(movie.nonstop_url)
    except urllib.error.URLError as exc:
        print(
            f"  Nonstop details failed ({exc.reason}); keeping film.",
            file=sys.stderr,
        )
        return
    page = parse_nonstop_movie_page(body)
    movie.synopsis = page.synopsis
    movie.cinema_sites = page.cinema_sites
    movie.details_known = True
    time.sleep(delay)


def attach_program(movie: Movie, film: ProgramFilm) -> None:
    movie.versions = ordered_versions(set(film.versions))
    movie.screenings = list(film.screenings)
    movie.poster_url = film.poster_url


def acceptable_versions(movie: Movie) -> list[str]:
    """Nonstop versions that count for this film's primary language."""
    listed = set(movie.versions)
    if movie.primary_language is None:
        return ordered_versions(listed)
    if movie.primary_language.casefold() in UNDERSTOOD_PRIMARY_LANGUAGES:
        return ordered_versions(listed & UNDERSTOOD_AUDIO_VERSIONS)
    return ordered_versions(listed & ENGLISH_SUBTITLE_VERSIONS)


def movie_is_watchable(movie: Movie) -> bool:
    """Unknown primary language is not filtered. Known languages need a matching version."""
    if movie.primary_language is None:
        return True
    return bool(acceptable_versions(movie))


def watchable_screenings(movie: Movie) -> list[Screening]:
    """Screenings whose version matches the same language rules as the film list."""
    if movie.primary_language is None:
        return list(movie.screenings)
    allowed = set(acceptable_versions(movie))
    return [screening for screening in movie.screenings if screening.language in allowed]


def venue_label(screening: Screening) -> str:
    if screening.venue_name and screening.city:
        return f"{screening.venue_name}, {screening.city}"
    return screening.venue_name or screening.city or screening.venue_slug or "Cinema"


def format_screening_date(weekday: str) -> str:
    try:
        day = date.fromisoformat(weekday)
    except ValueError:
        return weekday
    return f"{_WEEKDAYS[day.weekday()]} {day.day:02d} {_MONTHS_SHORT[day.month]}"


def format_language(movie: Movie) -> str:
    return movie.primary_language or "—"


def format_language_versions(movie: Movie) -> str:
    language = format_language(movie)
    codes = acceptable_versions(movie)
    if not codes:
        return language
    return f"{language} · {', '.join(codes)}"


def sort_movies(movies: list[Movie]) -> list[Movie]:
    def sort_key(m: Movie) -> tuple[int, float, str]:
        if m.rating is None:
            return (1, 0.0, film_title(m).casefold())
        return (0, -m.rating, film_title(m).casefold())

    return sorted(movies, key=sort_key)


def partition_program_movies(
    movies: list[Movie],
    watched_ratings: dict[str, float | None],
    watchlist_slugs: set[str],
) -> tuple[list[Movie], list[Movie], list[Movie]]:
    watchlist: list[Movie] = []
    watched: list[Movie] = []
    program: list[Movie] = []

    for movie in movies:
        slug = letterboxd_slug_from_url(movie.letterboxd_url)
        if slug and slug in watched_ratings:
            movie.user_rating = watched_ratings[slug]
            watched.append(movie)
            continue
        if slug and slug in watchlist_slugs:
            watchlist.append(movie)
            continue
        program.append(movie)

    return (
        sort_movies(watchlist),
        sort_movies(watched),
        sort_movies(program),
    )


def split_program_groups(movies: list[Movie]) -> tuple[list[Movie], list[Movie], list[Movie]]:
    """Rated films, Letterboxd matches with no score, and titles with no match."""
    rated: list[Movie] = []
    unrated: list[Movie] = []
    unfound: list[Movie] = []
    for movie in movies:
        if movie.rating is not None:
            rated.append(movie)
        elif movie.letterboxd_url:
            unrated.append(movie)
        else:
            unfound.append(movie)
    return sort_movies(rated), sort_movies(unrated), sort_movies(unfound)


def _format_year(m: Movie) -> str:
    return str(m.year) if m.year is not None else "—"


def _format_directors(m: Movie) -> str:
    return ", ".join(m.directors) if m.directors else "—"


def _format_genres(m: Movie) -> str:
    return ", ".join(m.genres) if m.genres else "—"


def _format_user_rating(rating: float | None) -> str:
    if rating is None:
        return "—"
    return f"{rating:.1f}"


# Text and badge colors blend between these scores. Stops past 4.2 and below
# 2.6 keep the ends of the Letterboxd scale from collapsing to one shade.
_RATING_COLOR_STOPS: tuple[tuple[float, tuple[int, int, int], tuple[int, int, int, float]], ...] = (
    (0.5, (220, 96, 86), (176, 58, 48, 0.28)),
    (2.6, (232, 138, 122), (196, 92, 74, 0.22)),
    (3.0, (232, 200, 90), (201, 162, 39, 0.20)),
    (3.4, (212, 222, 122), (168, 184, 74, 0.20)),
    (3.8, (159, 216, 106), (106, 171, 74, 0.22)),
    (4.2, (111, 212, 168), (61, 154, 110, 0.25)),
    (5.0, (126, 232, 214), (72, 186, 168, 0.32)),
)


def _lerp(start: float, end: float, t: float) -> float:
    return start + (end - start) * t


def _rating_colors(
    rating: float,
) -> tuple[tuple[int, int, int], tuple[int, int, int, float]]:
    stops = _RATING_COLOR_STOPS
    if rating <= stops[0][0]:
        return stops[0][1], stops[0][2]
    if rating >= stops[-1][0]:
        return stops[-1][1], stops[-1][2]
    for index in range(len(stops) - 1):
        low_rating, low_text, low_bg = stops[index]
        high_rating, high_text, high_bg = stops[index + 1]
        if low_rating <= rating <= high_rating:
            t = (rating - low_rating) / (high_rating - low_rating)
            text = tuple(round(_lerp(a, b, t)) for a, b in zip(low_text, high_text))
            background = (
                round(_lerp(low_bg[0], high_bg[0], t)),
                round(_lerp(low_bg[1], high_bg[1], t)),
                round(_lerp(low_bg[2], high_bg[2], t)),
                _lerp(low_bg[3], high_bg[3], t),
            )
            return text, background
    return stops[-1][1], stops[-1][2]


def _rating_style(rating: float | None) -> str:
    if rating is None:
        return ""
    (red, green, blue), (bg_red, bg_green, bg_blue, bg_alpha) = _rating_colors(rating)
    return (
        f"color: rgb({red},{green},{blue}); "
        f"background: rgba({bg_red},{bg_green},{bg_blue},{bg_alpha:.3f});"
    )


def _rating_badge(rating: float | None, text: str, extra_class: str = "", title: str = "") -> str:
    classes = " ".join(
        part
        for part in (
            "rating-badge",
            extra_class,
            "rating-none" if rating is None else "",
        )
        if part
    )
    style = _rating_style(rating)
    style_attr = f' style="{style}"' if style else ""
    title_attr = f' title="{html.escape(title, quote=True)}"' if title else ""
    return f'<span class="{classes}"{style_attr}{title_attr}>{text}</span>'


def _screenings_by_day(screenings: list[Screening]) -> dict[date, list[Screening]]:
    grouped: dict[date, list[Screening]] = {}
    for screening in screenings:
        try:
            day = date.fromisoformat(screening.weekday)
        except ValueError:
            continue
        grouped.setdefault(day, []).append(screening)
    return grouped


def _external_link(url: str, label: str, css_class: str = "") -> str:
    class_attr = f' class="{css_class}"' if css_class else ""
    return (
        f'<a{class_attr} href="{html.escape(url, quote=True)}" '
        f'target="_blank" rel="noopener noreferrer">{label}</a>'
    )


def _day_title(screenings: list[Screening]) -> str:
    parts = []
    for screening in screenings:
        clock = screening.time or "time TBC"
        parts.append(f"{clock} {venue_label(screening)}")
    return ", ".join(parts)


def _calendar_day_cell(
    day: date | None,
    by_day: dict[date, list[Screening]],
) -> str:
    if day is None:
        return '<span class="cal-day blank"></span>'
    screenings = by_day.get(day)
    if not screenings:
        return f'<span class="cal-day">{day.day}</span>'
    title = html.escape(_day_title(screenings), quote=True)
    return f'<span class="cal-day has" title="{title}">{day.day}</span>'


def _month_calendar_html(
    year: int,
    month: int,
    by_day: dict[date, list[Screening]],
) -> str:
    cal = calendar.Calendar(firstweekday=calendar.MONDAY)
    headers = "".join(f"<span>{name}</span>" for name in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"))
    cells: list[str] = []
    for week in cal.monthdayscalendar(year, month):
        for day_num in week:
            day = date(year, month, day_num) if day_num else None
            cells.append(_calendar_day_cell(day, by_day))
    caption = html.escape(f"{_MONTHS[month]} {year}")
    return (
        f'<div class="cal">'
        f'<div class="cal-caption">{caption}</div>'
        f'<div class="cal-grid cal-head">{headers}</div>'
        f'<div class="cal-grid">{"".join(cells)}</div>'
        f"</div>"
    )


def _showtimes_list_html(
    movie: Movie,
    by_day: dict[date, list[Screening]],
) -> str:
    blocks: list[str] = []
    for day in sorted(by_day):
        screenings = by_day[day]
        heading = html.escape(format_screening_date(day.isoformat()))
        blocks.append(
            f'<div class="showtime-day"><h3>{heading}</h3>'
            f"<ul>{_day_screening_items_html(movie, screenings)}</ul></div>"
        )
    return f'<div class="showtimes">{"".join(blocks)}</div>'


def _schedule_html(movie: Movie) -> str:
    by_day = _screenings_by_day(watchable_screenings(movie))
    if not by_day:
        return '<p class="muted">No screenings in a version you can follow.</p>'
    months: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for day in sorted(by_day):
        key = (day.year, day.month)
        if key not in seen:
            seen.add(key)
            months.append(key)
    calendars = "".join(
        _month_calendar_html(year, month, by_day) for year, month in months
    )
    return f'<div class="cal-row">{calendars}</div>{_showtimes_list_html(movie, by_day)}'


def _detail_panel_html(movie: Movie, detail_id: str) -> str:
    poster = ""
    if movie.poster_url:
        poster = (
            f'<img class="poster" src="{html.escape(movie.poster_url, quote=True)}" '
            f'alt="{html.escape(film_title(movie), quote=True)}">'
        )
    if movie.synopsis:
        synopsis = f'<p class="synopsis">{html.escape(movie.synopsis)}</p>'
    elif movie.details_known:
        synopsis = '<p class="synopsis muted">No synopsis available.</p>'
    else:
        synopsis = ""
    if movie.letterboxd_url:
        label = html.escape(film_title(movie))
        letterboxd = (
            f'<p class="detail-links">{_external_link(movie.letterboxd_url, label, "link-lb")}</p>'
        )
    else:
        letterboxd = ""
    return (
        f'<div class="detail" id="{html.escape(detail_id, quote=True)}">'
        f"{poster}"
        f'<div class="detail-body">{synopsis}{letterboxd}{_schedule_html(movie)}</div>'
        f"</div>"
    )


def _movie_search_blob(movie: Movie) -> str:
    no_follow = not watchable_screenings(movie)
    return html.escape(
        f"{film_title(movie)} {movie.title} {_format_directors(movie)} {_format_genres(movie)} "
        f"{movie.year or ''} {format_language_versions(movie)}"
        f"{' no followable version' if no_follow else ''}".lower()
    )


def _shift_month(year: int, month: int, delta: int = 1) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _calendar_groups(
    watchlist: list[Movie],
    watched: list[Movie],
    program: list[Movie],
) -> list[tuple[str, str, list[tuple[str, Movie]]]]:
    """Same section order and row ids as the list tables."""
    rated, unrated, unfound = split_program_groups(program)
    labeled = (
        ("watchlist", "Watchlist", watchlist),
        ("watched", "Watched", watched),
        ("program", "Program", rated),
        ("unrated", "Unrated", unrated),
        ("unfound", "Unfound", unfound),
    )
    groups: list[tuple[str, str, list[tuple[str, Movie]]]] = []
    for section_id, label, movies in labeled:
        if not movies:
            continue
        groups.append(
            (
                section_id,
                label,
                [(f"{section_id}-{index}", movie) for index, movie in enumerate(movies)],
            )
        )
    return groups


def _calendar_by_day(
    groups: list[tuple[str, str, list[tuple[str, Movie]]]],
) -> dict[date, list[tuple[str, str, str, Movie, list[Screening]]]]:
    by_day: dict[date, list[tuple[str, str, str, Movie, list[Screening]]]] = {}
    for section_id, label, movies in groups:
        for movie_id, movie in movies:
            for day, screenings in _screenings_by_day(watchable_screenings(movie)).items():
                by_day.setdefault(day, []).append(
                    (section_id, label, movie_id, movie, screenings)
                )
    return by_day


def _day_preview_title(
    entries: list[tuple[str, str, str, Movie, list[Screening]]],
) -> str:
    best = entries[0]
    best_rating = best[3].rating
    for entry in entries[1:]:
        rating = entry[3].rating
        if rating is not None and (best_rating is None or rating > best_rating):
            best = entry
            best_rating = rating
    return film_title(best[3])


def _day_screening_items_html(movie: Movie, screenings: list[Screening]) -> str:
    items: list[str] = []
    for screening in screenings:
        clock = html.escape(screening.time or "—")
        venue = html.escape(venue_label(screening))
        label = f"{clock} · {venue}"
        version = (
            f' <span class="version">{html.escape(screening.language)}</span>'
            if screening.language
            else ""
        )
        time_url = movie.cinema_sites.get(screening.venue_slug)
        when = _external_link(time_url, label, "showtime-link") if time_url else label
        items.append(f"<li>{when}{version}</li>")
    return "".join(items)


def _day_film_html(
    section_id: str,
    label: str,
    movie_id: str,
    movie: Movie,
    screenings: list[Screening],
) -> str:
    rating_text = f"{movie.rating:.2f}" if movie.rating is not None else "—"
    title = html.escape(film_title(movie))
    return (
        f'<article class="day-film" data-movie-id="{html.escape(movie_id, quote=True)}" '
        f'data-search="{_movie_search_blob(movie)}">'
        f'<div class="day-film-line">'
        f"{_rating_badge(movie.rating, rating_text)}"
        f'<button type="button" class="day-film-title" aria-expanded="false">{title}</button>'
        f'<span class="section-chip chip-{html.escape(section_id, quote=True)}">'
        f"{html.escape(label)}</span>"
        f"</div>"
        f'<ul class="day-times">{_day_screening_items_html(movie, screenings)}</ul>'
        f'<div class="day-film-detail" hidden></div>'
        f"</article>"
    )


def _day_panel_html(
    day: date,
    entries: list[tuple[str, str, str, Movie, list[Screening]]],
) -> str:
    films = "".join(
        _day_film_html(section_id, label, movie_id, movie, screenings)
        for section_id, label, movie_id, movie, screenings in entries
    )
    iso = day.isoformat()
    return f'<div class="month-band" id="day-{iso}" hidden>{films}</div>'


def _calendar_day_button(
    day: date,
    entries: list[tuple[str, str, str, Movie, list[Screening]]],
    today: date,
) -> str:
    today_class = " is-today" if day == today else ""
    if not entries:
        return (
            f'<span class="month-day blank{today_class}">'
            f'<span class="month-day-num">{day.day}</span></span>'
        )
    preview = _day_preview_title(entries)
    preview_attr = html.escape(preview, quote=True)
    preview_html = html.escape(preview)
    count = len(entries)
    iso = day.isoformat()
    return (
        f'<button type="button" class="month-day has{today_class}" '
        f'data-day="{iso}" data-count="{count}" data-title="{preview_attr}" '
        f'aria-expanded="false" aria-controls="day-{iso}">'
        f'<span class="month-day-num">{day.day}</span>'
        f'<span class="month-day-count">{count}</span>'
        f'<span class="month-day-title">{preview_html}</span>'
        f"</button>"
    )


def _month_board_html(
    year: int,
    month: int,
    by_day: dict[date, list[tuple[str, str, str, Movie, list[Screening]]]],
    today: date,
) -> str:
    cal = calendar.Calendar(firstweekday=calendar.MONDAY)
    headers = "".join(
        f"<span>{name}</span>" for name in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
    )
    weeks: list[str] = []
    for week in cal.monthdayscalendar(year, month):
        cells: list[str] = []
        panels: list[str] = []
        for day_num in week:
            if not day_num:
                cells.append('<span class="month-day pad"></span>')
                continue
            day = date(year, month, day_num)
            entries = by_day.get(day, [])
            cells.append(_calendar_day_button(day, entries, today))
            if entries:
                panels.append(_day_panel_html(day, entries))
        weeks.append(f'<div class="month-week">{"".join(cells)}{"".join(panels)}</div>')
    caption = html.escape(f"{_MONTHS[month]} {year}")
    return (
        f'<section class="month-board">'
        f'<h2 class="month-caption">{caption}</h2>'
        f'<div class="month-head">{headers}</div>'
        f'{"".join(weeks)}'
        f"</section>"
    )


def _also_showing_html(
    days: list[date],
    by_day: dict[date, list[tuple[str, str, str, Movie, list[Screening]]]],
) -> str:
    if not days:
        return ""
    blocks: list[str] = []
    for day in days:
        entries = by_day[day]
        preview = _day_preview_title(entries)
        preview_attr = html.escape(preview, quote=True)
        preview_html = html.escape(preview)
        count = len(entries)
        iso = day.isoformat()
        heading = html.escape(format_screening_date(iso))
        blocks.append(
            f'<div class="also-day">'
            f'<button type="button" class="also-day-button" '
            f'data-day="{iso}" data-count="{count}" data-title="{preview_attr}" '
            f'aria-expanded="false" aria-controls="day-{iso}">'
            f'<span class="month-day-num">{heading}</span>'
            f'<span class="month-day-count">{count}</span>'
            f'<span class="month-day-title">{preview_html}</span>'
            f"</button>"
            f"{_day_panel_html(day, entries)}"
            f"</div>"
        )
    return (
        f'<section class="also-showing">'
        f"<h2>Also showing</h2>"
        f'<div class="also-days">{"".join(blocks)}</div>'
        f"</section>"
    )


def _program_calendar_html(
    watchlist: list[Movie],
    watched: list[Movie],
    program: list[Movie],
    today: date | None = None,
) -> str:
    today = today or date.today()
    by_day = _calendar_by_day(_calendar_groups(watchlist, watched, program))
    current = (today.year, today.month)
    following = _shift_month(*current)
    shown = {current, following}
    boards = (
        _month_board_html(*current, by_day, today)
        + _month_board_html(*following, by_day, today)
    )
    outside = sorted(day for day in by_day if (day.year, day.month) not in shown)
    return (
        f'<div class="month-boards">{boards}</div>'
        f"{_also_showing_html(outside, by_day)}"
    )


def _format_html_movie_row(
    m: Movie,
    row_class: str = "",
    row_id: str = "",
    show_user_rating: bool = False,
) -> str:
    rating_text = f"{m.rating:.2f}" if m.rating is not None else "—"
    title = html.escape(film_title(m))
    if m.note:
        note_esc = html.escape(m.note)
        title += f' <span class="note" title="{note_esc}">⚠</span>'
    directors = html.escape(_format_directors(m))
    genres = _format_genres(m)
    genre_html = " ".join(
        f'<span class="genre">{html.escape(g.strip())}</span>'
        for g in genres.split(", ")
        if g.strip() and g != "—"
    )
    if not genre_html:
        genre_html = '<span class="muted">—</span>'
    year = html.escape(_format_year(m))
    no_follow = not watchable_screenings(m)
    follow_badge = ""
    if no_follow:
        follow_badge = (
            ' <span class="no-follow" title="No screenings in a version you can follow.">'
            "no followable version</span>"
        )
    classes = " ".join(
        part for part in (row_class, "row-no-follow" if no_follow else "") if part
    )
    extra = f' class="{classes}"' if classes else ""
    detail_classes = " ".join(part for part in ("detail-row", row_class) if part)
    language = html.escape(format_language(m))
    search_blob = _movie_search_blob(m)
    detail_id = f"{row_id}-detail" if row_id else "detail"
    movie_id_attr = (
        f' data-movie-id="{html.escape(row_id, quote=True)}"' if row_id else ""
    )
    user_cell = ""
    columns = 6
    sort_you = ""
    if show_user_rating:
        you = _format_user_rating(m.user_rating)
        user_cell = (
            '<td class="col-rating">'
            + _rating_badge(m.user_rating, you, "rating-you", "Your Letterboxd rating")
            + "</td>"
        )
        you_value = f"{m.user_rating:.4f}" if m.user_rating is not None else ""
        sort_you = f' data-sort-you="{you_value}"'
        columns = 7
    sort_rating = f"{m.rating:.4f}" if m.rating is not None else ""
    sort_year = str(m.year) if m.year is not None else ""
    sort_title = html.escape(film_title(m), quote=True)
    sort_director = html.escape(", ".join(m.directors), quote=True)
    sort_genres = html.escape(", ".join(m.genres), quote=True)
    sort_language = html.escape(m.primary_language or "", quote=True)
    return (
        f'<tr data-movie-row{extra}{movie_id_attr} data-search="{search_blob}"'
        f' data-sort-rating="{sort_rating}"{sort_you}'
        f' data-sort-year="{sort_year}"'
        f' data-sort-title="{sort_title}"'
        f' data-sort-director="{sort_director}"'
        f' data-sort-genres="{sort_genres}"'
        f' data-sort-language="{sort_language}">'
        f'<td class="col-rating">{_rating_badge(m.rating, rating_text)}</td>'
        f"{user_cell}"
        f"<td class=\"col-year\">{year}</td>"
        f'<td class="col-title"><button type="button" class="expand" '
        f'aria-expanded="false" aria-controls="{html.escape(detail_id, quote=True)}">'
        f"<strong>{title}</strong>{follow_badge}</button></td>"
        f"<td class=\"col-director\">{directors}</td>"
        f"<td class=\"col-genres\">{genre_html}</td>"
        f"<td class=\"col-language\">{language}</td>"
        f"</tr>"
        f'<tr class="{detail_classes}" data-movie-detail hidden>'
        f'<td colspan="{columns}">{_detail_panel_html(m, detail_id)}</td>'
        f"</tr>"
    )


def _sortable_header(
    label: str,
    key: str,
    kind: str,
    sorted_dir: str | None = None,
    extra_class: str = "",
) -> str:
    aria = f' aria-sort="{sorted_dir}"' if sorted_dir else ""
    css = f' class="{extra_class}"' if extra_class else ""
    return (
        f"<th{css}{aria}>"
        f'<button type="button" class="sort" data-sort-key="{key}" data-sort-type="{kind}">'
        f"{html.escape(label)}</button></th>"
    )


def _html_table_section(
    section_id: str,
    title: str,
    subtitle: str,
    movies: list[Movie],
    row_class: str = "",
    show_user_rating: bool = False,
) -> str:
    if not movies:
        return ""
    rows = "\n".join(
        _format_html_movie_row(m, row_class, f"{section_id}-{index}", show_user_rating)
        for index, m in enumerate(movies)
    )
    count = len(movies)
    you_header = _sortable_header("You", "you", "number", extra_class="col-rating") if show_user_rating else ""
    return f"""
<section class="program-section" id="{section_id}">
  <h2 class="section-head">
    <button type="button" class="section-toggle" aria-expanded="true" aria-controls="{section_id}-table">
      <span class="chevron" aria-hidden="true"></span>
      <span class="section-title">{html.escape(title)}</span>
      <span class="section-sub">{html.escape(subtitle)}</span>
      <span class="section-count">{count} film{"s" if count != 1 else ""}</span>
    </button>
  </h2>
  <div class="table-wrap" id="{section_id}-table">
    <table>
      <thead>
        <tr>
          {_sortable_header("Rating", "rating", "number", sorted_dir="descending", extra_class="col-rating")}
          {you_header}
          {_sortable_header("Year", "year", "number")}
          {_sortable_header("Film", "title", "text")}
          {_sortable_header("Director", "director", "text")}
          {_sortable_header("Genres", "genres", "text")}
          {_sortable_header("Language", "language", "text")}
        </tr>
      </thead>
      <tbody>
{rows}
      </tbody>
    </table>
  </div>
</section>"""


def format_html(
    watchlist: list[Movie],
    watched: list[Movie],
    program: list[Movie],
) -> str:
    sections = []
    if watchlist:
        sections.append(
            _html_table_section(
                "watchlist",
                "Watchlist",
                "On your Letterboxd watchlist and playing at Nonstop Wien",
                watchlist,
                row_class="row-watchlist",
            )
        )
    if watched:
        sections.append(
            _html_table_section(
                "watched",
                "Already watched",
                "You've seen these — they're back on the program",
                watched,
                row_class="row-watched",
                show_user_rating=True,
            )
        )
    rated, unrated, unfound = split_program_groups(program)
    if rated:
        sections.append(
            _html_table_section(
                "program",
                "Full program",
                "English or French original, or English subtitles — sorted by Letterboxd rating",
                rated,
            )
        )
    if unrated:
        sections.append(
            _html_table_section(
                "unrated",
                "Unrated",
                "Matched on Letterboxd, but the film has no community rating yet",
                unrated,
            )
        )
    if unfound:
        sections.append(
            _html_table_section(
                "unfound",
                "Unfound",
                "No Letterboxd page could be matched to these titles",
                unfound,
            )
        )
    sections_html = "\n".join(sections)
    calendar_html = _program_calendar_html(watchlist, watched, program)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nonstop Kino — Letterboxd rankings</title>
  <style>
    :root {{
      --bg: #0f0f12;
      --surface: #1a1a22;
      --surface2: #24242e;
      --border: #333342;
      --text: #e8e6e3;
      --muted: #9a9590;
      --accent: #e8b84a;
      --lb: #40bcf4;
      --ns: #c45c4a;
      --ok: #c9a227;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.45;
    }}
    .hero {{
      background: linear-gradient(135deg, #1a1520 0%, #0f1418 50%, #12101a 100%);
      border-bottom: 1px solid var(--border);
      padding: 2rem 1.5rem 1.75rem;
    }}
    .hero-inner {{ max-width: 1200px; margin: 0 auto; }}
    h1 {{
      margin: 0 0 0.35rem;
      font-size: 1.75rem;
      font-weight: 600;
      letter-spacing: -0.02em;
    }}
    .hero p.tagline {{
      margin: 0;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .toolbar {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 0.85rem 1.5rem 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      position: sticky;
      top: 0;
      background: rgba(15, 15, 18, 0.92);
      backdrop-filter: blur(8px);
      z-index: 10;
      border-bottom: 1px solid var(--border);
    }}
    .toolbar-tools {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      align-items: center;
    }}
    #search {{
      flex: 1;
      min-width: 200px;
      padding: 0.55rem 0.85rem;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--surface);
      color: var(--text);
      font-size: 0.95rem;
    }}
    #search:focus {{
      outline: 2px solid var(--accent);
      outline-offset: 1px;
    }}
    .nav-pills {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
    }}
    .nav-pills a {{
      font-size: 0.8rem;
      padding: 0.35rem 0.65rem;
      border-radius: 6px;
      background: var(--surface2);
      color: var(--text);
      text-decoration: none;
      border: 1px solid var(--border);
    }}
    .nav-pills a:hover {{ border-color: var(--accent); color: var(--accent); }}
    main {{ max-width: 1200px; margin: 0 auto; padding: 0 1.5rem 3rem; }}
    .program-section {{ margin-top: 2rem; }}
    .section-head {{
      margin: 0 0 0.75rem;
      font-size: 1rem;
      font-weight: 400;
    }}
    .program-section.is-folded:not(.is-search-open) .section-head {{
      margin-bottom: 0;
    }}
    .section-toggle {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.5rem 1rem;
      width: 100%;
      padding: 0.4rem 0.35rem;
      border: 0;
      border-radius: 8px;
      background: transparent;
      color: inherit;
      font: inherit;
      text-align: left;
      cursor: pointer;
    }}
    .section-toggle:hover,
    .section-toggle:focus-visible {{
      background: rgba(255, 255, 255, 0.04);
    }}
    .section-toggle:focus-visible {{
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }}
    .chevron {{
      width: 0.45rem;
      height: 0.45rem;
      margin-right: 0.15rem;
      border-right: 2px solid currentColor;
      border-bottom: 2px solid currentColor;
      transform: rotate(45deg) translateY(-1px);
      flex: 0 0 auto;
    }}
    .program-section.is-folded:not(.is-search-open) .chevron {{
      transform: rotate(-45deg) translateY(1px);
    }}
    .section-title {{
      font-size: 1.25rem;
      font-weight: 600;
    }}
    .section-sub {{
      margin: 0;
      flex: 1 1 12rem;
      font-size: 0.85rem;
      color: var(--muted);
      text-align: left;
    }}
    .section-count {{
      font-size: 0.75rem;
      padding: 0.2rem 0.5rem;
      background: var(--surface2);
      border-radius: 4px;
      color: var(--muted);
    }}
    .program-section.is-folded:not(.is-search-open) .table-wrap {{
      display: none;
    }}
    .table-wrap {{
      overflow-x: auto;
      border: 1px solid var(--border);
      border-radius: 12px;
      background: var(--surface);
      container-type: inline-size;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.88rem;
    }}
    th {{
      text-align: left;
      padding: 0;
      background: var(--surface2);
      color: var(--muted);
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }}
    th button.sort {{
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      width: 100%;
      padding: 0.65rem 0.75rem;
      border: 0;
      background: transparent;
      color: inherit;
      font: inherit;
      letter-spacing: inherit;
      text-transform: inherit;
      text-align: inherit;
      cursor: pointer;
    }}
    th button.sort:hover,
    th button.sort:focus-visible {{
      color: var(--text);
    }}
    th button.sort:focus-visible {{
      outline: 2px solid var(--accent);
      outline-offset: -2px;
    }}
    th button.sort::after {{
      content: "";
      width: 0;
      height: 0;
      border-left: 3.5px solid transparent;
      border-right: 3.5px solid transparent;
      border-top: 5px solid currentColor;
      opacity: 0.35;
    }}
    th[aria-sort] button.sort {{ color: var(--text); }}
    th[aria-sort="descending"] button.sort::after {{ opacity: 1; }}
    th[aria-sort="ascending"] button.sort::after {{
      border-top: 0;
      border-bottom: 5px solid currentColor;
      opacity: 1;
    }}
    th.col-rating {{ text-align: center; }}
    th.col-rating button.sort {{ justify-content: center; }}
    td {{
      padding: 0.55rem 0.75rem;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
    }}
    tr:last-child td {{ border-bottom: none; }}
    tr[data-movie-row] {{ cursor: pointer; }}
    tr:hover td {{ background: rgba(255,255,255,0.03); }}
    tr.row-watchlist td:first-child {{ border-left: 3px solid var(--lb); }}
    tr.row-watched td:first-child {{ border-left: 3px solid var(--accent); }}
    tr.row-no-follow td {{
      background: rgba(196, 92, 74, 0.07);
    }}
    tr.row-no-follow:hover td {{
      background: rgba(196, 92, 74, 0.12);
    }}
    .no-follow {{
      display: inline-block;
      margin-left: 0.4rem;
      padding: 0.1rem 0.4rem;
      border-radius: 4px;
      background: rgba(196, 92, 74, 0.22);
      color: #e88a7a;
      font-size: 0.68rem;
      font-weight: 600;
      letter-spacing: 0.02em;
      vertical-align: middle;
      white-space: nowrap;
    }}
    .col-rating {{ text-align: center; width: 4.5rem; }}
    .col-year {{ width: 4rem; color: var(--muted); }}
    .col-director {{ max-width: 11rem; }}
    .col-genres {{ max-width: 14rem; }}
    .col-language {{ white-space: nowrap; color: var(--muted); font-size: 0.8rem; }}
    .col-title {{ min-width: 12rem; }}
    .rating-badge {{
      display: inline-block;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      padding: 0.15rem 0.45rem;
      border-radius: 6px;
      font-size: 0.85rem;
    }}
    .rating-none {{ background: var(--surface2); color: var(--muted); }}
    .rating-you {{ box-shadow: inset 0 0 0 1px var(--accent); }}
    .genre {{
      display: inline-block;
      font-size: 0.72rem;
      padding: 0.12rem 0.4rem;
      margin: 0.1rem 0.15rem 0.1rem 0;
      background: var(--surface2);
      border-radius: 4px;
      color: var(--muted);
    }}
    .muted {{ color: var(--muted); }}
    .note {{ color: var(--ok); cursor: help; }}
    a.link-lb {{ color: var(--lb); text-decoration: none; }}
    a.link-lb:hover {{ text-decoration: underline; }}
    button.expand {{
      display: inline;
      width: 100%;
      padding: 0;
      border: 0;
      background: transparent;
      color: inherit;
      font: inherit;
      text-align: left;
      cursor: pointer;
    }}
    button.expand:focus-visible {{
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }}
    tr[hidden] {{ display: none !important; }}
    tr.detail-row td {{
      background: #14141b;
      padding: 0;
    }}
    tr.detail-row:hover td {{ background: #14141b; }}
    .detail {{
      position: sticky;
      left: 0;
      display: flex;
      gap: 1.1rem;
      align-items: flex-start;
      width: 100cqi;
      max-width: 100%;
      box-sizing: border-box;
      padding: 0.85rem 1rem 1rem;
    }}
    .poster {{
      width: 140px;
      max-width: 100%;
      height: auto;
      border-radius: 8px;
      flex: 0 0 auto;
      background: var(--surface2);
    }}
    @media (max-width: 720px) {{
      .detail {{ flex-direction: column; }}
      .poster {{ width: 120px; }}
    }}
    .detail-body {{ min-width: 0; flex: 1; }}
    .synopsis {{
      margin: 0 0 0.75rem;
      white-space: pre-wrap;
      color: var(--text);
      max-width: 70ch;
    }}
    .detail-links {{ margin: 0 0 0.9rem; }}
    .cal-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 1rem;
      margin-bottom: 0.85rem;
    }}
    .cal-caption {{
      font-size: 0.8rem;
      font-weight: 600;
      margin-bottom: 0.35rem;
    }}
    .cal-grid {{
      display: grid;
      grid-template-columns: repeat(7, 2rem);
      gap: 2px;
      text-align: center;
      font-size: 0.75rem;
    }}
    .cal-head {{
      color: var(--muted);
      margin-bottom: 0.2rem;
      font-size: 0.68rem;
      text-transform: uppercase;
    }}
    .cal-day {{
      display: flex;
      align-items: center;
      justify-content: center;
      height: 1.7rem;
      border-radius: 4px;
      color: var(--muted);
    }}
    .cal-day.blank {{ visibility: hidden; }}
    .cal-day.has {{
      background: var(--accent);
      color: #1a1408;
      font-weight: 700;
    }}
    .showtimes {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem 1.5rem;
    }}
    .showtime-day h3 {{
      margin: 0 0 0.25rem;
      font-size: 0.82rem;
      font-weight: 600;
    }}
    .showtime-day ul {{
      margin: 0;
      padding: 0;
      list-style: none;
      font-size: 0.8rem;
      color: var(--muted);
    }}
    .showtime-day li {{ margin: 0.1rem 0; }}
    a.showtime-link {{ color: var(--accent); text-decoration: none; }}
    a.showtime-link:hover {{ text-decoration: underline; }}
    .version {{
      display: inline-block;
      margin-left: 0.25rem;
      font-size: 0.72rem;
      color: var(--muted);
    }}
    [hidden] {{ display: none !important; }}
    .view-toggle {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
      background: var(--surface);
    }}
    .view-option {{
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 0.12rem;
      border: 0;
      background: transparent;
      color: var(--muted);
      font: inherit;
      font-size: 1.05rem;
      font-weight: 600;
      padding: 0.7rem 0.85rem 0.62rem;
      cursor: pointer;
    }}
    .view-option + .view-option {{
      box-shadow: inset 1px 0 0 var(--border);
    }}
    .view-hint {{
      font-size: 0.75rem;
      font-weight: 500;
    }}
    .view-option:hover:not([aria-pressed="true"]),
    .view-option:focus-visible:not([aria-pressed="true"]) {{
      background: var(--surface2);
      color: var(--text);
    }}
    .view-option[aria-pressed="true"] {{
      background: var(--accent);
      color: #1a1408;
    }}
    .view-option[aria-pressed="true"] .view-hint {{
      color: rgba(26, 20, 8, 0.72);
    }}
    .view-option:focus-visible {{
      outline: 2px solid var(--accent);
      outline-offset: -2px;
    }}
    body.view-calendar .toolbar,
    body.view-calendar main,
    body.view-calendar footer {{
      max-width: 1440px;
    }}
    .month-boards {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 1.25rem;
      margin-top: 1.25rem;
    }}
    @media (min-width: 1100px) {{
      .month-boards {{ grid-template-columns: 1fr 1fr; }}
    }}
    .month-board {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 0.85rem 0.75rem 0.5rem;
    }}
    .month-caption {{
      margin: 0 0 0.65rem;
      padding: 0 0.2rem;
      font-size: 1.2rem;
      font-weight: 600;
    }}
    .month-head,
    .month-week {{
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
      gap: 0.35rem;
    }}
    .month-head {{
      margin-bottom: 0.35rem;
      color: var(--muted);
      font-size: 0.68rem;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      text-align: center;
    }}
    .month-week {{ margin-bottom: 0.35rem; }}
    .month-day {{
      min-height: 5.6rem;
      border-radius: 8px;
      border: 1px solid transparent;
      background: var(--surface2);
      color: var(--muted);
      padding: 0.35rem 0.4rem;
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 0.12rem;
    }}
    .month-day.pad {{
      min-height: 0;
      background: transparent;
    }}
    .month-day.blank {{ opacity: 0.4; }}
    button.month-day {{
      font: inherit;
      text-align: left;
      color: var(--text);
      border-color: var(--border);
      cursor: pointer;
    }}
    button.month-day:hover,
    button.month-day:focus-visible,
    button.month-day[aria-expanded="true"] {{
      border-color: var(--accent);
    }}
    button.month-day:focus-visible {{
      outline: 2px solid var(--accent);
      outline-offset: 1px;
    }}
    button.month-day[aria-expanded="true"] {{
      background: #2a261c;
    }}
    button.month-day.is-filtered-out,
    button.also-day-button.is-filtered-out {{
      opacity: 0.4;
      color: var(--muted);
      border-color: transparent;
      cursor: default;
    }}
    button.month-day.is-filtered-out .month-day-count,
    button.month-day.is-filtered-out .month-day-title,
    button.also-day-button.is-filtered-out .month-day-count,
    button.also-day-button.is-filtered-out .month-day-title {{
      display: none;
    }}
    .month-day.is-today .month-day-num {{
      color: #1a1408;
      background: var(--accent);
      border-radius: 999px;
      min-width: 1.45rem;
      padding: 0 0.25rem;
      text-align: center;
    }}
    .month-day-num {{
      font-weight: 700;
      font-size: 0.85rem;
      font-variant-numeric: tabular-nums;
    }}
    .month-day-count {{
      font-size: 0.68rem;
      font-weight: 600;
      color: var(--accent);
    }}
    .month-day-title {{
      font-size: 0.72rem;
      font-weight: 500;
      line-height: 1.25;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .month-band {{
      grid-column: 1 / -1;
      background: #14141b;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 0.35rem 0.85rem;
    }}
    .day-film {{
      padding: 0.55rem 0;
      border-bottom: 1px solid var(--border);
    }}
    .day-film:last-child {{ border-bottom: 0; }}
    .day-film-line {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.4rem 0.7rem;
    }}
    .day-film-title {{
      border: 0;
      background: transparent;
      color: inherit;
      font: inherit;
      font-weight: 600;
      text-align: left;
      padding: 0;
      cursor: pointer;
      flex: 1;
      min-width: 8rem;
    }}
    .day-film-title:hover,
    .day-film-title:focus-visible {{ color: var(--accent); }}
    .day-film-title:focus-visible {{
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }}
    .section-chip {{
      margin-left: auto;
      font-size: 0.68rem;
      font-weight: 600;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--muted);
      white-space: nowrap;
    }}
    .chip-watchlist {{ color: var(--lb); }}
    .chip-watched {{
      display: inline-flex;
      align-items: center;
      gap: 0.28rem;
      padding: 0.14rem 0.5rem 0.14rem 0.38rem;
      border-radius: 999px;
      color: #1a1408;
      background: var(--accent);
      box-shadow: 0 0 0 1px rgba(232, 184, 74, 0.35);
    }}
    .chip-watched::before {{
      content: "";
      width: 0.72rem;
      height: 0.72rem;
      border-radius: 50%;
      background: #1a1408;
      flex-shrink: 0;
      mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12'%3E%3Cpath fill='black' d='M10.2 2.6 4.7 8.5 1.8 5.5l.9-.9 2 2.1 4.6-4.9z'/%3E%3C/svg%3E") center / contain no-repeat;
      -webkit-mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12'%3E%3Cpath fill='black' d='M10.2 2.6 4.7 8.5 1.8 5.5l.9-.9 2 2.1 4.6-4.9z'/%3E%3C/svg%3E") center / contain no-repeat;
    }}
    .day-times {{
      margin: 0.25rem 0 0;
      padding: 0;
      list-style: none;
      font-size: 0.8rem;
      color: var(--muted);
    }}
    .day-times li {{ margin: 0.1rem 0; }}
    .day-film-detail {{ margin-top: 0.65rem; }}
    .day-film-detail .detail {{
      position: static;
      width: auto;
      padding: 0;
    }}
    .also-showing {{ margin-top: 1.5rem; }}
    .also-showing h2 {{
      margin: 0 0 0.6rem;
      font-size: 1rem;
      font-weight: 600;
    }}
    .also-days {{
      display: flex;
      flex-direction: column;
      gap: 0.45rem;
    }}
    .also-day-button {{
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 0.35rem 0.75rem;
      width: 100%;
      padding: 0.55rem 0.75rem;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface);
      color: var(--text);
      font: inherit;
      text-align: left;
      cursor: pointer;
    }}
    .also-day-button:hover,
    .also-day-button:focus-visible,
    .also-day-button[aria-expanded="true"] {{
      border-color: var(--accent);
    }}
    .also-day-button:focus-visible {{
      outline: 2px solid var(--accent);
      outline-offset: 1px;
    }}
    .also-day-button .month-day-title {{
      -webkit-line-clamp: 1;
    }}
    .also-day .month-band {{ margin-top: 0.35rem; }}
    .empty-hint {{
      color: var(--muted);
      font-size: 0.85rem;
      padding: 1rem 0;
    }}
    footer {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 1.5rem;
      font-size: 0.75rem;
      color: var(--muted);
      border-top: 1px solid var(--border);
    }}
  </style>
</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      <h1>Nonstop Kino × Letterboxd</h1>
      <p class="tagline">Wien program ranked by community ratings — find what to see next.</p>
    </div>
  </header>
  <div class="toolbar">
    <div class="view-toggle" role="group" aria-label="How to browse the program">
      <button type="button" class="view-option" data-view="list" aria-pressed="true">List<span class="view-hint">Ranked by rating</span></button>
      <button type="button" class="view-option" data-view="calendar" aria-pressed="false">Calendar<span class="view-hint">Films by day</span></button>
    </div>
    <div class="toolbar-tools">
    <input type="search" id="search" placeholder="Filter by title, director, genre…" autocomplete="off">
    <nav class="nav-pills">
      {"<a href=\"#watchlist\">Watchlist</a>" if watchlist else ""}
      {"<a href=\"#watched\">Watched</a>" if watched else ""}
      {"<a href=\"#program\">Program</a>" if any(m.rating is not None for m in program) else ""}
      {"<a href=\"#unrated\">Unrated</a>" if any(m.rating is None and m.letterboxd_url for m in program) else ""}
      {"<a href=\"#unfound\">Unfound</a>" if any(m.rating is None and not m.letterboxd_url for m in program) else ""}
    </nav>
    </div>
  </div>
  <main>
    <div id="list-view">
{sections_html}
    </div>
    <div id="calendar-view" hidden>
{calendar_html}
    </div>
  </main>
  <footer>
    Generated by goodmoviefinder. Ratings from Letterboxd; showtimes from Nonstop Kino Wien.
  </footer>
  <script>
    const search = document.getElementById("search");

    function applySearch() {{
      const q = search.value.trim().toLowerCase();
      document.querySelectorAll("[data-movie-row]").forEach((row) => {{
        const blob = row.getAttribute("data-search") || "";
        const hide = q.length > 0 && !blob.includes(q);
        row.hidden = hide;
        const detail = row.nextElementSibling;
        if (!detail || !detail.hasAttribute("data-movie-detail")) return;
        const expanded = row.querySelector(".expand")?.getAttribute("aria-expanded") === "true";
        detail.hidden = hide || !expanded;
      }});
      const searching = q.length > 0;
      document.querySelectorAll(".program-section").forEach((section) => {{
        const hasMatch = searching && [...section.querySelectorAll("[data-movie-row]")].some((row) => !row.hidden);
        section.classList.toggle("is-search-open", hasMatch);
        syncSection(section);
      }});
      document.querySelectorAll(".day-film").forEach((film) => {{
        const blob = film.getAttribute("data-search") || "";
        const hide = q.length > 0 && !blob.includes(q);
        film.hidden = hide;
        if (!hide) return;
        const title = film.querySelector(".day-film-title");
        const slot = film.querySelector(".day-film-detail");
        if (title) title.setAttribute("aria-expanded", "false");
        if (slot) {{
          slot.hidden = true;
          slot.replaceChildren();
        }}
      }});
      document.querySelectorAll("[data-day]").forEach((button) => syncDay(button, searching));
    }}

    function closeDay(button) {{
      button.setAttribute("aria-expanded", "false");
      const panel = document.getElementById(button.getAttribute("aria-controls"));
      if (panel) panel.hidden = true;
    }}

    function syncDay(button, searching) {{
      const panel = document.getElementById(button.getAttribute("aria-controls"));
      const films = panel ? [...panel.querySelectorAll(".day-film")] : [];
      const visible = films.filter((film) => !film.hidden);
      const countEl = button.querySelector(".month-day-count");
      const titleEl = button.querySelector(".month-day-title");
      const total = button.getAttribute("data-count") || String(films.length);
      const originalTitle = button.getAttribute("data-title") || "";
      if (countEl) countEl.textContent = searching ? String(visible.length) : total;
      if (titleEl) {{
        titleEl.textContent = !searching || visible.length === 0
          ? originalTitle
          : (visible[0].querySelector(".day-film-title")?.textContent || originalTitle);
      }}
      const empty = searching && visible.length === 0;
      button.classList.toggle("is-filtered-out", empty);
      button.disabled = empty;
      if (empty && button.getAttribute("aria-expanded") === "true") closeDay(button);
    }}

    function toggleDay(button) {{
      if (button.disabled) return;
      const open = button.getAttribute("aria-expanded") === "true";
      document.querySelectorAll("[data-day][aria-expanded='true']").forEach(closeDay);
      if (open) return;
      button.setAttribute("aria-expanded", "true");
      const panel = document.getElementById(button.getAttribute("aria-controls"));
      if (panel) panel.hidden = false;
    }}

    function toggleFilmDetail(button) {{
      const film = button.closest(".day-film");
      const slot = film && film.querySelector(".day-film-detail");
      if (!film || !slot) return;
      const open = button.getAttribute("aria-expanded") === "true";
      if (open) {{
        button.setAttribute("aria-expanded", "false");
        slot.hidden = true;
        slot.replaceChildren();
        return;
      }}
      const movieId = film.getAttribute("data-movie-id");
      const row = document.querySelector(
        `[data-movie-row][data-movie-id="${{CSS.escape(movieId)}}"]`
      );
      const source = row && row.nextElementSibling && row.nextElementSibling.querySelector(".detail");
      if (!source) return;
      const clone = source.cloneNode(true);
      clone.querySelectorAll("[id]").forEach((node) => node.removeAttribute("id"));
      slot.replaceChildren(clone);
      slot.hidden = false;
      button.setAttribute("aria-expanded", "true");
    }}

    const FOLD_STORAGE = "gmf-folded-sections";

    function readFolded() {{
      try {{
        const raw = localStorage.getItem(FOLD_STORAGE);
        const ids = raw ? JSON.parse(raw) : [];
        return new Set(Array.isArray(ids) ? ids : []);
      }} catch (err) {{
        return new Set();
      }}
    }}

    function writeFolded(ids) {{
      try {{
        localStorage.setItem(FOLD_STORAGE, JSON.stringify([...ids]));
      }} catch (err) {{
        /* private mode or storage full */
      }}
    }}

    const foldedIds = readFolded();

    function sectionIsOpen(section) {{
      return !section.classList.contains("is-folded") || section.classList.contains("is-search-open");
    }}

    function syncSection(section) {{
      const button = section.querySelector(".section-toggle");
      if (button) button.setAttribute("aria-expanded", sectionIsOpen(section) ? "true" : "false");
    }}

    function setFolded(section, isFolded) {{
      section.classList.toggle("is-folded", isFolded);
      if (isFolded) foldedIds.add(section.id);
      else foldedIds.delete(section.id);
      writeFolded(foldedIds);
      syncSection(section);
    }}

    document.querySelectorAll(".program-section").forEach((section) => {{
      if (foldedIds.has(section.id)) section.classList.add("is-folded");
      syncSection(section);
      const button = section.querySelector(".section-toggle");
      if (!button) return;
      button.addEventListener("click", () => {{
        setFolded(section, !section.classList.contains("is-folded"));
      }});
    }});

    document.querySelectorAll(".nav-pills a[href^='#']").forEach((link) => {{
      link.addEventListener("click", () => {{
        const section = document.getElementById(link.getAttribute("href").slice(1));
        if (section && section.classList.contains("program-section")) setFolded(section, false);
      }});
    }});

    document.querySelectorAll("[data-movie-row]").forEach((row) => {{
      row.addEventListener("click", () => {{
        const button = row.querySelector(".expand");
        const detail = row.nextElementSibling;
        if (!button || !detail || !detail.hasAttribute("data-movie-detail")) return;
        const open = button.getAttribute("aria-expanded") !== "true";
        button.setAttribute("aria-expanded", open ? "true" : "false");
        row.classList.toggle("is-open", open);
        detail.hidden = !open;
      }});
    }});

    function sortValue(row, key, type) {{
      const raw = row.getAttribute("data-sort-" + key) || "";
      if (type === "number") {{
        if (raw === "") return null;
        const value = Number(raw);
        return Number.isFinite(value) ? value : null;
      }}
      return raw;
    }}

    function compareRows(a, b, key, type, direction) {{
      const left = sortValue(a, key, type);
      const right = sortValue(b, key, type);
      const leftMissing = left === null || left === "";
      const rightMissing = right === null || right === "";
      if (leftMissing && rightMissing) return 0;
      if (leftMissing) return 1;
      if (rightMissing) return -1;
      const cmp = type === "number"
        ? left - right
        : String(left).localeCompare(String(right), undefined, {{ sensitivity: "base", numeric: true }});
      return direction === "asc" ? cmp : -cmp;
    }}

    document.querySelectorAll("table").forEach((table) => {{
      const tbody = table.querySelector("tbody");
      if (!tbody) return;
      table.querySelectorAll("button.sort").forEach((button) => {{
        button.addEventListener("click", () => {{
          const header = button.closest("th");
          const key = button.dataset.sortKey;
          const type = button.dataset.sortType || "text";
          const current = header.getAttribute("aria-sort");
          const direction = current === "descending"
            ? "asc"
            : current === "ascending"
              ? "desc"
              : (type === "number" ? "desc" : "asc");
          table.querySelectorAll("th").forEach((cell) => cell.removeAttribute("aria-sort"));
          header.setAttribute("aria-sort", direction === "asc" ? "ascending" : "descending");
          const pairs = [...tbody.querySelectorAll("[data-movie-row]")].map((row) => {{
            const detail = row.nextElementSibling;
            return [row, detail && detail.hasAttribute("data-movie-detail") ? detail : null];
          }});
          pairs.sort((a, b) => compareRows(a[0], b[0], key, type, direction));
          for (const [row, detail] of pairs) {{
            tbody.appendChild(row);
            if (detail) tbody.appendChild(detail);
          }}
        }});
      }});
    }});

    const VIEW_STORAGE = "gmf-view";

    function setView(view) {{
      const calendar = view === "calendar";
      document.body.classList.toggle("view-calendar", calendar);
      const list = document.getElementById("list-view");
      const board = document.getElementById("calendar-view");
      if (list) list.hidden = calendar;
      if (board) board.hidden = !calendar;
      const pills = document.querySelector(".nav-pills");
      if (pills) pills.hidden = calendar;
      document.querySelectorAll(".view-option").forEach((button) => {{
        button.setAttribute("aria-pressed", button.dataset.view === view ? "true" : "false");
      }});
      try {{
        localStorage.setItem(VIEW_STORAGE, calendar ? "calendar" : "list");
      }} catch (err) {{
        /* private mode or storage full */
      }}
    }}

    document.querySelectorAll(".view-option").forEach((button) => {{
      button.addEventListener("click", () => setView(button.dataset.view));
    }});
    document.querySelectorAll("[data-day]").forEach((button) => {{
      button.addEventListener("click", () => toggleDay(button));
    }});
    document.querySelectorAll(".day-film-title").forEach((button) => {{
      button.addEventListener("click", () => toggleFilmDetail(button));
    }});

    let storedView = "list";
    try {{
      if (localStorage.getItem(VIEW_STORAGE) === "calendar") storedView = "calendar";
    }} catch (err) {{
      storedView = "list";
    }}
    setView(storedView);

    search.addEventListener("input", applySearch);
  </script>
</body>
</html>"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List Nonstop Kino program films sorted by Letterboxd rating.",
    )
    parser.add_argument(
        "--program-url",
        default=DEFAULT_PROGRAM_URL,
        help=f"Program page URL (default: {DEFAULT_PROGRAM_URL})",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE,
        help="JSON cache file for Letterboxd lookups",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only process the first N unique films (0 = all)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.35,
        help="Seconds to wait between Letterboxd requests",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore cached ratings",
    )
    parser.add_argument(
        "--letterboxd-user",
        default=DEFAULT_LETTERBOXD_USER,
        help=f"Letterboxd username for watched/watchlist filters (default: {DEFAULT_LETTERBOXD_USER})",
    )
    parser.add_argument(
        "--no-letterboxd-profile",
        action="store_true",
        help="Do not filter watched films or highlight watchlist",
    )
    parser.add_argument(
        "--refresh-profile",
        action="store_true",
        help="Re-fetch Letterboxd watched/watchlist (ignore profile cache)",
    )
    args = parser.parse_args()

    print(f"Fetching program: {args.program_url}", file=sys.stderr)
    try:
        program_html = fetch(args.program_url)
    except urllib.error.URLError as exc:
        print(f"Failed to fetch program: {exc.reason}", file=sys.stderr)
        return 1

    films = parse_program(program_html)
    if not films:
        print("No films found on the program page.", file=sys.stderr)
        return 1

    if args.limit > 0:
        films = films[: args.limit]

    cache = load_cache(args.cache)

    watched_ratings: dict[str, float | None] = {}
    watchlist_slugs: set[str] = set()

    if not args.no_letterboxd_profile:
        profile = None
        if not args.refresh_profile and not args.no_cache:
            profile = load_profile_cache(cache, args.letterboxd_user)
        if profile is None:
            user = args.letterboxd_user
            print(f"Fetching Letterboxd watched films for @{user}…", file=sys.stderr)
            try:
                watched_ratings = fetch_letterboxd_watched_ratings(user, delay=args.delay)
                print(f"Fetching Letterboxd watchlist for @{user}…", file=sys.stderr)
                watchlist_slugs = fetch_letterboxd_profile_slugs(
                    user, "watchlist", delay=args.delay
                )
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            save_profile_cache(cache, user, watched_ratings, watchlist_slugs)
            save_cache(args.cache, cache)
            rated = sum(1 for value in watched_ratings.values() if value is not None)
            print(
                f"Profile: {len(watched_ratings)} watched ({rated} rated), "
                f"{len(watchlist_slugs)} on watchlist.",
                file=sys.stderr,
            )
        else:
            watched_ratings, watchlist_slugs = profile
            print(
                f"Using cached profile: {len(watched_ratings)} watched, "
                f"{len(watchlist_slugs)} on watchlist.",
                file=sys.stderr,
            )

    results: list[Movie] = []

    for index, film in enumerate(films, start=1):
        print(f"[{index}/{len(films)}] {film.title}", file=sys.stderr)

        page: NonstopFilmPage | None = None
        if not args.no_cache:
            cached = cache_get(cache, film.url)
            if cached is not None:
                attach_program(cached, film)
                updated = False
                if not cached.primary_language_known or not cached.titles_known:
                    print("  Looking up Letterboxd title and language…", file=sys.stderr)
                    updated = backfill_letterboxd_details(cached, args.delay)
                if not cached.details_known:
                    print("  Looking up synopsis and cinema websites…", file=sys.stderr)
                    backfill_nonstop_details(cached, args.delay)
                    updated = updated or cached.details_known
                if cached.match_current:
                    if updated:
                        cache_put(cache, cached)
                        save_cache(args.cache, cache)
                    results.append(cached)
                    continue
                print("  Cross-checking director, runtime, and cast…", file=sys.stderr)
                try:
                    page = parse_nonstop_movie_page(fetch(film.url))
                except urllib.error.URLError as exc:
                    print(
                        f"  Nonstop page error: {exc.reason}",
                        file=sys.stderr,
                    )
                    page = None
                if page is None or not cached_match_should_reresolve(page, cached):
                    cache_put(cache, cached)
                    save_cache(args.cache, cache)
                    results.append(cached)
                    continue
                print(
                    "  Letterboxd film disagrees; searching again…",
                    file=sys.stderr,
                )

        if page is None:
            try:
                page = parse_nonstop_movie_page(fetch(film.url))
            except urllib.error.URLError as exc:
                movie = Movie(
                    title=film.title,
                    nonstop_url=film.url,
                    note=f"Nonstop page error: {exc.reason}",
                )
                attach_program(movie, film)
                results.append(movie)
                continue

        page_title = page.title
        release_year = page.year
        nonstop_director = page.director

        movie = resolve_letterboxd(
            film.title,
            film.url,
            page_title,
            release_year,
            nonstop_director,
            delay=args.delay,
            nonstop_cast=page.cast,
            nonstop_duration=page.duration_minutes,
        )
        attach_program(movie, film)
        if page is not None:
            movie.synopsis = page.synopsis
            movie.cinema_sites = page.cinema_sites
            movie.details_known = True
        apply_nonstop_metadata(movie, release_year, nonstop_director)
        cache_put(cache, movie)
        save_cache(args.cache, cache)
        results.append(movie)

    watchlist_movies, watched_movies, program_movies = partition_program_movies(
        results, watched_ratings, watchlist_slugs
    )
    if args.no_letterboxd_profile:
        program_movies = sort_movies(results)
        watchlist_movies = []
        watched_movies = []

    program_before_language = len(program_movies)
    program_movies = [movie for movie in program_movies if movie_is_watchable(movie)]
    removed_for_language = program_before_language - len(program_movies)

    output = format_html(watchlist_movies, watched_movies, program_movies)
    GENERATED_HTML.parent.mkdir(parents=True, exist_ok=True)
    GENERATED_HTML.write_text(output, encoding="utf-8")
    print(f"Wrote {GENERATED_HTML}", file=sys.stderr)

    rated = sum(1 for m in results if m.rating is not None)
    print(
        f"Language filter: removed {removed_for_language} from the main program.",
        file=sys.stderr,
    )
    print(
        f"Done: {len(program_movies)} in main program, "
        f"{len(watchlist_movies)} watchlist, "
        f"{len(watched_movies)} already watched, "
        f"{rated}/{len(results)} with Letterboxd ratings.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
