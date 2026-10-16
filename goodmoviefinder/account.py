"""The My Letterboxd page: watchlist with streaming, and recommendations."""

from __future__ import annotations

import html

from goodmoviefinder.config import DEFAULT_KNOWN_LANGUAGES
from goodmoviefinder.html import (
    _external_link,
    _friend_ratings_html,
    _html_table_section,
    _rating_badge,
    _sortable_header,
)
from goodmoviefinder.layout import NONSTOP_HREF, render_page, sign_in_card
from goodmoviefinder.letterboxd import letterboxd_poster_url
from goodmoviefinder.models import Movie
from goodmoviefinder.program import format_screening_date
from goodmoviefinder.viewer import canonical_languages, settings_stamp


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


def _stream_detail_html(film: dict[str, object], detail_id: str, title: str, film_url: str) -> str:
    poster_url = letterboxd_poster_url(film.get("poster"))
    poster = ""
    if poster_url:
        poster = (
            f'<img class="poster" src="{html.escape(poster_url, quote=True)}" '
            f'alt="{html.escape(title, quote=True)}">'
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
    return (
        f'<div class="detail panel panel-inset" id="{html.escape(detail_id, quote=True)}">'
        f"{poster}"
        f'<div class="detail-body">{synopsis}'
        f'<p class="detail-links">{letterboxd}</p>{friends}</div></div>'
    )


def _watchlist_html(
    films: list[dict[str, object]],
    nonstop_next: dict[str, str],
) -> tuple[str, int, int]:
    """Watchlist table, plus how many films can stream and how many play at Nonstop."""
    rows: list[str] = []
    streamable = 0
    at_nonstop = 0
    for index, film in enumerate(films):
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
        rows.append(
            f'<tr data-stream-row class="row-watchlist" data-stream-id="{safe_slug}" data-search="{search}"'
            f' data-streamable="{1 if offer_html else 0}" data-nonstop="{1 if next_day else 0}"'
            f' data-sort-rating="{sort_rating}" data-sort-year="{sort_year}"'
            f' data-sort-title="{sort_title}">'
            f'<td class="col-rating">{_rating_badge(rating, rating_text, title="Letterboxd rating")}</td>'
            f'<td class="col-year">{year_html}</td>'
            f'<td class="col-title"><span class="title-line"><button type="button" class="expand" '
            f'aria-expanded="false" aria-controls="{html.escape(detail_id, quote=True)}">'
            f"<strong>{html.escape(title)}</strong></button>"
            f'<span class="row-chevron" aria-hidden="true"></span></span></td>'
            f'<td class="col-stream">{services}</td>'
            f'<td class="col-nonstop">{nonstop}</td></tr>'
            f'<tr class="detail-row" data-stream-detail hidden>'
            f'<td colspan="5">{_stream_detail_html(film, detail_id, title, film_url)}</td>'
            f"</tr>"
        )
    table = (
        '<div class="table-wrap panel">'
        "<table>"
        "<thead><tr>"
        + _sortable_header("Rating", "rating", "number", extra_class="col-rating")
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
        "</div>"
    )
    body = (
        f'<div class="stream-summary">{chips}</div>'
        f"{table}"
        '<p class="empty-hint" id="stream-empty" hidden>No watchlist films match.</p>'
    )
    return body, len(films), streamable, at_nonstop


def format_taste_panel_html(
    *,
    see_first: list[Movie],
    like_you: list[Movie],
    step_outside: list[Movie],
    paragraph: str,
    note: str,
) -> tuple[str, str]:
    """Recommendations tab body and nav-pill links (without the Jump to label)."""
    known: frozenset[str] = frozenset()
    sections: list[str] = [
        '<section class="taste-intro" id="taste">'
        '<p class="taste-kicker">Your taste</p>'
        f'<p class="taste-lead">{html.escape(paragraph)}</p>'
        f'<p class="taste-note">{html.escape(note)} '
        "Films from across Letterboxd, not only what is playing now.</p>"
        "</section>"
    ]
    for section_id, title, sub, movies, row_class in (
        ("see-first", "See this first", "The strongest prediction that clears the bar", see_first, "row-recommend"),
        (
            "like-you",
            "More like what you love",
            "Close to directors, themes, and countries you rate highly",
            like_you,
            "row-recommend",
        ),
        (
            "step-outside",
            "A step outside",
            "A nanogenre you have liked only once or twice",
            step_outside,
            "row-outside",
        ),
    ):
        if movies:
            sections.append(
                _html_table_section(
                    section_id,
                    title,
                    sub,
                    movies,
                    known,
                    row_class=row_class,
                    show_predicted=True,
                    show_follow_badge=False,
                )
            )
    if len(sections) == 1:
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
    known_languages: list[str] | None = None,
    language_options: list[str] | None = None,
    following_user: str | None = None,
) -> str:
    """The account page. Signed out, both tabs ask for a Letterboxd username."""
    selected = list(DEFAULT_KNOWN_LANGUAGES if known_languages is None else known_languages)
    choices = list(language_options or selected)
    selected = canonical_languages(selected, choices)
    stats: list[str] = []
    if not signed_in:
        watchlist_panel = sign_in_card()
        taste_panel = sign_in_card()
        watch_label, taste_label = "Watchlist", "Recommendations"
        tagline = "Sign in with a Letterboxd username to see your watchlist and recommendations."
        tools_html = ""
    else:
        body, count, streamable, at_nonstop = _watchlist_panel(
            letterboxd_user, stream_films, nonstop_next
        )
        watchlist_panel = body
        watch_label = f"Watchlist ({count})" if count else "Watchlist"
        taste_label = "Recommendations"
        taste_panel, pills = format_taste_panel_html(
            see_first=see_first,
            like_you=like_you,
            step_outside=step_outside,
            paragraph=paragraph,
            note=note,
        )
        tagline = f"@{letterboxd_user}'s Letterboxd watchlist, and films picked for you."
        stats = [
            f"<strong>{count}</strong> on your watchlist",
            f"<strong>{streamable}</strong> streaming in Austria",
            f"<strong>{at_nonstop}</strong> playing at Nonstop",
        ]
        tools_html = (
            '      <div class="search-field" data-tools="watchlist recommendations">\n'
            '        <input type="search" id="search" placeholder="Filter your watchlist by title…" '
            'autocomplete="off" aria-describedby="search-count">\n'
            '        <span id="search-count" class="search-count" role="status" aria-live="polite" hidden></span>\n'
            '        <button type="button" id="search-clear" class="search-clear" hidden '
            'aria-label="Clear search">&times;</button>\n'
            "      </div>\n"
            '      <div class="taste-refresh-bar" data-tools="recommendations">\n'
            '        <button type="button" id="taste-refresh" class="taste-refresh">'
            "Refresh recommendations</button>\n"
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
        f"{taste_panel}\n    </div>"
    )
    return render_page(
        page="account",
        title="My Letterboxd — watchlist and recommendations",
        heading="My Letterboxd",
        tagline=tagline,
        stats=stats,
        hint_html="",
        tabs=[("watchlist", watch_label), ("recommendations", taste_label)],
        tools_html=tools_html,
        panels_html=panels_html,
        user=letterboxd_user,
        signed_in=signed_in,
        choices=choices,
        selected=selected,
        viewer={
            "stamp": settings_stamp(letterboxd_user, selected),
            "followingUser": following_user or letterboxd_user,
        },
        scripts=("page.js", "shell.js"),
        styles=("base.css", "account.css"),
        footer=(
            "Generated by goodmoviefinder. Ratings and themes from Letterboxd. "
            "Streaming in Austria from JustWatch."
        ),
    )
