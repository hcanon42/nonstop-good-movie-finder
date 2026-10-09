"""Render the ranked program as one self-contained HTML page."""

from __future__ import annotations

import calendar
import html
import json
from datetime import date
from pathlib import Path

from goodmoviefinder.config import (
    DEFAULT_KNOWN_LANGUAGES,
    DEFAULT_LETTERBOXD_USER,
    DEFAULT_SERVE_PORT,
)
from goodmoviefinder.models import Movie, Screening
from goodmoviefinder.program import (
    _MONTHS,
    LOVED_RATING,
    _format_directors,
    _format_genres,
    _format_user_rating,
    _format_year,
    acceptable_versions,
    film_title,
    format_language,
    format_screening_date,
    language_filter_blurb,
    movie_is_watchable,
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
    if movie.primary_language is None:
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


def _detail_panel_html(movie: Movie, detail_id: str, known: frozenset[str]) -> str:
    poster = ""
    if movie.poster_url:
        poster = (
            f'<img class="poster" src="{html.escape(movie.poster_url, quote=True)}" '
            f'alt="{html.escape(film_title(movie), quote=True)}">'
        )
    if movie.synopsis:
        synopsis = f'<p class="synopsis">{html.escape(movie.synopsis)}</p>'
    elif movie.details_known:
        synopsis = '<p class="synopsis muted">No synopsis available.</p>'
    else:
        synopsis = ""
    if movie.letterboxd_url:
        label = html.escape(film_title(movie))
        letterboxd = (
            f'<p class="detail-links">{_external_link(movie.letterboxd_url, label, "link-lb")}</p>'
        )
    else:
        letterboxd = ""
    return (
        f'<div class="detail panel panel-inset" id="{html.escape(detail_id, quote=True)}">'
        f"{poster}"
        f'<div class="detail-body">{synopsis}{letterboxd}{_schedule_html(movie, known)}</div>'
        f"</div>"
    )


def _because_html(movie: Movie, css_class: str = "because") -> str:
    if not movie.because:
        return ""
    return (
        f'<span class="{css_class}">{html.escape(movie.because)}</span>'
    )


def _movie_search_blob(movie: Movie) -> str:
    versions = " ".join(movie.versions)
    because = movie.because or ""
    return html.escape(
        f"{film_title(movie)} {movie.title} {_format_directors(movie)} {_format_genres(movie)} "
        f"{movie.year or ''} {format_language(movie)} {versions} {because}".lower()
    )


def _shift_month(year: int, month: int, delta: int = 1) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _calendar_groups(
    watchlist: list[Movie],
    recommendations: list[Movie],
    watched: list[Movie],
    program: list[Movie],
) -> list[tuple[str, str, list[tuple[str, Movie]]]]:
    """Same section order and row ids as the list tables."""
    rated, unrated, unfound = split_program_groups(program)
    labeled = (
        ("watchlist", "Watchlist", watchlist),
        ("recommendations", "Recommended", recommendations),
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
        f'data-search="{_movie_search_blob(movie)}">'
        f'<div class="day-film-line">'
        f"{_rating_badge(movie.rating, rating_text)}"
        f"{_plan_add_button(movie)}"
        f'<button type="button" class="day-film-title" aria-expanded="false">{title}</button>'
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
    filtered = "" if follow else " is-filtered-out"
    disabled = "" if follow else " disabled"
    iso = day.isoformat()
    return (
        f'<button type="button" class="month-day has{today_class}{filtered}"{disabled} '
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
) -> str:
    today = today or date.today()
    by_day = _calendar_by_day(
        _calendar_groups(watchlist, recommendations, watched, program)
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


def _format_html_movie_row(
    m: Movie,
    known: frozenset[str],
    *,
    row_class: str = "",
    row_id: str = "",
    show_user_rating: bool = False,
    language_filter: bool = False,
    show_because: bool = True,
) -> str:
    rating_text = f"{m.rating:.2f}" if m.rating is not None else "—"
    title = html.escape(film_title(m))
    if m.note:
        note_esc = html.escape(m.note)
        title += f' <span class="note" title="{note_esc}">⚠</span>'
    directors = html.escape(_format_directors(m))
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
    show_badge = not follow and not language_filter
    hide_row = language_filter and not follow
    follow_badge = (
        '<span class="chip chip-warn follow-badge" '
        'title="No screenings in a version you can follow."'
        f'{" hidden" if not show_badge else ""}>no followable version</span>'
    )
    because_html = _because_html(m) if show_because else ""
    classes = " ".join(
        part for part in (row_class, "row-no-follow" if show_badge else "") if part
    )
    extra = f' class="{classes}"' if classes else ""
    row_hidden = " hidden" if hide_row else ""
    lang_hide = "1" if hide_row else "0"
    filter_mode = "hide" if language_filter else "keep"
    primary = html.escape(m.primary_language or "", quote=True)
    versions = html.escape(" ".join(m.versions), quote=True)
    detail_classes = " ".join(part for part in ("detail-row", row_class) if part)
    language = html.escape(format_language(m))
    search_blob = _movie_search_blob(m)
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
    sort_rating = f"{m.rating:.4f}" if m.rating is not None else ""
    sort_year = str(m.year) if m.year is not None else ""
    sort_title = html.escape(film_title(m), quote=True)
    sort_director = html.escape(", ".join(m.directors), quote=True)
    sort_genres = html.escape(", ".join(m.genres), quote=True)
    sort_language = html.escape(m.primary_language or "", quote=True)
    return (
        f'<tr data-movie-row{extra}{row_hidden}{movie_id_attr}{plan_id_attr} '
        f'data-search="{search_blob}"'
        f' data-filter="{filter_mode}" data-lang-hide="{lang_hide}"'
        f' data-primary-language="{primary}" data-versions="{versions}"'
        f' data-sort-rating="{sort_rating}"{sort_you}'
        f' data-sort-year="{sort_year}"'
        f' data-sort-title="{sort_title}"'
        f' data-sort-director="{sort_director}"'
        f' data-sort-genres="{sort_genres}"'
        f' data-sort-language="{sort_language}">'
        f'<td class="col-rating">{_rating_badge(m.rating, rating_text)}</td>'
        f"{user_cell}"
        f"<td class=\"col-year\">{year}</td>"
        f'<td class="col-title"><span class="title-line">{_plan_add_button(m)}'
        f'<button type="button" class="expand" '
        f'aria-expanded="false" aria-controls="{html.escape(detail_id, quote=True)}">'
        f"<strong>{title}</strong> {follow_badge}{because_html}</button></span></td>"
        f"<td class=\"col-director\">{directors}</td>"
        f"<td class=\"col-genres\">{genre_html}</td>"
        f"<td class=\"col-language\">{language}</td>"
        f"</tr>"
        f'<tr class="{detail_classes}" data-movie-detail hidden>'
        f'<td colspan="{columns}">{_detail_panel_html(m, detail_id, known)}</td>'
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
    language_filter: bool = False,
    show_because: bool = True,
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
            language_filter=language_filter,
            show_because=show_because,
        )
        for index, movie in enumerate(movies)
    )
    visible = [
        movie
        for movie in movies
        if not language_filter or movie_is_watchable(movie, known)
    ]
    count = len(visible)
    section_hidden = " hidden" if count == 0 else ""
    you_header = _sortable_header("You", "you", "number", extra_class="col-rating") if show_user_rating else ""
    film_word = "film" if count == 1 else "films"
    note_id = ' id="program-language-note"' if section_id == "program" else ""
    return f"""
<section class="program-section" id="{section_id}"{section_hidden}>
  <h2 class="section-head">
    <button type="button" class="section-toggle" aria-expanded="true" aria-controls="{section_id}-table">
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
              {_sortable_header("Rating", "rating", "number", sorted_dir="descending", extra_class="col-rating")}
              {you_header}
              {_sortable_header("Year", "year", "number", extra_class="col-year")}
              {_sortable_header("Film", "title", "text", extra_class="col-title")}
              {_sortable_header("Director", "director", "text", extra_class="col-director")}
              {_sortable_header("Genres", "genres", "text", extra_class="col-genres")}
              {_sortable_header("Language", "language", "text", extra_class="col-language")}
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


def _settings_html(user: str, choices: list[str], selected: list[str]) -> str:
    selected_keys = {name.casefold() for name in selected}
    boxes = []
    for name in choices:
        checked = " checked" if name.casefold() in selected_keys else ""
        boxes.append(
            '<label class="lang-option">'
            f'<input type="checkbox" name="language" value="{html.escape(name, quote=True)}"{checked}>'
            f"<span>{html.escape(name)}</span></label>"
        )
    safe_user = html.escape(user)
    safe_value = html.escape(user, quote=True)
    return (
        '<div class="settings">'
        '<button type="button" class="settings-open" id="settings-open" '
        'aria-expanded="false" aria-controls="settings-panel" '
        'title="Letterboxd profile and languages">'
        f"@{safe_user}</button>"
        '<div id="settings-panel" class="settings-panel" role="dialog" '
        'aria-label="Viewer settings" hidden>'
        '<form id="settings-form">'
        '<label class="settings-user" for="letterboxd-user">Letterboxd profile</label>'
        '<div class="settings-user-row">'
        '<span class="settings-at" aria-hidden="true">@</span>'
        f'<input id="letterboxd-user" name="letterboxd" value="{safe_value}" '
        'autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false">'
        '<button type="submit" class="settings-load">Load</button>'
        "</div>"
        '<p id="settings-status" class="settings-status" role="status"></p>'
        '<fieldset class="settings-languages">'
        "<legend>Languages you know</legend>"
        f'<div class="lang-options">{"".join(boxes)}</div>'
        '<p class="settings-hint">Original versions count for these languages. '
        "Every other film needs English subtitles.</p>"
        "</fieldset>"
        '<button type="button" id="settings-reset" class="settings-reset">Reset languages</button>'
        "</form></div></div>"
    )


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
) -> str:
    selected = list(DEFAULT_KNOWN_LANGUAGES if known_languages is None else known_languages)
    found = [
        movie.primary_language
        for movie in (*watchlist, *recommendations, *watched, *program)
        if movie.primary_language
    ]
    choices = language_choices(found, selected)
    selected = canonical_languages(selected, choices)
    known = language_keys(selected)
    user_label = f"@{letterboxd_user}"
    sections = []
    if watchlist:
        sections.append(
            _html_table_section(
                "watchlist",
                "Watchlist",
                f"On {user_label}'s Letterboxd watchlist and playing at Nonstop Wien",
                watchlist,
                known,
                row_class="row-watchlist",
            )
        )
    if recommendations:
        sections.append(
            _html_table_section(
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
        sections.append(
            _html_table_section(
                "watched",
                "Already watched",
                f"{user_label} has seen these — they're back on the program",
                watched,
                known,
                row_class="row-watched",
                show_user_rating=True,
            )
        )
    rated, unrated, unfound = split_program_groups(program)
    if rated:
        sections.append(
            _html_table_section(
                "program",
                "Full program",
                language_filter_blurb(selected),
                rated,
                known,
                language_filter=True,
                show_because=False,
            )
        )
    if unrated:
        sections.append(
            _html_table_section(
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
        sections.append(
            _html_table_section(
                "unfound",
                "Unfound",
                "No Letterboxd page could be matched to these titles",
                unfound,
                known,
                language_filter=True,
                show_because=False,
            )
        )
    sections_html = "\n".join(sections)
    calendar_html = _program_calendar_html(
        watchlist, recommendations, watched, program, known
    )
    catalog_json = _plan_catalog_json(
        [*watchlist, *recommendations, *watched, *program]
    )
    busy_json = json.dumps(
        {"intervals": busy_intervals or []},
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    status_html = ""
    if calendar_status:
        status_html = (
            f'<p class="plan-calendar-status">{html.escape(calendar_status)}</p>'
        )
    settings_html = _settings_html(letterboxd_user, choices, selected)
    viewer_json = _json_for_html(
        {
            "letterboxdUser": letterboxd_user,
            "languages": selected,
            "defaults": list(DEFAULT_KNOWN_LANGUAGES),
            "stamp": settings_stamp(letterboxd_user, selected),
            "port": serve_port,
        }
    )
    nav_html = "".join(
        (
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
        )
    )

    return (
        f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nonstop Kino — Letterboxd rankings</title>
  <style>
'''
        + (_ASSET_DIR / "page.css").read_text(encoding="utf-8")
        + f'''  </style>

</head>
<body>
  <header class="hero">
    <div class="hero-inner">
      <h1>Nonstop Kino × Letterboxd</h1>
      <p class="tagline">Wien program ranked by community ratings — find what to see next.</p>
    </div>
  </header>
  <div class="toolbar">
    <div class="toolbar-row">
      <div class="view-toggle" role="group" aria-label="How to browse the program">
        <button type="button" class="view-option" data-view="list" aria-pressed="true" title="Ranked by rating">List</button>
        <button type="button" class="view-option" data-view="calendar" aria-pressed="false" title="Films by day">Calendar</button>
        <button type="button" class="view-option" data-view="plan" aria-pressed="false" title="Best schedule for films you pick">Plan<span id="plan-count" class="plan-count" hidden></span></button>
      </div>
      {settings_html}
      <p id="plan-cap" class="plan-cap" hidden>A plan holds 12 films.</p>
      <div class="search-field">
        <input type="search" id="search" placeholder="Filter by title, director, genre…" autocomplete="off" aria-describedby="search-count">
        <span id="search-count" class="search-count" role="status" hidden></span>
        <button type="button" id="search-clear" class="search-clear" hidden aria-label="Clear search">&times;</button>
      </div>
      <nav class="nav-pills" aria-label="Program sections">
        {nav_html}
      </nav>
    </div>
  </div>
  <main>
    <div id="list-view">
{sections_html}
      <p class="empty-hint" id="list-empty" hidden>No films match this search.</p>
    </div>
    <div id="calendar-view" hidden>
{calendar_html}
      <p class="empty-hint" id="calendar-empty" hidden>No films match.</p>
    </div>
    <div id="plan-view" hidden>
      <div class="plan-board">
        {status_html}
        <p class="empty-hint" id="plan-empty">Add films from the list or calendar. A plan holds up to 12.</p>
        <div class="plan-picked" id="plan-picked" hidden>
          <div class="plan-chips" id="plan-chips"></div>
          <button type="button" id="plan-clear" class="plan-clear">Clear all</button>
        </div>
        <div class="plan-options" id="plan-options" role="group" aria-label="Schedule options" hidden></div>
        <p class="plan-summary" id="plan-summary" hidden></p>
        <p class="plan-actions" id="plan-actions" hidden>
          <button type="button" id="plan-save" class="plan-save">Add plan to calendar</button>
          <span id="plan-save-status" class="plan-save-status" role="status"></span>
        </p>
        <div class="plan-calendar" id="plan-schedule" hidden></div>
        <section class="plan-left-out" id="plan-left-out" hidden>
          <h2>Not scheduled</h2>
          <ul id="plan-left-out-list"></ul>
        </section>
      </div>
    </div>
  </main>
  <footer>
    Generated by goodmoviefinder. Ratings from Letterboxd; showtimes from Nonstop Kino Wien.
  </footer>
  <script type="application/json" id="viewer-config">{viewer_json}</script>
  <script type="application/json" id="plan-busy">{busy_json}</script>
  <script type="application/json" id="plan-catalog">{catalog_json}</script>
  <script>
'''
        + (_ASSET_DIR / "page.js").read_text(encoding="utf-8")
        + '''  </script>
  <script>
'''
        + (_ASSET_DIR / "plan.js").read_text(encoding="utf-8")
        + '''  </script>
</body>
</html>'''
    )
