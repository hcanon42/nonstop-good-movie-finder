"""Letterboxd profile and the languages a viewer can follow."""

from __future__ import annotations

import json
import re
import urllib.parse

from goodmoviefinder.config import (
    DEFAULT_KNOWN_LANGUAGES,
    DEFAULT_LETTERBOXD_USER,
    REPO_ROOT,
)

VIEWER_PATH = REPO_ROOT / "viewer.json"
_USER_RE = re.compile(r"[A-Za-z0-9_-]{1,30}")


def language_keys(names: list[str]) -> frozenset[str]:
    return frozenset(name.strip().casefold() for name in names if name and name.strip())


def parse_language_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def normalize_letterboxd_user(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("Enter a Letterboxd username.")
    if text.startswith("@"):
        text = text[1:].strip()
    if "letterboxd.com" in text.casefold():
        if "://" not in text:
            text = "https://" + text
        parts = [part for part in urllib.parse.urlparse(text).path.split("/") if part]
        text = parts[0] if parts else ""
    text = text.strip().strip("/")
    if not _USER_RE.fullmatch(text):
        raise ValueError("Enter a Letterboxd username (letters, numbers, hyphens).")
    return text


def load_viewer() -> dict:
    if not VIEWER_PATH.is_file():
        return {}
    try:
        data = json.loads(VIEWER_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_viewer(
    user: str,
    languages: list[str],
    calendars: list[str] | None = None,
) -> None:
    payload: dict[str, object] = {"letterboxd_user": user, "languages": languages}
    if calendars is None:
        saved = load_calendars()
        if saved:
            payload["calendars"] = saved
    else:
        payload["calendars"] = calendars
    VIEWER_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_calendars() -> list[str]:
    raw = load_viewer().get("calendars")
    if not isinstance(raw, list):
        return []
    chosen: list[str] = []
    for item in raw:
        if item in ("apple", "google") and item not in chosen:
            chosen.append(item)
    return chosen


def language_choices(found: list[str], selected: list[str]) -> list[str]:
    """Languages to offer, with English and French first."""
    ordered: dict[str, str] = {}
    for name in (*DEFAULT_KNOWN_LANGUAGES, *selected, *found):
        cleaned = (name or "").strip()
        if cleaned:
            ordered.setdefault(cleaned.casefold(), cleaned)

    def rank(key: str) -> tuple[int, str]:
        if key == "english":
            return (0, key)
        if key == "french":
            return (1, key)
        return (2, key)

    return [ordered[key] for key in sorted(ordered, key=rank)]


def canonical_languages(selected: list[str], choices: list[str]) -> list[str]:
    """Selected languages, spelled like the program list, in checkbox order."""
    selected_keys = {name.strip().casefold() for name in selected if name and name.strip()}
    return [choice for choice in choices if choice.casefold() in selected_keys]


def settings_stamp(user: str, languages: list[str]) -> str:
    keys = ",".join(sorted(language_keys(languages)))
    return f"{user.casefold()}|{keys}"


def resolve_settings(
    user_arg: str | None,
    languages_arg: str | None,
) -> tuple[str, list[str], bool]:
    """Username, language names, and whether this run set them explicitly."""
    viewer = load_viewer()
    persist = user_arg is not None or languages_arg is not None
    if user_arg is not None:
        user = normalize_letterboxd_user(user_arg)
    else:
        raw_user = viewer.get("letterboxd_user") or DEFAULT_LETTERBOXD_USER
        user = normalize_letterboxd_user(str(raw_user))
    if languages_arg is not None:
        languages = parse_language_list(languages_arg) or list(DEFAULT_KNOWN_LANGUAGES)
    else:
        raw = viewer.get("languages")
        languages = []
        if isinstance(raw, list):
            languages = [str(item).strip() for item in raw if str(item).strip()]
        if not languages:
            languages = list(DEFAULT_KNOWN_LANGUAGES)
    return user, languages, persist
