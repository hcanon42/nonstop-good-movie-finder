"""Resolve one Nonstop program film to a Letterboxd page.

A cached link is kept when it still agrees with the Nonstop film page.
Each accepted match is written to the ratings cache before the next film,
so an interrupted run does not repeat work already finished.
"""

from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

from goodmoviefinder.cache import (
    apply_cinema_sites,
    backfill_letterboxd_details,
    backfill_nonstop_details,
    backfill_runtime,
    cache_get,
    cache_put,
    cinema_sites_stale,
    fill_letterboxd_poster,
    refresh_cinema_sites,
    save_cache,
)
from goodmoviefinder.http import fetch
from goodmoviefinder.letterboxd import (
    LetterboxdRefused,
    apply_nonstop_metadata,
    letterboxd_poster_url,
    resolve_letterboxd,
    stored_letterboxd_still_matches,
    vm_mode,
)
from goodmoviefinder.models import Movie, NonstopFilmPage, ProgramFilm
from goodmoviefinder.nonstop import parse_nonstop_movie_page
from goodmoviefinder.program import attach_program


def _remember_runtime(movie: Movie, page: NonstopFilmPage | None) -> None:
    """Keep a Nonstop runtime when Letterboxd did not already provide one."""
    if page is None or movie.duration_minutes:
        return
    if isinstance(page.duration_minutes, int) and page.duration_minutes > 0:
        movie.duration_minutes = page.duration_minutes
        movie.duration_known = True


def _mark_new(movie: Movie, new_in_program: bool) -> Movie:
    if new_in_program:
        movie.new_in_program = True
    return movie


class PosterLookup:
    """Poster fetches for one pass over the program.

    Letterboxd refusing three times in a row stops further poster lookups.
    """

    def __init__(self) -> None:
        self.refused = 0
        self.stopped = False

    def fill(self, movie: Movie, cache: dict, delay: float) -> bool:
        if (
            self.stopped
            or letterboxd_poster_url(movie.letterboxd_poster_url)
            or movie.poster_checked
        ):
            return False
        print("  Looking up the Letterboxd poster…", file=sys.stderr)
        try:
            changed = fill_letterboxd_poster(movie, cache, delay)
        except LetterboxdRefused as exc:
            self.refused += 1
            print(f"  {exc}", file=sys.stderr)
            if self.refused >= 3:
                self.stopped = True
                print(
                    "Letterboxd refused three times. Stopping poster lookup.",
                    file=sys.stderr,
                )
            return False
        except RuntimeError as exc:
            print(f"  {exc}", file=sys.stderr)
            return False
        self.refused = 0
        return changed


def _lookup_program_film_vm(
    film: ProgramFilm,
    cache: dict,
    cache_path: Path,
    delay: float,
    *,
    new_in_program: bool,
) -> Movie:
    """Keep a saved match. Fill Nonstop details only, and do not search Letterboxd."""
    cached = cache_get(cache, film.url)
    if cached is None:
        movie = Movie(
            title=film.title,
            nonstop_url=film.url,
            note="No saved Letterboxd match.",
        )
        attach_program(movie, film)
        return _mark_new(movie, new_in_program)
    attach_program(cached, film)
    updated = False
    if not cached.details_known:
        print("  Looking up synopsis and cinema websites…", file=sys.stderr)
        backfill_nonstop_details(cached, delay)
        updated = cached.details_known
    elif cinema_sites_stale(cached):
        updated = refresh_cinema_sites(cached, delay) or updated
    if updated:
        cache_put(cache, cached)
        save_cache(cache_path, cache)
    return _mark_new(cached, new_in_program)


def lookup_program_film(
    film: ProgramFilm,
    cache: dict,
    cache_path: Path,
    delay: float,
    *,
    no_cache: bool,
    new_in_program: bool,
    poster_lookup: PosterLookup | None = None,
) -> Movie:
    """Resolve one program title, reusing a cached Letterboxd match when it still fits."""
    if vm_mode():
        return _lookup_program_film_vm(
            film,
            cache,
            cache_path,
            delay,
            new_in_program=new_in_program,
        )
    page: NonstopFilmPage | None = None
    if not no_cache:
        cached = cache_get(cache, film.url)
        if cached is not None:
            attach_program(cached, film)
            updated = False
            if not cached.primary_language_known or not cached.titles_known:
                print("  Looking up Letterboxd title and language…", file=sys.stderr)
                updated = backfill_letterboxd_details(cached, delay)
            if not cached.details_known:
                print("  Looking up synopsis and cinema websites…", file=sys.stderr)
                backfill_nonstop_details(cached, delay)
                updated = updated or cached.details_known
            elif cinema_sites_stale(cached):
                updated = refresh_cinema_sites(cached, delay) or updated
            if not cached.duration_known:
                print("  Looking up runtime…", file=sys.stderr)
                updated = backfill_runtime(cached, delay) or updated
            # Unfound titles stay in cache with no letterboxd_url; keep trying
            # to match so "already watched" can use the profile slug.
            if cached.match_current and cached.letterboxd_url:
                posters = poster_lookup or PosterLookup()
                updated = posters.fill(cached, cache, delay) or updated
                if updated:
                    cache_put(cache, cached)
                    save_cache(cache_path, cache)
                return _mark_new(cached, new_in_program)
            print("  Cross-checking director, runtime, and cast…", file=sys.stderr)
            try:
                page = parse_nonstop_movie_page(fetch(film.url))
            except urllib.error.URLError as exc:
                print(
                    f"  Nonstop page error: {exc.reason}",
                    file=sys.stderr,
                )
                page = None
            if page is None:
                cache_put(cache, cached)
                save_cache(cache_path, cache)
                return _mark_new(cached, new_in_program)
            _remember_runtime(cached, page)
            if cached.letterboxd_url:
                print(
                    "  Checking this Letterboxd link again…",
                    file=sys.stderr,
                )
                still_matches = stored_letterboxd_still_matches(
                    cached.letterboxd_url,
                    page.year,
                    page.director,
                    page.cast,
                    page.duration_minutes,
                    delay,
                )
                if still_matches is None:
                    print(
                        "  Letterboxd lookup failed; keeping the saved link.",
                        file=sys.stderr,
                    )
                    if updated or cached.duration_minutes is not None:
                        cache_put(cache, cached)
                        save_cache(cache_path, cache)
                    return _mark_new(cached, new_in_program)
                if still_matches:
                    print("  Saved Letterboxd link still matches.", file=sys.stderr)
                    cache_put(cache, cached)
                    save_cache(cache_path, cache)
                    return _mark_new(cached, new_in_program)
                print(
                    "  Saved Letterboxd link does not match; searching again…",
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
            return _mark_new(movie, new_in_program)

    page_title = page.title
    release_year = page.year
    nonstop_director = page.director

    try:
        movie = resolve_letterboxd(
            film.title,
            film.url,
            page_title,
            release_year,
            nonstop_director,
            delay=delay,
            nonstop_cast=page.cast,
            nonstop_duration=page.duration_minutes,
        )
    except RuntimeError as exc:
        print(f"  Letterboxd lookup failed: {exc}", file=sys.stderr)
        movie = Movie(
            title=film.title,
            nonstop_url=film.url,
            note=str(exc),
        )
    attach_program(movie, film)
    if page is not None:
        movie.synopsis = page.synopsis
        apply_cinema_sites(movie, page)
        movie.details_known = True
        _remember_runtime(movie, page)
    apply_nonstop_metadata(movie, release_year, nonstop_director)
    _mark_new(movie, new_in_program)
    cache_put(cache, movie)
    save_cache(cache_path, cache)
    return movie
