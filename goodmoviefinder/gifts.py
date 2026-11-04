"""Films you set aside for one Letterboxd friend, with a line about why."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

from goodmoviefinder.friends import ensure_following_list
from goodmoviefinder.letterboxd import letterboxd_poster_url, normalize_letterboxd_film_slug
from goodmoviefinder.rating_style import rating_style
from goodmoviefinder.viewer import normalize_letterboxd_user

GIFT_LIMIT = 8
_NOTE_MAX = 240
_TITLE_MAX = 200
_LOCK = threading.Lock()
_YEAR_MIN = 1880
_YEAR_MAX = 2100


def _load(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    raw = data.get("gifts") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    gifts: list[dict[str, object]] = []
    for item in raw:
        gift = _stored_gift(item)
        if gift is not None:
            gifts.append(gift)
    return gifts


def _save(path: Path, gifts: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"gifts": gifts}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _stored_gift(item: object) -> dict[str, object] | None:
    if not isinstance(item, dict):
        return None
    giver = item.get("from")
    recipient = item.get("to")
    slug = item.get("slug")
    title = item.get("title")
    if not isinstance(giver, str) or not isinstance(recipient, str):
        return None
    if not isinstance(slug, str) or not isinstance(title, str) or not title.strip():
        return None
    try:
        giver_name = normalize_letterboxd_user(giver)
        recipient_name = normalize_letterboxd_user(recipient)
    except ValueError:
        return None
    film = normalize_letterboxd_film_slug(slug)
    if film is None or giver_name.casefold() == recipient_name.casefold():
        return None
    year = _stored_year(item.get("year"))
    note = item.get("note")
    text = note.strip() if isinstance(note, str) else ""
    if len(text) > _NOTE_MAX:
        text = text[:_NOTE_MAX].rstrip()
    added = item.get("addedAt")
    if isinstance(added, bool) or not isinstance(added, (int, float)):
        added_at = 0
    else:
        added_at = int(added)
    poster = letterboxd_poster_url(item.get("poster"))
    gift: dict[str, object] = {
        "from": giver_name,
        "to": recipient_name,
        "slug": film,
        "title": title.strip(),
        "note": text,
        "addedAt": added_at,
    }
    if year is not None:
        gift["year"] = year
    if poster is not None:
        gift["poster"] = poster
    return gift


def _stored_year(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    year = int(value)
    if year < _YEAR_MIN or year > _YEAR_MAX:
        return None
    return year


def load_gifts(path: Path) -> list[dict[str, object]]:
    with _LOCK:
        return _load(path)


def _clean_title(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("That film needs a title.")
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        raise ValueError("That film needs a title.")
    if len(text) > _TITLE_MAX:
        raise ValueError("That title is too long.")
    return text


def _clean_note(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Keep the note to one line.")
    text = value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    if len(text) > _NOTE_MAX:
        raise ValueError("Keep the note to one line.")
    return text


def _clean_year(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("That year is not valid.")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("That year is not valid.")
    year = int(value)
    if year < _YEAR_MIN or year > _YEAR_MAX:
        raise ValueError("That year is not valid.")
    return year


def _same_gift(gift: dict[str, object], giver: str, recipient: str, slug: str) -> bool:
    return (
        str(gift["from"]).casefold() == giver.casefold()
        and str(gift["to"]).casefold() == recipient.casefold()
        and str(gift["slug"]) == slug
    )


def _pair_count(gifts: list[dict[str, object]], giver: str, recipient: str) -> int:
    return sum(
        1
        for gift in gifts
        if str(gift["from"]).casefold() == giver.casefold()
        and str(gift["to"]).casefold() == recipient.casefold()
    )


def recipient_block_reason(record: dict[str, Any] | None, username: str) -> str | None:
    """Why this friend cannot be given a film already stored in friends.json."""
    if not isinstance(record, dict):
        return None
    key = username.casefold()
    friends = record.get("friends")
    if isinstance(friends, list):
        for friend in friends:
            if not isinstance(friend, dict):
                continue
            name = friend.get("username")
            if not isinstance(name, str) or name.casefold() != key:
                continue
            rating = friend.get("rating")
            if isinstance(rating, bool) or not isinstance(rating, (int, float)):
                continue
            if 0.5 <= float(rating) <= 5:
                return "They've already rated this."
    watchlist = record.get("watchlist")
    if isinstance(watchlist, list):
        for friend in watchlist:
            if not isinstance(friend, dict):
                continue
            name = friend.get("username")
            if isinstance(name, str) and name.casefold() == key:
                return "It's already on their watchlist."
    return None


def _friend_record(path: Path, user: str, slug: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    users = data.get(user.casefold())
    if not isinstance(users, dict):
        return None
    record = users.get(slug)
    return record if isinstance(record, dict) else None


def _upsert(
    gifts: list[dict[str, object]],
    entry: dict[str, object],
    *,
    display_name: str,
) -> list[dict[str, object]]:
    giver = str(entry["from"])
    recipient = str(entry["to"])
    slug = str(entry["slug"])
    kept = [gift for gift in gifts if not _same_gift(gift, giver, recipient, slug)]
    replacing = len(kept) != len(gifts)
    if not replacing and _pair_count(gifts, giver, recipient) >= GIFT_LIMIT:
        raise ValueError(f"{display_name}'s list is full — remove one first.")
    return [entry, *kept]


def save_gift(
    path: Path,
    *,
    giver: str,
    payload: dict[str, object],
    delay: float,
    following_path: Path,
    friends_path: Path,
) -> list[dict[str, object]]:
    """Add a film to one friend's list, or refresh the note if it is already there."""
    giver_name = normalize_letterboxd_user(giver)
    raw_to = payload.get("to")
    if not isinstance(raw_to, str):
        raise ValueError("Pick a friend.")
    recipient = normalize_letterboxd_user(raw_to)
    if recipient.casefold() == giver_name.casefold():
        raise ValueError("Pick someone else.")
    raw_slug = payload.get("slug")
    if not isinstance(raw_slug, str):
        raise ValueError("That film slug is not valid.")
    slug = normalize_letterboxd_film_slug(raw_slug)
    if slug is None:
        raise ValueError("That film slug is not valid.")
    title = _clean_title(payload.get("title"))
    year = _clean_year(payload.get("year"))
    note = _clean_note(payload.get("note"))
    poster = letterboxd_poster_url(payload.get("poster"))
    members = ensure_following_list(following_path, giver_name, delay)
    match = next(
        (
            member
            for member in members
            if str(member.get("username") or "").casefold() == recipient.casefold()
        ),
        None,
    )
    if match is None:
        raise ValueError("Follow them on Letterboxd first.")
    recipient_name = str(match.get("username") or recipient)
    display_name = str(match.get("name") or recipient_name)
    reason = recipient_block_reason(_friend_record(friends_path, giver_name, slug), recipient_name)
    if reason:
        raise ValueError(reason)
    entry: dict[str, object] = {
        "from": giver_name,
        "to": recipient_name,
        "slug": slug,
        "title": title,
        "note": note,
        "addedAt": int(time.time()),
    }
    if year is not None:
        entry["year"] = year
    if poster is not None:
        entry["poster"] = poster
    with _LOCK:
        gifts = _load(path)
        if poster is None:
            previous = next(
                (gift for gift in gifts if _same_gift(gift, giver_name, recipient_name, slug)),
                None,
            )
            if previous is not None and isinstance(previous.get("poster"), str):
                entry["poster"] = previous["poster"]
        updated = _upsert(gifts, entry, display_name=display_name)
        _save(path, updated)
        return updated


def remove_gift(
    path: Path,
    *,
    giver: str,
    payload: dict[str, object],
) -> list[dict[str, object]]:
    giver_name = normalize_letterboxd_user(giver)
    raw_to = payload.get("to")
    if not isinstance(raw_to, str):
        raise ValueError("Pick a friend.")
    recipient = normalize_letterboxd_user(raw_to)
    raw_slug = payload.get("slug")
    if not isinstance(raw_slug, str):
        raise ValueError("That film slug is not valid.")
    slug = normalize_letterboxd_film_slug(raw_slug)
    if slug is None:
        raise ValueError("That film slug is not valid.")
    with _LOCK:
        gifts = _load(path)
        kept = [gift for gift in gifts if not _same_gift(gift, giver_name, recipient, slug)]
        if len(kept) == len(gifts):
            raise ValueError("That film is not on their list.")
        _save(path, kept)
        return kept


def saved_people(following_path: Path) -> dict[str, dict[str, object]]:
    """Names and photos already stored with the following list."""
    if not following_path.is_file():
        return {}
    try:
        data = json.loads(following_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    people: dict[str, dict[str, object]] = {}
    for record in data.values():
        if not isinstance(record, dict):
            continue
        friends = record.get("friends")
        if not isinstance(friends, list):
            continue
        for member in friends:
            if not isinstance(member, dict):
                continue
            username = member.get("username")
            if not isinstance(username, str) or not username:
                continue
            name = member.get("name")
            avatar = member.get("avatar")
            people[username.casefold()] = {
                "username": username,
                "name": name.strip() if isinstance(name, str) and name.strip() else username,
                "avatar": avatar if isinstance(avatar, str) else None,
            }
    for key, record in data.items():
        if not isinstance(key, str) or not isinstance(record, dict):
            continue
        avatar = record.get("self_avatar")
        if not isinstance(avatar, str):
            continue
        current = people.get(key)
        if current is None:
            people[key] = {"username": key, "name": key, "avatar": avatar}
        elif not current.get("avatar"):
            current["avatar"] = avatar
    return people


def _person(people: dict[str, dict[str, object]], username: str) -> dict[str, object]:
    found = people.get(username.casefold())
    if found is None:
        return {"username": username, "name": username, "avatar": None}
    return found


def _film_payload(
    gift: dict[str, object],
    *,
    nonstop_labels: dict[str, str],
    watched: dict[str, float | None] | None,
    offers: list[dict[str, object]],
) -> dict[str, object]:
    slug = str(gift["slug"])
    year = gift.get("year")
    payload: dict[str, object] = {
        "slug": slug,
        "title": gift["title"],
        "note": gift.get("note") or "",
        "addedAt": gift.get("addedAt") or 0,
        "filmUrl": "https://letterboxd.com/film/" + urllib.parse.quote(slug, safe="") + "/",
        "poster": letterboxd_poster_url(gift.get("poster")),
        "nonstopLabel": nonstop_labels.get(slug),
        "offers": offers,
    }
    if isinstance(year, int):
        payload["year"] = year
    if watched is not None and slug in watched:
        score = watched[slug]
        payload["watched"] = True
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            payload["watchedRating"] = None
        else:
            value = float(score)
            payload["watchedRating"] = value
            payload["watchedRatingText"] = f"{value:.1f}"
            payload["watchedRatingStyle"] = rating_style(value)
    else:
        payload["watched"] = False
        payload["watchedRating"] = None
    return payload


def present_gifts(
    gifts: list[dict[str, object]],
    *,
    user: str,
    people: dict[str, dict[str, object]],
    nonstop_labels: dict[str, str],
    watched_by_user: dict[str, dict[str, float | None]],
    offers_by_slug: dict[str, list[dict[str, object]]],
) -> dict[str, object]:
    """Outgoing lists you keep, and lists addressed to the active account."""
    user_key = user.casefold()

    def groups(side: str) -> list[dict[str, object]]:
        buckets: dict[str, list[dict[str, object]]] = {}
        order: list[str] = []
        for gift in gifts:
            other = str(gift["to"] if side == "outgoing" else gift["from"])
            owner = str(gift["from"] if side == "outgoing" else gift["to"])
            if owner.casefold() != user_key:
                continue
            key = other.casefold()
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            buckets[key].append(gift)
        shown: list[dict[str, object]] = []
        for key in order:
            films = buckets[key]
            films.sort(key=lambda item: int(item.get("addedAt") or 0), reverse=True)
            person = _person(people, str(films[0]["to" if side == "outgoing" else "from"]))
            username = str(person["username"])
            # The rating belongs to whoever the film was picked for.
            watched_key = username.casefold() if side == "outgoing" else user_key
            watched = watched_by_user.get(watched_key)
            shown.append(
                {
                    "username": username,
                    "name": person.get("name") or username,
                    "avatar": person.get("avatar"),
                    "profile": "https://letterboxd.com/"
                    + urllib.parse.quote(username, safe="")
                    + "/",
                    "films": [
                        _film_payload(
                            gift,
                            nonstop_labels=nonstop_labels,
                            watched=watched,
                            offers=offers_by_slug.get(str(gift["slug"]), []),
                        )
                        for gift in films
                    ],
                }
            )
        shown.sort(
            key=lambda group: int(group["films"][0]["addedAt"]) if group["films"] else 0,
            reverse=True,
        )
        return shown

    return {"outgoing": groups("outgoing"), "incoming": groups("incoming")}
