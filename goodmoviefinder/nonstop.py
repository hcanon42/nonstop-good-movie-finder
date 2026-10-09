"""Parse Nonstop Kino program and film pages."""

from __future__ import annotations

import html
import re
import urllib.parse
from typing import Any

from goodmoviefinder.matching import normalize_title_chars
from goodmoviefinder.models import NonstopFilmPage, ProgramFilm, Screening

NONSTOP_LINK_QUERY = "weekday=all&time=all&location=wien"

PROGRAM_ARTICLE_RE = re.compile(
    r'<article class="event".*?</article>',
    re.IGNORECASE | re.DOTALL,
)


def nonstop_display_url(url: str) -> str:
    """Nonstop links in output include default program filters."""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{NONSTOP_LINK_QUERY}"


def slug_from_nonstop_url(nonstop_url: str) -> str:
    path = urllib.parse.urlparse(nonstop_url).path.rstrip("/")
    slug = path.split("/")[-1]
    slug = re.sub(r"-\d+$", "", slug)
    return slug


def _plain_chunk(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", "", fragment)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _parse_clock_time(article: str) -> str:
    normal = re.search(
        r'<div class="normal">\s*(.*?)\s*</div>',
        article,
        re.IGNORECASE | re.DOTALL,
    )
    blob = normal.group(1) if normal else ""
    match = re.search(r"(\d{1,2}:\d{2})", blob)
    return match.group(1) if match else ""


def screening_in_wien(screening: Screening) -> bool:
    return screening.city.casefold().strip() == "wien"


def _parse_venue_city(article: str) -> tuple[str, str]:
    match = re.search(
        r'<div class="location">\s*(.*?)\s*</div>',
        article,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return "", ""
    parts = re.split(r"<br\s*/?>", match.group(1), maxsplit=1, flags=re.IGNORECASE)
    venue = _plain_chunk(parts[0])
    city = _plain_chunk(parts[1]) if len(parts) > 1 else ""
    return venue, city


def _parse_poster_url(article: str) -> str | None:
    match = re.search(r'<img[^>]+src="([^"]+)"', article, re.IGNORECASE)
    if not match:
        return None
    url = html.unescape(match.group(1)).strip()
    return url or None


def parse_screening(article: str) -> Screening | None:
    weekday_match = re.search(r'data-weekday="(\d{4}-\d{2}-\d{2})"', article)
    if not weekday_match:
        return None
    venue_match = re.search(r'data-venue="([^"]*)"', article)
    language_match = re.search(r'data-language="([^"]*)"', article)
    venue_slug = venue_match.group(1).strip() if venue_match else ""
    language = language_match.group(1).strip() if language_match else ""
    venue_name, city = _parse_venue_city(article)
    if not venue_name and venue_slug:
        venue_name = venue_slug.replace("-", " ").title()
    return Screening(
        weekday=weekday_match.group(1),
        time=_parse_clock_time(article),
        language=language,
        venue_slug=venue_slug,
        venue_name=venue_name,
        city=city,
    )


def parse_program(html_text: str) -> list[ProgramFilm]:
    films: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for article in PROGRAM_ARTICLE_RE.findall(html_text):
        url_match = re.search(
            r'href="(https://nonstopkino\.at/en/movies/[^"]+)"',
            article,
        )
        if not url_match:
            continue
        url = url_match.group(1)
        title_match = re.search(r"<h3>\s*(.*?)\s*</h3>", article, re.DOTALL)
        title = url
        if title_match:
            title = re.sub(r"\s+", " ", html.unescape(title_match.group(1))).strip()
        if url not in films:
            films[url] = {
                "title": title,
                "versions": set(),
                "poster_url": _parse_poster_url(article),
                "screenings": [],
                "seen": set(),
            }
            order.append(url)
        entry = films[url]
        screening = parse_screening(article)
        if screening is None or not screening_in_wien(screening):
            continue
        if screening.language:
            entry["versions"].add(screening.language)
        key = (
            screening.weekday,
            screening.time,
            screening.venue_slug,
            screening.language,
        )
        if key not in entry["seen"]:
            entry["seen"].add(key)
            entry["screenings"].append(screening)
        if entry["poster_url"] is None:
            entry["poster_url"] = _parse_poster_url(article)
    programs: list[ProgramFilm] = []
    for url in order:
        entry = films[url]
        screenings = sorted(
            entry["screenings"],
            key=lambda s: (s.weekday, s.time, s.venue_name, s.language),
        )
        if not screenings:
            continue
        programs.append(
            ProgramFilm(
                title=entry["title"],
                url=url,
                versions=frozenset(entry["versions"]),
                poster_url=entry["poster_url"],
                screenings=screenings,
            )
        )
    return programs


def parse_synopsis(html_text: str) -> str | None:
    chunks = re.findall(
        r'<p class="description">\s*(.*?)\s*</p>',
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    best = ""
    for chunk in chunks:
        text = re.sub(r"<br\s*/?>", "\n", chunk, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) > len(best):
            best = text
    return best or None


def parse_cinema_sites(html_text: str) -> dict[str, str]:
    sites: dict[str, str] = {}
    for article in PROGRAM_ARTICLE_RE.findall(html_text):
        venue_match = re.search(r'data-venue="([^"]*)"', article)
        if not venue_match:
            continue
        venue = venue_match.group(1).strip()
        if not venue or venue in sites:
            continue
        for tag in re.findall(r"<a\b[^>]*>", article, re.IGNORECASE):
            class_match = re.search(r'class="([^"]*)"', tag)
            href_match = re.search(r'href="([^"]+)"', tag)
            if not class_match or not href_match:
                continue
            classes = class_match.group(1).split()
            if "button" not in classes or "ics" in classes:
                continue
            url = html.unescape(href_match.group(1)).strip()
            if url:
                sites[venue] = url
                break
    return sites


def _meta_class_value(html_text: str, class_name: str) -> str | None:
    match = re.search(
        rf'class="{class_name}".*?class="value">(.*?)</span>',
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    text = re.sub(r"<[^>]+>", " ", match.group(1))
    text = normalize_title_chars(re.sub(r"\s+", " ", html.unescape(text)).strip())
    return text or None


def _parse_nonstop_cast(html_text: str) -> list[str]:
    raw = _meta_class_value(html_text, "cast")
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_nonstop_duration(html_text: str) -> int | None:
    raw = _meta_class_value(html_text, "duration")
    if not raw:
        return None
    match = re.search(r"(\d+)", raw)
    if not match:
        return None
    return int(match.group(1))


def parse_nonstop_movie_page(html_text: str) -> NonstopFilmPage:
    h1 = re.search(r"<h1>\s*(.*?)\s*</h1>", html_text, re.IGNORECASE | re.DOTALL)
    title = None
    if h1:
        title = normalize_title_chars(re.sub(r"\s+", " ", h1.group(1)).strip())

    year_match = re.search(
        r'class="releaseYear".*?class="value">\s*(\d{4})\s*<',
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    year = int(year_match.group(1)) if year_match else None

    return NonstopFilmPage(
        title=title,
        year=year,
        director=_meta_class_value(html_text, "director"),
        synopsis=parse_synopsis(html_text),
        cinema_sites=parse_cinema_sites(html_text),
        cast=_parse_nonstop_cast(html_text),
        duration_minutes=_parse_nonstop_duration(html_text),
    )
