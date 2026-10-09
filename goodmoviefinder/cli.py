"""Command-line entry point."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import threading
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

from goodmoviefinder.apple_calendar import fetch_apple_busy, save_apple_events
from goodmoviefinder.cache import (
    backfill_letterboxd_details,
    backfill_nonstop_details,
    backfill_runtime,
    cache_get,
    cache_put,
    ensure_loved_film_credits,
    load_cache,
    load_profile_cache,
    save_cache,
    save_profile_cache,
)
from goodmoviefinder.calendar_busy import (
    CalendarError,
    active_intervals,
    fetch_window,
    interval_payload,
    latest_screening_date,
    status_sentence,
    VIENNA,
)
from goodmoviefinder.config import (
    DEFAULT_CACHE,
    DEFAULT_KNOWN_LANGUAGES,
    DEFAULT_LETTERBOXD_USER,
    DEFAULT_PROGRAM_URL,
    DEFAULT_SERVE_PORT,
    GENERATED_HTML,
)
from goodmoviefinder.google_calendar import fetch_google_busy, save_google_events
from goodmoviefinder.html import format_html
from goodmoviefinder.http import fetch
from goodmoviefinder.letterboxd import (
    apply_nonstop_metadata,
    fetch_letterboxd_profile_slugs,
    fetch_letterboxd_watched_ratings,
    resolve_letterboxd,
    stored_letterboxd_still_matches,
)
from goodmoviefinder.models import Movie, NonstopFilmPage
from goodmoviefinder.nonstop import parse_nonstop_movie_page, parse_program
from goodmoviefinder.program import (
    attach_program,
    movie_is_watchable,
    partition_program_movies,
    sort_movies,
)
from goodmoviefinder.serve import serve
from goodmoviefinder.viewer import (
    canonical_languages,
    language_choices,
    language_keys,
    load_calendars,
    normalize_letterboxd_user,
    resolve_settings,
    save_viewer,
)

def _calendar_for_plan(
    sources: list[str],
    movies: list[Movie],
) -> tuple[list[dict[str, str]], str]:
    ordered = list(dict.fromkeys(sources))
    if not ordered:
        return [], ""
    window = fetch_window(movies)
    through = latest_screening_date(movies)
    if window is None or through is None:
        return [], "No screenings to compare with your calendar."
    start, end = window
    found = []
    ready: list[str] = []
    failed: list[str] = []
    for source in ordered:
        label = "Apple Calendar" if source == "apple" else "Google Calendar"
        print(f"Reading {label}…", file=sys.stderr)
        try:
            if source == "apple":
                intervals = fetch_apple_busy(start, end)
            else:
                intervals = fetch_google_busy(start, end)
        except CalendarError as exc:
            print(f"{label}: {exc}", file=sys.stderr)
            failed.append(source)
            continue
        found.extend(intervals)
        ready.append(source)
        print(f"{label}: {len(intervals)} busy block(s).", file=sys.stderr)
    merged = active_intervals(found)
    if ready:
        print(
            f"Calendar: {len(merged)} busy block(s) through {through.isoformat()}.",
            file=sys.stderr,
        )
    return interval_payload(merged), status_sentence(ready, failed, through)


_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME_RE = re.compile(r"\d{2}:\d{2}")


def _plan_event(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ValueError("A film in the plan is invalid.")
    title = str(raw.get("title") or "").strip()
    if not title or len(title) > 180:
        raise ValueError("Each film needs a title.")
    date_raw = str(raw.get("date") or "")
    time_raw = str(raw.get("time") or "")
    if not _DATE_RE.fullmatch(date_raw) or not _TIME_RE.fullmatch(time_raw):
        raise ValueError("A screening time is invalid.")
    minutes = raw.get("minutes")
    if isinstance(minutes, float) and minutes.is_integer():
        minutes = int(minutes)
    if not isinstance(minutes, int) or minutes <= 0 or minutes > 12 * 60:
        raise ValueError("A screening length is invalid.")
    start = datetime.fromisoformat(f"{date_raw}T{time_raw}").replace(tzinfo=VIENNA)
    location = str(raw.get("location") or "").strip()[:180]
    url = str(raw.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        url = ""
    key = hashlib.sha256(f"{title}|{start.isoformat()}".encode()).hexdigest()[:16]
    return {
        "title": title,
        "start": start,
        "end": start + timedelta(minutes=minutes),
        "location": location,
        "url": url,
        "key": key,
    }


def _saved_sentence(added: int, skipped: int, name: str) -> str:
    if added == 0 and skipped:
        return f"Those films are already on {name}."
    films = "film" if added == 1 else "films"
    sentence = f"Added {added} {films} to {name}."
    if skipped:
        already = "1 was" if skipped == 1 else f"{skipped} were"
        sentence += f" {already} already there."
    return sentence


def _remember_runtime(movie: Movie, page: NonstopFilmPage | None) -> None:
    """Keep a Nonstop runtime when Letterboxd did not already provide one."""
    if page is None or movie.duration_minutes:
        return
    if isinstance(page.duration_minutes, int) and page.duration_minutes > 0:
        movie.duration_minutes = page.duration_minutes
        movie.duration_known = True


class ProgramPage:
    """Rendered program, plus enough state to load another Letterboxd profile."""

    def __init__(
        self,
        results: list[Movie],
        cache: dict,
        cache_path: Path,
        delay: float,
        user: str,
        languages: list[str],
        *,
        no_profile: bool,
        port: int,
        busy_intervals: list[dict[str, str]],
        calendar_status: str,
        calendar_sources: list[str],
    ) -> None:
        self.results = results
        self.cache = cache
        self.cache_path = cache_path
        self.delay = delay
        self.letterboxd_user = user
        self.languages = list(languages)
        self.no_profile = no_profile
        self.port = port
        self.busy_intervals = busy_intervals
        self.calendar_status = calendar_status
        self.calendar_sources = list(calendar_sources)
        self.watched_ratings: dict[str, float | None] = {}
        self.watchlist_slugs: set[str] = set()
        self.loved_credits: dict[str, dict] = {}
        self._lock = threading.Lock()

    @property
    def html_path(self) -> Path:
        return GENERATED_HTML

    def viewer_payload(self) -> dict[str, object]:
        return {
            "letterboxdUser": self.letterboxd_user,
            "languages": self.languages,
        }

    def write(self) -> tuple[list[Movie], list[Movie], list[Movie], list[Movie]]:
        if self.no_profile:
            watchlist: list[Movie] = []
            recommendations: list[Movie] = []
            watched: list[Movie] = []
            program = sort_movies(self.results)
        else:
            watchlist, recommendations, watched, program = partition_program_movies(
                self.results,
                self.watched_ratings,
                self.watchlist_slugs,
                self.loved_credits,
            )
        found = [
            movie.primary_language for movie in self.results if movie.primary_language
        ]
        choices = language_choices(found, self.languages)
        self.languages = canonical_languages(self.languages, choices)
        output = format_html(
            watchlist,
            recommendations,
            watched,
            program,
            letterboxd_user=self.letterboxd_user,
            known_languages=self.languages,
            serve_port=self.port,
            busy_intervals=self.busy_intervals,
            calendar_status=self.calendar_status,
        )
        GENERATED_HTML.parent.mkdir(parents=True, exist_ok=True)
        GENERATED_HTML.write_text(output, encoding="utf-8")
        return watchlist, recommendations, watched, program

    def apply_viewer(self, payload: dict) -> bool:
        with self._lock:
            reload = False
            if "languages" in payload:
                raw = payload["languages"]
                if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
                    raise ValueError("Languages must be a list of names.")
                languages: list[str] = []
                for item in raw:
                    name = item.strip()
                    if not name:
                        continue
                    if len(name) > 40 or len(languages) >= 40:
                        raise ValueError("That language list is too long.")
                    languages.append(name)
                self.languages = languages
            if payload.get("letterboxdUser") is not None:
                user = normalize_letterboxd_user(str(payload["letterboxdUser"]))
                if user.casefold() != self.letterboxd_user.casefold():
                    watched, watchlist = load_profile(
                        self.cache,
                        self.cache_path,
                        user,
                        self.delay,
                        refresh=True,
                        no_cache=False,
                    )
                    self.watched_ratings = watched
                    self.watchlist_slugs = watchlist
                    self.loved_credits = ensure_loved_film_credits(
                        self.cache,
                        self.cache_path,
                        watched,
                        self.delay,
                        self.results,
                    )
                    self.letterboxd_user = user
                    self.no_profile = False
                    reload = True
            if reload:
                self.write()
            save_viewer(self.letterboxd_user, self.languages)
            return reload

    def save_plan(self, payload: dict) -> str:
        with self._lock:
            raw = payload.get("events")
            if not isinstance(raw, list) or not raw:
                raise ValueError("Choose a plan before adding it to your calendar.")
            if len(raw) > 12:
                raise ValueError("A plan holds up to 12 films.")
            events = [_plan_event(item) for item in raw]
            sources = self.calendar_sources or load_calendars()
            if not sources:
                raise ValueError(
                    "No calendar is connected. Run with --calendar apple or --calendar google."
                )
            sentences: list[str] = []
            problems: list[str] = []
            for source in sources:
                label = "Apple Calendar" if source == "apple" else "Google Calendar"
                try:
                    if source == "apple":
                        added, skipped, name = save_apple_events(events)
                        sentences.append(_saved_sentence(added, skipped, name))
                    else:
                        added, skipped = save_google_events(events)
                        sentences.append(_saved_sentence(added, skipped, "Google Calendar"))
                except CalendarError as exc:
                    problems.append(f"{label}: {exc}")
            if not sentences:
                raise CalendarError(" ".join(problems) or "Could not add the plan.")
            message = " ".join(sentences)
            if problems:
                message = f"{message} {' '.join(problems)}"
            return message


def load_profile(
    cache: dict,
    cache_path: Path,
    user: str,
    delay: float,
    *,
    refresh: bool,
    no_cache: bool,
) -> tuple[dict[str, float | None], set[str]]:
    profile = None
    if not refresh and not no_cache:
        profile = load_profile_cache(cache, user)
    if profile is not None:
        watched_ratings, watchlist_slugs = profile
        print(
            f"Using cached profile: {len(watched_ratings)} watched, "
            f"{len(watchlist_slugs)} on watchlist.",
            file=sys.stderr,
        )
        return watched_ratings, watchlist_slugs

    print(f"Fetching Letterboxd watched films for @{user}…", file=sys.stderr)
    try:
        watched_ratings = fetch_letterboxd_watched_ratings(user, delay=delay)
        print(f"Fetching Letterboxd watchlist for @{user}…", file=sys.stderr)
        watchlist_slugs = fetch_letterboxd_profile_slugs(user, "watchlist", delay=delay)
    except RuntimeError as exc:
        stale = load_profile_cache(cache, user, allow_stale=True)
        if stale is None:
            raise
        watched_ratings, watchlist_slugs = stale
        print(
            f"{exc}; using the saved profile "
            f"({len(watched_ratings)} watched, {len(watchlist_slugs)} on watchlist).",
            file=sys.stderr,
        )
        return watched_ratings, watchlist_slugs

    save_profile_cache(cache, user, watched_ratings, watchlist_slugs)
    save_cache(cache_path, cache)
    rated = sum(1 for value in watched_ratings.values() if value is not None)
    print(
        f"Profile: {len(watched_ratings)} watched ({rated} rated), "
        f"{len(watchlist_slugs)} on watchlist.",
        file=sys.stderr,
    )
    return watched_ratings, watchlist_slugs


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
        "--serve",
        action="store_true",
        help="Keep serving the page so Settings can load another Letterboxd profile",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_SERVE_PORT,
        help=f"Port for --serve (default: {DEFAULT_SERVE_PORT})",
    )
    parser.add_argument(
        "--calendar",
        action="append",
        choices=("apple", "google"),
        default=None,
        metavar="{apple,google}",
        help="Skip plan showtimes that overlap this calendar (repeat to use both)",
    )
    parser.add_argument(
        "--no-calendar",
        action="store_true",
        help="Stop using a saved calendar when planning",
    )
    args = parser.parse_args()
    try:
        letterboxd_user, known_languages, persist_settings = resolve_settings(
            args.letterboxd_user, args.languages
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.no_calendar:
        calendar_sources: list[str] = []
        persist_calendars = True
    elif args.calendar:
        calendar_sources = list(dict.fromkeys(args.calendar))
        persist_calendars = True
    else:
        calendar_sources = load_calendars()
        persist_calendars = False
        if calendar_sources:
            print(
                "Using saved calendar: " + ", ".join(calendar_sources),
                file=sys.stderr,
            )

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
        try:
            watched_ratings, watchlist_slugs = load_profile(
                cache,
                args.cache,
                letterboxd_user,
                args.delay,
                refresh=args.refresh_profile,
                no_cache=args.no_cache,
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

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
                if not cached.duration_known:
                    print("  Looking up runtime…", file=sys.stderr)
                    updated = backfill_runtime(cached, args.delay) or updated
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
                if page is None:
                    cache_put(cache, cached)
                    save_cache(args.cache, cache)
                    results.append(cached)
                    continue
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
                        args.delay,
                    )
                    if still_matches is None:
                        print(
                            "  Letterboxd lookup failed; keeping the saved link.",
                            file=sys.stderr,
                        )
                        if cached.duration_minutes is not None:
                            cache_put(cache, cached)
                            save_cache(args.cache, cache)
                        results.append(cached)
                        continue
                    if still_matches:
                        print("  Saved Letterboxd link still matches.", file=sys.stderr)
                        cache_put(cache, cached)
                        save_cache(args.cache, cache)
                        results.append(cached)
                        continue
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
            _remember_runtime(movie, page)
        apply_nonstop_metadata(movie, release_year, nonstop_director)
        cache_put(cache, movie)
        save_cache(args.cache, cache)
        results.append(movie)

    loved_credits: dict[str, dict] = {}
    if args.no_letterboxd_profile:
        grouped_watchlist: list[Movie] = []
        grouped_recommendations: list[Movie] = []
        grouped_watched: list[Movie] = []
        grouped_program = sort_movies(results)
    else:
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
    busy_intervals, calendar_status = _calendar_for_plan(
        calendar_sources,
        [
            *grouped_watchlist,
            *grouped_recommendations,
            *grouped_watched,
            *grouped_program,
        ],
    )
    page = ProgramPage(
        results,
        cache,
        args.cache,
        args.delay,
        letterboxd_user,
        known_languages,
        no_profile=args.no_letterboxd_profile,
        port=args.port,
        busy_intervals=busy_intervals,
        calendar_status=calendar_status,
        calendar_sources=calendar_sources,
    )
    page.watched_ratings = watched_ratings
    page.watchlist_slugs = watchlist_slugs
    page.loved_credits = loved_credits
    watchlist_movies, recommendation_movies, watched_movies, program_movies = page.write()
    if persist_settings or persist_calendars:
        save_viewer(
            page.letterboxd_user,
            page.languages,
            calendar_sources if persist_calendars else None,
        )
    print(f"Wrote {GENERATED_HTML}", file=sys.stderr)

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
        f"{len(watched_movies)} already watched, "
        f"{rated}/{len(results)} with Letterboxd ratings.",
        file=sys.stderr,
    )
    if args.serve:
        return serve(page, args.port)
    return 0
