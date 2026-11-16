"""The My Letterboxd page: watchlist, recommendations, and films for friends."""

from __future__ import annotations

import html
import re

from goodmoviefinder.config import DEFAULT_KNOWN_LANGUAGES
from goodmoviefinder.html import (
    _detail_hero_html,
    _external_link,
    _friend_ratings_html,
    _html_table_section,
    _rating_badge,
    _sortable_header,
)
from goodmoviefinder.layout import NONSTOP_HREF, render_page, sign_in_card
from goodmoviefinder.letterboxd import letterboxd_backdrop_url, letterboxd_poster_url
from goodmoviefinder.models import Movie
from goodmoviefinder.program import format_screening_date, letterboxd_slug_from_url
from goodmoviefinder.viewer import canonical_languages, settings_stamp


# Splits service names on a watchlist row. Names from JustWatch never contain it.
_PLATFORM_SEP = "\x1f"
_PLATFORM_KIND_ORDER = ("subscription", "free", "with ads", "rent", "buy")
_PLATFORM_KIND_LABEL = {
    "subscription": "Subscription",
    "free": "Free",
    "with ads": "With ads",
    "rent": "Rent",
    "buy": "Buy",
}


def _offer_names(offers: list[object]) -> list[tuple[str, str]]:
    """Unique streaming services on one film, with how each one is offered."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        name = offer.get("name")
        if not isinstance(name, str):
            continue
        name = name.strip()
        url = offer.get("url")
        if not name or _PLATFORM_SEP in name or name in seen:
            continue
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            continue
        seen.add(name)
        kind = offer.get("kind")
        kind_name = kind.strip() if isinstance(kind, str) else ""
        found.append((name, kind_name))
    return found


def _platform_filter_html(films: list[dict[str, object]]) -> str:
    """A menu of services that actually appear on this watchlist."""
    kind_counts: dict[str, dict[str, int]] = {}
    for film in films:
        raw = film.get("offers")
        offers = raw if isinstance(raw, list) else []
        for name, kind in _offer_names(offers):
            bucket = kind if kind in _PLATFORM_KIND_LABEL else ""
            per_kind = kind_counts.setdefault(name, {})
            per_kind[bucket] = per_kind.get(bucket, 0) + 1
    if not kind_counts:
        return ""
    grouped: dict[str, list[tuple[str, int]]] = {}
    for name, per_kind in kind_counts.items():
        total = sum(per_kind.values())
        kind = min(
            per_kind,
            key=lambda item: (
                -per_kind[item],
                _PLATFORM_KIND_ORDER.index(item)
                if item in _PLATFORM_KIND_ORDER
                else len(_PLATFORM_KIND_ORDER),
            ),
        )
        grouped.setdefault(kind, []).append((name, total))
    options = ['<option value="">Any platform</option>']
    kind_order = [kind for kind in _PLATFORM_KIND_ORDER if grouped.get(kind)]
    if grouped.get(""):
        kind_order.append("")
    for kind in kind_order:
        rows = sorted(grouped[kind], key=lambda item: (-item[1], item[0].casefold()))
        choices = "".join(
            f'<option value="{html.escape(name, quote=True)}">'
            f"{html.escape(name)} ({count})</option>"
            for name, count in rows
        )
        label = _PLATFORM_KIND_LABEL.get(kind, "Other")
        options.append(
            f'<optgroup label="{html.escape(label, quote=True)}">{choices}</optgroup>'
        )
    return (
        '<label class="platform-filter">'
        '<span class="visually-hidden">Streaming platform</span>'
        '<select id="wl-platform">' + "".join(options) + "</select></label>"
    )


def _stream_offer_html(offer: object) -> str:
    if not isinstance(offer, dict):
        return ""
    name = offer.get("name")
    url = offer.get("url")
    if not isinstance(name, str) or not name:
        return ""
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        return ""
    kind = offer.get("kind")
    label = f"{name}, {kind}" if isinstance(kind, str) and kind else name
    safe_label = html.escape(label, quote=True)
    icon = offer.get("icon")
    if isinstance(icon, str) and icon.startswith("https://images.justwatch.com/"):
        inner = f'<img src="{html.escape(icon, quote=True)}" alt="">'
        css = "stream-offer"
    else:
        inner = html.escape(name)
        css = "stream-offer stream-offer-text"
    return (
        f'<a class="{css}" href="{html.escape(url, quote=True)}" '
        f'target="_blank" rel="noopener noreferrer" '
        f'aria-label="{safe_label}" title="{safe_label}">{inner}</a>'
    )


def _stream_rating(film: dict[str, object]) -> float | None:
    rating = film.get("rating")
    if isinstance(rating, bool) or not isinstance(rating, (int, float)):
        return None
    return float(rating)


def _availability_markup(
    film: dict[str, object] | None,
    next_day: str | None,
) -> str:
    """Services, a Nonstop date, or an explicit unchecked state."""
    offers: list[object] = []
    checked = False
    if isinstance(film, dict):
        raw = film.get("offers")
        offers = raw if isinstance(raw, list) else []
        checked = bool(film.get("checked"))
    offer_html = "".join(_stream_offer_html(offer) for offer in offers)
    parts: list[str] = []
    if offer_html:
        parts.append(f'<span class="stream-offers">{offer_html}</span>')
    elif checked:
        parts.append('<span class="where-note">Not streaming in Austria</span>')
    else:
        parts.append('<span class="where-note">Could not check</span>')
    if next_day:
        parts.append(
            f'<span class="where-note">Nonstop · {html.escape(format_screening_date(next_day))}</span>'
        )
    return f'<p class="where-line">{"".join(parts)}</p>'


def _stream_detail_html(film: dict[str, object], detail_id: str, title: str, film_url: str, next_day: str | None = None) -> str:
    poster_url = letterboxd_poster_url(film.get("poster"))
    poster = ""
    if poster_url:
        poster = (
            f'<img class="poster" src="{html.escape(poster_url, quote=True)}" '
            f'alt="{html.escape(title, quote=True)}">'
        )
    slug = str(film.get("slug") or "")
    hero = _detail_hero_html(
        letterboxd_backdrop_url(film.get("backdrop")),
        slug or None,
    )
    synopsis_text = film.get("synopsis")
    if film.get("details_known"):
        if isinstance(synopsis_text, str) and synopsis_text.strip():
            synopsis = f'<p class="synopsis">{html.escape(synopsis_text)}</p>'
        else:
            synopsis = '<p class="synopsis muted">No synopsis available.</p>'
    else:
        synopsis = '<p class="synopsis muted">Letterboxd details could not be loaded.</p>'
    letterboxd = _external_link(film_url, html.escape(title), "link-lb")
    friends = _friend_ratings_html(str(film.get("slug") or ""))
    facts: list[str] = []
    year = film.get("year")
    if isinstance(year, int):
        facts.append(str(year))
    directors = film.get("directors")
    if isinstance(directors, list):
        names = ", ".join(str(name) for name in directors if str(name).strip())
        if names:
            facts.append(html.escape(names))
    facts_html = (
        '<p class="detail-facts">' + "".join(f"<span>{part}</span>" for part in facts) + "</p>"
        if facts
        else ""
    )
    return (
        f'<div class="detail panel panel-inset" id="{html.escape(detail_id, quote=True)}">'
        f"{hero}"
        f'<div class="detail-main">'
        f"{poster}"
        f'<div class="detail-body">'
        f'<div class="detail-about">{facts_html}{synopsis}{_availability_markup(film, next_day)}'
        f'<p class="detail-links">{letterboxd}</p></div>'
        f'<div class="detail-columns">{friends}</div>'
        f"</div></div></div>"
    )


def _watch_sort_key(
    film: dict[str, object],
    nonstop_next: dict[str, str],
) -> tuple[int, str, str]:
    slug = str(film.get("slug") or "")
    title = str(film.get("title") or slug).casefold()
    offers = film.get("offers")
    has_offers = isinstance(offers, list) and bool(offers)
    if nonstop_next.get(slug):
        return (0, nonstop_next[slug], title)
    if has_offers:
        return (1, "", title)
    return (2, "", title)


def _watchlist_html(
    films: list[dict[str, object]],
    nonstop_next: dict[str, str],
) -> tuple[str, int, int]:
    """Watchlist table, plus how many films can stream and how many play at Nonstop."""
    rows: list[str] = []
    streamable = 0
    at_nonstop = 0
    ordered = sorted(films, key=lambda film: _watch_sort_key(film, nonstop_next))
    for index, film in enumerate(ordered):
        slug = str(film.get("slug") or "")
        title = str(film.get("title") or slug)
        year = film.get("year")
        year_html = html.escape(str(year)) if isinstance(year, int) else "—"
        sort_year = str(year) if isinstance(year, int) else ""
        film_url = "https://letterboxd.com/film/" + html.escape(slug, quote=True) + "/"
        search = html.escape(title.lower(), quote=True)
        rating = _stream_rating(film)
        rating_text = f"{rating:.2f}" if rating is not None else "—"
        sort_rating = f"{rating:.4f}" if rating is not None else ""
        raw_offers = film.get("offers")
        offers = raw_offers if isinstance(raw_offers, list) else []
        offer_names = _offer_names(offers)
        platform_attr = ""
        if offer_names:
            joined = _PLATFORM_SEP.join(name for name, _kind in offer_names)
            platform_attr = f' data-platforms="{html.escape(joined, quote=True)}"'
        offer_html = "".join(_stream_offer_html(offer) for offer in offers)
        if offer_html:
            streamable += 1
            services = f'<div class="stream-offers">{offer_html}</div>'
        elif film.get("checked"):
            services = '<span class="stream-missing">Not available to stream in Austria.</span>'
        else:
            services = '<span class="stream-missing">Streaming availability could not be checked.</span>'
        next_day = nonstop_next.get(slug)
        if next_day:
            at_nonstop += 1
            nonstop = (
                f'<a class="nonstop-badge" href="{NONSTOP_HREF}#list" '
                f'title="Playing at Nonstop Kino Wien">'
                f"Nonstop &middot; {html.escape(format_screening_date(next_day))}</a>"
            )
        else:
            nonstop = '<span class="stream-missing">—</span>'
        detail_id = f"watchlist-{index}-detail"
        safe_slug = html.escape(slug, quote=True)
        sort_title = html.escape(title, quote=True)
        poster_url = letterboxd_poster_url(film.get("poster"))
        row_poster = ""
        if poster_url:
            row_poster = (
                f'<img class="row-poster" src="{html.escape(poster_url, quote=True)}" '
                f'alt="" width="32" height="48">'
            )
        rows.append(
            f'<tr data-stream-row class="row-watchlist" data-stream-id="{safe_slug}" data-search="{search}"'
            f' data-streamable="{1 if offer_html else 0}" data-nonstop="{1 if next_day else 0}"'
            f"{platform_attr}"
            f' data-sort-rating="{sort_rating}" data-sort-year="{sort_year}"'
            f' data-sort-title="{sort_title}">'
            f'<td class="col-rating">{_rating_badge(rating, rating_text, title="Letterboxd community score. This watchlist was read separately from the Nonstop program, so the number can differ slightly.")}</td>'
            f'<td class="col-year">{year_html}</td>'
            f'<td class="col-title"><span class="title-line">{row_poster}<button type="button" class="expand" '
            f'aria-expanded="false" aria-controls="{html.escape(detail_id, quote=True)}">'
            f"<strong>{html.escape(title)}</strong></button>"
            f'<span class="row-chevron" aria-hidden="true"></span></span></td>'
            f'<td class="col-stream">{services}</td>'
            f'<td class="col-nonstop">{nonstop}</td></tr>'
            f'<tr class="detail-row" data-stream-detail hidden>'
            f'<td colspan="5">{_stream_detail_html(film, detail_id, title, film_url, next_day)}</td>'
            f"</tr>"
        )
    table = (
        '<div class="table-wrap panel">'
        "<table>"
        "<thead><tr>"
        + _sortable_header(
            "Rating",
            "rating",
            "number",
            extra_class="col-rating",
            title="Letterboxd community score. This watchlist was read separately from the Nonstop program, so the number can differ slightly.",
        )
        + _sortable_header("Year", "year", "number", extra_class="col-year")
        + _sortable_header("Film", "title", "text", extra_class="col-title")
        + "<th>Streaming in Austria</th><th>Nonstop</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )
    return table, streamable, at_nonstop


def _watchlist_panel(
    user: str,
    films: list[dict[str, object]] | None,
    nonstop_next: dict[str, str],
) -> tuple[str, int, int, int]:
    if films is None:
        return '<p class="stream-note">No Letterboxd watchlist is loaded.</p>', 0, 0, 0
    if not films:
        return '<p class="stream-note">Nothing is on this Letterboxd watchlist.</p>', 0, 0, 0
    table, streamable, at_nonstop = _watchlist_html(films, nonstop_next)
    chips = (
        '<div class="filter-chips" role="group" aria-label="Filter the watchlist">'
        f'<button type="button" class="btn filter-chip" data-wl-filter="all" aria-pressed="true">All ({len(films)})</button>'
        f'<button type="button" class="btn filter-chip" data-wl-filter="stream" aria-pressed="false">Streaming in Austria ({streamable})</button>'
        f'<button type="button" class="btn filter-chip" data-wl-filter="nonstop" aria-pressed="false">Playing at Nonstop ({at_nonstop})</button>'
        f"{_platform_filter_html(films)}"
        "</div>"
    )
    body = (
        f'<div class="stream-summary">{chips}</div>'
        f"{table}"
        '<p class="empty-hint" id="stream-empty" hidden>No watchlist films match.</p>'
    )
    return body, len(films), streamable, at_nonstop


_RATING_CHECK = re.compile(
    r"When the rating model predicted you would like a film, you had rated it "
    r"4 or higher (\d+)% of the time \((\d+) films\)\."
)
_MEMBER_CHECK = re.compile(
    r"When members like you pointed at a film, you had rated it "
    r"4 or higher (\d+)% of the time \((\d+) films\)\."
)
_RATING_UNCHECKED = "The rating model did not make enough held-out predictions to check."
_MEMBER_UNCHECKED = "The member model did not make enough held-out predictions to check."
_MEMBER_MISSING = "Similar members were not available."
_NOT_CONFIDENT = "That is not confident enough to recommend a film."
_UNDER_BAR = (
    "That check is under the usual bar. These are still the strongest predictions from your ratings."
)
_RATINGS_ONLY = "These picks use your ratings only."


def _film_count(raw: str) -> str:
    count = int(raw)
    return "1 film" if count == 1 else f"{count} films"


def _taste_readout(note: str) -> tuple[list[tuple[str, str, str, str]] | None, list[str]]:
    """Stat tiles and short caveats. None means the note should stay as prose."""
    text = note.strip()
    if not text:
        return [], []
    rating = _RATING_CHECK.search(text)
    member = _MEMBER_CHECK.search(text)
    known = bool(
        rating
        or member
        or _RATING_UNCHECKED in text
        or _MEMBER_UNCHECKED in text
        or _MEMBER_MISSING in text
    )
    if not known:
        return None, []
    stats: list[tuple[str, str, str, str]] = []
    if rating:
        stats.append(
            (
                "Your ratings",
                f"{rating.group(1)}%",
                "of predictions you rated 4 or higher",
                _film_count(rating.group(2)),
            )
        )
    elif _RATING_UNCHECKED in text:
        stats.append(("Your ratings", "—", "Not enough films to check", ""))
    if member:
        stats.append(
            (
                "Similar members",
                f"{member.group(1)}%",
                "of their picks you rated 4 or higher",
                _film_count(member.group(2)),
            )
        )
    elif _MEMBER_MISSING in text:
        stats.append(("Similar members", "—", "Not available", ""))
    elif _MEMBER_UNCHECKED in text:
        stats.append(("Similar members", "—", "Not enough overlap to check", ""))
    caveats: list[str] = []
    if _NOT_CONFIDENT in text:
        caveats.append("Not confident enough to recommend a film.")
    elif _UNDER_BAR in text:
        caveats.append("Under the usual bar. These are still the strongest predictions from your ratings.")
    if _RATINGS_ONLY in text:
        caveats.append(_RATINGS_ONLY)
    return stats, caveats


def _taste_group(kind: str, label: str, phrases: list[str]) -> str:
    items = "".join(f"<li>{html.escape(phrase)}</li>" for phrase in phrases)
    return (
        f'<div class="taste-group taste-group-{kind}">'
        f'<p class="taste-label">{html.escape(label)}</p>'
        f'<ul class="taste-list">{items}</ul>'
        "</div>"
    )


def _confidence_sentence(note: str) -> str:
    """One sentence for the model check. The films themselves stay above this."""
    stats, caveats = _taste_readout(note)
    if stats is None:
        return note.strip()
    parts: list[str] = []
    folded = ("not confident enough", "under the usual bar")
    if any("not confident enough" in item.lower() for item in caveats):
        parts.append("Not confident enough to recommend a film.")
    elif any("under the usual bar" in item.lower() for item in caveats):
        parts.append("Under the usual bar.")
    for label, value, caption, _count in stats:
        if value == "—":
            lowered = caption[:1].lower() + caption[1:] if caption else caption
            parts.append(f"{label}: {lowered}.")
            continue
        if label == "Your ratings":
            parts.append(f"You rated earlier predictions 4 or higher {value} of the time.")
        elif label == "Similar members":
            parts.append(f"You rated similar members' picks 4 or higher {value} of the time.")
    for item in caveats:
        if any(phrase in item.lower() for phrase in folded):
            continue
        parts.append(item if item.endswith(".") else f"{item}.")
    return " ".join(parts)


def _taste_intro_html(
    paragraph: str,
    note: str,
    loved: list[str],
    lower: list[str],
    *,
    loved_are_themes: bool,
) -> str:
    parts: list[str] = [
        '<details class="taste-intro" id="taste">',
        "<summary>Your taste</summary>",
        '<p class="taste-scope">From across Letterboxd, not only what is playing now.</p>',
    ]
    sentence = _confidence_sentence(note)
    if sentence:
        parts.append(f'<p class="taste-sentence">{html.escape(sentence)}</p>')
    elif paragraph:
        parts.append(f'<p class="taste-lead">{html.escape(paragraph)}</p>')
    if loved or lower:
        groups: list[str] = []
        if loved:
            label = "Rated highly" if loved_are_themes else "You tend to rate highly"
            groups.append(_taste_group("loved", label, loved))
        if lower:
            groups.append(_taste_group("lower", "Sits lower", lower))
        single = " is-single" if len(groups) == 1 else ""
        parts.append(f'<div class="taste-groups{single}">{"".join(groups)}</div>')
    parts.append("</details>")
    return "".join(parts)


def format_taste_panel_html(
    *,
    see_first: list[Movie],
    like_you: list[Movie],
    step_outside: list[Movie],
    paragraph: str,
    note: str,
    loved: list[str] | None = None,
    lower: list[str] | None = None,
    loved_are_themes: bool = False,
    availability: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Recommendations tab body and nav-pill links (without the Jump to label)."""
    known: frozenset[str] = frozenset()
    score_line = (
        "Rating is Letterboxd's community score. "
        "For you is the score this model expects from you."
    )
    blocks: list[tuple[str, str, str, list[Movie], str, bool]] = [
        ("see-first", "See this first", score_line, see_first, "row-recommend", False),
        (
            "like-you",
            "More like what you love",
            "Close to directors, themes, and countries you rate highly",
            like_you,
            "row-recommend",
            False,
        ),
        (
            "step-outside",
            "A step outside",
            "A nanogenre you have liked only once or twice",
            step_outside,
            "row-outside",
            bool(see_first or like_you),
        ),
    ]
    primary: list[str] = []
    outside: list[str] = []
    for section_id, title, sub, movies, row_class, folded in blocks:
        if not movies:
            continue
        if section_id == "like-you" and not see_first:
            sub = score_line
        table = _html_table_section(
            section_id,
            title,
            sub,
            movies,
            known,
            row_class=row_class,
            show_predicted=True,
            show_follow_badge=False,
            show_because=False,
            because_in_detail=True,
            availability=availability,
            start_folded=folded,
        )
        if section_id == "step-outside":
            outside.append(table)
        else:
            primary.append(table)
    taste = _taste_intro_html(
        paragraph,
        note,
        list(loved or []),
        list(lower or []),
        loved_are_themes=loved_are_themes,
    )
    sections = primary + [taste] + outside
    if not primary and not outside:
        sections.append('<p class="empty-hint">No film cleared the bar.</p>')
    panel = "\n".join(sections) + (
        '\n<p class="empty-hint" id="list-empty" hidden>No films match this search.</p>'
    )
    pills = "".join(
        f'<a href="#{sid}" data-nav="{sid}">{label}</a>'
        for sid, label, movies in (
            ("see-first", "See this first", see_first),
            ("like-you", "More like what you love", like_you),
            ("step-outside", "A step outside", step_outside),
        )
        if movies
    )
    return panel, pills


def format_account_html(
    *,
    letterboxd_user: str,
    signed_in: bool,
    stream_films: list[dict[str, object]] | None,
    nonstop_next: dict[str, str],
    see_first: list[Movie],
    like_you: list[Movie],
    step_outside: list[Movie],
    paragraph: str,
    note: str,
    loved: list[str] | None = None,
    lower: list[str] | None = None,
    loved_are_themes: bool = False,
    updated_label: str = "",
    known_languages: list[str] | None = None,
    language_options: list[str] | None = None,
    following_user: str | None = None,
    letterboxd_avatar: str = "",
    vm: bool = False,
    program_read_label: str = "",
    recommendation_streams: list[dict[str, object]] | None = None,
) -> str:
    """The account page. Signed out, each tab asks for a Letterboxd username."""
    selected = list(DEFAULT_KNOWN_LANGUAGES if known_languages is None else known_languages)
    choices = list(language_options or selected)
    selected = canonical_languages(selected, choices)
    stats: list[str] = []
    friends_label = "Friends"
    if not signed_in:
        watchlist_panel = sign_in_card()
        taste_panel = sign_in_card()
        friends_panel = sign_in_card()
        watch_label, taste_label = "Watchlist", "Recommendations"
        tagline = "Sign in with a Letterboxd username to see your watchlist and recommendations."
        tools_html = ""
    else:
        body, _count, _streamable, _at_nonstop = _watchlist_panel(
            letterboxd_user, stream_films, nonstop_next
        )
        watchlist_panel = body
        watch_label, taste_label = "Watchlist", "Recommendations"
        availability: dict[str, str] | None = None
        if see_first or like_you or step_outside:
            streams = {
                str(row.get("slug") or ""): row
                for row in (recommendation_streams or [])
                if isinstance(row, dict)
            }
            availability = {}
            for movie in (*see_first, *like_you, *step_outside):
                slug = (
                    letterboxd_slug_from_url(movie.letterboxd_url)
                    if movie.letterboxd_url
                    else ""
                )
                if not slug or slug in availability:
                    continue
                record = streams.get(slug)
                film = record if isinstance(record, dict) else {"checked": False, "offers": []}
                availability[slug] = _availability_markup(film, nonstop_next.get(slug))
        taste_panel, pills = format_taste_panel_html(
            see_first=see_first,
            like_you=like_you,
            step_outside=step_outside,
            paragraph=paragraph,
            note=note,
            loved=loved,
            lower=lower,
            loved_are_themes=loved_are_themes,
            availability=availability,
        )
        tagline = (
            f"@{letterboxd_user}'s Letterboxd watchlist, films picked for you, "
            "and films for friends."
        )
        friends_panel = (
            '<p class="gift-board-status muted" id="gift-board-status" role="status">'
            "Loading the lists…</p>\n"
            '    <div id="gift-board"></div>\n'
            '    <p class="empty-hint" id="gift-empty" hidden>No films match.</p>'
        )
        refresh_button = ""
        if not vm:
            refresh_button = (
                '        <button type="button" id="taste-refresh" class="taste-refresh">'
                "Refresh recommendations</button>\n"
            )
        tools_html = (
            '      <button type="button" class="btn lang-chip" data-open-languages '
            'aria-expanded="false" aria-controls="languages-panel" aria-haspopup="dialog" '
            'title="Change the languages you follow">Languages: '
            f'<strong id="lang-summary">{html.escape(", ".join(selected) or "English subtitles only")}</strong>'
            "</button>\n"
            '      <div class="search-field" data-tools="watchlist recommendations for-friends">\n'
            '        <input type="search" id="search" placeholder="Filter your watchlist by title…" '
            'autocomplete="off" aria-describedby="search-count">\n'
            '        <span id="search-count" class="search-count" role="status" aria-live="polite" hidden></span>\n'
            '        <button type="button" id="search-clear" class="search-clear" hidden '
            'aria-label="Clear search">&times;</button>\n'
            "      </div>\n"
            '      <div class="taste-refresh-bar" data-tools="recommendations">\n'
            f"{refresh_button}"
            f'        <span class="taste-updated" id="taste-updated"'
            f'{" hidden" if not updated_label else ""}>'
            f"{html.escape(updated_label)}</span>\n"
            '        <p class="taste-refresh-status" id="taste-refresh-status" role="status" hidden></p>\n'
            "      </div>\n"
            '      <nav class="nav-pills" aria-label="Recommendation sections" data-tools="recommendations" '
            'id="recommendations-nav">'
            f'<span class="nav-pills-label">Jump to</span>{pills}</nav>'
        )
    panels_html = (
        '    <div id="watchlist-view" role="tabpanel" aria-labelledby="tab-watchlist">\n'
        f"{watchlist_panel}\n    </div>\n"
        '    <div id="recommendations-view" role="tabpanel" aria-labelledby="tab-recommendations" hidden>\n'
        f"{taste_panel}\n    </div>\n"
        '    <div id="for-friends-view" role="tabpanel" aria-labelledby="tab-for-friends" hidden>\n'
        f"{friends_panel}\n    </div>"
    )
    return render_page(
        page="account",
        title="My Letterboxd — watchlist, recommendations, and friends",
        heading="My Letterboxd",
        tagline=tagline,
        stats=stats,
        hint_html="",
        tabs=[
            ("watchlist", watch_label),
            ("recommendations", taste_label),
            ("for-friends", friends_label),
        ],
        tools_html=tools_html,
        panels_html=panels_html,
        user=letterboxd_user,
        signed_in=signed_in,
        choices=choices,
        selected=selected,
        viewer={
            "stamp": settings_stamp(letterboxd_user, selected),
            "followingUser": following_user or letterboxd_user,
            "letterboxdAvatar": letterboxd_avatar,
            "vm": vm,
        },
        scripts=("page.js", "shell.js"),
        styles=("base.css", "account.css"),
        program_read_label=program_read_label,
        footer=(
            "Generated by goodmoviefinder. Ratings and themes from Letterboxd. "
            "Streaming in Austria from JustWatch."
        ),
    )
