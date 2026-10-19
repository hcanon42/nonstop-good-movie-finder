"""Command-line entry point."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import threading
import urllib.error
from datetime import date, datetime, timedelta
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
    save_watchlist_films,
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
    GENERATED_ACCOUNT,
    GENERATED_HTML,
)
from goodmoviefinder.google_calendar import fetch_google_busy, save_google_events
from goodmoviefinder.account import format_account_html, format_taste_panel_html
from goodmoviefinder.html import format_html
from goodmoviefinder.http import fetch
from goodmoviefinder.letterboxd import (
    apply_nonstop_metadata,
    fetch_letterboxd_watched_ratings,
    fetch_letterboxd_watchlist,
    resolve_letterboxd,
    stored_letterboxd_still_matches,
)
from goodmoviefinder.models import Movie, NonstopFilmPage
from goodmoviefinder.nonstop import parse_nonstop_movie_page, parse_program
from goodmoviefinder.neighbors import ensure_neighbors
from goodmoviefinder.program import (
    attach_program,
    letterboxd_slug_from_url,
    counts_as_viewer_language,
    movie_is_watchable,
    partition_program_movies,
    sort_movies,
)
from goodmoviefinder import progress
from goodmoviefinder.serve import ServeHub, start_server, wait_server
from goodmoviefinder.streaming import resolve_watchlist_streaming
from goodmoviefinder.taste import build_for_you
from goodmoviefinder.viewer import (
    canonical_languages,
    language_choices,
    language_keys,
    load_calendars,
    load_following_user,
    load_signed_out,
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
    for index, source in enumerate(ordered, start=1):
        label = "Apple Calendar" if source == "apple" else "Google Calendar"
        print(f"Reading {label}…", file=sys.stderr)
        progress.tick(current=index, total=len(ordered), detail=f"Reading {label}")
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
        signed_out: bool = False,
        following_user: str | None = None,
    ) -> None:
        self.results = results
        self.cache = cache
        self.cache_path = cache_path
        self.delay = delay
        self.letterboxd_user = user
        self.following_user = following_user or user
        self.languages = list(languages)
        self.no_profile = no_profile
        self.signed_out = signed_out
        self.port = port
        self.busy_intervals = busy_intervals
        self.calendar_status = calendar_status
        self.calendar_sources = list(calendar_sources)
        self.watched_ratings: dict[str, float | None] = {}
        self.watchlist_slugs: set[str] = set()
        self.watchlist_films: list[dict[str, object]] = []
        self.stream_films: list[dict[str, object]] | None = None
        self.refresh_streaming = False
        self.loved_credits: dict[str, dict] = {}
        self.refresh_neighbors = False
        self.see_first: list[Movie] = []
        self.like_you: list[Movie] = []
        self.step_outside: list[Movie] = []
        self.taste_paragraph = ""
        self.taste_note: str | None = None
        self._rec_running = False
        self._rec_error: str | None = None
        self._lock = threading.Lock()

    @property
    def html_path(self) -> Path:
        return GENERATED_HTML

    def viewer_payload(self) -> dict[str, object]:
        return {
            "letterboxdUser": self.letterboxd_user,
            "followingUser": self.following_user,
            "languages": self.languages,
        }

    def following_list(self) -> dict[str, object]:
        from goodmoviefinder.friends import ensure_following_list, present_following_list

        if self.no_profile:
            raise ValueError("Sign in to switch profiles.")
        members = ensure_following_list(
            self.cache_path.parent / "following.json",
            self.following_user,
            self.delay,
        )
        return present_following_list(
            members,
            root_user=self.following_user,
            active_user=self.letterboxd_user,
        )

    def friend_ratings(self, slug: str) -> dict[str, object]:
        from goodmoviefinder.friends import ensure_friend_ratings, present_friend_ratings

        if self.no_profile:
            raise ValueError("Sign in to see friend ratings.")

        friends, watchlist = ensure_friend_ratings(
            self.cache_path.parent / "friends.json",
            self.letterboxd_user,
            slug,
            self.delay,
        )
        return present_friend_ratings(friends, watchlist)

    def _taste_panel_parts(self) -> tuple[str, str]:
        return format_taste_panel_html(
            see_first=self.see_first,
            like_you=self.like_you,
            step_outside=self.step_outside,
            paragraph=self.taste_paragraph,
            note=self.taste_note or "",
        )

    def recommendations_status(self) -> dict[str, object]:
        with self._lock:
            if self._rec_running:
                return {"status": "running"}
            payload: dict[str, object] = {"status": "idle"}
            if not self.no_profile:
                panel, pills = self._taste_panel_parts()
                payload["panel"] = panel
                payload["pills"] = pills
            if self._rec_error:
                payload["error"] = self._rec_error
                self._rec_error = None
            return payload

    def start_recommendations_refresh(self) -> dict[str, object]:
        with self._lock:
            if self.no_profile:
                raise ValueError("Sign in to refresh recommendations.")
            if self._rec_running:
                return {"status": "running"}
            self._rec_running = True
        threading.Thread(target=self._recommendations_refresh_worker, daemon=True).start()
        return {"status": "running"}

    def _recommendations_refresh_worker(self) -> None:
        try:
            with self._lock:
                user = self.letterboxd_user
                cache = self.cache
                cache_path = self.cache_path
                delay = self.delay
                languages = language_keys(self.languages)
                results = self.results
            watched, watchlist, _films = load_profile(
                cache,
                cache_path,
                user,
                delay,
                refresh=True,
                no_cache=False,
            )
            neighbors, _aborted = ensure_neighbors(
                cache_path,
                user,
                watched,
                delay,
                refresh=True,
            )
            taste = build_for_you(
                cache,
                cache_path,
                watched,
                neighbors,
                watchlist,
                known_languages=languages,
                user=user,
                delay=delay,
            )
            with self._lock:
                self.watched_ratings = watched
                self.watchlist_slugs = watchlist
                self.see_first = taste.see_first
                self.like_you = taste.like_you
                self.step_outside = taste.step_outside
                self.taste_paragraph = taste.paragraph
                self.taste_note = taste.note
                self.loved_credits = ensure_loved_film_credits(
                    cache,
                    cache_path,
                    watched,
                    delay,
                    results,
                )
                self._write_account_page()
        except Exception as exc:
            print(f"[serve] recommendations refresh: {exc}", file=sys.stderr)
            with self._lock:
                self._rec_error = str(exc)
        finally:
            with self._lock:
                self._rec_running = False

    def _write_account_page(self) -> None:
        found = [
            movie.primary_language
            for movie in self.results
            if counts_as_viewer_language(movie.primary_language)
        ]
        choices = language_choices(found, self.languages)
        self.languages = canonical_languages(self.languages, choices)
        account = format_account_html(
            letterboxd_user=self.letterboxd_user,
            following_user=self.following_user,
            signed_in=not self.no_profile,
            stream_films=self.stream_films,
            nonstop_next=self._nonstop_next(),
            see_first=self.see_first,
            like_you=self.like_you,
            step_outside=self.step_outside,
            paragraph=self.taste_paragraph,
            note=self.taste_note or "",
            known_languages=self.languages,
            language_options=choices,
        )
        GENERATED_ACCOUNT.parent.mkdir(parents=True, exist_ok=True)
        GENERATED_ACCOUNT.write_text(account, encoding="utf-8")

    def _nonstop_next(self) -> dict[str, str]:
        """Letterboxd slug -> date of the next Nonstop screening."""
        today = date.today().isoformat()
        upcoming: dict[str, str] = {}
        for movie in self.results:
            slug = letterboxd_slug_from_url(movie.letterboxd_url) if movie.letterboxd_url else None
            if not slug:
                continue
            days = [
                screening.weekday
                for screening in movie.screenings
                if _DATE_RE.fullmatch(screening.weekday or "") and screening.weekday >= today
            ]
            if days:
                upcoming[slug] = min(days)
        return upcoming

    def _program_groups(self) -> tuple[list[Movie], list[Movie], list[Movie], list[Movie]]:
        if self.no_profile:
            self.see_first = []
            self.like_you = []
            self.step_outside = []
            self.taste_paragraph = ""
            self.taste_note = None
            return [], [], [], sort_movies(self.results)
        return partition_program_movies(
            self.results,
            self.watched_ratings,
            self.watchlist_slugs,
            self.loved_credits,
        )

    def write_program(self) -> tuple[list[Movie], list[Movie], list[Movie], list[Movie]]:
        """Write the Nonstop page. Letterboxd recommendations are filled in separately."""
        watchlist, recommendations, watched, program = self._program_groups()
        found = [
            movie.primary_language
            for movie in self.results
            if counts_as_viewer_language(movie.primary_language)
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
            signed_in=not self.no_profile,
            following_user=self.following_user,
        )
        GENERATED_HTML.parent.mkdir(parents=True, exist_ok=True)
        GENERATED_HTML.write_text(output, encoding="utf-8")
        return watchlist, recommendations, watched, program

    def fill_recommendations(self) -> None:
        if self.no_profile:
            return
        neighbors, _aborted = ensure_neighbors(
            self.cache_path,
            self.letterboxd_user,
            self.watched_ratings,
            self.delay,
            refresh=self.refresh_neighbors,
        )
        self.refresh_neighbors = False
        taste = build_for_you(
            self.cache,
            self.cache_path,
            self.watched_ratings,
            neighbors,
            self.watchlist_slugs,
            known_languages=language_keys(self.languages),
            user=self.letterboxd_user,
            delay=self.delay,
        )
        self.see_first = taste.see_first
        self.like_you = taste.like_you
        self.step_outside = taste.step_outside
        self.taste_paragraph = taste.paragraph
        self.taste_note = taste.note

    def write(self) -> tuple[list[Movie], list[Movie], list[Movie], list[Movie]]:
        groups = self.write_program()
        self.fill_recommendations()
        self._write_account_page()
        return groups

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
            if payload.get("signOut") is True and not self.no_profile:
                self.no_profile = True
                self.signed_out = True
                self.following_user = self.letterboxd_user
                self.watched_ratings = {}
                self.watchlist_slugs = set()
                self.watchlist_films = []
                self.stream_films = None
                self.loved_credits = {}
                reload = True
            if payload.get("letterboxdUser") is not None:
                user = normalize_letterboxd_user(str(payload["letterboxdUser"]))
                changed = user.casefold() != self.letterboxd_user.casefold()
                root_changed = user.casefold() != self.following_user.casefold()
                if changed or self.no_profile:
                    watched, watchlist, films = load_profile(
                        self.cache,
                        self.cache_path,
                        user,
                        self.delay,
                        refresh=changed,
                        no_cache=False,
                    )
                    self.watched_ratings = watched
                    self.watchlist_slugs = watchlist
                    self.watchlist_films = films
                    self.stream_films = resolve_watchlist_streaming(
                        films,
                        self.delay,
                        refresh=self.refresh_streaming,
                    )
                    self.loved_credits = ensure_loved_film_credits(
                        self.cache,
                        self.cache_path,
                        watched,
                        self.delay,
                        self.results,
                    )
                    self.letterboxd_user = user
                    self.refresh_neighbors = True
                    self.no_profile = False
                    self.signed_out = False
                self.following_user = user
                if changed or self.no_profile or root_changed:
                    reload = True
            if reload:
                self.write()
            save_viewer(
                self.letterboxd_user,
                self.languages,
                signed_out=self.signed_out,
                following_user=self.following_user,
            )
            return reload

    def save_plan(self, payload: dict) -> dict[str, object]:
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
            try:
                calendar = self._reload_calendar_locked()
            except Exception as exc:
                calendar = {
                    "intervals": self.busy_intervals,
                    "status": self.calendar_status,
                }
                message = f"{message} The calendar could not be refreshed: {exc}"
            return {"message": message, **calendar}

    def refresh_calendar(self) -> dict[str, object]:
        with self._lock:
            sources = self.calendar_sources or load_calendars()
            if not sources:
                raise ValueError(
                    "No calendar is connected. Run with --calendar apple or --calendar google."
                )
            return self._reload_calendar_locked()

    def _reload_calendar_locked(self) -> dict[str, object]:
        sources = self.calendar_sources or load_calendars()
        intervals, status = _calendar_for_plan(sources, self.results)
        self.busy_intervals = intervals
        self.calendar_status = status
        self.write()
        return {"intervals": intervals, "status": status}


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

    signed_out = load_signed_out() and args.letterboxd_user is None
    no_profile = args.no_letterboxd_profile or signed_out
    following_user = load_following_user(letterboxd_user) if not no_profile else letterboxd_user

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

    hub = ServeHub()
    progress.bind(hub.progress)
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

    with progress.step("films", total=len(films)):
        for index, film in enumerate(films, start=1):
            print(f"[{index}/{len(films)}] {film.title}", file=sys.stderr)
            progress.tick(current=index, total=len(films), detail=film.title)

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
                    # Unfound titles stay in cache with no letterboxd_url; keep trying
                    # to match so "already watched" can use the profile slug.
                    if cached.match_current and cached.letterboxd_url:
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

            try:
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
                movie.cinema_sites = page.cinema_sites
                movie.details_known = True
                _remember_runtime(movie, page)
            apply_nonstop_metadata(movie, release_year, nonstop_director)
            if not first_program_cache_build and film.url not in cached_program_urls:
                movie.new_in_program = True
            cache_put(cache, movie)
            save_cache(args.cache, cache)
            results.append(movie)

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
    if calendar_sources:
        with progress.step("calendar", total=len(calendar_sources)):
            busy_intervals, calendar_status = _calendar_for_plan(
                calendar_sources,
                [
                    *grouped_watchlist,
                    *grouped_recommendations,
                    *grouped_watched,
                    *grouped_program,
                ],
            )
    else:
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
        no_profile=no_profile,
        signed_out=signed_out,
        port=args.port,
        busy_intervals=busy_intervals,
        calendar_status=calendar_status,
        calendar_sources=calendar_sources,
        following_user=following_user,
    )
    page.watched_ratings = watched_ratings
    page.watchlist_slugs = watchlist_slugs
    page.watchlist_films = watchlist_films
    page.refresh_streaming = args.refresh_streaming
    page.refresh_neighbors = args.refresh_profile
    page.loved_credits = loved_credits
    if no_profile:
        page.stream_films = None
    with progress.step("pages", detail="Writing the Nonstop program"):
        watchlist_movies, recommendation_movies, watched_movies, program_movies = (
            page.write_program()
        )
        if no_profile:
            page._write_account_page()
    hub.attach(page)
    hub.progress.mark_program()
    print(f"Wrote {GENERATED_HTML}", file=sys.stderr)
    if not no_profile:
        with progress.step(
            "streaming",
            total=max(len(watchlist_films), 1),
            detail="Where watchlist films stream",
        ):
            page.stream_films = resolve_watchlist_streaming(
                watchlist_films,
                args.delay,
                refresh=args.refresh_streaming,
            )
        with progress.step("recommendations", detail="Similar members and For you"):
            page.fill_recommendations()
            page._write_account_page()
    hub.progress.ready()
    if persist_settings or persist_calendars:
        save_viewer(
            page.letterboxd_user,
            page.languages,
            calendar_sources if persist_calendars else None,
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
        server = start_server(hub, args.port, open_browser=True)
        if server is None:
            return 1
    return finish(0)
