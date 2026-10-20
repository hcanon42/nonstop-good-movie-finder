"""Render the ranked program as one self-contained HTML page."""

from __future__ import annotations

import calendar
import html
import json
import re
from datetime import date
from pathlib import Path

from goodmoviefinder.config import (
    DEFAULT_KNOWN_LANGUAGES,
    DEFAULT_LETTERBOXD_USER,
    DEFAULT_SERVE_PORT,
)
from goodmoviefinder.cache import backdrop_lookup
from goodmoviefinder.letterboxd import letterboxd_backdrop_url, letterboxd_poster_url
from goodmoviefinder.layout import render_page
from goodmoviefinder.models import Movie, Screening
from goodmoviefinder.program import (
    _MONTHS,
    LOVED_RATING,
    _DISPLAY_DIRECTOR_LIMIT,
    _format_directors,
    _format_genres,
    _format_user_rating,
    _format_year,
    acceptable_versions,
    counts_as_viewer_language,
    primary_language_constrains_versions,
    film_title,
    format_language,
    format_screening_date,
    is_last_chance,
    language_filter_blurb,
    last_followable_day,
    letterboxd_slug_from_url,
    movie_is_watchable,
    sort_movies,
    split_program_groups,
    venue_label,
)
from goodmoviefinder.viewer import (
    canonical_languages,
    language_choices,
    language_keys,
    settings_stamp,
)

_ASSET_DIR = Path(__file__).resolve().parent

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


def _plan_title(movie: Movie) -> str:
    """List title, or the program title when it adds a qualifier such as 70mm."""
    title = film_title(movie)
    program = (movie.title or "").strip()
    if program and program.casefold() != title.casefold() and title.casefold() in program.casefold():
        return program
    return title


def _plan_add_button(movie: Movie) -> str:
    plan_id = html.escape(movie.nonstop_url, quote=True)
    return (
        f'<button type="button" class="plan-add" data-plan-id="{plan_id}" '
        f'aria-pressed="false" title="Add to plan">'
        f'<span class="plan-add-icon" aria-hidden="true">+</span>'
        f'<span class="visually-hidden">Add to plan</span>'
        f"</button>"
    )


def _json_for_html(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")


def _screening_followable(movie: Movie, screening: Screening, known: frozenset[str]) -> bool:
    if not primary_language_constrains_versions(movie):
        return True
    return screening.language in set(acceptable_versions(movie, known))


def _plan_catalog_json(movies: list[Movie]) -> str:
    catalog: list[dict[str, object]] = []
    seen: set[str] = set()
    for movie in movies:
        if not movie.nonstop_url or movie.nonstop_url in seen:
            continue
        seen.add(movie.nonstop_url)
        screenings: list[dict[str, str]] = []
        for screening in movie.screenings:
            if not screening.weekday or not screening.time:
                continue
            screenings.append(
                {
                    "date": screening.weekday,
                    "time": screening.time,
                    "venue": venue_label(screening),
                    "venueSlug": screening.venue_slug,
                    "language": screening.language,
                    "url": movie.cinema_sites.get(screening.venue_slug) or "",
                }
            )
        catalog.append(
            {
                "id": movie.nonstop_url,
                "title": _plan_title(movie),
                "duration": movie.duration_minutes,
                "primaryLanguage": movie.primary_language or "",
                "screenings": screenings,
            }
        )
    return _json_for_html(catalog)


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


def _friend_ratings_html(slug: str | None) -> str:
    """Placeholder filled in the background, and shown when the film is opened."""
    if not slug or len(slug) > 160:
        return ""
    if any(not (character.isalnum() or character == "-") for character in slug):
        return ""
    safe = html.escape(slug, quote=True)
    return (
        f'<section class="friend-ratings" data-friend-slug="{safe}">'
        f'<h3 class="schedule-label">Letterboxd friends</h3>'
        f'<p class="friend-status muted">Loading friend ratings and watchlists…</p>'
        f'<div class="friend-group" data-friend-section="comments" hidden>'
        f'<h4 class="friend-group-label">Comments</h4>'
        f'<ul class="friend-list" hidden></ul>'
        f"</div>"
        f'<div class="friend-group" data-friend-section="rated">'
        f'<h4 class="friend-group-label">Rated</h4>'
        f'<ul class="friend-list friend-list-compact" hidden></ul>'
        f'<p class="friend-empty muted" hidden>None of your Letterboxd friends have rated this.</p>'
        f"</div>"
        f'<div class="friend-group" data-friend-section="watchlist">'
        f'<h4 class="friend-group-label">On watchlist</h4>'
        f'<ul class="friend-list friend-list-compact" hidden></ul>'
        f'<p class="friend-empty muted" hidden>None of your Letterboxd friends have this on their watchlist.</p>'
        f"</div>"
        f"</section>"
    )


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
    movie: Movie,
    known: frozenset[str],
) -> str:
    if day is None:
        return '<span class="cal-day blank"></span>'
    screenings = by_day.get(day)
    if not screenings:
        return f'<span class="cal-day">{day.day}</span>'
    follow = [screening for screening in screenings if _screening_followable(movie, screening, known)]
    payload = _json_for_html(
        [
            {
                "v": screening.language,
                "t": f"{screening.time or 'time TBC'} {venue_label(screening)}",
            }
            for screening in screenings
        ]
    )
    mark = " day-mark" if follow else ""
    title = html.escape(_day_title(follow), quote=True) if follow else ""
    return (
        f'<span class="cal-day{mark}" data-screenings="{html.escape(payload, quote=True)}" '
        f'title="{title}">{day.day}</span>'
    )


def _month_calendar_html(
    year: int,
    month: int,
    by_day: dict[date, list[Screening]],
    movie: Movie,
    known: frozenset[str],
) -> str:
    cal = calendar.Calendar(firstweekday=calendar.MONDAY)
    headers = "".join(f"<span>{name}</span>" for name in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"))
    cells: list[str] = []
    for week in cal.monthdayscalendar(year, month):
        for day_num in week:
            day = date(year, month, day_num) if day_num else None
            cells.append(_calendar_day_cell(day, by_day, movie, known))
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
    known: frozenset[str],
) -> str:
    blocks: list[str] = []
    for day in sorted(by_day):
        screenings = by_day[day]
        heading = html.escape(format_screening_date(day.isoformat()))
        visible = any(_screening_followable(movie, screening, known) for screening in screenings)
        hidden = "" if visible else " hidden"
        blocks.append(
            f'<div class="showtime-day"{hidden}><h3>{heading}</h3>'
            f"<ul>{_day_screening_items_html(movie, screenings, known)}</ul></div>"
        )
    return f'<div class="showtimes">{"".join(blocks)}</div>'


def _schedule_html(movie: Movie, known: frozenset[str]) -> str:
    if not movie.screenings:
        return ""
    by_day = _screenings_by_day(movie.screenings)
    if not by_day:
        return '<p class="muted">No screenings in a version you can follow.</p>'
    follow = any(
        _screening_followable(movie, screening, known)
        for screenings in by_day.values()
        for screening in screenings
    )
    months: list[tuple[int, int]] = []
    seen_months: set[tuple[int, int]] = set()
    for day in sorted(by_day):
        key = (day.year, day.month)
        if key not in seen_months:
            seen_months.add(key)
            months.append(key)
    calendars = "".join(
        _month_calendar_html(year, month, by_day, movie, known) for year, month in months
    )
    schedule_hidden = "" if follow else " hidden"
    empty_hidden = " hidden" if follow else ""
    return (
        f'<p class="muted schedule-empty"{empty_hidden}>No screenings in a version you can follow.</p>'
        f'<div class="schedule" data-schedule{schedule_hidden}>'
        f'<h3 class="schedule-label">Showtimes</h3>'
        f'<div class="cal-row">{calendars}</div>'
        f"{_showtimes_list_html(movie, by_day, known)}"
        f"</div>"
    )


def _format_runtime(minutes: int | None) -> str:
    if not isinstance(minutes, int) or minutes <= 0:
        return ""
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}h {mins:02d}m"
    if hours:
        return f"{hours}h"
    return f"{mins} min"


def _detail_facts_html(movie: Movie) -> str:
    parts: list[str] = []
    year = _format_year(movie)
    if year != "—":
        parts.append(year)
    directors = _format_directors(movie)
    if directors != "—":
        parts.append(html.escape(directors))
    runtime = _format_runtime(movie.duration_minutes)
    if runtime:
        parts.append(html.escape(runtime))
    language = format_language(movie)
    if language and language != "—":
        parts.append(html.escape(language))
    if not parts:
        return ""
    return '<p class="detail-facts">' + "".join(f"<span>{part}</span>" for part in parts) + "</p>"


def _detail_genres_html(movie: Movie) -> str:
    chips = [
        f'<span class="chip">{html.escape(genre.strip())}</span>'
        for genre in movie.genres
        if genre.strip()
    ]
    if not chips:
        return ""
    return f'<p class="detail-genres">{"".join(chips)}</p>'


def _on_nonstop(movie: Movie) -> bool:
    return movie.nonstop_url.startswith("https://nonstopkino.at/")


_SKIP_REASON = re.compile(
    r"^Predicted \d+(?:\.\d+)?(?: \(lower \d+(?:\.\d+)?\))?\.$"
    r"|^From your own ratings only\.$"
)


def because_reasons(text: str) -> list[str]:
    """Split a recommendation explanation into one reason per line."""
    raw = text.strip()
    if not raw:
        return []
    if "\n" in raw:
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
    else:
        lines = _split_because_sentences(raw)
    reasons: list[str] = []
    for line in lines:
        for reason in _split_nearest_reasons(line):
            if _SKIP_REASON.match(reason):
                continue
            if reasons and reason.startswith("It showed up"):
                previous = reasons[-1].rstrip(".")
                reasons[-1] = f"{previous}. {reason}"
            else:
                reasons.append(reason)
    return reasons


def _split_because_sentences(text: str) -> list[str]:
    """Split on sentence ends without breaking ratings such as 4.6."""
    reasons: list[str] = []
    buf = ""
    for piece in text.split(". "):
        piece = piece.strip()
        if not piece:
            continue
        if not buf:
            buf = piece
            continue
        if re.match(r"\d{4}\b", piece) or re.match(r"[A-Z]", piece):
            reasons.append(buf if buf.endswith(".") else f"{buf}.")
            buf = piece
        else:
            buf = f"{buf}. {piece}"
    if buf:
        reasons.append(buf if buf.endswith(".") else f"{buf}.")
    return reasons


def _split_nearest_reasons(sentence: str) -> list[str]:
    """One bullet per loved film inside a 'nearest of your ratings' sentence."""
    prefix = "Nearest of your own ratings: "
    if not sentence.startswith(prefix):
        return [sentence]
    body = sentence[len(prefix) :].strip()
    if body.endswith("."):
        body = body[:-1]
    clauses: list[str] = []
    buf = ""
    for part in body.split(" and "):
        candidate = f"{buf} and {part}" if buf else part
        if re.search(r", which you rated \d+(?:\.\d+)?$", candidate):
            clauses.append(candidate)
            buf = ""
        else:
            buf = candidate
    if buf:
        if clauses:
            clauses[-1] = f"{clauses[-1]} and {buf}"
        else:
            clauses = [buf]
    if len(clauses) <= 1:
        return [sentence]
    return [f"Close to {clause}." for clause in clauses]


def _why_recommend_html(movie: Movie) -> str:
    reasons = because_reasons(movie.because or "")
    if not reasons:
        return ""
    items = "".join(f"<li>{html.escape(reason)}</li>" for reason in reasons)
    return (
        '<div class="why-recommend">'
        '<p class="why-recommend-label">Why you would like this</p>'
        f'<ul class="why-recommend-list">{items}</ul>'
        "</div>"
    )


def _chosen_poster(movie: Movie) -> tuple[str, str] | None:
    """Vertical Letterboxd poster when we have one, otherwise the wide Nonstop image."""
    vertical = letterboxd_poster_url(movie.letterboxd_poster_url) or letterboxd_poster_url(
        movie.poster_url
    )
    if vertical:
        return vertical, "vertical"
    if movie.poster_url:
        return movie.poster_url, "wide"
    return None


def _detail_hero_html(backdrop: str | None, slug: str | None) -> str:
    """Wide Letterboxd header. A slug with no saved image is filled in when the row opens."""
    url = letterboxd_backdrop_url(backdrop)
    known = url is not None
    if url is None and slug:
        known, url = backdrop_lookup(slug)
    safe_slug = ""
    if slug and len(slug) <= 160 and all(character.isalnum() or character == "-" for character in slug):
        safe_slug = html.escape(slug, quote=True)
    if url:
        src = html.escape(url, quote=True)
        slug_attr = f' data-backdrop-slug="{safe_slug}"' if safe_slug else ""
        return (
            f'<div class="detail-hero"{slug_attr}>'
            f'<img class="detail-backdrop" src="{src}" alt="">'
            f"</div>"
        )
    if not safe_slug or known:
        return ""
    return f'<div class="detail-hero" data-backdrop-slug="{safe_slug}" hidden></div>'


def _poster_img(movie: Movie, *, row: bool) -> str:
    chosen = _chosen_poster(movie)
    if chosen is None:
        return ""
    url, kind = chosen
    src = html.escape(url, quote=True)
    if row and kind == "wide":
        return f'<img class="row-poster row-poster-wide" src="{src}" alt="">'
    if row:
        return (
            f'<img class="row-poster" src="{src}" alt="" width="32" height="48">'
        )
    alt = html.escape(film_title(movie), quote=True)
    css = "poster poster-wide" if kind == "wide" else "poster"
    return f'<img class="{css}" src="{src}" alt="{alt}">'


def _detail_panel_html(
    movie: Movie,
    detail_id: str,
    known: frozenset[str],
    *,
    because_in_detail: bool = False,
) -> str:
    poster = _poster_img(movie, row=False)
    slug = letterboxd_slug_from_url(movie.letterboxd_url) if movie.letterboxd_url else None
    hero = _detail_hero_html(movie.letterboxd_backdrop_url, slug)
    if movie.synopsis:
        synopsis = f'<p class="synopsis">{html.escape(movie.synopsis)}</p>'
    elif movie.details_known:
        synopsis = '<p class="synopsis muted">No synopsis available.</p>'
    else:
        synopsis = ""
    if movie.letterboxd_url:
        label = html.escape(film_title(movie))
        letterboxd = _external_link(movie.letterboxd_url, label, "link-lb")
        friends = _friend_ratings_html(slug)
    else:
        letterboxd = '<span class="muted">No Letterboxd page matched this title.</span>'
        friends = ""
    # The pencil only saves a match for a Nonstop title. Recommendation films
    # are already Letterboxd pages, so the control cannot do anything there.
    if _on_nonstop(movie):
        if movie.letterboxd_url:
            match_label = "Wrong Letterboxd page"
            match_kind = "fix"
        else:
            match_label = "Find on Letterboxd"
            match_kind = "find"
        query = html.escape(film_title(movie), quote=True)
        match_url = html.escape(movie.nonstop_url, quote=True)
        match_label_attr = html.escape(match_label, quote=True)
        match_repair = (
            f'<section class="match-repair" data-match-url="{match_url}">'
            f'<p class="detail-links">'
            f"{letterboxd}"
            f'<button type="button" class="match-toggle match-toggle-{match_kind}" '
            f'aria-expanded="false" title="{match_label_attr}" '
            f'aria-label="{match_label_attr}">'
            f'<span class="match-toggle-icon" aria-hidden="true"></span>'
            f"</button>"
            f"</p>"
            f'<form class="match-form" hidden>'
            f'<label class="match-label">Search Letterboxd'
            f'<input type="search" class="match-query" value="{query}" maxlength="120" '
            f'autocomplete="off" spellcheck="false">'
            f"</label>"
            f'<button type="submit" class="match-search">Search</button>'
            f'<p class="match-status muted" role="status" hidden></p>'
            f'<ul class="match-results" hidden></ul>'
            f"</form>"
            f"</section>"
        )
    else:
        match_repair = f'<p class="detail-links">{letterboxd}</p>'
    why = _why_recommend_html(movie) if because_in_detail else ""
    return (
        f'<div class="detail panel panel-inset" id="{html.escape(detail_id, quote=True)}">'
        f"{hero}"
        f'<div class="detail-main">'
        f"{poster}"
        f'<div class="detail-body">'
        f'<div class="detail-about">{_detail_facts_html(movie)}{_detail_genres_html(movie)}'
        f"{synopsis}{why}{match_repair}</div>"
        f'<div class="detail-columns">{friends}{_schedule_html(movie, known)}</div>'
        f"</div></div></div>"
    )


def _because_html(movie: Movie, css_class: str = "because") -> str:
    if not movie.because:
        return ""
    return (
        f'<span class="{css_class}">{html.escape(movie.because)}</span>'
    )


def _last_chance_chip_html(movie: Movie, known: frozenset[str]) -> str:
    if not is_last_chance(movie, known):
        return ""
    last = last_followable_day(movie, known)
    title = ""
    if last is not None:
        label = format_screening_date(last.isoformat())
        title = f' title="Last followable screening {html.escape(label, quote=True)}"'
    return f'<span class="chip chip-soon"{title}>Leaving soon</span> '


def _movie_search_blob(movie: Movie, known: frozenset[str] | None = None) -> str:
    versions = " ".join(movie.versions)
    because = movie.because or ""
    extras = []
    if movie.new_in_program:
        extras.append("new")
    if known is not None and is_last_chance(movie, known):
        extras.append("leaving soon")
    extra = f" {' '.join(extras)}" if extras else ""
    directors = ", ".join(movie.directors) if movie.directors else ""
    return html.escape(
        f"{film_title(movie)} {movie.title} {directors} {_format_genres(movie)} "
        f"{movie.year or ''} {format_language(movie)} {versions} {because}{extra}".lower()
    )


def _shift_month(year: int, month: int, delta: int = 1) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _calendar_groups(
    watchlist: list[Movie],
    recommendations: list[Movie],
    watched: list[Movie],
    program: list[Movie],
    see_first: list[Movie] | None = None,
    like_you: list[Movie] | None = None,
    step_outside: list[Movie] | None = None,
    just_arrived: list[Movie] | None = None,
) -> list[tuple[str, str, list[tuple[str, Movie]]]]:
    """Same section order and row ids as the list tables."""
    rated, unrated, unfound = split_program_groups(program)
    if see_first is not None or like_you is not None or step_outside is not None:
        taste_labeled = (
            ("see-first", "See this first", see_first or []),
            ("like-you", "More like what you love", like_you or []),
            ("step-outside", "A step outside", step_outside or []),
        )
    else:
        taste_labeled = (("recommendations", "Recommended", recommendations),)
    labeled = (
        ("just-arrived", "Just arrived", just_arrived or []),
        *taste_labeled,
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
) -> dict[date, list[tuple[str, str, str, Movie, list[Screening], list[str]]]]:
    """One calendar row per film. A watchlist or recommendation title also belongs to the full program."""
    by_day: dict[date, list[tuple[str, str, str, Movie, list[Screening], list[str]]]] = {}
    placed: dict[date, dict[str, list[str]]] = {}
    for section_id, label, movies in groups:
        for movie_id, movie in movies:
            key = movie.nonstop_url or movie_id
            for day, screenings in _screenings_by_day(movie.screenings).items():
                sections = placed.setdefault(day, {}).get(key)
                if sections is not None:
                    if section_id not in sections:
                        sections.append(section_id)
                    continue
                sections = [section_id]
                placed[day][key] = sections
                by_day.setdefault(day, []).append(
                    (section_id, label, movie_id, movie, screenings, sections)
                )
    return by_day


def _day_preview_title(
    entries: list[tuple[str, str, str, Movie, list[Screening], list[str]]],
) -> str:
    best = entries[0]
    best_rating = best[3].rating
    for entry in entries[1:]:
        rating = entry[3].rating
        if rating is not None and (best_rating is None or rating > best_rating):
            best = entry
            best_rating = rating
    return film_title(best[3])


def _day_times_line_html(
    movie: Movie,
    screenings: list[Screening],
    known: frozenset[str],
) -> str:
    """Collapsed calendar row: successive followable showtimes, without venue or version."""
    follow = [screening for screening in screenings if _screening_followable(movie, screening, known)]
    ordered = sorted(follow, key=lambda screening: screening.time or "")
    clocks = " · ".join(html.escape(screening.time or "—") for screening in ordered)
    return f'<p class="day-times-line">{clocks}</p>'


def _day_screening_items_html(
    movie: Movie,
    screenings: list[Screening],
    known: frozenset[str],
) -> str:
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
        version_attr = html.escape(screening.language, quote=True)
        clock_attr = html.escape(screening.time or "—", quote=True)
        hidden = "" if _screening_followable(movie, screening, known) else " hidden"
        items.append(
            f'<li data-version="{version_attr}" data-clock="{clock_attr}"{hidden}>'
            f"{when}{version}</li>"
        )
    return "".join(items)


def _entry_followable(
    entry: tuple[str, str, str, Movie, list[Screening], list[str]],
    known: frozenset[str],
) -> bool:
    movie, screenings = entry[3], entry[4]
    return any(_screening_followable(movie, screening, known) for screening in screenings)


def _day_film_html(
    section_id: str,
    label: str,
    movie_id: str,
    movie: Movie,
    screenings: list[Screening],
    known: frozenset[str],
    sections: list[str],
) -> str:
    rating_text = f"{movie.rating:.2f}" if movie.rating is not None else "—"
    title = html.escape(film_title(movie))
    title_attr = html.escape(film_title(movie), quote=True)
    plan_id = html.escape(movie.nonstop_url, quote=True)
    rating_attr = f"{movie.rating:.4f}" if movie.rating is not None else ""
    primary = html.escape(movie.primary_language or "", quote=True)
    follow = any(_screening_followable(movie, screening, known) for screening in screenings)
    hidden = "" if follow else " hidden"
    lang_hide = "0" if follow else "1"
    return (
        f'<article class="day-film" data-movie-id="{html.escape(movie_id, quote=True)}" '
        f'data-plan-id="{plan_id}" '
        f'data-section="{html.escape(section_id, quote=True)}" '
        f'data-sections="{html.escape(" ".join(sections), quote=True)}" '
        f'data-primary-language="{primary}" '
        f'data-rating="{rating_attr}" data-title="{title_attr}" '
        f'data-lang-hide="{lang_hide}"{hidden} '
        f'data-search="{_movie_search_blob(movie, known)}">'
        f'<div class="day-film-line">'
        f"{_rating_badge(movie.rating, rating_text)}"
        f"{_plan_add_button(movie)}"
        f'<button type="button" class="day-film-title" aria-expanded="false">{title}</button>'
        f"{_new_program_chip_html(movie)}{_last_chance_chip_html(movie, known)}"
        f'<span class="chip section-chip chip-{html.escape(section_id, quote=True)}">'
        f"{html.escape(label)}</span>"
        f"</div>"
        f"{_because_html(movie, 'day-because')}"
        f"{_day_times_line_html(movie, screenings, known)}"
        f'<ul class="day-times">{_day_screening_items_html(movie, screenings, known)}</ul>'
        f'<div class="day-film-detail" hidden></div>'
        f"</article>"
    )


def _day_panel_html(
    day: date,
    entries: list[tuple[str, str, str, Movie, list[Screening], list[str]]],
    known: frozenset[str],
) -> str:
    films = "".join(
        _day_film_html(section_id, label, movie_id, movie, screenings, known, sections)
        for section_id, label, movie_id, movie, screenings, sections in entries
    )
    iso = day.isoformat()
    return f'<div class="month-band panel panel-inset" id="day-{iso}" hidden>{films}</div>'


def _calendar_day_button(
    day: date,
    entries: list[tuple[str, str, str, Movie, list[Screening], list[str]]],
    today: date,
    known: frozenset[str],
) -> str:
    today_class = " is-today" if day == today else ""
    if not entries:
        return (
            f'<span class="month-day blank{today_class}">'
            f'<span class="month-day-num">{day.day}</span></span>'
        )
    follow = [entry for entry in entries if _entry_followable(entry, known)]
    preview = _day_preview_title(follow) if follow else ""
    preview_attr = html.escape(preview, quote=True)
    preview_html = html.escape(preview)
    count = len(follow)
    last_chance = any(
        last_followable_day(entry[3], known) == day for entry in follow
    )
    last_class = " is-last-chance" if last_chance else ""
    filtered = "" if follow else " is-filtered-out"
    disabled = "" if follow else " disabled"
    iso = day.isoformat()
    return (
        f'<button type="button" class="month-day has{today_class}{filtered}{last_class}"{disabled} '
        f'data-day="{iso}" data-count="{count}" data-title="{preview_attr}" '
        f'aria-expanded="false" aria-controls="day-{iso}">'
        f'<span class="month-day-num">{day.day}</span>'
        f'<span class="month-day-count day-mark">{count}</span>'
        f'<span class="month-day-title">{preview_html}</span>'
        f"</button>"
    )


def _month_board_html(
    year: int,
    month: int,
    by_day: dict[date, list[tuple[str, str, str, Movie, list[Screening], list[str]]]],
    today: date,
    known: frozenset[str],
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
            cells.append(_calendar_day_button(day, entries, today, known))
            if entries:
                panels.append(_day_panel_html(day, entries, known))
        weeks.append(f'<div class="month-week">{"".join(cells)}{"".join(panels)}</div>')
    caption = html.escape(f"{_MONTHS[month]} {year}")
    return (
        f'<section class="month-board panel">'
        f'<h2 class="month-caption">{caption}</h2>'
        f'<div class="month-head">{headers}</div>'
        f'{"".join(weeks)}'
        f"</section>"
    )


def _also_showing_html(
    days: list[date],
    by_day: dict[date, list[tuple[str, str, str, Movie, list[Screening], list[str]]]],
    known: frozenset[str],
) -> str:
    if not days:
        return ""
    blocks: list[str] = []
    visible_days = 0
    for day in days:
        entries = by_day[day]
        follow = [entry for entry in entries if _entry_followable(entry, known)]
        preview = _day_preview_title(follow) if follow else ""
        preview_attr = html.escape(preview, quote=True)
        preview_html = html.escape(preview)
        count = len(follow)
        if follow:
            visible_days += 1
        filtered = "" if follow else " is-filtered-out"
        disabled = "" if follow else " disabled"
        day_hidden = "" if follow else " hidden"
        iso = day.isoformat()
        heading = html.escape(format_screening_date(iso))
        blocks.append(
            f'<div class="also-day"{day_hidden}>'
            f'<button type="button" class="also-day-button{filtered}"{disabled} '
            f'data-day="{iso}" data-count="{count}" data-title="{preview_attr}" '
            f'aria-expanded="false" aria-controls="day-{iso}">'
            f'<span class="month-day-num">{heading}</span>'
            f'<span class="month-day-count day-mark">{count}</span>'
            f'<span class="month-day-title">{preview_html}</span>'
            f"</button>"
            f"{_day_panel_html(day, entries, known)}"
            f"</div>"
        )
    section_hidden = "" if visible_days else " hidden"
    return (
        f'<section class="also-showing"{section_hidden}>'
        f"<h2>Also showing</h2>"
        f'<div class="also-days">{"".join(blocks)}</div>'
        f"</section>"
    )


def _program_calendar_html(
    watchlist: list[Movie],
    recommendations: list[Movie],
    watched: list[Movie],
    program: list[Movie],
    known: frozenset[str],
    today: date | None = None,
    see_first: list[Movie] | None = None,
    like_you: list[Movie] | None = None,
    step_outside: list[Movie] | None = None,
    just_arrived: list[Movie] | None = None,
) -> str:
    today = today or date.today()
    by_day = _calendar_by_day(
        _calendar_groups(
            watchlist,
            recommendations,
            watched,
            program,
            see_first,
            like_you,
            step_outside,
            just_arrived,
        )
    )
    current = (today.year, today.month)
    following = _shift_month(*current)
    shown = {current, following}
    boards = (
        _month_board_html(*current, by_day, today, known)
        + _month_board_html(*following, by_day, today, known)
    )
    outside = sorted(day for day in by_day if (day.year, day.month) not in shown)
    return (
        f'<div class="month-boards">{boards}</div>'
        f"{_also_showing_html(outside, by_day, known)}"
    )


def _new_program_chip_html(movie: Movie) -> str:
    if not movie.new_in_program:
        return ""
    return (
        '<span class="chip chip-new" title="New in the program since your last run">'
        "New</span> "
    )


def _format_html_movie_row(
    m: Movie,
    known: frozenset[str],
    *,
    row_class: str = "",
    row_id: str = "",
    show_user_rating: bool = False,
    show_predicted: bool = False,
    language_filter: bool = False,
    unavailable: bool = False,
    show_because: bool = True,
    show_follow_badge: bool = True,
    because_in_detail: bool = False,
) -> str:
    rating_text = f"{m.rating:.2f}" if m.rating is not None else "—"
    title = html.escape(film_title(m))
    if m.note:
        note_esc = html.escape(m.note)
        title += f' <span class="note" title="{note_esc}">⚠</span>'
    full_directors = _format_directors(m)
    shown_directors = _format_directors(m, limit=_DISPLAY_DIRECTOR_LIMIT)
    directors = html.escape(shown_directors)
    director_title = (
        f' title="{html.escape(full_directors, quote=True)}"'
        if shown_directors != full_directors and full_directors != "—"
        else ""
    )
    genres = _format_genres(m)
    genre_html = " ".join(
        f'<span class="chip">{html.escape(g.strip())}</span>'
        for g in genres.split(", ")
        if g.strip() and g != "—"
    )
    if not genre_html:
        genre_html = '<span class="muted">—</span>'
    year = html.escape(_format_year(m))
    follow = movie_is_watchable(m, known)
    if unavailable:
        show_badge = False
        hide_row = follow
        filter_mode = "unavailable"
    else:
        show_badge = show_follow_badge and not follow and not language_filter
        hide_row = language_filter and not follow
        filter_mode = "hide" if language_filter else ("catalog" if not show_follow_badge else "keep")
    follow_badge = (
        '<span class="chip chip-warn follow-badge" '
        'title="No screenings in a version you can follow."'
        f'{" hidden" if not show_badge else ""}>no followable version</span>'
    )
    because_html = _because_html(m) if show_because else ""
    soon_html = _last_chance_chip_html(m, known)
    new_html = _new_program_chip_html(m)
    classes = " ".join(
        part for part in (row_class, "row-no-follow" if show_badge else "") if part
    )
    extra = f' class="{classes}"' if classes else ""
    row_hidden = " hidden" if hide_row else ""
    lang_hide = "1" if hide_row else "0"
    primary = html.escape(m.primary_language or "", quote=True)
    versions = html.escape(" ".join(m.versions), quote=True)
    detail_classes = " ".join(part for part in ("detail-row", row_class) if part)
    language = html.escape(format_language(m))
    search_blob = _movie_search_blob(m, known)
    detail_id = f"{row_id}-detail" if row_id else "detail"
    movie_id_attr = (
        f' data-movie-id="{html.escape(row_id, quote=True)}"' if row_id else ""
    )
    plan_id_attr = f' data-plan-id="{html.escape(m.nonstop_url, quote=True)}"'
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
    predicted_cell = ""
    sort_predicted = ""
    if show_predicted:
        predicted_text = f"{m.predicted_rating:.1f}" if m.predicted_rating is not None else "—"
        predicted_cell = (
            '<td class="col-rating">'
            + _rating_badge(
                m.predicted_rating,
                predicted_text,
                "rating-you",
                "Predicted rating for you",
            )
            + "</td>"
        )
        predicted_value = f"{m.predicted_rating:.4f}" if m.predicted_rating is not None else ""
        sort_predicted = f' data-sort-predicted="{predicted_value}"'
        columns += 1
    columns += 1
    sort_rating = f"{m.rating:.4f}" if m.rating is not None else ""
    sort_year = str(m.year) if m.year is not None else ""
    sort_title = html.escape(film_title(m), quote=True)
    sort_director = html.escape(", ".join(m.directors), quote=True)
    sort_genres = html.escape(", ".join(m.genres), quote=True)
    sort_language = html.escape(m.primary_language or "", quote=True)
    chosen = _chosen_poster(m)
    # Unfound titles only have the wide Nonstop still. The opened detail keeps
    # it; the folded row stays a text line.
    wide_poster = (
        _poster_img(m, row=True) if chosen and chosen[1] == "wide" and m.letterboxd_url else ""
    )
    thumb = _poster_img(m, row=True) if chosen and chosen[1] == "vertical" else ""
    return (
        f'<tr data-movie-row{extra}{row_hidden}{movie_id_attr}{plan_id_attr} '
        f'data-search="{search_blob}"'
        f' data-filter="{filter_mode}" data-lang-hide="{lang_hide}"'
        f' data-primary-language="{primary}" data-versions="{versions}"'
        f' data-sort-rating="{sort_rating}"{sort_you}{sort_predicted}'
        f' data-sort-year="{sort_year}"'
        f' data-sort-title="{sort_title}"'
        f' data-sort-director="{sort_director}"'
        f' data-sort-genres="{sort_genres}"'
        f' data-sort-language="{sort_language}">'
        f'<td class="col-rating">{_rating_badge(m.rating, rating_text)}</td>'
        f"{predicted_cell}"
        f"{user_cell}"
        f'<td class="col-title"><span class="title-stack">{wide_poster}'
        f'<span class="title-line">{thumb}'
        f'<button type="button" class="expand" '
        f'aria-expanded="false" aria-controls="{html.escape(detail_id, quote=True)}">'
        f"<strong>{title}</strong> {follow_badge}{new_html}{soon_html}{because_html}</button>"
        f"</span></span></td>"
        f"<td class=\"col-director\"{director_title}>{directors}</td>"
        f"<td class=\"col-genres\">{genre_html}</td>"
        f"<td class=\"col-year\">{year}</td>"
        f"<td class=\"col-language\">{language}</td>"
        f'<td class="col-actions"><span class="row-actions">{_plan_add_button(m)}'
        f'<span class="row-chevron" aria-hidden="true"></span></span></td>'
        f"</tr>"
        f'<tr class="{detail_classes}" data-movie-detail hidden>'
        f'<td colspan="{columns}">'
        f"{_detail_panel_html(m, detail_id, known, because_in_detail=because_in_detail)}"
        f"</td>"
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
    known: frozenset[str],
    row_class: str = "",
    show_user_rating: bool = False,
    show_predicted: bool = False,
    language_filter: bool = False,
    unavailable: bool = False,
    show_because: bool = True,
    start_folded: bool = False,
    show_follow_badge: bool = True,
    because_in_detail: bool = False,
) -> str:
    if not movies:
        return ""
    rows = "\n".join(
        _format_html_movie_row(
            movie,
            known,
            row_class=row_class,
            row_id=f"{section_id}-{index}",
            show_user_rating=show_user_rating,
            show_predicted=show_predicted,
            language_filter=language_filter,
            unavailable=unavailable,
            show_because=show_because,
            show_follow_badge=show_follow_badge,
            because_in_detail=because_in_detail,
        )
        for index, movie in enumerate(movies)
    )
    if unavailable:
        visible = [movie for movie in movies if not movie_is_watchable(movie, known)]
    else:
        visible = [
            movie
            for movie in movies
            if not language_filter or movie_is_watchable(movie, known)
        ]
    count = len(visible)
    section_hidden = " hidden" if count == 0 else ""
    folded_class = " is-folded" if start_folded else ""
    folded_attr = " data-start-folded" if start_folded else ""
    expanded = "false" if start_folded else "true"
    you_sort = "descending" if show_user_rating and not show_predicted else None
    you_header = (
        _sortable_header("You", "you", "number", sorted_dir=you_sort, extra_class="col-rating")
        if show_user_rating
        else ""
    )
    rating_sort = None if show_predicted or show_user_rating else "descending"
    predicted_header = (
        _sortable_header(
            "For you",
            "predicted",
            "number",
            sorted_dir="descending",
            extra_class="col-rating",
        )
        if show_predicted
        else ""
    )
    film_word = "film" if count == 1 else "films"
    note_id = ' id="program-language-note"' if section_id == "program" else ""
    return f"""
<section class="program-section{folded_class}" id="{section_id}"{folded_attr}{section_hidden}>
  <h2 class="section-head">
    <button type="button" class="section-toggle" aria-expanded="{expanded}" aria-controls="{section_id}-table">
      <span class="section-rail" aria-hidden="true"></span>
      <span class="section-copy">
        <span class="section-title-row">
          <span class="section-title">{html.escape(title)}</span>
          <span class="chip chip-count" data-section-count>{count} {film_word}</span>
        </span>
        <span class="section-sub"{note_id}>{html.escape(subtitle)}</span>
      </span>
      <span class="chevron" aria-hidden="true"></span>
    </button>
  </h2>
  <div class="section-body">
    <div class="section-body-inner">
      <div class="table-wrap panel" id="{section_id}-table">
        <table>
          <thead>
            <tr>
              {_sortable_header("Rating", "rating", "number", sorted_dir=rating_sort, extra_class="col-rating")}
              {predicted_header}
              {you_header}
              {_sortable_header("Film", "title", "text", extra_class="col-title")}
              {_sortable_header("Director", "director", "text", extra_class="col-director")}
              {_sortable_header("Genres", "genres", "text", extra_class="col-genres")}
              {_sortable_header("Year", "year", "number", extra_class="col-year")}
              {_sortable_header("Language", "language", "text", extra_class="col-language")}
              <th class="col-actions"><span class="visually-hidden">Actions</span></th>
            </tr>
          </thead>
          <tbody>
{rows}
          </tbody>
        </table>
      </div>
    </div>
  </div>
</section>"""


def _language_candidates(*groups: list[Movie]) -> list[Movie]:
    """Films whose primary language can leave them without a followable version."""
    seen: set[str] = set()
    chosen: list[Movie] = []
    for group in groups:
        for movie in group:
            key = movie.nonstop_url or film_title(movie).casefold()
            if key in seen or not primary_language_constrains_versions(movie):
                continue
            seen.add(key)
            chosen.append(movie)
    return sort_movies(chosen)


def _nav_link(section_id: str, label: str, present: bool, visible: bool) -> str:
    if not present:
        return ""
    hidden = "" if visible else " hidden"
    return f'<a href="#{section_id}" data-nav="{section_id}"{hidden}>{label}</a>'


def format_html(
    watchlist: list[Movie],
    recommendations: list[Movie],
    watched: list[Movie],
    program: list[Movie],
    *,
    letterboxd_user: str = DEFAULT_LETTERBOXD_USER,
    known_languages: list[str] | None = None,
    serve_port: int = DEFAULT_SERVE_PORT,
    busy_intervals: list[dict[str, str]] | None = None,
    calendar_status: str = "",
    signed_in: bool = True,
    following_user: str | None = None,
) -> str:
    """The Nonstop Kino page: List, Calendar and Plan tabs."""
    selected = list(DEFAULT_KNOWN_LANGUAGES if known_languages is None else known_languages)
    found = [
        movie.primary_language
        for movie in (*watchlist, *recommendations, *watched, *program)
        if counts_as_viewer_language(movie.primary_language)
    ]
    choices = language_choices(found, selected)
    selected = canonical_languages(selected, choices)
    known = language_keys(selected)
    user_label = f"@{letterboxd_user}"
    rated, unrated, unfound = split_program_groups(program)
    unavailable_movies = _language_candidates(watchlist, recommendations, watched, program)
    just_arrived = [movie for movie in watchlist if movie.new_in_program]

    def section(*args: object, **kwargs: object) -> str:
        return _html_table_section(*args, **kwargs)  # type: ignore[arg-type]

    personal: list[str] = []
    if just_arrived:
        personal.append(
            section(
                "just-arrived",
                "Just arrived",
                f"On {user_label}'s watchlist and new in the program since the last run",
                just_arrived,
                known,
                row_class="row-just-arrived",
            )
        )
    if watchlist:
        personal.append(
            section(
                "watchlist",
                "Watchlist",
                f"On {user_label}'s Letterboxd watchlist and playing at Nonstop Wien",
                watchlist,
                known,
                row_class="row-watchlist",
            )
        )
    if recommendations:
        personal.append(
            section(
                "recommendations",
                "Recommendations",
                (
                    "Playing at Nonstop Wien, by a director of a film "
                    f"{user_label} rated {LOVED_RATING:.1f} or higher"
                ),
                recommendations,
                known,
                row_class="row-recommend",
                language_filter=True,
            )
        )
    if watched:
        personal.append(
            section(
                "watched",
                "Already watched",
                f"{user_label} has seen these — they're back on the program",
                watched,
                known,
                row_class="row-watched",
                show_user_rating=True,
            )
        )
    main_sections: list[str] = []
    if rated:
        main_sections.append(
            section(
                "program",
                "Full program",
                language_filter_blurb(selected),
                rated,
                known,
                language_filter=True,
                show_because=False,
            )
        )
    other: list[str] = []
    if unrated:
        other.append(
            section(
                "unrated",
                "Unrated",
                "Matched on Letterboxd, but the film has no community rating yet",
                unrated,
                known,
                language_filter=True,
                show_because=False,
            )
        )
    if unfound:
        other.append(
            section(
                "unfound",
                "Unfound",
                "No Letterboxd page could be matched to these titles",
                unfound,
                known,
                language_filter=True,
                show_because=False,
            )
        )
    if unavailable_movies:
        other.append(
            section(
                "unavailable",
                "Unavailable movies for you",
                "No screening in a version you can follow",
                unavailable_movies,
                known,
                unavailable=True,
                show_because=False,
            )
        )
    groups: list[str] = []
    if personal:
        heading = (
            f"Picked for {html.escape(user_label)}"
            if signed_in
            else "This week"
        )
        groups.append(
            '<section class="section-group" id="group-personal">'
            f'<h2 class="group-title">{heading}</h2>'
            + "\n".join(personal)
            + "</section>"
        )
    if main_sections:
        groups.append(
            '<section class="section-group" id="group-program">'
            '<h2 class="group-title">Program</h2>'
            + "\n".join(main_sections)
            + "</section>"
        )
    if other:
        groups.append(
            '<details class="section-group group-other" id="group-other">'
            '<summary><h2 class="group-title">More: unrated, unfound and unavailable</h2></summary>'
            + "\n".join(other)
            + "</details>"
        )
    list_html = "\n".join(groups)

    calendar_html = _program_calendar_html(
        watchlist,
        recommendations,
        watched,
        program,
        known,
        just_arrived=just_arrived,
    )
    catalog_json = _plan_catalog_json([*watchlist, *recommendations, *watched, *program])
    busy_json = json.dumps(
        {"intervals": busy_intervals or []},
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    bar_hidden = "" if calendar_status else " hidden"
    status_html = (
        f'<div class="plan-calendar-bar" id="plan-calendar-bar"{bar_hidden}>'
        f'<p class="plan-calendar-status" id="plan-calendar-status">'
        f"{html.escape(calendar_status)}</p>"
        f'<button type="button" id="plan-refresh" class="plan-refresh">'
        f"Refresh calendar</button></div>"
    )

    nav_links = "".join(
        (
            _nav_link(
                "just-arrived",
                "Just arrived",
                bool(just_arrived),
                any(movie_is_watchable(movie, known) for movie in just_arrived),
            ),
            _nav_link("watchlist", "Watchlist", bool(watchlist), bool(watchlist)),
            _nav_link(
                "recommendations",
                "Recommended",
                bool(recommendations),
                any(movie_is_watchable(movie, known) for movie in recommendations),
            ),
            _nav_link("watched", "Watched", bool(watched), bool(watched)),
            _nav_link(
                "program",
                "Program",
                bool(rated),
                any(movie_is_watchable(movie, known) for movie in rated),
            ),
            _nav_link(
                "unrated",
                "Unrated",
                bool(unrated),
                any(movie_is_watchable(movie, known) for movie in unrated),
            ),
            _nav_link(
                "unfound",
                "Unfound",
                bool(unfound),
                any(movie_is_watchable(movie, known) for movie in unfound),
            ),
            _nav_link(
                "unavailable",
                "Unavailable",
                bool(unavailable_movies),
                any(not movie_is_watchable(movie, known) for movie in unavailable_movies),
            ),
        )
    )
    tools_html = (
        '      <div class="search-field" data-tools="list calendar">\n'
        '        <input type="search" id="search" placeholder="Filter by title, director, genre…" '
        'autocomplete="off" aria-describedby="search-count">\n'
        '        <span id="search-count" class="search-count" role="status" aria-live="polite" hidden></span>\n'
        '        <button type="button" id="search-clear" class="search-clear" hidden '
        'aria-label="Clear search">&times;</button>\n'
        "      </div>\n"
        '      <button type="button" class="btn lang-chip" data-open-account data-tools="list calendar" '
        'title="Change the languages you follow">Languages: <strong id="lang-summary">'
        f"{html.escape(', '.join(selected) or 'English subtitles only')}</strong></button>\n"
        '      <div data-tools="list calendar plan"><p id="plan-cap" class="plan-cap" hidden>'
        "A plan holds 12 films.</p></div>\n"
        '      <nav class="nav-pills" aria-label="Program sections" data-tools="list calendar">'
        '<span class="nav-pills-label" id="nav-pills-label">Jump to</span>'
        f"{nav_links}</nav>"
    )
    panels_html = f"""    <div id="list-view" role="tabpanel" aria-labelledby="tab-list">
{list_html}
      <p class="empty-hint" id="list-empty" hidden>No films match this search.</p>
    </div>
    <div id="calendar-view" role="tabpanel" aria-labelledby="tab-calendar" hidden>
{calendar_html}
      <p class="empty-hint" id="calendar-empty" hidden>No films match.</p>
    </div>
    <div id="plan-view" role="tabpanel" aria-labelledby="tab-plan" hidden>
      <div class="plan-board">
        {status_html}
        <p class="empty-hint" id="plan-empty">Add films from the list or calendar with the + button. A plan holds up to 12.</p>
        <div class="plan-picked" id="plan-picked" hidden>
          <div class="plan-chips" id="plan-chips"></div>
          <button type="button" id="plan-clear" class="plan-clear">Clear all</button>
        </div>
        <div class="plan-options" id="plan-options" role="group" aria-label="Schedule options" hidden></div>
        <p class="plan-summary" id="plan-summary" hidden></p>
        <p class="plan-actions" id="plan-actions" hidden>
          <button type="button" id="plan-save" class="plan-save">Add plan to calendar</button>
        </p>
        <p id="plan-save-status" class="plan-save-status" role="status" hidden></p>
        <div class="plan-calendar" id="plan-schedule" hidden></div>
        <section class="plan-left-out" id="plan-left-out" hidden>
          <h2>Not scheduled</h2>
          <ul id="plan-left-out-list"></ul>
        </section>
      </div>
    </div>"""
    total = len({movie.nonstop_url or film_title(movie) for movie in (*program, *watchlist, *recommendations, *watched)})
    stats = [f"<strong>{total}</strong> films playing"]
    hint = ""
    if signed_in:
        stats.append(f"<strong>{len(watchlist)}</strong> on your watchlist")
        stats.append(f"<strong>{len(recommendations)}</strong> recommended")
        stats.append(f"<strong>{len(watched)}</strong> already seen")
    else:
        hint = (
            '<p class="page-hint"><a href="#" data-open-account>Sign in</a> with your Letterboxd '
            "username to highlight your watchlist, recommendations and films you have seen.</p>"
        )
    extra = (
        f'  <script type="application/json" id="plan-busy">{busy_json}</script>\n'
        f'  <script type="application/json" id="plan-catalog">{catalog_json}</script>\n'
    )
    return render_page(
        page="nonstop",
        title="Nonstop Kino — Letterboxd rankings",
        heading="Nonstop Kino Wien",
        tagline="The Wien program ranked by Letterboxd community ratings. Find what to see next.",
        stats=stats,
        hint_html=hint,
        tabs=[("list", "List"), ("calendar", "Calendar"), ("plan", 'Plan<span id="plan-count" class="plan-count" hidden></span>')],
        tools_html=tools_html,
        panels_html=panels_html,
        user=letterboxd_user,
        signed_in=signed_in,
        choices=choices,
        selected=selected,
        viewer={
            "port": serve_port,
            "stamp": settings_stamp(letterboxd_user, selected),
            "followingUser": following_user or letterboxd_user,
        },
        scripts=("page.js", "shell.js", "plan.js"),
        styles=("base.css", "nonstop.css"),
        extra_json=extra,
        footer=(
            "Generated by goodmoviefinder. Ratings from Letterboxd; "
            "showtimes from Nonstop Kino Wien."
        ),
    )
