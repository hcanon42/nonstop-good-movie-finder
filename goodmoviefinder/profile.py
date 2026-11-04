"""Load a Letterboxd member's watched films and watchlist.

The result is cached on the ratings store. A page Letterboxd refuses is
retried by the fetcher; a run that still fails keeps the last complete copy.
"""

from __future__ import annotations

import sys
from pathlib import Path

from goodmoviefinder.cache import (
    load_profile_cache,
    save_cache,
    save_profile_cache,
    save_watchlist_films,
)
from goodmoviefinder.letterboxd import (
    fetch_letterboxd_watched_ratings,
    fetch_letterboxd_watchlist,
    vm_mode,
)
from goodmoviefinder import progress


def _watchlist_titles_current(
    slugs: set[str],
    films: list[dict[str, object]] | None,
) -> bool:
    if films is None:
        return not slugs
    return {str(film["slug"]) for film in films} == slugs


def load_profile(
    cache: dict,
    cache_path: Path,
    user: str,
    delay: float,
    *,
    refresh: bool,
    no_cache: bool,
) -> tuple[dict[str, float | None], set[str], list[dict[str, object]]]:
    if vm_mode():
        profile = load_profile_cache(cache, user, allow_stale=True)
        if profile is None:
            print(f"VM mode: no saved profile for @{user}.", file=sys.stderr)
            return {}, set(), []
        watched_ratings, watchlist_slugs, watchlist_films = profile
        print(
            f"VM mode: using saved profile: {len(watched_ratings)} watched, "
            f"{len(watchlist_slugs)} on watchlist.",
            file=sys.stderr,
        )
        progress.tick(
            detail=(
                f"Saved profile: {len(watched_ratings)} watched, "
                f"{len(watchlist_slugs)} on watchlist"
            )
        )
        return watched_ratings, watchlist_slugs, watchlist_films or []

    profile = None
    if not refresh and not no_cache:
        profile = load_profile_cache(cache, user)
    if profile is not None:
        watched_ratings, watchlist_slugs, watchlist_films = profile
        if _watchlist_titles_current(watchlist_slugs, watchlist_films):
            print(
                f"Using cached profile: {len(watched_ratings)} watched, "
                f"{len(watchlist_slugs)} on watchlist.",
                file=sys.stderr,
            )
            progress.tick(
                detail=(
                    f"Cached profile: {len(watched_ratings)} watched, "
                    f"{len(watchlist_slugs)} on watchlist"
                )
            )
            return watched_ratings, watchlist_slugs, watchlist_films or []
        print(f"Fetching Letterboxd watchlist titles for @{user}…", file=sys.stderr)
        progress.tick(detail=f"Watchlist titles for @{user}")
        try:
            watchlist_films = fetch_letterboxd_watchlist(user, delay=delay)
        except RuntimeError as exc:
            print(f"{exc}; watchlist titles were not updated.", file=sys.stderr)
            return watched_ratings, watchlist_slugs, []
        save_watchlist_films(cache, user, watchlist_films)
        save_cache(cache_path, cache)
        watchlist_slugs = {str(film["slug"]) for film in watchlist_films}
        print(f"Watchlist: {len(watchlist_films)} films.", file=sys.stderr)
        return watched_ratings, watchlist_slugs, watchlist_films

    print(f"Fetching Letterboxd watched films for @{user}…", file=sys.stderr)
    progress.tick(detail=f"Watched films for @{user}")
    watched_ratings: dict[str, float | None] = {}
    try:
        watched_ratings = fetch_letterboxd_watched_ratings(
            user, delay=delay, until_complete=True
        )
    except RuntimeError as exc:
        print(f"{exc}; watched list was not updated.", file=sys.stderr)
        stale = load_profile_cache(cache, user, allow_stale=True)
        if stale is not None:
            watched_ratings, watchlist_slugs, watchlist_films = stale
            print(
                f"Using the saved profile "
                f"({len(watched_ratings)} watched, {len(watchlist_slugs)} on watchlist).",
                file=sys.stderr,
            )
            return watched_ratings, watchlist_slugs, watchlist_films or []
        watched_ratings = {}

    print(f"Fetching Letterboxd watchlist for @{user}…", file=sys.stderr)
    progress.tick(detail=f"Watchlist for @{user}")
    try:
        watchlist_films = fetch_letterboxd_watchlist(user, delay=delay)
    except RuntimeError as exc:
        print(f"{exc}; watchlist was not updated.", file=sys.stderr)
        stale = load_profile_cache(cache, user, allow_stale=True)
        if stale is not None:
            saved_watched, watchlist_slugs, watchlist_films = stale
            if not watched_ratings:
                watched_ratings = saved_watched
            watchlist_films = watchlist_films or []
            if not watchlist_films:
                watchlist_slugs = set(watchlist_slugs)
            else:
                watchlist_slugs = {str(film["slug"]) for film in watchlist_films}
            if watched_ratings or watchlist_slugs:
                save_profile_cache(
                    cache,
                    user,
                    watched_ratings,
                    watchlist_slugs,
                    watchlist_films,
                    profile_complete=False,
                )
                save_cache(cache_path, cache)
            return watched_ratings, watchlist_slugs, watchlist_films
        watchlist_films = []

    watchlist_slugs = {str(film["slug"]) for film in watchlist_films}
    if watched_ratings or watchlist_slugs:
        save_profile_cache(cache, user, watched_ratings, watchlist_slugs, watchlist_films)
        save_cache(cache_path, cache)
        rated = sum(1 for value in watched_ratings.values() if value is not None)
        print(
            f"Profile: {len(watched_ratings)} watched ({rated} rated), "
            f"{len(watchlist_slugs)} on watchlist.",
            file=sys.stderr,
        )
    else:
        print(
            "Letterboxd profile is empty; continuing without watchlist or watched filters.",
            file=sys.stderr,
        )
    return watched_ratings, watchlist_slugs, watchlist_films
