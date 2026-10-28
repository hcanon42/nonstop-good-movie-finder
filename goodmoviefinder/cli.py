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
    fill_letterboxd_poster,
    cache_put,
    ensure_loved_film_credits,
    load_cache,
    load_profile_cache,
    load_recommendations_cache,
    recommendations_updated_label,
    save_cache,
    save_profile_cache,
    save_recommendations_cache,
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
    LetterboxdRefused,
    apply_manual_letterboxd_match,
    apply_nonstop_metadata,
    fetch_letterboxd_watched_ratings,
    fetch_letterboxd_watchlist,
    letterboxd_autocomplete_films,
    letterboxd_poster_url,
    normalize_letterboxd_film_slug,
    resolve_letterboxd,
    stored_letterboxd_still_matches,
)
from goodmoviefinder.models import Movie, NonstopFilmPage, ProgramFilm
from goodmoviefinder.nonstop import parse_nonstop_movie_page, parse_program
from goodmoviefinder.neighbors import ensure_neighbors
from goodmoviefinder.program import (
    apply_known_letterboxd_posters,
    attach_program,
    film_title,
    letterboxd_slug_from_url,
    counts_as_viewer_language,
    movie_is_watchable,
    partition_program_movies,
    sort_movies,
)
from goodmoviefinder import progress
from goodmoviefinder.serve import ServeHub, start_server, wait_server
from goodmoviefinder.streaming import resolve_watchlist_streaming
from goodmoviefinder.taste import TasteView, build_for_you
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
) -> tuple[list[dict[str, str]], str, list[str]]:
    ordered = list(dict.fromkeys(sources))
    if not ordered:
        return [], "", []
    window = fetch_window(movies)
    through = latest_screening_date(movies)
    if window is None or through is None:
        return [], "No screenings to compare with your calendar.", []
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
    return interval_payload(merged), status_sentence(ready, failed, through), ready


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


def _mark_new(movie: Movie, new_in_program: bool) -> Movie:
    if new_in_program:
        movie.new_in_program = True
    return movie


def _program_change_message(
    added: list[str],
    removed: list[str],
    *,
    screenings_changed: bool,
) -> str:
    """Status line after a Wien program check."""

    def _names(titles: list[str]) -> str:
        shown = titles[:8]
        extra = f" and {len(titles) - len(shown)} more" if len(titles) > len(shown) else ""
        return ", ".join(shown) + extra

    parts: list[str] = []
    if added:
        label = "film" if len(added) == 1 else "films"
        parts.append(f"{len(added)} new {label}: {_names(added)}")
    if removed:
        label = "film" if len(removed) == 1 else "films"
        parts.append(f"Removed {len(removed)} {label}: {_names(removed)}")
    if not parts:
        return "Showtimes updated." if screenings_changed else "The Wien program is unchanged."
    if screenings_changed:
        parts.append("Showtimes updated")
    return ". ".join(parts) + "."


class _PosterLookup:
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


def lookup_program_film(
    film: ProgramFilm,
    cache: dict,
    cache_path: Path,
    delay: float,
    *,
    no_cache: bool,
    new_in_program: bool,
    poster_lookup: _PosterLookup | None = None,
) -> Movie:
    """Resolve one program title, reusing a cached Letterboxd match when it still fits."""
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
            if not cached.duration_known:
                print("  Looking up runtime…", file=sys.stderr)
                updated = backfill_runtime(cached, delay) or updated
            # Unfound titles stay in cache with no letterboxd_url; keep trying
            # to match so "already watched" can use the profile slug.
            if cached.match_current and cached.letterboxd_url:
                posters = poster_lookup or _PosterLookup()
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
                    if cached.duration_minutes is not None:
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
        movie.cinema_sites = page.cinema_sites
        movie.details_known = True
        _remember_runtime(movie, page)
    apply_nonstop_metadata(movie, release_year, nonstop_director)
    _mark_new(movie, new_in_program)
    cache_put(cache, movie)
    save_cache(cache_path, cache)
    return movie


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
        program_url: str = DEFAULT_PROGRAM_URL,
        no_cache: bool = False,
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
        self.program_url = program_url
        self.no_cache = no_cache
        self._program_running = False
        self._program_error: str | None = None
        self._program_message: str | None = None
        self._program_detail = ""
        self._program_reload = False
        self._program_epoch = 0
        self.watched_ratings: dict[str, float | None] = {}
        self.watchlist_slugs: set[str] = set()
        self.watchlist_films: list[dict[str, object]] = []
        self.stream_films: list[dict[str, object]] | None = None
        self.refresh_streaming = False
        self.loved_credits: dict[str, dict] = {}
        self.refresh_neighbors = False
        self.ignore_recommendation_cache = False
        self.see_first: list[Movie] = []
        self.like_you: list[Movie] = []
        self.step_outside: list[Movie] = []
        self.taste_paragraph = ""
        self.taste_note: str | None = None
        self.taste_loved: list[str] = []
        self.taste_lower: list[str] = []
        self.taste_loved_are_themes = False
        self.taste_fetched_at: int | None = None
        self._rec_running = False
        self._rec_error: str | None = None
        self._rec_epoch = 0
        self._epoch = 0
        self._rebuilding = False
        self._lock = threading.RLock()

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
        from goodmoviefinder.friends import (
            ensure_following_list,
            ensure_member_avatar,
            present_following_list,
        )

        if self.no_profile:
            raise ValueError("Sign in to switch profiles.")
        following_path = self.cache_path.parent / "following.json"
        members = ensure_following_list(
            following_path,
            self.letterboxd_user,
            self.delay,
        )
        root_avatar = ensure_member_avatar(
            following_path,
            self.letterboxd_user,
            self.delay,
        )
        return present_following_list(
            members,
            root_user=self.letterboxd_user,
            active_user=self.following_user,
            root_avatar=root_avatar,
        )

    def _letterboxd_avatar_for_page(self) -> str:
        if self.no_profile:
            return ""
        if self.following_user.casefold() != self.letterboxd_user.casefold():
            return ""
        from goodmoviefinder.friends import cached_member_avatar

        return cached_member_avatar(
            self.cache_path.parent / "following.json",
            self.letterboxd_user,
        ) or ""

    def search_letterboxd(self, query: str) -> dict[str, object]:
        text = query.strip()
        if not text:
            raise ValueError("Type a title to search Letterboxd.")
        if len(text) > 120:
            raise ValueError("That search is too long.")
        with self._lock:
            delay = self.delay
        films = letterboxd_autocomplete_films(text, delay, limit=8)
        return {"films": films}

    def letterboxd_backdrop(self, slug: str) -> dict[str, object]:
        from goodmoviefinder.cache import backdrop_lookup, remember_backdrop
        from goodmoviefinder.letterboxd import (
            fetch_letterboxd_html,
            letterboxd_film_page_url,
            normalize_letterboxd_film_slug,
            parse_letterboxd_backdrop,
        )

        normalized = normalize_letterboxd_film_slug(slug)
        if not normalized:
            raise ValueError("That is not a Letterboxd film.")
        with self._lock:
            known, url = backdrop_lookup(normalized)
            delay = self.delay
        if known:
            return {"url": url}
        body = fetch_letterboxd_html(letterboxd_film_page_url(normalized), delay)
        url = parse_letterboxd_backdrop(body)
        with self._lock:
            remember_backdrop(normalized, url)
            features = self.cache.get("film_features")
            feature = features.get(normalized) if isinstance(features, dict) else None
            if isinstance(feature, dict) and url:
                feature["backdrop"] = url
        return {"url": url}

    def apply_match(self, payload: dict) -> dict[str, object]:
        with self._lock:
            raw_url = payload.get("nonstopUrl")
            raw_slug = payload.get("slug")
            if not isinstance(raw_url, str) or not raw_url.strip():
                raise ValueError("Choose a film from the program.")
            slug = normalize_letterboxd_film_slug(str(raw_slug or ""))
            if not slug:
                raise ValueError("That is not a Letterboxd film address.")
            movie = next(
                (item for item in self.results if item.nonstop_url == raw_url.strip()),
                None,
            )
            if movie is None:
                raise ValueError("That film is not in the current program.")
            apply_manual_letterboxd_match(movie, slug, self.delay)
            cache_put(self.cache, movie)
            save_cache(self.cache_path, self.cache)
            self.write_program()
            self._write_account_page()
            return {"reload": True}

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
            loved=self.taste_loved,
            lower=self.taste_lower,
            loved_are_themes=self.taste_loved_are_themes,
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
                payload["updatedLabel"] = recommendations_updated_label(self.taste_fetched_at)
            if self._rec_error:
                payload["error"] = self._rec_error
                self._rec_error = None
            return payload

    def start_recommendations_refresh(self) -> dict[str, object]:
        with self._lock:
            if self.no_profile:
                raise ValueError("Sign in to refresh recommendations.")
            if self._rec_running or self._rebuilding:
                return {"status": "running"}
            self._rec_running = True
            self._rec_epoch = self._epoch
        threading.Thread(target=self._recommendations_refresh_worker, daemon=True).start()
        return {"status": "running"}

    def _recommendations_refresh_worker(self) -> None:
        epoch = self._rec_epoch
        try:
            self.fill_recommendations(force=True, refresh_profile=True)
            with self._lock:
                if epoch != self._epoch:
                    return
                self.loved_credits = ensure_loved_film_credits(
                    self.cache,
                    self.cache_path,
                    self.watched_ratings,
                    self.delay,
                    self.results,
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
            loved=self.taste_loved,
            lower=self.taste_lower,
            loved_are_themes=self.taste_loved_are_themes,
            updated_label=recommendations_updated_label(self.taste_fetched_at),
            known_languages=self.languages,
            language_options=choices,
            letterboxd_avatar=self._letterboxd_avatar_for_page(),
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
            self.taste_loved = []
            self.taste_lower = []
            self.taste_loved_are_themes = False
            self.taste_fetched_at = None
            return [], [], [], sort_movies(self.results)
        return partition_program_movies(
            self.results,
            self.watched_ratings,
            self.watchlist_slugs,
            self.loved_credits,
        )

    def write_program(self) -> tuple[list[Movie], list[Movie], list[Movie], list[Movie]]:
        """Write the Nonstop page. Letterboxd recommendations are filled in separately."""
        apply_known_letterboxd_posters(self.results, self.cache)
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
            calendar_sources=self.calendar_sources,
            signed_in=not self.no_profile,
            following_user=self.following_user,
            letterboxd_avatar=self._letterboxd_avatar_for_page(),
        )
        GENERATED_HTML.parent.mkdir(parents=True, exist_ok=True)
        GENERATED_HTML.write_text(output, encoding="utf-8")
        return watchlist, recommendations, watched, program

    def _apply_taste(self, view: TasteView, fetched_at: int | None) -> None:
        self.see_first = view.see_first
        self.like_you = view.like_you
        self.step_outside = view.step_outside
        self.taste_paragraph = view.paragraph
        self.taste_note = view.note
        self.taste_loved = list(view.loved)
        self.taste_lower = list(view.lower)
        self.taste_loved_are_themes = view.loved_are_themes
        self.taste_fetched_at = fetched_at

    def load_cached_recommendations(self) -> bool:
        if self.no_profile or self.ignore_recommendation_cache:
            return False
        loaded = load_recommendations_cache(self.cache, self.letterboxd_user)
        if loaded is None:
            return False
        view, fetched_at = loaded
        self._apply_taste(view, fetched_at)
        print(
            f"Using cached recommendations for @{self.letterboxd_user}.",
            file=sys.stderr,
        )
        return True

    def fill_recommendations(self, *, force: bool = False, refresh_profile: bool = False) -> None:
        with self._lock:
            epoch = self._epoch
            if self.no_profile:
                return
            if not force and self.load_cached_recommendations():
                return
            user = self.letterboxd_user
            cache = self.cache
            cache_path = self.cache_path
            delay = self.delay
            languages = language_keys(self.languages)
            if refresh_profile:
                refresh_neighbors = True
            else:
                watched = dict(self.watched_ratings)
                watchlist = set(self.watchlist_slugs)
                refresh_neighbors = self.refresh_neighbors
                self.refresh_neighbors = False
        if refresh_profile:
            watched, watchlist, films = load_profile(
                cache,
                cache_path,
                user,
                delay,
                refresh=True,
                no_cache=False,
            )
        print(f"Building recommendations for @{user}…", file=sys.stderr)
        neighbors, _aborted = ensure_neighbors(
            cache_path,
            user,
            watched,
            delay,
            refresh=refresh_neighbors,
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
        fetched_at = save_recommendations_cache(cache, cache_path, user, taste)
        with self._lock:
            if epoch != self._epoch or self.no_profile:
                return
            if refresh_profile:
                self.watched_ratings = watched
                self.watchlist_slugs = watchlist
                self.watchlist_films = films
            self._apply_taste(taste, fetched_at)
            self.ignore_recommendation_cache = False

    def write(self) -> tuple[list[Movie], list[Movie], list[Movie], list[Movie]]:
        groups = self.write_program()
        self.fill_recommendations()
        self.write_program()
        self._write_account_page()
        return groups

    def apply_viewer(self, payload: dict) -> bool:
        switch: tuple[str, bool, bool] | None = None
        with self._lock:
            if self._rebuilding:
                raise ValueError("Still switching accounts.")
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
                if changed or self.no_profile or root_changed:
                    switch = (user, changed or self.no_profile, changed)
                    reload = True
                else:
                    self.following_user = user
            if switch is not None and progress.get() is not None:
                # Hand the browser the building page while this account reloads.
                self._epoch += 1
                self._rebuilding = True
                generation = self._begin_switch_progress(load_profile=switch[1])
                threading.Thread(
                    target=self._switch_worker,
                    args=(switch, generation),
                    daemon=True,
                ).start()
                return True
            if switch is not None:
                self._epoch += 1
                self._switch_to(*switch)
            elif reload:
                self.write()
            self._save_viewer()
            return reload

    def _save_viewer(self) -> None:
        save_viewer(
            self.letterboxd_user,
            self.languages,
            calendars=self.calendar_sources,
            signed_out=self.signed_out,
            following_user=self.following_user,
        )

    def _begin_switch_progress(self, *, load_profile: bool) -> int:
        tracker = progress.get()
        assert tracker is not None
        done = {
            "program": "Already loaded",
            "films": "Already matched",
        }
        skipped: dict[str, str] = {}
        if self.calendar_sources:
            done["calendar"] = "Already loaded"
        else:
            skipped["calendar"] = "No calendar connected"
        if not load_profile:
            skipped["profile"] = "Already loaded"
            skipped["credits"] = "Already loaded"
            skipped["streaming"] = "Already loaded"
        return tracker.restart(done=done, skipped=skipped)

    def _switch_worker(self, switch: tuple[str, bool, bool], generation: int) -> None:
        progress.adopt(generation)
        user, fetch_profile, refresh = switch
        print(f"Switching to @{user}…", file=sys.stderr)
        try:
            with self._lock:
                self._switch_to(user, fetch_profile, refresh)
                tracker = progress.get()
                if tracker is not None:
                    tracker.ready()
        except Exception as exc:
            print(f"[serve] account switch: {exc}", file=sys.stderr)
            tracker = progress.get()
            if tracker is not None and tracker.phase != "error":
                tracker.fail(str(exc))
        finally:
            with self._lock:
                self._rebuilding = False

    def _switch_to(self, user: str, fetch_profile: bool, refresh: bool) -> None:
        if fetch_profile:
            with progress.step("profile", detail=f"@{user}"):
                watched, watchlist, films = load_profile(
                    self.cache,
                    self.cache_path,
                    user,
                    self.delay,
                    refresh=refresh,
                    no_cache=False,
                )
                progress.tick(
                    detail=f"{len(watched)} watched, {len(watchlist)} on watchlist"
                )
            with progress.step(
                "streaming",
                total=max(len(films), 1),
                detail="Where watchlist films stream",
            ):
                stream_films = resolve_watchlist_streaming(
                    films,
                    self.delay,
                    refresh=self.refresh_streaming,
                )
            with progress.step("credits", detail="Films you rated 4.0 or higher"):
                loved_credits = ensure_loved_film_credits(
                    self.cache,
                    self.cache_path,
                    watched,
                    self.delay,
                    self.results,
                )
            self.watched_ratings = watched
            self.watchlist_slugs = watchlist
            self.watchlist_films = films
            self.stream_films = stream_films
            self.loved_credits = loved_credits
            self.letterboxd_user = user
            self.no_profile = False
            self.signed_out = False
            from goodmoviefinder.friends import ensure_member_avatar

            ensure_member_avatar(
                self.cache_path.parent / "following.json",
                user,
                self.delay,
            )
            self.see_first = []
            self.like_you = []
            self.step_outside = []
            self.taste_paragraph = ""
            self.taste_note = None
            self.taste_loved = []
            self.taste_lower = []
            self.taste_loved_are_themes = False
            self.taste_fetched_at = None
        self.following_user = user
        self._save_viewer()
        with progress.step("pages", detail="Writing the Nonstop program"):
            self.write_program()
        if self.no_profile:
            progress.skip("recommendations", "Signed out")
        elif self.load_cached_recommendations():
            progress.skip("recommendations", "Using saved recommendations")
        else:
            self._write_account_page()
            with progress.step("recommendations", detail="Similar members and For you"):
                self.fill_recommendations(force=True)
        self.write_program()
        self._write_account_page()
        self._save_viewer()

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
                    "No calendar is connected. Add Google Calendar or Apple Calendar on the Plan tab."
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

    def program_check_status(self) -> dict[str, object]:
        with self._lock:
            if self._program_running:
                payload: dict[str, object] = {"status": "running"}
                if self._program_detail:
                    payload["message"] = self._program_detail
                return payload
            payload = {"status": "idle", "ready": True}
            if self._program_error:
                payload["error"] = self._program_error
                self._program_error = None
            elif self._program_message:
                payload["message"] = self._program_message
                payload["reload"] = self._program_reload
                self._program_message = None
                self._program_reload = False
            return payload

    def start_program_check(self) -> dict[str, object]:
        with self._lock:
            if self._rebuilding:
                raise ValueError("Still updating the page. Try again in a moment.")
            if self._program_running:
                return {"status": "running"}
            self._program_running = True
            self._program_error = None
            self._program_message = None
            self._program_detail = "Reading the Wien program…"
            self._program_reload = False
            self._program_epoch = self._epoch
        threading.Thread(target=self._program_check_worker, daemon=True).start()
        return {"status": "running", "message": "Reading the Wien program…"}

    def _program_check_worker(self) -> None:
        epoch = self._program_epoch
        try:
            with self._lock:
                program_url = self.program_url
            print(f"Checking program: {program_url}", file=sys.stderr)
            try:
                program_html = fetch(program_url)
            except urllib.error.URLError as exc:
                raise RuntimeError(f"Could not reach Nonstop: {exc.reason}") from exc
            films = parse_program(program_html)
            if not films:
                raise RuntimeError("No films found on the Wien program.")
            current_urls = {film.url for film in films}
            with self._lock:
                if epoch != self._epoch:
                    return
                known = {movie.nonstop_url for movie in self.results}
            fresh = [film for film in films if film.url not in known]
            added: list[str] = []
            poster_lookup = _PosterLookup()
            for index, film in enumerate(fresh, start=1):
                print(f"[new {index}/{len(fresh)}] {film.title}", file=sys.stderr)
                detail = f"Looking up {index} of {len(fresh)}: {film.title}"
                with self._lock:
                    if epoch != self._epoch:
                        return
                    self._program_detail = detail
                    if any(item.nonstop_url == film.url for item in self.results):
                        continue
                    movie = lookup_program_film(
                        film,
                        self.cache,
                        self.cache_path,
                        self.delay,
                        no_cache=self.no_cache,
                        new_in_program=True,
                        poster_lookup=poster_lookup,
                    )
                    self.results.append(movie)
                    added.append(film_title(movie))
            with self._lock:
                if epoch != self._epoch:
                    return
                removed: list[str] = []
                kept: list[Movie] = []
                for movie in self.results:
                    if movie.nonstop_url in current_urls:
                        kept.append(movie)
                    else:
                        removed.append(film_title(movie))
                self.results[:] = kept
                by_url = {movie.nonstop_url: movie for movie in self.results}
                screenings_changed = False
                for film in films:
                    movie = by_url.get(film.url)
                    if movie is None:
                        continue
                    before = (list(movie.versions), list(movie.screenings), movie.poster_url)
                    attach_program(movie, film)
                    after = (movie.versions, movie.screenings, movie.poster_url)
                    if before != after:
                        screenings_changed = True
                if not added and not removed and not screenings_changed:
                    self._program_message = "The Wien program is unchanged."
                    self._program_reload = False
                    return
                self._program_detail = "Updating the page…"
                self.write_program()
                self._write_account_page()
                self._program_message = _program_change_message(
                    added,
                    removed,
                    screenings_changed=screenings_changed,
                )
                self._program_reload = True
        except Exception as exc:
            print(f"[serve] program check: {exc}", file=sys.stderr)
            with self._lock:
                self._program_error = str(exc)
        finally:
            with self._lock:
                self._program_running = False

    def refresh_calendar(self) -> dict[str, object]:
        with self._lock:
            sources = self.calendar_sources or load_calendars()
            if not sources:
                raise ValueError(
                    "No calendar is connected. Add Google Calendar or Apple Calendar on the Plan tab."
                )
            return self._reload_calendar_locked()

    def set_calendar(self, payload: dict) -> dict[str, object]:
        source = str(payload.get("source") or "").strip()
        if source not in ("apple", "google"):
            raise ValueError("Choose Google Calendar or Apple Calendar.")
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("Choose whether to use that calendar.")
        label = "Apple Calendar" if source == "apple" else "Google Calendar"
        with self._lock:
            sources = [item for item in self.calendar_sources if item != source]
            if enabled:
                sources.append(source)
            intervals, status, ready = _calendar_for_plan(sources, self.results)
            if enabled and source not in ready:
                raise CalendarError(status or f"{label} could not be read.")
            self.calendar_sources = sources
            self.busy_intervals = intervals
            self.calendar_status = status
            self._save_viewer()
            self.write()
            return {"intervals": intervals, "status": status, "sources": list(sources)}

    def _reload_calendar_locked(self) -> dict[str, object]:
        sources = self.calendar_sources or load_calendars()
        intervals, status, _ready = _calendar_for_plan(sources, self.results)
        self.busy_intervals = intervals
        self.calendar_status = status
        self.write()
        return {"intervals": intervals, "status": status, "sources": list(sources)}


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
    poster_lookup = _PosterLookup()

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
            busy_intervals, calendar_status, _ready = _calendar_for_plan(
                calendar_sources,
                screening_movies,
            )
    else:
        busy_intervals, calendar_status, _ready = _calendar_for_plan(
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
        server = start_server(hub, args.port, open_browser=True)
        if server is None:
            return 1
    return finish(0)
