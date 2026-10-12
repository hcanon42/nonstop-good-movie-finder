"""A capped crawl of prolific Letterboxd members who loved the same films."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from goodmoviefinder.letterboxd import (
    LetterboxdRefused,
    fetch_letterboxd_html,
    fetch_letterboxd_watched_ratings,
    letterboxd_film_ratings_url,
    letterboxd_member_url,
    parse_film_rating_members,
    parse_profile_film_count,
)

NEIGHBOR_CACHE_MAX_AGE = 7 * 24 * 60 * 60
NEIGHBOR_MIN_FILMS = 500
NEIGHBOR_KEEP = 30
NEIGHBOR_DIARY_PAGES = 40
NEIGHBOR_PROFILE_CHECKS = 150
NEIGHBOR_SEED_COUNT = 20


def neighbors_cache_path(cache_path: Path) -> Path:
    return cache_path.parent / "neighbors.json"


def load_neighbors_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_neighbors_cache(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")


def _ratings(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    return {
        slug: float(value)
        for slug, value in raw.items()
        if isinstance(slug, str) and isinstance(value, (int, float))
    }


def _fresh(record: dict[str, Any], user: str) -> dict[str, dict[str, float]] | None:
    if record.get("user") != user or not record.get("complete"):
        return None
    fetched_at = record.get("fetched_at") or 0
    if not isinstance(fetched_at, (int, float)):
        return None
    if time.time() - fetched_at > NEIGHBOR_CACHE_MAX_AGE:
        return None
    members = record.get("members")
    if not isinstance(members, dict):
        return None
    kept: dict[str, dict[str, float]] = {}
    for name, entry in members.items():
        if isinstance(name, str) and isinstance(entry, dict) and entry.get("kept"):
            kept[name] = _ratings(entry.get("ratings"))
    return kept


def _seeds(watched_ratings: dict[str, float | None]) -> list[str]:
    loved = [
        (float(rating), slug)
        for slug, rating in watched_ratings.items()
        if isinstance(rating, (int, float)) and float(rating) >= 4.5
    ]
    loved.sort(key=lambda item: (-item[0], item[1]))
    return [slug for _rating, slug in loved[:NEIGHBOR_SEED_COUNT]]


def ensure_neighbors(
    cache_path: Path,
    user: str,
    watched_ratings: dict[str, float | None],
    delay: float,
    *,
    refresh: bool = False,
) -> tuple[dict[str, dict[str, float]], bool]:
    """Diaries of up to 30 prolific members, and whether Letterboxd cut the crawl short."""
    path = neighbors_cache_path(cache_path)
    record = {} if refresh else load_neighbors_cache(path)
    if record.get("user") != user:
        record = {}
    fresh = None if refresh else _fresh(record, user)
    if fresh is not None:
        print(f"Using cached similar members: {len(fresh)} diaries.", file=sys.stderr)
        return fresh, bool(record.get("aborted"))

    pause = max(delay, 1.0)
    raw_members = record.get("members")
    members: dict[str, Any] = raw_members if isinstance(raw_members, dict) else {}
    aborted = False
    refused = 0

    def polite(url: str) -> str | None:
        nonlocal refused, aborted
        if aborted:
            return None
        try:
            body = fetch_letterboxd_html(url, pause)
        except LetterboxdRefused as exc:
            refused += 1
            print(f"  {exc}", file=sys.stderr)
            if refused >= 3:
                aborted = True
                print(
                    "Letterboxd refused three times. Stopping the member lookup.",
                    file=sys.stderr,
                )
            return None
        except RuntimeError as exc:
            print(f"  {exc}", file=sys.stderr)
            return None
        refused = 0
        time.sleep(pause)
        return body

    seeds = _seeds(watched_ratings)
    print(
        f"Finding members who rated {len(seeds)} films you scored 4.5 or higher…",
        file=sys.stderr,
    )
    seed_hits: dict[str, set[str]] = {}
    for slug in seeds:
        if aborted:
            break
        for page in (1, 2):
            body = polite(letterboxd_film_ratings_url(slug, page))
            if body is None:
                break
            for name in parse_film_rating_members(body, slug):
                if name.casefold() == user.casefold():
                    continue
                seed_hits.setdefault(name, set()).add(slug)

    ranked = sorted(seed_hits, key=lambda name: (-len(seed_hits[name]), name.casefold()))
    print(
        f"Found {len(ranked)} members. Keeping up to {NEIGHBOR_KEEP} with "
        f"at least {NEIGHBOR_MIN_FILMS} films.",
        file=sys.stderr,
    )
    kept_count = sum(
        1 for entry in members.values() if isinstance(entry, dict) and entry.get("kept")
    )
    checks = 0
    for name in ranked:
        if aborted or kept_count >= NEIGHBOR_KEEP or checks >= NEIGHBOR_PROFILE_CHECKS:
            break
        entry = members.get(name)
        if isinstance(entry, dict) and (entry.get("kept") or entry.get("skipped")):
            if entry.get("kept"):
                continue
            continue
        checks += 1
        body = polite(letterboxd_member_url(name))
        if body is None:
            continue
        count = parse_profile_film_count(body)
        if count is None or count < NEIGHBOR_MIN_FILMS:
            members[name] = {
                "film_count": count,
                "kept": False,
                "skipped": True,
                "ratings": {},
            }
            continue
        print(f"  @{name} has logged {count} films. Reading ratings…", file=sys.stderr)
        try:
            ratings = fetch_letterboxd_watched_ratings(
                name,
                pause,
                max_pages=NEIGHBOR_DIARY_PAGES,
            )
        except RuntimeError as exc:
            print(f"  @{name}: {exc}", file=sys.stderr)
            message = str(exc)
            if any(code in message for code in ("403", "429", "503")):
                refused += 1
                if refused >= 3:
                    aborted = True
                    print(
                        "Letterboxd refused three times. Stopping the member lookup.",
                        file=sys.stderr,
                    )
            continue
        rated = {
            slug: float(value)
            for slug, value in ratings.items()
            if isinstance(value, (int, float))
        }
        members[name] = {
            "film_count": count,
            "kept": True,
            "skipped": False,
            "ratings": rated,
        }
        kept_count += 1
        save_neighbors_cache(
            path,
            {
                "user": user,
                "fetched_at": None,
                "complete": False,
                "aborted": aborted,
                "members": members,
            },
        )
        print(
            f"  Saved @{name} ({len(rated)} ratings). {kept_count} of {NEIGHBOR_KEEP}.",
            file=sys.stderr,
        )

    kept = {
        name: _ratings(entry.get("ratings"))
        for name, entry in members.items()
        if isinstance(entry, dict) and entry.get("kept")
    }
    finished = not aborted
    save_neighbors_cache(
        path,
        {
            "user": user,
            "fetched_at": None if aborted else int(time.time()),
            "complete": finished,
            "aborted": aborted,
            "members": members,
        },
    )
    print(
        f"Similar members ready: {len(kept)} diaries"
        + (" (lookup stopped early)." if aborted else "."),
        file=sys.stderr,
    )
    return kept, aborted
