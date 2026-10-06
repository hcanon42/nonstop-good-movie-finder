#!/usr/bin/env python3
"""
Fetch Nonstop Kino program listings, look up Letterboxd ratings, sort best → worst.
"""

from __future__ import annotations

import argparse
import html
import http.cookiejar
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PROGRAM_URL = (
    "https://nonstopkino.at/en/program/?weekday=all&time=all&location=wien"
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
CACHE_VERSION = 3
NONSTOP_LINK_QUERY = "weekday=all&time=all&location=wien"
DEFAULT_LETTERBOXD_USER = "hcanon"
PROFILE_CACHE_MAX_AGE = 6 * 60 * 60  # seconds
LETTERBOXD_SLUG_RE = re.compile(r'data-item-slug="([^"]+)"')
LETTERBOXD_NEXT_PAGE_RE = re.compile(
    r'<a class="next" href="([^"]+)"[^>]*>\s*Older\s*</a>',
    re.IGNORECASE,
)


def nonstop_display_url(url: str) -> str:
    """Nonstop links in output include default program filters."""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{NONSTOP_LINK_QUERY}"


def letterboxd_slug_from_url(url: str | None) -> str | None:
    if not url:
        return None
    path = urllib.parse.urlparse(url).path.rstrip("/")
    parts = path.split("/film/")
    if len(parts) < 2 or not parts[-1]:
        return None
    return parts[-1].split("/")[0] or None


def letterboxd_profile_url(user: str, section: str, page: int = 1) -> str:
    base = f"https://letterboxd.com/{user}/{section}/"
    if page <= 1:
        return base
    return f"{base}page/{page}/"


def parse_letterboxd_profile_slugs(html_text: str) -> set[str]:
    return set(LETTERBOXD_SLUG_RE.findall(html_text))


def fetch_letterboxd_profile_slugs(
    user: str,
    section: str,
    delay: float,
) -> set[str]:
    slugs: set[str] = set()
    page = 1
    opener = make_cookie_opener()
    profile_delay = max(delay, 1.0)

    while page <= 50:
        url = letterboxd_profile_url(user, section, page)
        html_text = None
        for attempt in range(4):
            try:
                html_text = fetch(url, opener=opener)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in {403, 429, 503} and attempt < 3:
                    time.sleep(profile_delay * (attempt + 2))
                    continue
                raise RuntimeError(f"Failed to fetch {url}: HTTP {exc.code}") from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"Failed to fetch {url}: {exc.reason}") from exc

        if html_text is None:
            raise RuntimeError(f"Failed to fetch {url}")

        slugs |= parse_letterboxd_profile_slugs(html_text)
        next_match = LETTERBOXD_NEXT_PAGE_RE.search(html_text)
        if not next_match:
            break
        page += 1
        time.sleep(profile_delay)
    return slugs


def load_profile_cache(
    cache: dict[str, Any],
    user: str,
) -> tuple[set[str], set[str]] | None:
    profile = cache.get("profile", {}).get(user)
    if not profile:
        return None
    fetched_at = profile.get("fetched_at", 0)
    if time.time() - fetched_at > PROFILE_CACHE_MAX_AGE:
        return None
    watched = set(profile.get("watched", []))
    watchlist = set(profile.get("watchlist", []))
    return watched, watchlist


def save_profile_cache(
    cache: dict[str, Any],
    user: str,
    watched: set[str],
    watchlist: set[str],
) -> None:
    cache.setdefault("profile", {})[user] = {
        "watched": sorted(watched),
        "watchlist": sorted(watchlist),
        "fetched_at": int(time.time()),
    }


@dataclass
class Movie:
    title: str
    nonstop_url: str
    letterboxd_url: str | None = None
    letterboxd_title: str | None = None
    rating: float | None = None
    year: int | None = None
    note: str | None = None


def _request_headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }


def make_cookie_opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def fetch(
    url: str,
    timeout: float = 45.0,
    opener: urllib.request.OpenerDirector | None = None,
) -> str:
    req = urllib.request.Request(url, headers=_request_headers())
    if opener is not None:
        resp_cm = opener.open(req, timeout=timeout)
    else:
        resp_cm = urllib.request.urlopen(req, timeout=timeout)
    with resp_cm as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def fetch_status(url: str, timeout: float = 45.0) -> tuple[int, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        body = resp.read().decode(charset, errors="replace")
        return resp.status, resp.geturl(), body


def normalize_title_chars(title: str) -> str:
    title = html.unescape(title).strip()
    for uchar, replacement in (
        ("\u2019", "'"),
        ("\u2018", "'"),
        ("\u201c", '"'),
        ("\u201d", '"'),
    ):
        title = title.replace(uchar, replacement)
    return title


def parenthetical_segments(title: str) -> list[str]:
    title = normalize_title_chars(title)
    segments: list[str] = []
    for match in re.finditer(r"\(([^)]+)\)", title):
        inner = match.group(1).strip()
        if inner:
            segments.append(inner)
    return segments


def slugify(title: str) -> str:
    title = normalize_title_chars(title)
    title = unicodedata.normalize("NFKD", title)
    title = title.encode("ascii", "ignore").decode("ascii")
    title = title.lower().replace("&", "and")
    title = re.sub(r"[^a-z0-9]+", "-", title)
    return title.strip("-")


def search_titles_from_display_title(title: str) -> list[str]:
    title = normalize_title_chars(title)
    titles: list[str] = []
    for inner in parenthetical_segments(title):
        if re.search(r"[A-Za-z]", inner):
            titles.append(inner)
    stripped = re.sub(r"\s*\([^)]+\)\s*", " ", title).strip()
    if stripped:
        titles.append(stripped)
    if not titles:
        titles.append(title)
    seen: set[str] = set()
    out: list[str] = []
    for t in titles:
        key = t.casefold()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out


def parenthetical_search_queries(
    display_title: str,
    nonstop_page_title: str | None,
) -> list[str]:
    """Alternate titles in parentheses, for Letterboxd search when slugs fail."""
    seen: set[str] = set()
    queries: list[str] = []
    for title in (display_title, nonstop_page_title):
        if not title:
            continue
        for inner in parenthetical_segments(title):
            key = inner.casefold()
            if key not in seen:
                seen.add(key)
                queries.append(inner)
    return queries


def letterboxd_autocomplete_slugs(
    query: str,
    delay: float,
    limit: int = 5,
) -> list[str]:
    query = query.strip()
    if not query:
        return []
    url = (
        "https://letterboxd.com/s/autocompletefilm?q="
        + urllib.parse.quote(query)
    )
    try:
        body = fetch(url)
    except urllib.error.URLError:
        return []
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return []
    slugs: list[str] = []
    for item in payload.get("data") or []:
        if item.get("type") != "film":
            continue
        slug = item.get("slug")
        if not slug:
            continue
        slugs.append(slug)
        if len(slugs) >= limit:
            break
    time.sleep(delay)
    return slugs


def slug_from_nonstop_url(nonstop_url: str) -> str:
    path = urllib.parse.urlparse(nonstop_url).path.rstrip("/")
    slug = path.split("/")[-1]
    slug = re.sub(r"-\d+$", "", slug)
    return slug


def parse_program(html_text: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r'<a href="(https://nonstopkino\.at/en/movies/[^"]+)" '
        r'class="full-card-link"[^>]*>\s*<div class="content">\s*<h3>\s*'
        r"(.*?)\s*</h3>",
        re.IGNORECASE | re.DOTALL,
    )
    seen: set[str] = set()
    movies: list[tuple[str, str]] = []
    for url, raw_title in pattern.findall(html_text):
        title = re.sub(r"\s+", " ", html.unescape(raw_title)).strip()
        if url in seen:
            continue
        seen.add(url)
        movies.append((title, url))
    return movies


def parse_nonstop_movie_page(html_text: str) -> tuple[str | None, int | None]:
    h1 = re.search(r"<h1>\s*(.*?)\s*</h1>", html_text, re.IGNORECASE | re.DOTALL)
    title = None
    if h1:
        title = re.sub(r"\s+", " ", html.unescape(h1.group(1))).strip()

    year_match = re.search(
        r'class="releaseYear".*?class="value">\s*(\d{4})\s*<',
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    year = int(year_match.group(1)) if year_match else None
    return title, year


def parse_letterboxd_film(html_text: str) -> tuple[str | None, int | None, float | None]:
    name = None
    year = None
    rating = None

    name_match = re.search(r'"name":"([^"]+)","genre"', html_text)
    if name_match:
        name = html.unescape(name_match.group(1))

    year_match = re.search(r'"dateCreated":"(\d{4})', html_text)
    if year_match:
        year = int(year_match.group(1))

    rating_match = re.search(
        r'"aggregateRating":\{[^}]*"ratingValue":([0-9.]+)',
        html_text,
    )
    if rating_match:
        rating = float(rating_match.group(1))
    else:
        twitter = re.search(
            r'name="twitter:data2"\s+content="([0-9.]+)\s+out of 5"',
            html_text,
        )
        if twitter:
            rating = float(twitter.group(1))

    return name, year, rating


def letterboxd_slug_candidates(
    display_title: str,
    nonstop_url: str,
    nonstop_page_title: str | None,
    release_year: int | None,
) -> list[str]:
    slugs: list[str] = []
    url_slug = slug_from_nonstop_url(nonstop_url)
    if url_slug:
        slugs.append(url_slug)

    for title in search_titles_from_display_title(display_title):
        slugs.append(slugify(title))

    if nonstop_page_title:
        slugs.append(slugify(nonstop_page_title))

    if release_year:
        for base in list(slugs):
            slugs.append(f"{base}-{release_year}")

    seen: set[str] = set()
    ordered: list[str] = []
    for slug in slugs:
        if slug and slug not in seen:
            seen.add(slug)
            ordered.append(slug)
    return ordered


def _letterboxd_fetch_candidate(
    slug: str,
    delay: float,
) -> tuple[tuple[str, str | None, int | None, float | None] | None, str | None]:
    """Fetch a film page by slug. Returns (candidate, fatal_error_note)."""
    url = f"https://letterboxd.com/film/{slug}/"
    try:
        status, final_url, body = fetch_status(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, None
        return None, f"Letterboxd error: {exc}"
    except urllib.error.URLError as exc:
        return None, f"Network error: {exc.reason}"

    if status != 200 or "/film/" not in final_url:
        return None, None

    lb_title, lb_year, rating = parse_letterboxd_film(body)
    candidate = (final_url.rstrip("/") + "/", lb_title, lb_year, rating)
    time.sleep(delay)
    return candidate, None


def resolve_letterboxd(
    display_title: str,
    nonstop_url: str,
    nonstop_page_title: str | None,
    release_year: int | None,
    delay: float,
) -> Movie:
    movie = Movie(title=display_title, nonstop_url=nonstop_url, year=release_year)
    candidates: list[tuple[str, str | None, int | None, float | None]] = []
    tried_slugs: set[str] = set()

    def consider_slug(slug: str) -> bool:
        if not slug or slug in tried_slugs:
            return False
        tried_slugs.add(slug)
        candidate, error = _letterboxd_fetch_candidate(slug, delay)
        if error:
            movie.note = error
            return True
        if candidate is None:
            return False
        candidates.append(candidate)
        lb_title, lb_year, rating = candidate[1], candidate[2], candidate[3]
        year_ok = release_year is None or lb_year == release_year
        if year_ok and rating is not None:
            movie.letterboxd_url = candidate[0]
            movie.letterboxd_title = lb_title
            movie.year = lb_year
            movie.rating = rating
            return True
        if year_ok and movie.letterboxd_url is None:
            movie.letterboxd_url = candidate[0]
            movie.letterboxd_title = lb_title
            movie.year = lb_year
            movie.rating = rating
        return False

    for slug in letterboxd_slug_candidates(
        display_title, nonstop_url, nonstop_page_title, release_year
    ):
        if consider_slug(slug):
            return movie

    if not candidates:
        for query in parenthetical_search_queries(display_title, nonstop_page_title):
            for slug in letterboxd_autocomplete_slugs(query, delay):
                if consider_slug(slug):
                    break
            if movie.letterboxd_url and movie.rating is not None:
                break
            if movie.note:
                return movie

    if not candidates:
        movie.note = "No Letterboxd match"
        return movie

    if movie.letterboxd_url is None:
        def score(
            item: tuple[str, str | None, int | None, float | None],
        ) -> tuple[int, int, int]:
            _, _, lb_year, rating = item
            year_match = 1 if release_year and lb_year == release_year else 0
            has_rating = 1 if rating is not None else 0
            return (year_match, has_rating, lb_year or 0)

        best = max(candidates, key=score)
        movie.letterboxd_url, movie.letterboxd_title, movie.year, movie.rating = best

    if release_year and movie.year and movie.year != release_year:
        movie.note = f"Year mismatch (Nonstop {release_year}, Letterboxd {movie.year})"
    return movie


def load_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": CACHE_VERSION, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": CACHE_VERSION, "entries": {}}
    if data.get("version") != CACHE_VERSION:
        return {"version": CACHE_VERSION, "entries": {}}
    return data


def save_cache(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def cache_get(cache: dict[str, Any], nonstop_url: str) -> Movie | None:
    entry = cache.get("entries", {}).get(nonstop_url)
    if not entry:
        return None
    return Movie(
        title=entry["title"],
        nonstop_url=nonstop_url,
        letterboxd_url=entry.get("letterboxd_url"),
        letterboxd_title=entry.get("letterboxd_title"),
        rating=entry.get("rating"),
        year=entry.get("year"),
        note=entry.get("note"),
    )


def cache_put(cache: dict[str, Any], movie: Movie) -> None:
    cache.setdefault("entries", {})[movie.nonstop_url] = {
        "title": movie.title,
        "letterboxd_url": movie.letterboxd_url,
        "letterboxd_title": movie.letterboxd_title,
        "rating": movie.rating,
        "year": movie.year,
        "note": movie.note,
    }


def sort_movies(movies: list[Movie]) -> list[Movie]:
    def sort_key(m: Movie) -> tuple[int, float, str]:
        if m.rating is None:
            return (1, 0.0, m.title.casefold())
        return (0, -m.rating, m.title.casefold())

    return sorted(movies, key=sort_key)


def partition_program_movies(
    movies: list[Movie],
    watched_slugs: set[str],
    watchlist_slugs: set[str],
) -> tuple[list[Movie], list[Movie]]:
    watchlist: list[Movie] = []
    program: list[Movie] = []

    for movie in movies:
        slug = letterboxd_slug_from_url(movie.letterboxd_url)
        if slug and slug in watched_slugs:
            continue
        if slug and slug in watchlist_slugs:
            watchlist.append(movie)
            continue
        program.append(movie)

    return sort_movies(watchlist), sort_movies(program)


def _format_markdown_row(m: Movie) -> str:
    rating = f"{m.rating:.2f}" if m.rating is not None else "—"
    lb = m.letterboxd_url or "—"
    if m.letterboxd_url:
        label = m.letterboxd_title or m.title
        lb = f"[{label}]({m.letterboxd_url})"
    ns = f"[Nonstop]({nonstop_display_url(m.nonstop_url)})"
    title = m.title
    if m.note:
        title += f" ({m.note})"
    return f"| {rating} | {title} | {lb} | {ns} |"


def _format_plain_movie(m: Movie) -> list[str]:
    if m.rating is not None:
        rating = f"{m.rating:.2f}/5"
    elif m.letterboxd_url:
        rating = "unrated"
    else:
        rating = "no match"
    lines = [f"{rating:>8}  {m.title}"]
    if m.letterboxd_url:
        lines.append(f"          Letterboxd: {m.letterboxd_url}")
    if m.note:
        lines.append(f"          Note: {m.note}")
    lines.append(f"          Nonstop:   {nonstop_display_url(m.nonstop_url)}")
    lines.append("")
    return lines


def format_markdown(watchlist: list[Movie], program: list[Movie]) -> str:
    lines: list[str] = []

    if watchlist:
        lines.extend(
            [
                "# Watchlist — now in Nonstop program",
                "",
                "| Rating | Film | Letterboxd | Nonstop |",
                "| ---: | --- | --- | --- |",
            ]
        )
        for m in watchlist:
            lines.append(_format_markdown_row(m))
        lines.append("")

    lines.extend(
        [
            "# Nonstop Kino program — sorted by Letterboxd rating (unseen)",
            "",
            "| Rating | Film | Letterboxd | Nonstop |",
            "| ---: | --- | --- | --- |",
        ]
    )
    for m in program:
        lines.append(_format_markdown_row(m))
    lines.append("")
    return "\n".join(lines)


def format_plain(watchlist: list[Movie], program: list[Movie]) -> str:
    lines: list[str] = []
    if watchlist:
        lines.append("=== Watchlist — now in Nonstop program ===")
        lines.append("")
        for m in watchlist:
            lines.extend(_format_plain_movie(m))
        lines.append("")

    lines.append("=== Program (unseen) ===")
    lines.append("")
    for m in program:
        lines.extend(_format_plain_movie(m))
    return "\n".join(lines).rstrip() + "\n"


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
        default=Path.home() / ".cache" / "goodmoviefinder" / "ratings.json",
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
        "--markdown",
        action="store_true",
        help="Print Markdown table instead of plain text",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write output to this file",
    )
    parser.add_argument(
        "--letterboxd-user",
        default=DEFAULT_LETTERBOXD_USER,
        help=f"Letterboxd username for watched/watchlist filters (default: {DEFAULT_LETTERBOXD_USER})",
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
    args = parser.parse_args()

    print(f"Fetching program: {args.program_url}", file=sys.stderr)
    try:
        program_html = fetch(args.program_url)
    except urllib.error.URLError as exc:
        print(f"Failed to fetch program: {exc.reason}", file=sys.stderr)
        return 1

    pairs = parse_program(program_html)
    if not pairs:
        print("No films found on the program page.", file=sys.stderr)
        return 1

    if args.limit > 0:
        pairs = pairs[: args.limit]

    cache = load_cache(args.cache)

    watched_slugs: set[str] = set()
    watchlist_slugs: set[str] = set()

    if not args.no_letterboxd_profile:
        profile = None
        if not args.refresh_profile and not args.no_cache:
            profile = load_profile_cache(cache, args.letterboxd_user)
        if profile is None:
            user = args.letterboxd_user
            print(f"Fetching Letterboxd watched films for @{user}…", file=sys.stderr)
            try:
                watched_slugs = fetch_letterboxd_profile_slugs(
                    user, "films", delay=args.delay
                )
                print(f"Fetching Letterboxd watchlist for @{user}…", file=sys.stderr)
                watchlist_slugs = fetch_letterboxd_profile_slugs(
                    user, "watchlist", delay=args.delay
                )
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 1
            save_profile_cache(cache, user, watched_slugs, watchlist_slugs)
            save_cache(args.cache, cache)
            print(
                f"Profile: {len(watched_slugs)} watched, {len(watchlist_slugs)} on watchlist.",
                file=sys.stderr,
            )
        else:
            watched_slugs, watchlist_slugs = profile
            print(
                f"Using cached profile: {len(watched_slugs)} watched, "
                f"{len(watchlist_slugs)} on watchlist.",
                file=sys.stderr,
            )

    results: list[Movie] = []

    for index, (title, nonstop_url) in enumerate(pairs, start=1):
        print(f"[{index}/{len(pairs)}] {title}", file=sys.stderr)

        if not args.no_cache:
            cached = cache_get(cache, nonstop_url)
            if cached is not None:
                results.append(cached)
                continue

        page_title: str | None = None
        release_year: int | None = None
        try:
            movie_html = fetch(nonstop_url)
            page_title, release_year = parse_nonstop_movie_page(movie_html)
        except urllib.error.URLError as exc:
            movie = Movie(
                title=title,
                nonstop_url=nonstop_url,
                note=f"Nonstop page error: {exc.reason}",
            )
            results.append(movie)
            continue

        movie = resolve_letterboxd(
            title,
            nonstop_url,
            page_title,
            release_year,
            delay=args.delay,
        )
        cache_put(cache, movie)
        save_cache(args.cache, cache)
        results.append(movie)

    watchlist_movies, program_movies = partition_program_movies(
        results, watched_slugs, watchlist_slugs
    )
    if args.no_letterboxd_profile:
        program_movies = sort_movies(results)
        watchlist_movies = []

    output = (
        format_markdown(watchlist_movies, program_movies)
        if args.markdown
        else format_plain(watchlist_movies, program_movies)
    )

    if args.output:
        args.output.write_text(output, encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(output)

    rated = sum(1 for m in results if m.rating is not None)
    skipped = len(results) - len(watchlist_movies) - len(program_movies)
    print(
        f"Done: {len(program_movies)} unseen in program, "
        f"{len(watchlist_movies)} watchlist matches, "
        f"{skipped} already watched skipped, "
        f"{rated}/{len(results)} with Letterboxd ratings.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
