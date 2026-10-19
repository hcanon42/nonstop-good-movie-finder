"""Where a Letterboxd watchlist can be watched online in Austria."""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from goodmoviefinder.config import STREAMING_CACHE, USER_AGENT
from goodmoviefinder.http import fetch
from goodmoviefinder.letterboxd import letterboxd_poster_url, parse_letterboxd_film_card
from goodmoviefinder.progress import tick
from goodmoviefinder.title_lookup import english_title_from_german_wikipedia

STREAMING_CACHE_VERSION = 1
FILM_CARD_VERSION = 2
STREAMING_MAX_AGE = 3 * 24 * 60 * 60  # seconds
_JUSTWATCH_URL = "https://apis.justwatch.com/graphql"
_ICON_ROOT = "https://images.justwatch.com"
_COUNTRY = "AT"

# Subscription first, then free, ads, rent, and buy.
_MONETIZATION_RANK = {
    "FLATRATE": 0,
    "FREE": 1,
    "ADS": 2,
    "RENT": 3,
    "BUY": 4,
}
_KIND_LABEL = {
    "FLATRATE": "subscription",
    "FREE": "free",
    "ADS": "with ads",
    "RENT": "rent",
    "BUY": "buy",
}

_SEARCH_QUERY = """
query GetSearchTitles(
  $country: Country!
  $language: Language!
  $searchTitlesFilter: TitleFilter
  $first: Int!
) {
  popularTitles(country: $country, first: $first, filter: $searchTitlesFilter) {
    edges {
      node {
        objectType
        content(country: $country, language: $language) {
          title
          originalReleaseYear
        }
        offers(country: $country, platform: WEB) {
          monetizationType
          standardWebURL
          package {
            clearName
            packageId
            icon
          }
        }
      }
    }
  }
}
"""

_TITLE_KEY_RE = re.compile(r"[^a-z0-9]+")


def _title_key(value: str) -> str:
    return _TITLE_KEY_RE.sub("", value.casefold())


def _title_distance(wanted: str, found: str) -> int:
    if not wanted or not found:
        return 3
    if wanted == found:
        return 0
    if wanted in found or found in wanted:
        return 1
    return 2


def _icon_url(icon: object) -> str | None:
    if not isinstance(icon, str) or not icon.startswith("/"):
        return None
    path = icon.replace("{profile}", "s100").replace("{format}", "webp")
    if "{" in path or "}" in path:
        return None
    return _ICON_ROOT + path


def _offer_record(offer: object) -> tuple[int, dict[str, str]] | None:
    if not isinstance(offer, dict):
        return None
    package = offer.get("package")
    if not isinstance(package, dict):
        return None
    package_id = package.get("packageId")
    name = package.get("clearName")
    url = offer.get("standardWebURL")
    if isinstance(package_id, float) and package_id.is_integer():
        package_id = int(package_id)
    if not isinstance(package_id, int) or package_id < 0:
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        return None
    kind = offer.get("monetizationType")
    kind_name = kind if isinstance(kind, str) else ""
    record = {
        "name": name.strip(),
        "url": url,
        "kind": _KIND_LABEL.get(kind_name, kind_name.replace("_", " ").lower()),
    }
    icon = _icon_url(package.get("icon"))
    if icon:
        record["icon"] = icon
    return _MONETIZATION_RANK.get(kind_name, 9), record


def offers_for_node(node: dict[str, Any]) -> list[dict[str, str]]:
    """One link per service, preferring a subscription over renting the same service."""
    best: dict[int, tuple[int, dict[str, str]]] = {}
    raw_offers = node.get("offers")
    if not isinstance(raw_offers, list):
        return []
    for offer in raw_offers:
        parsed = _offer_record(offer)
        if parsed is None:
            continue
        rank, record = parsed
        package = offer.get("package") if isinstance(offer, dict) else None
        package_id = package.get("packageId") if isinstance(package, dict) else None
        if isinstance(package_id, float) and package_id.is_integer():
            package_id = int(package_id)
        if not isinstance(package_id, int):
            continue
        current = best.get(package_id)
        if current is None or rank < current[0]:
            best[package_id] = (rank, record)
    chosen = sorted(best.values(), key=lambda item: (item[0], item[1]["name"].casefold()))
    return [record for _rank, record in chosen]


def _pick_movie(
    nodes: list[dict[str, Any]],
    title: str,
    year: int | None,
) -> dict[str, Any] | None:
    movies = [node for node in nodes if node.get("objectType") == "MOVIE"]
    if year is not None:
        movies = [
            node
            for node in movies
            if isinstance(node.get("content"), dict)
            and node["content"].get("originalReleaseYear") == year
        ]
    if not movies:
        return None
    wanted = _title_key(title)

    def distance(node: dict[str, Any]) -> int:
        content = node.get("content")
        found = content.get("title") if isinstance(content, dict) else ""
        if not isinstance(found, str):
            found = ""
        return _title_distance(wanted, _title_key(found))

    movies.sort(key=distance)
    best = distance(movies[0])
    if best <= 1:
        return movies[0]
    if year is not None and len(movies) == 1:
        return movies[0]
    return None


def _post_graphql(payload: dict[str, object]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        _JUSTWATCH_URL,
        data=data,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        body = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise RuntimeError("JustWatch returned an unexpected response.")
    if parsed.get("errors"):
        raise RuntimeError("JustWatch could not search for that film.")
    return parsed


def _justwatch_search_queries(title: str, year: int | None) -> list[str]:
    queries = [title.strip()]
    english = english_title_from_german_wikipedia(title, year)
    if english and english.casefold() != queries[0].casefold():
        queries.append(english)
    seen: set[str] = set()
    ordered: list[str] = []
    for query in queries:
        key = query.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(query)
    return ordered


def _search_austria_offers_once(title: str, year: int | None) -> list[dict[str, str]]:
    payload = {
        "query": _SEARCH_QUERY,
        "variables": {
            "country": _COUNTRY,
            "language": "en",
            "first": 8,
            "searchTitlesFilter": {"searchQuery": title},
        },
    }
    last_error: Exception | None = None
    parsed: dict[str, Any] | None = None
    for attempt in range(3):
        try:
            parsed = _post_graphql(payload)
            break
        except urllib.error.HTTPError as exc:
            if exc.code in {403, 429, 503} and attempt < 2:
                time.sleep(1.5 * (attempt + 2))
                last_error = exc
                continue
            raise RuntimeError(f"JustWatch search failed: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"JustWatch search failed: {exc.reason}") from exc
        except (json.JSONDecodeError, RuntimeError, TimeoutError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"JustWatch search failed: {exc}") from exc
    if parsed is None:
        raise RuntimeError(f"JustWatch search failed: {last_error}")
    popular = parsed.get("data", {}).get("popularTitles") if isinstance(parsed.get("data"), dict) else None
    edges = popular.get("edges") if isinstance(popular, dict) else None
    if not isinstance(edges, list):
        raise RuntimeError("JustWatch returned no search results.")
    nodes: list[dict[str, Any]] = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        node = edge.get("node")
        if isinstance(node, dict):
            nodes.append(node)
    chosen = _pick_movie(nodes, title, year)
    if chosen is None:
        return []
    return offers_for_node(chosen)


def search_austria_offers(title: str, year: int | None) -> list[dict[str, str]]:
    """Services in Austria for this film. An empty list means none were found."""
    for query in _justwatch_search_queries(title, year):
        offers = _search_austria_offers_once(query, year)
        if offers:
            return offers
    return []


def _clean_offers(raw: object) -> list[dict[str, str]] | None:
    if not isinstance(raw, list):
        return None
    offers: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        name = item.get("name")
        url = item.get("url")
        kind = item.get("kind")
        if not isinstance(name, str) or not name:
            return None
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            return None
        record = {
            "name": name,
            "url": url,
            "kind": kind if isinstance(kind, str) else "",
        }
        icon = item.get("icon")
        if isinstance(icon, str) and icon.startswith(_ICON_ROOT + "/"):
            record["icon"] = icon
        offers.append(record)
    return offers


def load_streaming_cache(path: Path = STREAMING_CACHE) -> dict[str, Any]:
    if not path.is_file():
        return {"version": STREAMING_CACHE_VERSION, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": STREAMING_CACHE_VERSION, "entries": {}}
    if not isinstance(data, dict) or data.get("version") != STREAMING_CACHE_VERSION:
        return {"version": STREAMING_CACHE_VERSION, "entries": {}}
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    return data


def save_streaming_cache(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _clean_details(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, dict) or raw.get("card") != FILM_CARD_VERSION or "synopsis" not in raw:
        return None
    synopsis = raw.get("synopsis")
    if not isinstance(synopsis, str):
        return None
    rating = raw.get("rating")
    if isinstance(rating, bool) or not isinstance(rating, (int, float)):
        rating = None
    else:
        rating = float(rating)
    return {
        "card": FILM_CARD_VERSION,
        "rating": rating,
        "synopsis": synopsis,
        "poster": letterboxd_poster_url(raw.get("poster")),
    }


def _fetch_film_html(slug: str) -> str:
    url = (
        "https://letterboxd.com/film/"
        + urllib.parse.quote(slug, safe="-_")
        + "/"
    )
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            return fetch(url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise
            if exc.code in {403, 429, 503} and attempt < 3:
                time.sleep(1.5 * (attempt + 2))
                last_error = exc
                continue
            raise RuntimeError(f"Letterboxd film page failed: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"Letterboxd film page failed: {exc.reason}") from exc
    raise RuntimeError(f"Letterboxd film page failed: {last_error}")


def fetch_letterboxd_film_card(slug: str) -> dict[str, object]:
    """Score, synopsis, and poster. A missing film page has none of them."""
    try:
        body = _fetch_film_html(slug)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"card": FILM_CARD_VERSION, "rating": None, "synopsis": "", "poster": None}
        raise
    card = parse_letterboxd_film_card(body)
    card["card"] = FILM_CARD_VERSION
    return card


def _store_details(
    entries: dict[str, Any],
    slug: str,
    title: str,
    year: int | None,
    details: dict[str, object],
) -> None:
    current = entries.get(slug)
    if not isinstance(current, dict):
        current = {"title": title, "year": year, "offers": []}
        entries[slug] = current
    current["details"] = details


def _letterboxd_details(
    entries: dict[str, Any],
    slug: str,
    title: str,
    year: int | None,
    *,
    refresh: bool,
    cache_path: Path,
    cache: dict[str, Any],
    delay: float,
    pause: bool,
) -> dict[str, object] | None:
    stored = entries.get(slug)
    cached = _clean_details(stored.get("details") if isinstance(stored, dict) else None)
    if cached is not None and not refresh:
        return cached
    if not slug:
        return cached
    try:
        details = fetch_letterboxd_film_card(slug)
    except (RuntimeError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"  Could not read the Letterboxd page: {exc}", file=sys.stderr)
        return cached
    _store_details(entries, slug, title, year, details)
    save_streaming_cache(cache_path, cache)
    rating = details.get("rating")
    label = f"{rating:.2f}" if isinstance(rating, float) else "no score"
    print(f"  Letterboxd {label}.", file=sys.stderr)
    if pause and delay > 0:
        time.sleep(delay)
    return details


def _cached_offers(
    entry: object,
    title: str,
    year: int | None,
    *,
    now: float,
) -> list[dict[str, str]] | None:
    if not isinstance(entry, dict):
        return None
    if entry.get("title") != title or entry.get("year") != year:
        return None
    fetched_at = entry.get("fetched_at", 0)
    if not isinstance(fetched_at, (int, float)):
        return None
    if now - fetched_at > STREAMING_MAX_AGE:
        return None
    return _clean_offers(entry.get("offers"))


def resolve_watchlist_streaming(
    films: list[dict[str, object]],
    delay: float,
    *,
    refresh: bool = False,
    cache_path: Path = STREAMING_CACHE,
) -> list[dict[str, object]]:
    """Look up each watchlist film. A failed lookup stays unchecked so the next run retries it."""
    cache = load_streaming_cache(cache_path)
    entries = cache.setdefault("entries", {})
    rows: list[dict[str, object]] = []
    now = time.time()
    total = len(films)
    for index, film in enumerate(films, start=1):
        slug = str(film.get("slug") or "")
        title = film.get("title")
        year = film.get("year")
        if not isinstance(title, str) or not title.strip():
            title = slug
        else:
            title = title.strip()
        if isinstance(year, float) and year.is_integer():
            year = int(year)
        if not isinstance(year, int):
            year = None
        print(f"Streaming in Austria [{index}/{total}] {title}", file=sys.stderr)
        tick(current=index, total=total, detail=title)
        offers: list[dict[str, str]] | None = None
        if not refresh:
            offers = _cached_offers(entries.get(slug), title, year, now=now)
            if offers is not None:
                count = len(offers)
                services = "service" if count == 1 else "services"
                print(f"  {count} {services} (cached).", file=sys.stderr)
        checked = True
        if offers is None:
            try:
                offers = search_austria_offers(title, year)
            except (RuntimeError, TimeoutError) as exc:
                print(f"  Could not check streaming: {exc}", file=sys.stderr)
                stale = entries.get(slug) if isinstance(entries.get(slug), dict) else None
                stale_offers = None
                if (
                    isinstance(stale, dict)
                    and stale.get("title") == title
                    and stale.get("year") == year
                ):
                    stale_offers = _clean_offers(stale.get("offers"))
                offers = stale_offers or []
                checked = stale_offers is not None
                if delay > 0 and index < total:
                    time.sleep(delay)
            else:
                previous = entries.get(slug)
                kept_details = previous.get("details") if isinstance(previous, dict) else None
                entries[slug] = {
                    "title": title,
                    "year": year,
                    "fetched_at": int(time.time()),
                    "offers": offers,
                }
                if isinstance(kept_details, dict):
                    entries[slug]["details"] = kept_details
                save_streaming_cache(cache_path, cache)
                count = len(offers)
                services = "service" if count == 1 else "services"
                print(f"  {count} {services}.", file=sys.stderr)
                if delay > 0 and index < total:
                    time.sleep(delay)
        details = _letterboxd_details(
            entries,
            slug,
            title,
            year,
            refresh=refresh,
            cache_path=cache_path,
            cache=cache,
            delay=delay,
            pause=index < total,
        )
        rows.append(
            {
                "slug": slug,
                "title": title,
                "year": year,
                "offers": offers,
                "checked": checked,
                "rating": None if details is None else details.get("rating"),
                "synopsis": "" if details is None else details.get("synopsis") or "",
                "poster": None if details is None else details.get("poster"),
                "details_known": details is not None,
            }
        )
    return rows
