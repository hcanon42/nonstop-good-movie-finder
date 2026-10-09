"""JSON cache for Letterboxd matches and the viewer profile."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
from pathlib import Path
from typing import Any

from goodmoviefinder.http import fetch
from goodmoviefinder.letterboxd import (
    fetch_letterboxd_film_credit,
    parse_letterboxd_runtime,
    parse_original_title,
    parse_primary_language,
)
from goodmoviefinder.models import Movie
from goodmoviefinder.nonstop import parse_nonstop_movie_page
from goodmoviefinder.program import LOVED_RATING, letterboxd_slug_from_url

CACHE_VERSION = 4
PROFILE_CACHE_MAX_AGE = 6 * 60 * 60  # seconds

LANGUAGE_PARSER = 2
# Bump to re-check cached Letterboxd pages against Nonstop director, runtime, and cast.
MATCHER_VERSION = 4
# Bump to re-read original titles for films already cached with a Letterboxd page.
TITLE_PARSER = 1


def load_profile_cache(
    cache: dict[str, Any],
    user: str,
    *,
    allow_stale: bool = False,
) -> tuple[dict[str, float | None], set[str]] | None:
    profile = cache.get("profile", {}).get(user)
    if not profile:
        return None
    fetched_at = profile.get("fetched_at", 0)
    if not allow_stale and time.time() - fetched_at > PROFILE_CACHE_MAX_AGE:
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
    raw_duration = entry.get("duration_minutes")
    duration_minutes = (
        raw_duration if isinstance(raw_duration, int) and raw_duration > 0 else None
    )
    duration_known = bool(entry.get("duration_known")) or duration_minutes is not None
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
        duration_minutes=duration_minutes,
        duration_known=duration_known,
        note=entry.get("note"),
        match_current=entry.get("matcher") == MATCHER_VERSION,
    )


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
    if isinstance(movie.duration_minutes, int) and movie.duration_minutes > 0:
        entry["duration_minutes"] = movie.duration_minutes
        entry["duration_known"] = True
    elif movie.duration_known:
        entry["duration_known"] = True
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
    runtime = parse_letterboxd_runtime(body)
    if runtime and not movie.duration_minutes:
        movie.duration_minutes = runtime
        movie.duration_known = True
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
    if isinstance(page.duration_minutes, int) and page.duration_minutes > 0:
        movie.duration_minutes = page.duration_minutes
        movie.duration_known = True
    time.sleep(delay)


def _film_credit_get(cache: dict[str, Any], slug: str) -> dict[str, Any] | None:
    raw = cache.get("film_credits", {}).get(slug)
    if not isinstance(raw, dict) or not isinstance(raw.get("directors"), list):
        return None
    title = raw.get("title")
    year = raw.get("year")
    return {
        "title": title.strip() if isinstance(title, str) and title.strip() else None,
        "year": year if isinstance(year, int) else None,
        "directors": [
            name.strip()
            for name in raw["directors"]
            if isinstance(name, str) and name.strip()
        ],
    }


def _film_credit_put(cache: dict[str, Any], slug: str, credit: dict[str, Any]) -> None:
    cache.setdefault("film_credits", {})[slug] = {
        "title": credit.get("title"),
        "year": credit.get("year"),
        "directors": list(credit.get("directors") or []),
    }


def _remember_film_credit(
    cache: dict[str, Any],
    slug: str | None,
    title: str | None,
    year: object,
    directors: object,
) -> bool:
    if not slug or _film_credit_get(cache, slug) is not None:
        return False
    if not isinstance(directors, list) or not directors:
        return False
    names = [name.strip() for name in directors if isinstance(name, str) and name.strip()]
    if not names:
        return False
    _film_credit_put(
        cache,
        slug,
        {
            "title": title.strip() if isinstance(title, str) and title.strip() else None,
            "year": year if isinstance(year, int) else None,
            "directors": names,
        },
    )
    return True


def ensure_loved_film_credits(
    cache: dict[str, Any],
    cache_path: Path,
    watched_ratings: dict[str, float | None],
    delay: float,
    movies: list[Movie] | None = None,
) -> dict[str, dict]:
    """Directors for films rated at least 4.0. Missing ones are fetched and saved."""
    loved = sorted(
        slug
        for slug, rating in watched_ratings.items()
        if isinstance(rating, (int, float)) and rating >= LOVED_RATING
    )
    if not loved:
        return {}

    changed = False
    for movie in movies or []:
        changed = (
            _remember_film_credit(
                cache,
                letterboxd_slug_from_url(movie.letterboxd_url),
                movie.letterboxd_title or movie.title,
                movie.year,
                movie.directors,
            )
            or changed
        )
    for entry in cache.get("entries", {}).values():
        if not isinstance(entry, dict):
            continue
        changed = (
            _remember_film_credit(
                cache,
                letterboxd_slug_from_url(entry.get("letterboxd_url")),
                entry.get("letterboxd_title") or entry.get("title"),
                entry.get("year"),
                entry.get("directors"),
            )
            or changed
        )

    credits: dict[str, dict] = {}
    missing: list[str] = []
    for slug in loved:
        credit = _film_credit_get(cache, slug)
        if credit is None:
            missing.append(slug)
        else:
            credits[slug] = credit

    if missing:
        print(
            f"Looking up directors for {len(missing)} films rated {LOVED_RATING:.1f} or higher…",
            file=sys.stderr,
        )
    for index, slug in enumerate(missing, start=1):
        try:
            fetched = fetch_letterboxd_film_credit(slug)
        except RuntimeError as exc:
            print(f"  [{index}/{len(missing)}] {slug}: {exc}", file=sys.stderr)
            time.sleep(delay)
            continue
        credit = {
            "title": fetched.get("title"),
            "year": fetched.get("year"),
            "directors": list(fetched.get("directors") or []),
        }
        _film_credit_put(cache, slug, credit)
        credits[slug] = credit
        changed = True
        label = credit["title"] or slug
        names = ", ".join(credit["directors"]) or "no director"
        print(f"  [{index}/{len(missing)}] {label} — {names}", file=sys.stderr)
        if index % 10 == 0:
            save_cache(cache_path, cache)
        time.sleep(delay)

    if changed:
        save_cache(cache_path, cache)
    ready = sum(1 for slug in loved if credits.get(slug, {}).get("directors"))
    print(
        f"Directors ready for {ready} of {len(loved)} films rated {LOVED_RATING:.1f} or higher.",
        file=sys.stderr,
    )
    return {slug: credits[slug] for slug in loved if slug in credits}


def backfill_runtime(movie: Movie, delay: float) -> bool:
    """Fill runtime from Letterboxd, then the Nonstop film page."""
    if movie.duration_known or movie.duration_minutes:
        movie.duration_known = True
        return False
    if movie.letterboxd_url:
        try:
            body = fetch(movie.letterboxd_url)
        except urllib.error.URLError as exc:
            print(
                f"  Letterboxd runtime failed ({exc.reason}); keeping film.",
                file=sys.stderr,
            )
            return False
        runtime = parse_letterboxd_runtime(body)
        time.sleep(delay)
        if runtime:
            movie.duration_minutes = runtime
            movie.duration_known = True
            return True
    try:
        body = fetch(movie.nonstop_url)
    except urllib.error.URLError as exc:
        print(
            f"  Nonstop runtime failed ({exc.reason}); keeping film.",
            file=sys.stderr,
        )
        return False
    page = parse_nonstop_movie_page(body)
    if isinstance(page.duration_minutes, int) and page.duration_minutes > 0:
        movie.duration_minutes = page.duration_minutes
    movie.duration_known = True
    time.sleep(delay)
    return True
