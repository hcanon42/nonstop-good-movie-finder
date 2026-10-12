"""Ratings and reviews from the Letterboxd members a viewer follows."""

from __future__ import annotations

import html
import json
import re
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

from goodmoviefinder.html import _rating_style
from goodmoviefinder.letterboxd import LetterboxdRefused, fetch_letterboxd_html

FRIEND_CACHE_MAX_AGE = 6 * 60 * 60
_MAX_PAGES = 4
_LOCK = threading.Lock()
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,160}$")
_USER_RE = re.compile(r"^[A-Za-z0-9_]{1,30}$")
_NEXT_PAGE_RE = re.compile(
    r'<a[^>]*class="[^"]*\bnext\b[^"]*"[^>]*href="([^"]+)"'
    r'|<a[^>]*href="([^"]+)"[^>]*class="[^"]*\bnext\b[^"]*"',
    re.IGNORECASE,
)
_AVATAR_PREFIXES = ("https://a.ltrbxd.com/", "https://s.ltrbxd.com/")


def friend_slug_ok(slug: str) -> bool:
    return bool(_SLUG_RE.fullmatch(slug))


def _pause(delay: float) -> None:
    time.sleep(max(delay, 0.2))


def _avatar(value: str | None) -> str | None:
    if not value or not value.startswith(_AVATAR_PREFIXES):
        return None
    return value


def _prefer_avatar(current: str | None, new: str | None) -> str | None:
    chosen = _avatar(new)
    if chosen is None:
        return current
    if current is None or "/static/img/avatar" in current:
        return chosen
    return current


def _stars(label: str) -> float | None:
    if "★" not in label and "½" not in label:
        return None
    value = float(label.count("★")) + 0.5 * label.count("½")
    if value < 0.5 or value > 5:
        return None
    return value


def _plain_text(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.IGNORECASE)
    text = re.sub(r"</p>\s*<p[^>]*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _next_page(html_text: str) -> str | None:
    match = _NEXT_PAGE_RE.search(html_text)
    if not match:
        return None
    href = html.unescape(next(group for group in match.groups() if group))
    if href.startswith("/"):
        return "https://letterboxd.com" + href
    if href.startswith("https://letterboxd.com/"):
        return href
    return None


def _username(fragment: str) -> str | None:
    match = re.search(r'href="/([A-Za-z0-9_]+)/"', fragment)
    if not match or not _USER_RE.fullmatch(match.group(1)):
        return None
    return match.group(1)


def parse_friend_rating_rows(html_text: str) -> list[dict[str, object]]:
    """Friends who logged a film, from the viewer's friends-for-this-film page."""
    friends: list[dict[str, object]] = []
    seen: set[str] = set()
    for chunk in html_text.split('<div class="person-summary">')[1:]:
        end = chunk.find("</tr>")
        row = chunk if end < 0 else chunk[:end]
        username = _username(row)
        if username is None:
            continue
        key = username.casefold()
        if key in seen:
            continue
        seen.add(key)
        name_match = re.search(r'class="name"[^>]*>\s*([^<]+?)\s*</a>', row)
        name = html.unescape(name_match.group(1)).strip() if name_match else username
        image = re.search(r'<img[^>]+src="([^"]+)"', row)
        rated = re.search(r"\brated-(\d+)\b", row)
        rating = int(rated.group(1)) / 2 if rated else None
        if rating is not None and (rating < 0.5 or rating > 5):
            rating = None
        friends.append(
            {
                "username": username,
                "name": name or username,
                "avatar": _avatar(html.unescape(image.group(1))) if image else None,
                "rating": rating,
                "comment": None,
                "has_review": "icon-review" in row,
            }
        )
    return friends


def parse_friend_reviews(html_text: str) -> list[dict[str, object]]:
    """Review text from the same viewer's friends-reviews page for one film."""
    reviews: list[dict[str, object]] = []
    seen: set[str] = set()
    parts = html_text.split('<article class="production-viewing')
    for part in parts[1:]:
        end = part.find("</article>")
        item = part if end < 0 else part[:end]
        username = _username(item)
        if username is None:
            continue
        key = username.casefold()
        if key in seen:
            continue
        seen.add(key)
        name_match = re.search(r'class="displayname">\s*([^<]+?)\s*</', item)
        name = html.unescape(name_match.group(1)).strip() if name_match else username
        image = re.search(r'<img[^>]+src="([^"]+)"', item)
        rating = None
        for label in re.findall(r'aria-label="([^"]*)"', item):
            rating = _stars(html.unescape(label))
            if rating is not None:
                break
        body = re.search(
            r'js-review-body([^>]*)>(.*?)</div>\s*<div class="viewing-actions"',
            item,
            re.DOTALL,
        )
        comment = _plain_text(body.group(2)) if body else ""
        full_url = None
        if body:
            full_match = re.search(r'data-full-text-url="([^"]+)"', body.group(1))
            if full_match and full_match.group(1).startswith("/s/full-text/"):
                full_url = full_match.group(1)
        reviews.append(
            {
                "username": username,
                "name": name or username,
                "avatar": _avatar(html.unescape(image.group(1))) if image else None,
                "rating": rating,
                "comment": comment or None,
                "full_url": full_url,
            }
        )
    return reviews


def _needs_full_text(comment: str) -> bool:
    if len(comment) >= 450:
        return True
    return len(comment) >= 280 and comment[-1] not in ".!?…\"'»)"


def _complete_comment(review: dict[str, object], referer: str, delay: float) -> str | None:
    comment = review.get("comment")
    text = comment if isinstance(comment, str) else ""
    full_url = review.get("full_url")
    if not text or not isinstance(full_url, str) or not _needs_full_text(text):
        return text or None
    _pause(delay)
    try:
        body = fetch_letterboxd_html(
            "https://letterboxd.com" + full_url,
            delay,
            extra_headers={
                "Referer": referer,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "*/*",
            },
        )
    except (LetterboxdRefused, RuntimeError):
        return text
    completed = _plain_text(body)
    return completed or text


def _fetch_pages(url: str, delay: float) -> list[str]:
    pages: list[str] = []
    seen: set[str] = set()
    current: str | None = url
    while current and current not in seen and len(pages) < _MAX_PAGES:
        seen.add(current)
        if pages:
            _pause(delay)
        body = fetch_letterboxd_html(current, delay)
        pages.append(body)
        current = _next_page(body)
    return pages


def _film_urls(user: str, slug: str) -> tuple[str, str]:
    quoted_user = urllib.parse.quote(user, safe="")
    quoted_slug = urllib.parse.quote(slug, safe="-")
    base = f"https://letterboxd.com/{quoted_user}/friends/film/{quoted_slug}/"
    return base, base + "reviews/"


def fetch_friend_ratings(user: str, slug: str, delay: float) -> list[dict[str, object]]:
    """Friends who rated this film, with a review when they wrote one."""
    if not _USER_RE.fullmatch(user):
        raise ValueError("That Letterboxd username is not valid.")
    if not friend_slug_ok(slug):
        raise ValueError("That film slug is not valid.")
    ratings_url, reviews_url = _film_urls(user, slug)
    pages = _fetch_pages(ratings_url, delay)
    if not pages or "who have watched" not in pages[0] and "person-summary" not in pages[0]:
        raise RuntimeError(f"Letterboxd did not return friend ratings for {slug}.")
    by_user: dict[str, dict[str, object]] = {}
    order: list[str] = []
    for body in pages:
        for friend in parse_friend_rating_rows(body):
            key = str(friend["username"]).casefold()
            if key == user.casefold() or key in by_user:
                continue
            by_user[key] = friend
            order.append(key)
    wants_reviews = any(friend.get("has_review") for friend in by_user.values())
    if wants_reviews:
        _pause(delay)
        try:
            review_pages = _fetch_pages(reviews_url, delay)
        except (LetterboxdRefused, RuntimeError) as exc:
            print(f"  Friend reviews for {slug}: {exc}", file=sys.stderr)
            review_pages = []
        referer = reviews_url
        for body in review_pages:
            for review in parse_friend_reviews(body):
                key = str(review["username"]).casefold()
                if key == user.casefold():
                    continue
                comment = _complete_comment(review, referer, delay)
                current = by_user.get(key)
                if current is None:
                    by_user[key] = {
                        "username": review["username"],
                        "name": review["name"],
                        "avatar": review["avatar"],
                        "rating": review["rating"],
                        "comment": comment,
                    }
                    order.append(key)
                    continue
                current["comment"] = comment
                current["avatar"] = _prefer_avatar(
                    current.get("avatar") if isinstance(current.get("avatar"), str) else None,
                    review.get("avatar") if isinstance(review.get("avatar"), str) else None,
                )
                if current.get("rating") is None and review.get("rating") is not None:
                    current["rating"] = review["rating"]
                if current.get("name") == current.get("username") and review.get("name"):
                    current["name"] = review["name"]
    friends = [by_user[key] for key in order]
    kept = [
        friend
        for friend in friends
        if friend.get("rating") is not None or friend.get("comment")
    ]
    kept.sort(
        key=lambda friend: (
            0 if isinstance(friend.get("rating"), float) else 1,
            -(friend["rating"] if isinstance(friend.get("rating"), float) else 0),
            str(friend.get("name") or "").casefold(),
        )
    )
    cleaned: list[dict[str, object]] = []
    for friend in kept[:80]:
        rating = friend.get("rating")
        comment = friend.get("comment")
        cleaned.append(
            {
                "username": friend["username"],
                "name": friend["name"],
                "avatar": friend.get("avatar"),
                "rating": rating if isinstance(rating, float) else None,
                "comment": comment if isinstance(comment, str) and comment else None,
            }
        )
    return cleaned


def _stored_friends(raw: object) -> list[dict[str, object]] | None:
    if not isinstance(raw, list):
        return None
    friends: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        username = item.get("username")
        name = item.get("name")
        if not isinstance(username, str) or not _USER_RE.fullmatch(username):
            continue
        if not isinstance(name, str) or not name.strip():
            name = username
        rating = item.get("rating")
        if isinstance(rating, int):
            rating = float(rating)
        if not isinstance(rating, float):
            rating = None
        comment = item.get("comment")
        if not isinstance(comment, str) or not comment.strip():
            comment = None
        avatar = item.get("avatar")
        friends.append(
            {
                "username": username,
                "name": name.strip(),
                "avatar": avatar if isinstance(avatar, str) else None,
                "rating": rating,
                "comment": comment,
            }
        )
    return friends


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")


def _cached_friends(
    record: dict[str, Any],
    *,
    allow_stale: bool,
) -> list[dict[str, object]] | None:
    fetched_at = record.get("fetched_at")
    if not isinstance(fetched_at, (int, float)):
        return None
    if not allow_stale and time.time() - fetched_at > FRIEND_CACHE_MAX_AGE:
        return None
    return _stored_friends(record.get("friends"))


def ensure_friend_ratings(
    path: Path,
    user: str,
    slug: str,
    delay: float,
) -> list[dict[str, object]]:
    """Friend ratings for one film, reused for six hours."""
    if not _USER_RE.fullmatch(user):
        raise ValueError("That Letterboxd username is not valid.")
    if not friend_slug_ok(slug):
        raise ValueError("That film slug is not valid.")
    with _LOCK:
        cache = _load_cache(path)
        users = cache.get(user.casefold())
        record = users.get(slug) if isinstance(users, dict) else None
        if isinstance(record, dict):
            fresh = _cached_friends(record, allow_stale=False)
            if fresh is not None:
                return fresh
        friends: list[dict[str, object]] | None = None
        last_exc: Exception | None = None
        pause = max(delay, 1.0)
        for attempt in range(4):
            try:
                friends = fetch_friend_ratings(user, slug, delay)
                break
            except (LetterboxdRefused, RuntimeError) as exc:
                last_exc = exc
                if attempt < 3:
                    time.sleep(pause * (attempt + 2))
                    continue
        if friends is None:
            exc = last_exc or RuntimeError(f"Friend ratings for {slug} failed.")
            stale = (
                _cached_friends(record, allow_stale=True)
                if isinstance(record, dict)
                else None
            )
            if stale is not None:
                print(
                    f"Friend ratings for {slug} failed ({exc}); using the saved list.",
                    file=sys.stderr,
                )
                return stale
            raise exc
        bucket = cache.setdefault(user.casefold(), {})
        if not isinstance(bucket, dict):
            bucket = {}
            cache[user.casefold()] = bucket
        bucket[slug] = {"fetched_at": int(time.time()), "friends": friends}
        _save_cache(path, cache)
        return friends


def present_friend_ratings(friends: list[dict[str, object]]) -> dict[str, object]:
    """JSON for the unfold panel, including the same rating colors as the list."""
    shown: list[dict[str, object]] = []
    for friend in friends:
        username = str(friend["username"])
        rating = friend.get("rating")
        score = rating if isinstance(rating, float) else None
        shown.append(
            {
                "username": username,
                "name": friend.get("name") or username,
                "avatar": friend.get("avatar"),
                "profile": f"https://letterboxd.com/{urllib.parse.quote(username, safe='')}/",
                "rating": score,
                "ratingText": None if score is None else f"{score:.1f}",
                "ratingStyle": _rating_style(score),
                "comment": friend.get("comment"),
            }
        )
    return {"friends": shown}
