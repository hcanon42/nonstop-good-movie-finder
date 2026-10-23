"""JSON cache for Letterboxd matches and the viewer profile."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any

from goodmoviefinder.http import fetch
from goodmoviefinder.progress import tick
from goodmoviefinder.letterboxd import (
    LetterboxdRefused,
    fetch_letterboxd_film_credit,
    fetch_letterboxd_html,
    letterboxd_backdrop_url,
    letterboxd_film_nanogenres_url,
    letterboxd_film_page_url,
    letterboxd_poster_url,
    parse_letterboxd_backdrop,
    parse_letterboxd_film_features,
    parse_letterboxd_nanogenres,
    parse_letterboxd_poster,
    parse_letterboxd_runtime,
    parse_letterboxd_synopsis,
    parse_original_title,
    parse_primary_language,
)
from goodmoviefinder.models import Movie
from goodmoviefinder.nonstop import parse_nonstop_movie_page
from goodmoviefinder.program import LOVED_RATING, letterboxd_slug_from_url

CACHE_VERSION = 4
PROFILE_CACHE_MAX_AGE = 6 * 60 * 60  # seconds
RECOMMENDATIONS_CACHE_VERSION = 1

LANGUAGE_PARSER = 2
# Bump to re-check cached Letterboxd pages against Nonstop director, runtime, and cast.
MATCHER_VERSION = 4
# Bump to re-read original titles for films already cached with a Letterboxd page.
TITLE_PARSER = 1


def _stored_watchlist_films(profile: dict[str, Any]) -> list[dict[str, object]] | None:
    """None when this profile was saved before watchlist titles were stored."""
    if "watchlist_films" not in profile:
        return None
    raw = profile.get("watchlist_films")
    if not isinstance(raw, list):
        return None
    films: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug or slug in seen:
            continue
        title = item.get("title")
        display = title.strip() if isinstance(title, str) and title.strip() else slug
        year = item.get("year")
        if isinstance(year, float) and year.is_integer():
            year = int(year)
        if not isinstance(year, int):
            year = None
        seen.add(slug)
        films.append({"slug": slug, "title": display, "year": year})
    return films


def load_profile_cache(
    cache: dict[str, Any],
    user: str,
    *,
    allow_stale: bool = False,
) -> tuple[dict[str, float | None], set[str], list[dict[str, object]] | None] | None:
    profile = cache.get("profile", {}).get(user)
    if not isinstance(profile, dict):
        return None
    fetched_at = profile.get("fetched_at", 0)
    if not allow_stale and time.time() - fetched_at > PROFILE_CACHE_MAX_AGE:
        return None
    # A profile saved before every films page loaded must be fetched again.
    if not allow_stale and not profile.get("profile_complete"):
        return None
    raw_ratings = profile.get("watched_ratings")
    if not isinstance(raw_ratings, dict):
        return None
    watched = {
        slug: None if value is None else float(value)
        for slug, value in raw_ratings.items()
    }
    watchlist = set(profile.get("watchlist", []))
    return watched, watchlist, _stored_watchlist_films(profile)


def save_profile_cache(
    cache: dict[str, Any],
    user: str,
    watched_ratings: dict[str, float | None],
    watchlist: set[str],
    watchlist_films: list[dict[str, object]],
    *,
    profile_complete: bool = True,
) -> None:
    cache.setdefault("profile", {})[user] = {
        "watched": sorted(watched_ratings),
        "watched_ratings": watched_ratings,
        "watchlist": sorted(watchlist),
        "watchlist_films": watchlist_films,
        "fetched_at": int(time.time()),
        "profile_complete": profile_complete,
    }


def save_watchlist_films(
    cache: dict[str, Any],
    user: str,
    watchlist_films: list[dict[str, object]],
) -> None:
    """Fill in watchlist titles without marking the rest of the profile stale."""
    profile = cache.get("profile", {}).get(user)
    if not isinstance(profile, dict):
        return
    profile["watchlist"] = sorted({str(film["slug"]) for film in watchlist_films})
    profile["watchlist_films"] = watchlist_films


def _opt_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _opt_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _taste_movie_payload(movie: Movie) -> dict[str, Any]:
    return {
        "title": movie.title,
        "nonstop_url": movie.nonstop_url,
        "letterboxd_url": movie.letterboxd_url,
        "letterboxd_title": movie.letterboxd_title,
        "rating": movie.rating,
        "year": movie.year,
        "directors": list(movie.directors),
        "genres": list(movie.genres),
        "primary_language": movie.primary_language,
        "primary_language_known": movie.primary_language_known,
        "poster_url": movie.poster_url,
        "synopsis": movie.synopsis,
        "details_known": movie.details_known,
        "because": movie.because,
        "predicted_rating": movie.predicted_rating,
        "lower_estimate": movie.lower_estimate,
        "member_average": movie.member_average,
        "member_support": movie.member_support,
    }


def _taste_movie_from_payload(raw: object) -> Movie | None:
    if not isinstance(raw, dict):
        return None
    title = raw.get("title")
    url = raw.get("nonstop_url")
    if not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(url, str) or not url.strip():
        return None
    letterboxd_url = raw.get("letterboxd_url")
    letterboxd_title = raw.get("letterboxd_title")
    poster = raw.get("poster_url")
    synopsis = raw.get("synopsis")
    because = raw.get("because")
    language = raw.get("primary_language")
    support = raw.get("member_support")
    return Movie(
        title=title.strip(),
        nonstop_url=url.strip(),
        letterboxd_url=letterboxd_url.strip()
        if isinstance(letterboxd_url, str) and letterboxd_url.strip()
        else None,
        letterboxd_title=letterboxd_title.strip()
        if isinstance(letterboxd_title, str) and letterboxd_title.strip()
        else None,
        rating=_opt_float(raw.get("rating")),
        year=_opt_int(raw.get("year")),
        directors=_string_list(raw.get("directors")),
        genres=_string_list(raw.get("genres")),
        primary_language=language.strip() if isinstance(language, str) and language.strip() else None,
        primary_language_known=bool(raw.get("primary_language_known")),
        poster_url=poster.strip() if isinstance(poster, str) and poster.strip() else None,
        synopsis=synopsis if isinstance(synopsis, str) else None,
        details_known=bool(raw.get("details_known")),
        because=because.strip() if isinstance(because, str) and because.strip() else None,
        predicted_rating=_opt_float(raw.get("predicted_rating")),
        lower_estimate=_opt_float(raw.get("lower_estimate")),
        member_average=_opt_float(raw.get("member_average")),
        member_support=support if isinstance(support, int) and support > 0 else 0,
    )


def _taste_movies(raw: object) -> list[Movie] | None:
    if not isinstance(raw, list):
        return None
    movies: list[Movie] = []
    for item in raw:
        movie = _taste_movie_from_payload(item)
        if movie is not None:
            movies.append(movie)
    return movies


def load_recommendations_cache(
    cache: dict[str, Any],
    user: str,
) -> tuple[Any, int] | None:
    """Saved For you lists for this Letterboxd user, if a complete cache exists."""
    from goodmoviefinder.taste import TasteView

    record = cache.get("recommendations", {}).get(user)
    if not isinstance(record, dict):
        return None
    if record.get("version") != RECOMMENDATIONS_CACHE_VERSION:
        return None
    fetched_at = record.get("fetched_at")
    if not isinstance(fetched_at, (int, float)):
        return None
    paragraph = record.get("paragraph")
    note = record.get("note")
    if not isinstance(paragraph, str) or not isinstance(note, str):
        return None
    see_first = _taste_movies(record.get("see_first"))
    like_you = _taste_movies(record.get("like_you"))
    step_outside = _taste_movies(record.get("step_outside"))
    if see_first is None or like_you is None or step_outside is None:
        return None
    loved = _string_list(record.get("loved"))
    lower = _string_list(record.get("lower"))
    loved_are_themes = bool(record.get("loved_are_themes"))
    if not loved and not lower:
        recovered = _recover_taste_highlights(cache, user)
        if recovered is not None:
            loved, lower, loved_are_themes = recovered
    view = TasteView(
        paragraph=paragraph,
        note=note,
        loved=loved,
        lower=lower,
        loved_are_themes=loved_are_themes,
        see_first=see_first,
        like_you=like_you,
        step_outside=step_outside,
    )
    profile = load_profile_cache(cache, user, allow_stale=True)
    if profile is not None:
        from goodmoviefinder.taste import refresh_view_because

        watched_ratings, _watchlist, _films = profile
        refresh_view_because(view, cache, watched_ratings)
    return (
        view,
        int(fetched_at),
    )


def save_recommendations_cache(
    cache: dict[str, Any],
    cache_path: Path,
    user: str,
    view: Any,
    *,
    fetched_at: int | None = None,
) -> int:
    saved_at = int(time.time() if fetched_at is None else fetched_at)
    cache.setdefault("recommendations", {})[user] = {
        "version": RECOMMENDATIONS_CACHE_VERSION,
        "fetched_at": saved_at,
        "paragraph": view.paragraph,
        "note": view.note,
        "loved": list(view.loved),
        "lower": list(view.lower),
        "loved_are_themes": bool(view.loved_are_themes),
        "see_first": [_taste_movie_payload(movie) for movie in view.see_first],
        "like_you": [_taste_movie_payload(movie) for movie in view.like_you],
        "step_outside": [_taste_movie_payload(movie) for movie in view.step_outside],
    }
    save_cache(cache_path, cache)
    return saved_at


def _recover_taste_highlights(
    cache: dict[str, Any],
    user: str,
) -> tuple[list[str], list[str], bool] | None:
    """Rebuild taste phrases from a saved profile when an older recommendations cache has only prose."""
    profile = load_profile_cache(cache, user, allow_stale=True)
    if profile is None:
        return None
    watched, _watchlist, _films = profile
    rated = [slug for slug, rating in watched.items() if isinstance(rating, (int, float))]
    if not rated:
        return None
    features: dict[str, dict[str, Any]] = {}
    for slug in rated:
        feature = film_feature_get(cache, slug)
        if feature is None:
            return None
        features[slug] = feature
    from goodmoviefinder.taste import taste_highlights

    return taste_highlights(watched, features)


def recommendations_updated_label(fetched_at: int | None) -> str:
    if not fetched_at:
        return ""
    when = datetime.fromtimestamp(int(fetched_at)).astimezone().strftime("%d %b %Y, %H:%M")
    return f"Last built {when}"


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
        letterboxd_poster_url=letterboxd_poster_url(entry.get("letterboxd_poster_url")),
        poster_checked=bool(entry.get("poster_checked"))
        or letterboxd_poster_url(entry.get("letterboxd_poster_url")) is not None,
        letterboxd_backdrop_url=letterboxd_backdrop_url(entry.get("letterboxd_backdrop_url")),
        manual_match=bool(entry.get("manual_match")),
        match_current=entry.get("matcher") == MATCHER_VERSION
        or bool(entry.get("manual_match")),
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
    poster = letterboxd_poster_url(movie.letterboxd_poster_url)
    if poster:
        entry["letterboxd_poster_url"] = poster
    if movie.poster_checked or poster:
        entry["poster_checked"] = True
    backdrop = letterboxd_backdrop_url(movie.letterboxd_backdrop_url)
    if backdrop:
        entry["letterboxd_backdrop_url"] = backdrop
    if movie.manual_match:
        entry["manual_match"] = True
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


def fill_letterboxd_poster(movie: Movie, cache: dict[str, Any], delay: float) -> bool:
    """Attach Letterboxd's poster. Returns True when the cache entry should be saved."""
    if letterboxd_poster_url(movie.letterboxd_poster_url) or movie.poster_checked:
        return False
    from goodmoviefinder.program import apply_known_letterboxd_posters

    apply_known_letterboxd_posters([movie], cache)
    if letterboxd_poster_url(movie.letterboxd_poster_url):
        movie.poster_checked = True
        return True
    if not movie.letterboxd_url:
        movie.poster_checked = True
        return True
    body = fetch_letterboxd_html(movie.letterboxd_url, delay)
    poster = parse_letterboxd_poster(body)
    movie.poster_checked = True
    if poster:
        movie.letterboxd_poster_url = poster
    backdrop = parse_letterboxd_backdrop(body)
    if backdrop and not letterboxd_backdrop_url(movie.letterboxd_backdrop_url):
        movie.letterboxd_backdrop_url = backdrop
        slug = letterboxd_slug_from_url(movie.letterboxd_url)
        if slug:
            remember_backdrop(slug, backdrop)
    return True


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
    poster = parse_letterboxd_poster(body)
    movie.poster_checked = True
    if poster:
        movie.letterboxd_poster_url = poster
    backdrop = parse_letterboxd_backdrop(body)
    if backdrop:
        movie.letterboxd_backdrop_url = backdrop
        slug = letterboxd_slug_from_url(movie.letterboxd_url)
        if slug:
            remember_backdrop(slug, backdrop)
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
        tick(current=index, total=len(missing), detail=f"{label} — {names}")
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


def _slug_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        name: slug
        for name, slug in value.items()
        if isinstance(name, str) and name.strip() and isinstance(slug, str) and slug.strip()
    }


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def film_feature_get(cache: dict[str, Any], slug: str) -> dict[str, Any] | None:
    raw = cache.get("film_features", {}).get(slug)
    if not isinstance(raw, dict) or not raw.get("fetched"):
        return None
    year = raw.get("year")
    runtime = raw.get("runtime")
    community = raw.get("community_rating")
    members = raw.get("member_count")
    return {
        "title": raw.get("title") if isinstance(raw.get("title"), str) else None,
        "year": year if isinstance(year, int) else None,
        "directors": _string_list(raw.get("directors")),
        "actors": _string_list(raw.get("actors")),
        "genres": _string_list(raw.get("genres")),
        "themes": _string_list(raw.get("themes")),
        "mini_themes": _string_list(raw.get("mini_themes")),
        "nanogenres": _string_list(raw.get("nanogenres")),
        "nanogenre_slugs": _slug_map(raw.get("nanogenre_slugs")),
        "nanogenres_known": bool(raw.get("nanogenres_known")),
        "countries": _string_list(raw.get("countries")),
        "language": raw.get("language") if isinstance(raw.get("language"), str) else None,
        "runtime": runtime if isinstance(runtime, int) else None,
        "community_rating": float(community) if isinstance(community, (int, float)) else None,
        "member_count": members if isinstance(members, int) else None,
        "themes_known": bool(raw.get("themes_known")),
        "synopsis": raw.get("synopsis") if isinstance(raw.get("synopsis"), str) else "",
        "poster": raw.get("poster") if isinstance(raw.get("poster"), str) else None,
        "backdrop": letterboxd_backdrop_url(raw.get("backdrop")),
        "card_known": bool(raw.get("card_known")),
        "fetched": True,
    }


_BACKDROP_MAP: dict[str, str | None] | None = None
_BACKDROP_MTIME: float | None = None


def backdrop_path(cache_path: Path | None = None) -> Path:
    from goodmoviefinder.config import DEFAULT_CACHE

    folder = cache_path.parent if cache_path is not None else DEFAULT_CACHE.parent
    return folder / "backdrops.json"


def load_backdrops(cache_path: Path | None = None) -> dict[str, str | None]:
    """Slug to wide Letterboxd header image. None means the film page has no backdrop."""
    global _BACKDROP_MAP, _BACKDROP_MTIME
    path = backdrop_path(cache_path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _BACKDROP_MAP = {}
        _BACKDROP_MTIME = None
        return _BACKDROP_MAP
    if _BACKDROP_MAP is not None and _BACKDROP_MTIME == mtime:
        return _BACKDROP_MAP
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    mapping: dict[str, str | None] = {}
    if isinstance(raw, dict):
        for slug, url in raw.items():
            if not isinstance(slug, str) or not slug:
                continue
            if url is None:
                mapping[slug] = None
                continue
            cleaned = letterboxd_backdrop_url(url)
            if cleaned:
                mapping[slug] = cleaned
    _BACKDROP_MAP = mapping
    _BACKDROP_MTIME = mtime
    return mapping


def backdrop_for_slug(slug: str | None, cache_path: Path | None = None) -> str | None:
    known, url = backdrop_lookup(slug, cache_path)
    return url if known else None


def backdrop_lookup(
    slug: str | None,
    cache_path: Path | None = None,
) -> tuple[bool, str | None]:
    """(already checked, image url). A checked film can still have no header image."""
    if not slug:
        return False, None
    mapping = load_backdrops(cache_path)
    if slug not in mapping:
        return False, None
    url = mapping[slug]
    return True, url if isinstance(url, str) else None


def remember_backdrop(
    slug: str,
    url: str | None,
    cache_path: Path | None = None,
) -> None:
    """Remember whether a film has a wide Letterboxd header, including when it does not."""
    if not slug:
        return
    mapping = dict(load_backdrops(cache_path))
    mapping[slug] = letterboxd_backdrop_url(url)
    path = backdrop_path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    global _BACKDROP_MAP, _BACKDROP_MTIME
    _BACKDROP_MAP = mapping
    try:
        _BACKDROP_MTIME = path.stat().st_mtime
    except OSError:
        _BACKDROP_MTIME = None


def _kept_backdrop(previous: object, feature: dict[str, Any]) -> str | None:
    backdrop = letterboxd_backdrop_url(feature.get("backdrop"))
    if backdrop:
        return backdrop
    if isinstance(previous, dict):
        return letterboxd_backdrop_url(previous.get("backdrop"))
    return None


def _film_feature_put(cache: dict[str, Any], slug: str, feature: dict[str, Any]) -> None:
    previous = cache.get("film_features", {}).get(slug)
    nanogenres = list(feature.get("nanogenres") or [])
    nanogenre_slugs = _slug_map(feature.get("nanogenre_slugs"))
    known = bool(feature.get("nanogenres_known"))
    if (
        isinstance(previous, dict)
        and previous.get("nanogenres_known")
        and not known
    ):
        nanogenres = _string_list(previous.get("nanogenres"))
        nanogenre_slugs = _slug_map(previous.get("nanogenre_slugs"))
        known = True
    cache.setdefault("film_features", {})[slug] = {
        "title": feature.get("title"),
        "year": feature.get("year"),
        "directors": list(feature.get("directors") or []),
        "actors": list(feature.get("actors") or []),
        "genres": list(feature.get("genres") or []),
        "themes": list(feature.get("themes") or []),
        "mini_themes": list(feature.get("mini_themes") or []),
        "nanogenres": nanogenres,
        "nanogenre_slugs": nanogenre_slugs,
        "nanogenres_known": known,
        "countries": list(feature.get("countries") or []),
        "language": feature.get("language"),
        "runtime": feature.get("runtime"),
        "community_rating": feature.get("community_rating"),
        "member_count": feature.get("member_count"),
        "themes_known": bool(feature.get("themes_known")),
        "synopsis": feature.get("synopsis") or "",
        "poster": feature.get("poster"),
        "backdrop": _kept_backdrop(previous, feature),
        "card_known": bool(feature.get("card_known")),
        "fetched": True,
    }
    stored_backdrop = letterboxd_backdrop_url(cache["film_features"][slug].get("backdrop"))
    if stored_backdrop and backdrop_for_slug(slug) != stored_backdrop:
        remember_backdrop(slug, stored_backdrop)


def ensure_film_features(
    cache: dict[str, Any],
    cache_path: Path,
    slugs: list[str],
    delay: float,
) -> dict[str, dict[str, Any]]:
    """Letterboxd themes, country, cast, and ratings for each slug. Cached after the first fetch."""
    features: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for slug in dict.fromkeys(slugs):
        stored = film_feature_get(cache, slug)
        if stored is None:
            missing.append(slug)
        else:
            features[slug] = stored
    if not missing:
        return features

    print(
        f"Looking up themes and countries for {len(missing)} films…",
        file=sys.stderr,
    )
    refused = 0
    for index, slug in enumerate(missing, start=1):
        try:
            body = fetch_letterboxd_html(letterboxd_film_page_url(slug), delay)
        except LetterboxdRefused as exc:
            refused += 1
            print(f"  [{index}/{len(missing)}] {slug}: {exc}", file=sys.stderr)
            if refused >= 3:
                print(
                    "Letterboxd refused three times. Stopping the feature lookup.",
                    file=sys.stderr,
                )
                break
            time.sleep(max(delay, 1.0))
            continue
        except RuntimeError as exc:
            refused = 0
            print(f"  [{index}/{len(missing)}] {slug}: {exc}", file=sys.stderr)
            time.sleep(delay)
            continue
        refused = 0
        parsed = parse_letterboxd_film_features(body)
        _film_feature_put(cache, slug, parsed)
        stored = film_feature_get(cache, slug)
        if stored is not None:
            features[slug] = stored
        label = parsed.get("title") or slug
        themes = list(parsed.get("themes") or [])
        countries = list(parsed.get("countries") or [])
        detail = ", ".join([*countries, *themes[:2]]) or "no themes"
        print(f"  [{index}/{len(missing)}] {label} — {detail}", file=sys.stderr)
        tick(current=index, total=len(missing), detail=f"{label} — {detail}")
        if index % 10 == 0:
            save_cache(cache_path, cache)
        time.sleep(delay)
    save_cache(cache_path, cache)
    print(
        f"Features ready for {len(features)} of {len(set(slugs))} films.",
        file=sys.stderr,
    )
    return features


def ensure_nanogenres(
    cache: dict[str, Any],
    cache_path: Path,
    slugs: list[str],
    delay: float,
) -> None:
    """Fill nanogenres from each film's nanogenres page. The film page itself does not list them."""
    stored = cache.setdefault("film_features", {})
    missing = [
        slug
        for slug in dict.fromkeys(slugs)
        if isinstance(stored.get(slug), dict)
        and stored[slug].get("fetched")
        and not stored[slug].get("nanogenres_known")
    ]
    if not missing:
        return
    print(
        f"Looking up nanogenres for {len(missing)} films…",
        file=sys.stderr,
    )
    refused = 0
    found = 0
    for index, slug in enumerate(missing, start=1):
        try:
            body = fetch_letterboxd_html(letterboxd_film_nanogenres_url(slug), delay)
        except LetterboxdRefused as exc:
            refused += 1
            print(f"  [{index}/{len(missing)}] {slug}: {exc}", file=sys.stderr)
            if refused >= 3:
                print(
                    "Letterboxd refused three times. Stopping the nanogenre lookup.",
                    file=sys.stderr,
                )
                break
            time.sleep(max(delay, 1.0))
            continue
        except RuntimeError as exc:
            refused = 0
            print(f"  [{index}/{len(missing)}] {slug}: {exc}", file=sys.stderr)
            time.sleep(delay)
            continue
        refused = 0
        pairs = parse_letterboxd_nanogenres(body)
        raw = stored.get(slug)
        if isinstance(raw, dict):
            raw["nanogenres"] = [name for name, _nano in pairs]
            raw["nanogenre_slugs"] = {name: nano for name, nano in pairs}
            raw["nanogenres_known"] = True
            found += 1
        if index % 25 == 0 or index == len(missing):
            print(
                f"  [{index}/{len(missing)}] nanogenres saved",
                file=sys.stderr,
            )
            save_cache(cache_path, cache)
        tick(current=index, total=len(missing), detail=f"Nanogenres {index}/{len(missing)}")
        time.sleep(delay)
    save_cache(cache_path, cache)
    print(f"Nanogenres ready for {found} films.", file=sys.stderr)


def ensure_film_cards(
    cache: dict[str, Any],
    cache_path: Path,
    slugs: list[str],
    delay: float,
) -> None:
    """Synopsis and poster for films whose features were saved before those fields existed."""
    missing = []
    for slug in dict.fromkeys(slugs):
        raw = cache.get("film_features", {}).get(slug)
        if isinstance(raw, dict) and raw.get("fetched") and not raw.get("card_known"):
            missing.append(slug)
    if not missing:
        return
    print(f"Looking up posters and synopses for {len(missing)} films…", file=sys.stderr)
    refused = 0
    for index, slug in enumerate(missing, start=1):
        raw = cache.get("film_features", {}).get(slug)
        if not isinstance(raw, dict):
            continue
        try:
            body = fetch_letterboxd_html(letterboxd_film_page_url(slug), delay)
        except LetterboxdRefused as exc:
            refused += 1
            print(f"  [{index}/{len(missing)}] {slug}: {exc}", file=sys.stderr)
            if refused >= 3:
                print("Letterboxd refused three times. Stopping poster lookup.", file=sys.stderr)
                break
            time.sleep(max(delay, 1.0))
            continue
        except RuntimeError as exc:
            refused = 0
            print(f"  [{index}/{len(missing)}] {slug}: {exc}", file=sys.stderr)
            time.sleep(delay)
            continue
        refused = 0
        raw["synopsis"] = parse_letterboxd_synopsis(body) or ""
        raw["poster"] = parse_letterboxd_poster(body)
        backdrop = parse_letterboxd_backdrop(body)
        if backdrop:
            raw["backdrop"] = backdrop
            remember_backdrop(slug, backdrop)
        raw["card_known"] = True
        label = raw.get("title") or slug
        print(f"  [{index}/{len(missing)}] {label}", file=sys.stderr)
        tick(current=index, total=len(missing), detail=str(label))
        time.sleep(delay)
    save_cache(cache_path, cache)
