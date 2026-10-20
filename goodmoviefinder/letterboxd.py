"""Letterboxd profile pages, film pages, and title resolution."""

from __future__ import annotations

import html
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse

from goodmoviefinder.http import fetch, fetch_status, make_cookie_opener
from goodmoviefinder.progress import tick
from goodmoviefinder.matching import (
    director_overlaps,
    duration_relation,
    film_identity_confirms,
    film_identity_conflicts,
    flexible_search_queries,
    parse_iso_duration_minutes,
    people_overlap_count,
    search_titles_from_display_title,
    slugify,
)
from goodmoviefinder.models import LetterboxdCandidate, Movie
from goodmoviefinder.nonstop import slug_from_nonstop_url
from goodmoviefinder.program import letterboxd_slug_from_url
from goodmoviefinder.title_lookup import english_title_from_german_wikipedia

LETTERBOXD_SLUG_RE = re.compile(r'data-item-slug="([^"]+)"')
# Letterboxd encodes a viewer's score as rated-N, where N is half-stars (rated-7 = 3.5).
LETTERBOXD_USER_RATING_RE = re.compile(r'class="rating[^"]*\brated-(\d+)')
LETTERBOXD_NEXT_PAGE_RE = re.compile(
    r'<a class="next" href="([^"]+)"[^>]*>\s*Older\s*</a>',
    re.IGNORECASE,
)
LETTERBOXD_ITEM_NAME_RE = re.compile(r'data-item-name="([^"]*)"')
# Letterboxd display names end with the release year: "Title (2006)".
_DISPLAY_YEAR_RE = re.compile(r"\s+\(((?:19|20)\d{2})\)$")

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


def letterboxd_profile_url(user: str, section: str, page: int = 1) -> str:
    base = f"https://letterboxd.com/{user}/{section}/"
    if page <= 1:
        return base
    return f"{base}page/{page}/"


def parse_letterboxd_profile_slugs(html_text: str) -> set[str]:
    return set(LETTERBOXD_SLUG_RE.findall(html_text))


def letterboxd_catalog_url(kind: str, slug: str, page: int = 1) -> str:
    base = f"https://letterboxd.com/csi/films/films-browser-list/{kind}/{slug}/by/rating/"
    if page > 1:
        base += f"page/{page}/"
    return base + "?esi"


def fetch_catalog_film_slugs(kind: str, slug: str, delay: float, pages: int = 1) -> list[str]:
    """Film slugs from a Letterboxd country or theme list, highest rated first."""
    found: list[str] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        try:
            body = fetch_letterboxd_html(letterboxd_catalog_url(kind, slug, page), delay)
        except (LetterboxdRefused, RuntimeError):
            break
        time.sleep(max(delay, 1.0))
        for film_slug in LETTERBOXD_SLUG_RE.findall(body):
            if film_slug in seen:
                continue
            seen.add(film_slug)
            found.append(film_slug)
        if not body or "data-item-slug" not in body:
            break
    return found


def watchlist_film_record(slug: str, display_name: str) -> dict[str, object]:
    """Title and release year from a Letterboxd poster name such as 'Title (2006)'."""
    name = html.unescape(display_name).strip()
    year: int | None = None
    match = _DISPLAY_YEAR_RE.search(name)
    title = name
    if match:
        year = int(match.group(1))
        title = name[: match.start()].strip()
    return {"slug": slug, "title": title or slug, "year": year}


def parse_letterboxd_watchlist_films(html_text: str) -> list[dict[str, object]]:
    """Films on one watchlist page, in the order Letterboxd shows them."""
    films: list[dict[str, object]] = []
    seen: set[str] = set()
    for chunk in html_text.split('<li class="griditem">')[1:]:
        end = chunk.find("</li>")
        item = chunk if end < 0 else chunk[:end]
        slug_match = LETTERBOXD_SLUG_RE.search(item)
        if not slug_match:
            continue
        slug = slug_match.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        name_match = LETTERBOXD_ITEM_NAME_RE.search(item)
        display_name = name_match.group(1) if name_match else slug
        films.append(watchlist_film_record(slug, display_name))
    return films


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


# Own-profile reads keep requesting a refused page. Neighbor diaries stay capped.
_PROFILE_PAGE_LIMIT = 200
_PROFILE_RETRY_CAP_SECONDS = 90.0
_PAGE_NUMBER_RE = re.compile(r"/page/(\d+)/")


def _profile_page_count(html_text: str) -> int | None:
    numbers = [int(value) for value in _PAGE_NUMBER_RE.findall(html_text)]
    if not numbers:
        return None
    return max(numbers)


def _iter_letterboxd_profile_pages(
    user: str,
    section: str,
    delay: float,
    max_pages: int = 50,
    *,
    page_attempts: int = 4,
    profile_delay: float | None = None,
    until_complete: bool = False,
):
    """Yield each profile page. `until_complete` retries a refused page until it loads."""
    page = 1
    opener = make_cookie_opener()
    pause = max(profile_delay if profile_delay is not None else delay, 1.0)
    attempts = max(1, page_attempts)
    label = "watchlist" if section == "watchlist" else "watched films"
    page_total: int | None = None

    while page <= max_pages:
        url = letterboxd_profile_url(user, section, page)
        html_text: str | None = None
        fetch_error: str | None = None
        attempt = 0
        while html_text is None:
            _wait_letterboxd_gap(pause)
            try:
                html_text = fetch(url, opener=opener)
                _mark_letterboxd_fetch()
                break
            except urllib.error.HTTPError as exc:
                _mark_letterboxd_fetch()
                transient = exc.code in {403, 429, 503}
                fetch_error = f"Failed to fetch {url}: HTTP {exc.code}"
                if not transient:
                    raise RuntimeError(fetch_error) from exc
            except urllib.error.URLError as exc:
                _mark_letterboxd_fetch()
                fetch_error = f"Failed to fetch {url}: {exc.reason}"

            attempt += 1
            if not until_complete and attempt >= attempts:
                if page > 1:
                    print(
                        f"{fetch_error}; using the profile pages fetched so far.",
                        file=sys.stderr,
                    )
                else:
                    print(fetch_error, file=sys.stderr)
                return
            if until_complete:
                wait = min(_PROFILE_RETRY_CAP_SECONDS, pause * (2 ** min(attempt, 6)))
            else:
                wait = pause * (attempt + 2) * 1.5
            print(
                f"{fetch_error}; retrying {label} page {page} in {wait:.0f}s…",
                file=sys.stderr,
            )
            tick(detail=f"{label}, page {page}, retrying")
            time.sleep(wait)
            if until_complete and attempt % 3 == 0:
                opener = make_cookie_opener()

        assert html_text is not None
        found_pages = _profile_page_count(html_text)
        if found_pages is not None:
            page_total = max(page_total or 0, found_pages)
        tick(
            current=page,
            total=page_total,
            detail=f"{label}, page {page}"
            + (f" of {page_total}" if page_total else ""),
        )
        yield html_text
        if not LETTERBOXD_NEXT_PAGE_RE.search(html_text):
            return
        page += 1
        time.sleep(pause)

    if until_complete:
        raise RuntimeError(
            f"Letterboxd still had another {label} page after {max_pages}."
        )


def fetch_letterboxd_profile_slugs(
    user: str,
    section: str,
    delay: float,
    max_pages: int = 50,
) -> set[str]:
    slugs: set[str] = set()
    for html_text in _iter_letterboxd_profile_pages(user, section, delay, max_pages):
        slugs |= parse_letterboxd_profile_slugs(html_text)
    return slugs


def fetch_letterboxd_watchlist(user: str, delay: float) -> list[dict[str, object]]:
    """Watchlist films in page order, with the title and year shown on each poster."""
    films: list[dict[str, object]] = []
    seen: set[str] = set()
    for html_text in _iter_letterboxd_profile_pages(
        user,
        "watchlist",
        delay,
        max_pages=_PROFILE_PAGE_LIMIT,
        until_complete=True,
    ):
        for film in parse_letterboxd_watchlist_films(html_text):
            slug = str(film["slug"])
            if slug in seen:
                continue
            seen.add(slug)
            films.append(film)
    return films


def fetch_letterboxd_watched_ratings(
    user: str,
    delay: float,
    max_pages: int = 50,
    *,
    page_attempts: int = 4,
    profile_delay: float | None = None,
    until_complete: bool = False,
) -> dict[str, float | None]:
    ratings: dict[str, float | None] = {}
    for html_text in _iter_letterboxd_profile_pages(
        user,
        "films",
        delay,
        _PROFILE_PAGE_LIMIT if until_complete else max_pages,
        page_attempts=page_attempts,
        profile_delay=profile_delay,
        until_complete=until_complete,
    ):
        ratings.update(parse_letterboxd_watched_ratings(html_text))
    return ratings


def _director_names(raw: object) -> list[str]:
    if isinstance(raw, dict):
        items: list[object] = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    names: list[str] = []
    for item in items:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            name = item["name"]
        else:
            continue
        name = html.unescape(name).strip()
        if name:
            names.append(name)
    return names


def parse_letterboxd_film_json(body: str) -> dict[str, object] | None:
    """Title, year, and directors from Letterboxd's compact film JSON."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("result"):
        return None
    title = data.get("name")
    year = data.get("releaseYear")
    return {
        "title": title.strip() if isinstance(title, str) and title.strip() else None,
        "year": year if isinstance(year, int) else None,
        "directors": _director_names(data.get("directors")),
    }


class LetterboxdRefused(RuntimeError):
    """Letterboxd answered 403, 429, or 503 after retries."""


_LETTERBOXD_GAP_LOCK = threading.Lock()
_LAST_LETTERBOXD_FETCH = 0.0


def _wait_letterboxd_gap(pause: float) -> None:
    global _LAST_LETTERBOXD_FETCH
    with _LETTERBOXD_GAP_LOCK:
        gap = pause - (time.monotonic() - _LAST_LETTERBOXD_FETCH)
        if gap > 0:
            time.sleep(gap)


def _mark_letterboxd_fetch() -> None:
    global _LAST_LETTERBOXD_FETCH
    with _LETTERBOXD_GAP_LOCK:
        _LAST_LETTERBOXD_FETCH = time.monotonic()


def fetch_letterboxd_html(
    url: str,
    delay: float,
    extra_headers: dict[str, str] | None = None,
) -> str:
    """Fetch one Letterboxd page. Repeated refusals raise LetterboxdRefused."""
    pause = max(delay, 1.0)
    last: urllib.error.HTTPError | None = None
    for attempt in range(4):
        _wait_letterboxd_gap(pause)
        try:
            body = fetch(url, extra_headers=extra_headers)
            _mark_letterboxd_fetch()
            return body
        except urllib.error.HTTPError as exc:
            _mark_letterboxd_fetch()
            last = exc
            if exc.code == 404:
                raise RuntimeError(f"Failed to fetch {url}: HTTP 404") from exc
            if exc.code not in {403, 429, 503}:
                raise RuntimeError(f"Failed to fetch {url}: HTTP {exc.code}") from exc
            time.sleep(pause * (attempt + 2))
        except urllib.error.URLError as exc:
            _mark_letterboxd_fetch()
            raise RuntimeError(f"Failed to fetch {url}: {exc.reason}") from exc
    code = last.code if last is not None else 0
    raise LetterboxdRefused(f"Failed to fetch {url}: HTTP {code}")


_TAXONOMY_LINK_RE = re.compile(
    r'href="(/films/(theme|mini-theme|nanogenre|country)/[^"]*)"[^>]*>\s*([^<]+?)\s*</a>',
    re.IGNORECASE,
)
_HEADING_BLOCK_RE = re.compile(
    r"<h3><span>(Themes|Nanogenres|Country)</span></h3>\s*"
    r'<div class="text-sluglist[^"]*">(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_RATING_COUNT_RE = re.compile(r'"ratingCount"\s*:\s*(\d+)')
_PROFILE_FILM_COUNT_RE = re.compile(
    r'href="/[^"]+/films/"><span class="value">([\d,]+)</span>'
    r'<span class="definition[^"]*">Films</span>',
    re.IGNORECASE,
)


def _taxonomy_from_block(block: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {
        "themes": [],
        "mini_themes": [],
        "nanogenres": [],
        "countries": [],
    }
    buckets = {
        "theme": "themes",
        "mini-theme": "mini_themes",
        "nanogenre": "nanogenres",
        "country": "countries",
    }
    seen: set[tuple[str, str]] = set()
    for _href, kind, label in _TAXONOMY_LINK_RE.findall(block):
        name = re.sub(r"\s+", " ", html.unescape(label)).strip()
        if not name or name.casefold().startswith("show all"):
            continue
        bucket = buckets.get(kind.casefold())
        if bucket is None:
            continue
        key = (bucket, name.casefold())
        if key in seen:
            continue
        seen.add(key)
        found[bucket].append(name)
    return found


_NANOGENRE_LINK_RE = re.compile(
    r'href="/films/nanogenre/([^"/]+)/[^"]*"[^>]*>\s*([^<]+?)\s*</a>',
    re.IGNORECASE,
)


def parse_letterboxd_nanogenres(html_text: str) -> list[tuple[str, str]]:
    """Nanogenre name and catalog slug from a film's nanogenres page."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for slug, label in _NANOGENRE_LINK_RE.findall(html_text):
        name = re.sub(r"\s+", " ", html.unescape(label)).strip()
        if not name or name.casefold().startswith("show all"):
            continue
        if slug in seen:
            continue
        seen.add(slug)
        found.append((name, slug))
    return found


def parse_letterboxd_taxonomy(html_text: str) -> dict[str, list[str]]:
    """Themes, mini-themes, nanogenres, and countries from a film page."""
    combined: dict[str, list[str]] = {
        "themes": [],
        "mini_themes": [],
        "nanogenres": [],
        "countries": [],
    }
    seen: set[tuple[str, str]] = set()
    for _heading, block in _HEADING_BLOCK_RE.findall(html_text):
        for bucket, names in _taxonomy_from_block(block).items():
            for name in names:
                key = (bucket, name.casefold())
                if key in seen:
                    continue
                seen.add(key)
                combined[bucket].append(name)
    return combined


def parse_letterboxd_rating_count(html_text: str) -> int | None:
    match = _RATING_COUNT_RE.search(html_text)
    if not match:
        return None
    return int(match.group(1))


def parse_letterboxd_film_features(html_text: str) -> dict[str, object]:
    """Metadata used to describe taste and to score a film."""
    (
        name,
        year,
        rating,
        genres,
        directors,
        language,
        _original_title,
        _original_language,
        actors,
        runtime,
    ) = parse_letterboxd_film(html_text)
    taxonomy = parse_letterboxd_taxonomy(html_text)
    return {
        "title": name,
        "year": year,
        "directors": directors,
        "actors": actors,
        "genres": genres,
        "themes": taxonomy["themes"],
        "mini_themes": taxonomy["mini_themes"],
        "nanogenres": taxonomy["nanogenres"],
        "countries": taxonomy["countries"],
        "language": language,
        "runtime": runtime,
        "community_rating": rating,
        "member_count": parse_letterboxd_rating_count(html_text),
        "themes_known": bool(taxonomy["themes"] or taxonomy["mini_themes"] or taxonomy["nanogenres"]),
        "synopsis": parse_letterboxd_synopsis(html_text) or "",
        "poster": parse_letterboxd_poster(html_text),
        "card_known": True,
    }


def parse_profile_film_count(html_text: str) -> int | None:
    match = _PROFILE_FILM_COUNT_RE.search(html_text)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def parse_film_rating_members(html_text: str, film_slug: str) -> list[str]:
    """Usernames on a film's ratings page, highest ratings first."""
    pattern = re.compile(
        rf'href="/([A-Za-z0-9_]+)/film/{re.escape(film_slug)}/activity/"',
        re.IGNORECASE,
    )
    seen: set[str] = set()
    names: list[str] = []
    for name in pattern.findall(html_text):
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def letterboxd_film_page_url(slug: str) -> str:
    return "https://letterboxd.com/film/" + urllib.parse.quote(slug, safe="-_") + "/"


def letterboxd_film_nanogenres_url(slug: str) -> str:
    return letterboxd_film_page_url(slug) + "nanogenres/"


def letterboxd_film_ratings_url(slug: str, page: int = 1) -> str:
    base = letterboxd_film_page_url(slug) + "ratings/"
    if page <= 1:
        return base
    return f"{base}page/{page}/"


def letterboxd_member_url(user: str) -> str:
    return "https://letterboxd.com/" + urllib.parse.quote(user, safe="-_") + "/"


def fetch_letterboxd_film_credit(slug: str) -> dict[str, object]:
    """Directors for one Letterboxd film. An unknown slug has no directors."""
    url = (
        "https://letterboxd.com/film/"
        + urllib.parse.quote(slug, safe="-_")
        + "/json/"
    )
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            parsed = parse_letterboxd_film_json(fetch(url))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {"title": None, "year": None, "directors": []}
            if exc.code in {403, 429, 503} and attempt < 3:
                time.sleep(1.5 * (attempt + 2))
                last_error = exc
                continue
            raise RuntimeError(f"Failed to fetch {url}: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"Failed to fetch {url}: {exc.reason}") from exc
        if parsed is None:
            raise RuntimeError(f"Letterboxd returned no film data for {slug}")
        return parsed
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


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


_SYNOPSIS_RE = re.compile(
    r'<div class="truncate"[^>]*\bdata-truncate="\d+"[^>]*>\s*<p>(.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)
_POSTER_RE = re.compile(
    r'"image"\s*:\s*"(https://a\.ltrbxd\.com/resized/(?:film-poster|sm/upload)/[^"]+)"'
)
_TAG_RE = re.compile(r"<[^>]+>")


def parse_letterboxd_synopsis(html_text: str) -> str | None:
    """The synopsis Letterboxd shows under the film title."""
    match = _SYNOPSIS_RE.search(html_text)
    if not match:
        return None
    text = re.sub(r"<br\s*/?>", "\n", match.group(1), flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def letterboxd_poster_url(value: object) -> str | None:
    """A Letterboxd poster, not the square social image or an empty frame."""
    if not isinstance(value, str) or not value.startswith("https://a.ltrbxd.com/resized/"):
        return None
    if "/film-poster/" in value or "-0-600-0-900-crop" in value:
        return value
    return None


def parse_letterboxd_poster(html_text: str) -> str | None:
    """Poster URL when Letterboxd has one. A placeholder is left out."""
    match = _POSTER_RE.search(html_text)
    if not match:
        return None
    return letterboxd_poster_url(match.group(1))


def parse_letterboxd_film_card(html_text: str) -> dict[str, object]:
    """Community score, synopsis, and poster from a Letterboxd film page."""
    rating = parse_letterboxd_film(html_text)[2]
    return {
        "rating": rating,
        "synopsis": parse_letterboxd_synopsis(html_text) or "",
        "poster": parse_letterboxd_poster(html_text),
    }


def parse_letterboxd_runtime(html_text: str) -> int | None:
    _, duration = _parse_letterboxd_actors_and_runtime(html_text)
    if isinstance(duration, int) and duration > 0:
        return duration
    return None


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
    duration_minutes: int | None = None,
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
    if isinstance(duration_minutes, int) and duration_minutes > 0:
        movie.duration_minutes = duration_minutes
    movie.duration_known = True


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


# Nearby-year guesses after autocomplete. Enough for a year and a numeric suffix
# on a few title bases, not every combination.
COLLISION_FETCH_LIMIT = 12


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


def stored_letterboxd_still_matches(
    letterboxd_url: str,
    release_year: int | None,
    nonstop_director: str | None,
    nonstop_cast: list[str] | None,
    nonstop_duration: int | None,
    delay: float,
) -> bool | None:
    """Re-check one stored Letterboxd page against Nonstop credits.

    True when that page confirms the film and does not conflict. False when the
    page is missing, identity conflicts, or the page does not confirm, so the
    caller should search again. None when Letterboxd could not be reached; the
    caller should keep the saved link.
    """
    slug = letterboxd_slug_from_url(letterboxd_url)
    if not slug:
        return False
    candidate, error = _letterboxd_fetch_candidate(slug, delay)
    if error:
        return None
    if candidate is None:
        return False
    _, _, lb_year, _, _, directors, _, _, _, actors, duration = candidate
    cast = list(nonstop_cast or [])
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
    return confirms and not conflicts


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
    search_ids: set[int] = set()

    def _supported(item: LetterboxdCandidate) -> bool:
        """Director, cast, or runtime agrees. A shared year alone is not enough."""
        _, _, lb_year, _, _, directors, _, _, _, actors, duration = item
        director = (
            director_overlaps(nonstop_director, directors)
            if nonstop_director and directors
            else None
        )
        cast_count = people_overlap_count(cast, actors) if cast and actors else 0
        duration_gap = duration_relation(nonstop_duration, duration)
        year_delta = (
            abs(release_year - lb_year)
            if release_year is not None and lb_year is not None
            else None
        )
        if director is False and cast_count < 2:
            return False
        if cast_count >= 2 and duration_gap != "conflict":
            return True
        if cast_count >= 1 and duration_gap == "close" and director is not False:
            return True
        if director is True and duration_gap == "close":
            return True
        if (
            director is True
            and cast_count >= 2
            and year_delta is not None
            and year_delta <= 1
        ):
            return True
        if (
            director is True
            and duration_gap != "conflict"
            and year_delta is not None
            and year_delta <= 1
        ):
            return True
        if (
            director is not False
            and duration_gap == "close"
            and year_delta is not None
            and year_delta <= 1
        ):
            return True
        return False

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
            duration,
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
            duration,
        )

    def _finish() -> Movie:
        if movie.note and not str(movie.note).startswith("Year mismatch"):
            return movie
        if release_year and movie.year and movie.year != release_year:
            movie.note = (
                f"Year mismatch (Nonstop {release_year}, Letterboxd {movie.year})"
            )
        return movie

    def consider_slug(slug: str, *, from_search: bool = False) -> bool:
        """Fetch one slug. True means stop: identity confirmed, or Letterboxd failed.

        Search hits (autocomplete, nearby-year guesses) must agree on director,
        cast, or runtime. A direct slug of this title may still match on year
        when Nonstop has no credits to compare.
        """
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
        if from_search:
            search_ids.add(id(candidate))
            confirms = confirms and _supported(candidate)
        if conflicts:
            candidates.append(candidate)
            return False
        candidates.append(candidate)
        if confirms:
            _apply(candidate)
            return True
        return False

    def _search_queries() -> None:
        queries = flexible_search_queries(
            display_title, nonstop_page_title, nonstop_url
        )
        seen_queries: set[str] = {q.casefold() for q in queries}
        for source in (display_title, nonstop_page_title):
            if not source:
                continue
            english = english_title_from_german_wikipedia(source, release_year)
            if not english:
                continue
            slug = slugify(english)
            if consider_slug(slug, from_search=True):
                return
            key = english.casefold()
            if key not in seen_queries:
                seen_queries.add(key)
                queries.insert(0, english)
        for query in queries:
            for slug in letterboxd_autocomplete_slugs(query, delay, limit=8):
                if consider_slug(slug, from_search=True):
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
        _search_queries()
        if movie.letterboxd_url:
            return _finish()
        fetched = 0
        for slug in collision_slug_candidates(
            display_title, nonstop_url, nonstop_page_title, release_year
        ):
            if not slug or slug in tried_slugs:
                continue
            fetched += 1
            if consider_slug(slug, from_search=True):
                return _finish()
            if fetched >= COLLISION_FETCH_LIMIT:
                break
    elif not candidates:
        _search_queries()
        if movie.letterboxd_url:
            return _finish()

    if movie.note and not movie.letterboxd_url and "error" in movie.note.casefold():
        return movie

    pool = []
    for item in candidates:
        confirms, conflicts = _identity(item)
        if conflicts or not confirms:
            continue
        if id(item) in search_ids and not _supported(item):
            continue
        pool.append(item)
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
