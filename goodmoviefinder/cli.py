"""Command-line entry point.

Reads the Wien program, resolves each film, loads the Letterboxd profile,
and leaves a ProgramPage running for the local server. Film matching lives
in lookup, the profile fetch in profile, and the live page in session.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
from pathlib import Path

from goodmoviefinder.cache import ensure_loved_film_credits, load_cache
from goodmoviefinder.config import (
    DEFAULT_CACHE,
    DEFAULT_KNOWN_LANGUAGES,
    DEFAULT_LETTERBOXD_USER,
    DEFAULT_PROGRAM_URL,
    DEFAULT_SERVE_PORT,
    GENERATED_ACCOUNT,
    GENERATED_HTML,
)
from goodmoviefinder.http import fetch
from goodmoviefinder.lookup import PosterLookup, lookup_program_film
from goodmoviefinder.models import Movie
from goodmoviefinder.nonstop import parse_program
from goodmoviefinder.profile import load_profile
from goodmoviefinder.program import movie_is_watchable, partition_program_movies, sort_movies
from goodmoviefinder import progress
from goodmoviefinder.serve import ServeHub, start_server, wait_server
from goodmoviefinder.session import ProgramPage, calendar_for_plan
from goodmoviefinder.viewer import (
    language_keys,
    load_calendars,
    load_following_user,
    load_signed_out,
    resolve_settings,
    save_viewer,
)


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
        default=None,
        help=(
            "Letterboxd username for watched/watchlist filters "
            f"(default: {DEFAULT_LETTERBOXD_USER}, or viewer.json)"
        ),
    )
    parser.add_argument(
        "--languages",
        default=None,
        help=(
            "Languages you can follow, comma-separated "
            f"(default: {', '.join(DEFAULT_KNOWN_LANGUAGES)})"
        ),
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
    parser.add_argument(
        "--refresh-streaming",
        action="store_true",
        help="Re-check where watchlist films are streaming in Austria",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Serve the page while it builds (default). Kept for compatibility.",
    )
    parser.add_argument(
        "--no-serve",
        action="store_true",
        help="Write the HTML files and exit without starting a local server",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_SERVE_PORT,
        help=f"Port for --serve (default: {DEFAULT_SERVE_PORT})",
    )
    args = parser.parse_args()
    try:
        letterboxd_user, known_languages, persist_settings = resolve_settings(
            args.letterboxd_user, args.languages
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    signed_out = load_signed_out() and args.letterboxd_user is None
    no_profile = args.no_letterboxd_profile or signed_out
    following_user = load_following_user(letterboxd_user) if not no_profile else letterboxd_user

    calendar_sources = load_calendars()

    hub = ServeHub()
    progress.bind(hub.progress)
    progress.adopt(hub.progress.generation)
    server = None
    keep_serving = not args.no_serve
    if keep_serving:
        server = start_server(hub, args.port)
        if server is None:
            print("Continuing without a local preview.", file=sys.stderr)

    def finish(code: int) -> int:
        if keep_serving and server is not None:
            wait_server(server)
        elif server is not None:
            server.shutdown()
        progress.bind(None)
        return code

    if no_profile:
        progress.skip("profile", "Signed out")
        progress.skip("credits", "Signed out")
        progress.skip("streaming", "Signed out")
        progress.skip("recommendations", "Signed out")
    if not calendar_sources:
        progress.skip("calendar", "No calendar connected")

    print(f"Fetching program: {args.program_url}", file=sys.stderr)
    with progress.step("program", detail="Reading the Nonstop program"):
        try:
            program_html = fetch(args.program_url)
        except urllib.error.URLError as exc:
            message = f"Failed to fetch program: {exc.reason}"
            print(message, file=sys.stderr)
            hub.progress.fail(message, "program")
            return finish(1)

        films = parse_program(program_html)
        if not films:
            message = "No films found on the program page."
            print(message, file=sys.stderr)
            hub.progress.fail(message, "program")
            return finish(1)
        if args.limit > 0:
            films = films[: args.limit]
        progress.tick(current=len(films), total=len(films), detail=f"{len(films)} films")

    cache = load_cache(args.cache)
    cached_program_urls = set(cache.get("entries", {}))
    first_program_cache_build = not cached_program_urls

    watched_ratings: dict[str, float | None] = {}
    watchlist_slugs: set[str] = set()
    watchlist_films: list[dict[str, object]] = []

    if not no_profile:
        with progress.step("profile", detail=f"@{letterboxd_user}"):
            try:
                watched_ratings, watchlist_slugs, watchlist_films = load_profile(
                    cache,
                    args.cache,
                    letterboxd_user,
                    args.delay,
                    refresh=args.refresh_profile,
                    no_cache=args.no_cache,
                )
            except RuntimeError as exc:
                print(f"{exc}; continuing without a Letterboxd profile.", file=sys.stderr)
                watched_ratings, watchlist_slugs, watchlist_films = {}, set(), []
            progress.tick(
                detail=(
                    f"{len(watched_ratings)} watched, {len(watchlist_slugs)} on watchlist"
                )
            )

    results: list[Movie] = []
    poster_lookup = PosterLookup()

    with progress.step("films", total=len(films)):
        for index, film in enumerate(films, start=1):
            print(f"[{index}/{len(films)}] {film.title}", file=sys.stderr)
            progress.tick(current=index, total=len(films), detail=film.title)
            results.append(
                lookup_program_film(
                    film,
                    cache,
                    args.cache,
                    args.delay,
                    no_cache=args.no_cache,
                    new_in_program=(
                        not first_program_cache_build and film.url not in cached_program_urls
                    ),
                    poster_lookup=poster_lookup,
                )
            )

    loved_credits: dict[str, dict] = {}
    if no_profile:
        grouped_watchlist: list[Movie] = []
        grouped_recommendations: list[Movie] = []
        grouped_watched: list[Movie] = []
        grouped_program = sort_movies(results)
    else:
        with progress.step("credits", detail="Films you rated 4.0 or higher"):
            loved_credits = ensure_loved_film_credits(
                cache,
                args.cache,
                watched_ratings,
                args.delay,
                results,
            )
        (
            grouped_watchlist,
            grouped_recommendations,
            grouped_watched,
            grouped_program,
        ) = partition_program_movies(
            results, watched_ratings, watchlist_slugs, loved_credits
        )
    screening_movies = [
        *grouped_watchlist,
        *grouped_recommendations,
        *grouped_watched,
        *grouped_program,
    ]
    if calendar_sources:
        with progress.step("calendar", total=len(calendar_sources)):
            busy_intervals, calendar_status, _ready = calendar_for_plan(
                calendar_sources,
                screening_movies,
            )
    else:
        busy_intervals, calendar_status, _ready = calendar_for_plan(
            calendar_sources,
            screening_movies,
        )
    page = ProgramPage(
        results,
        cache,
        args.cache,
        args.delay,
        letterboxd_user,
        known_languages,
        no_profile=no_profile,
        signed_out=signed_out,
        port=args.port,
        busy_intervals=busy_intervals,
        calendar_status=calendar_status,
        calendar_sources=calendar_sources,
        following_user=following_user,
        program_url=args.program_url,
        no_cache=args.no_cache,
    )
    page.watched_ratings = watched_ratings
    page.watchlist_slugs = watchlist_slugs
    page.watchlist_films = watchlist_films
    page.refresh_streaming = args.refresh_streaming
    page.refresh_neighbors = args.refresh_profile
    page.ignore_recommendation_cache = args.no_cache or args.refresh_profile
    page.loved_credits = loved_credits
    if no_profile:
        page.stream_films = None
    with progress.step("pages", detail="Writing the Nonstop program"):
        watchlist_movies, recommendation_movies, watched_movies, program_movies = (
            page.write_program()
        )
        if no_profile:
            page._write_account_page()
    page_epoch = page._epoch
    hub.attach(page)
    hub.progress.mark_program()
    print(f"Wrote {GENERATED_HTML}", file=sys.stderr)
    if not no_profile:
        with progress.step(
            "streaming",
            total=max(len(watchlist_films), 1),
            detail="Where watchlist films stream",
        ):
            streamed = resolve_watchlist_streaming(
                watchlist_films,
                args.delay,
                refresh=args.refresh_streaming,
            )
        cached_recommendations = False
        with page._lock:
            still_current = page._epoch == page_epoch
            if still_current:
                page.stream_films = streamed
                cached_recommendations = page.load_cached_recommendations()
                if cached_recommendations:
                    page._write_account_page()
        if still_current and cached_recommendations:
            progress.skip("recommendations", "Using saved recommendations")
        elif still_current:
            with progress.step("recommendations", detail="Similar members and For you"):
                page.fill_recommendations(force=True)
                with page._lock:
                    if page._epoch == page_epoch:
                        page.write_program()
                        page._write_account_page()
    with page._lock:
        if page._epoch == page_epoch:
            hub.progress.ready()
    if persist_settings:
        save_viewer(
            page.letterboxd_user,
            page.languages,
            signed_out=signed_out,
            following_user=page.following_user,
        )
    print(f"Wrote {GENERATED_ACCOUNT}", file=sys.stderr)

    known = language_keys(page.languages)
    visible_program = [
        movie for movie in program_movies if movie_is_watchable(movie, known)
    ]
    visible_recommendations = [
        movie for movie in recommendation_movies if movie_is_watchable(movie, known)
    ]
    hidden_for_language = len(program_movies) - len(visible_program)
    language_label = ", ".join(page.languages) if page.languages else "English subtitles only"
    rated = sum(1 for movie in results if movie.rating is not None)
    print(
        f"Languages ({language_label}): hiding {hidden_for_language} from the main program.",
        file=sys.stderr,
    )
    print(
        f"Done: {len(visible_program)} in the full program (watchlist and recommendations included), "
        f"{len(watchlist_movies)} watchlist, "
        f"{len(visible_recommendations)} recommended, "
        f"{len(page.see_first) + len(page.like_you) + len(page.step_outside)} on For you, "
        f"{len(watched_movies)} already watched, "
        f"{rated}/{len(results)} with Letterboxd ratings.",
        file=sys.stderr,
    )
    if keep_serving and server is None:
        server = start_server(hub, args.port)
        if server is None:
            return 1
    return finish(0)
