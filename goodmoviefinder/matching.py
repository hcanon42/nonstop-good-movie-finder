"""Title slugs and the checks that decide two credits are the same film."""

from __future__ import annotations

import html
import re
import unicodedata

# Runtimes within this many minutes count as the same cut.
DURATION_CLOSE_MINUTES = 5
# A larger gap means a different film (a short versus a feature, for example).
DURATION_CONFLICT_MINUTES = 20


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


_TITLE_PIECE_RE = re.compile(r"\s+(?:[–—]|-)\s+|\s*:\s*")
_YEAR_OR_RANGE_RE = re.compile(r"^\d{4}(?:\s*[–—-]\s*\d{4})?$")


def _usable_search_query(text: str) -> bool:
    text = text.strip()
    if len(text) < 2:
        return False
    return _YEAR_OR_RANGE_RE.match(text) is None


def flexible_search_queries(
    display_title: str,
    nonstop_page_title: str | None,
) -> list[str]:
    """Titles to try with Letterboxd autocomplete after slug guesses miss.

    Order: full titles, parenthetical alternates, then pieces split on
    spaced dashes and colons. Year ranges and tiny fragments are skipped.
    """
    seen: set[str] = set()
    queries: list[str] = []

    def add(raw: str | None) -> None:
        if not raw:
            return
        text = normalize_title_chars(raw).strip()
        if not _usable_search_query(text):
            return
        key = text.casefold()
        if key in seen:
            return
        seen.add(key)
        queries.append(text)

    titles = [display_title]
    if nonstop_page_title and nonstop_page_title != display_title:
        titles.append(nonstop_page_title)
    for title in titles:
        add(title)
    for inner in parenthetical_search_queries(display_title, nonstop_page_title):
        add(inner)
    for title in titles:
        normalized = normalize_title_chars(title)
        for piece in _TITLE_PIECE_RE.split(normalized):
            add(piece)
    return queries


def _name_fold(name: str) -> str:
    name = normalize_title_chars(name)
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z]+", " ", name).strip().casefold()


def _surnames(name: str) -> set[str]:
    folded = _name_fold(name)
    if not folded:
        return set()
    return {folded.split()[-1]}


_PERSON_SPLIT = re.compile(r"\s*(?:/|,|&|\band\b)\s*")


def director_person_keys(names: list[str]) -> set[str]:
    """Folded full names for each person in a director credit."""
    keys: set[str] = set()
    for name in names:
        for chunk in _PERSON_SPLIT.split(name):
            folded = _name_fold(chunk)
            if folded:
                keys.add(folded)
    return keys


def director_overlaps(
    nonstop_director: str | None,
    letterboxd_directors: list[str],
) -> bool:
    """True when a Nonstop credit shares a surname with a Letterboxd director."""
    if not nonstop_director or not letterboxd_directors:
        return False
    left: set[str] = set()
    for chunk in re.split(r"\s*(?:,|&|\band\b)\s*", nonstop_director):
        left |= _surnames(chunk)
    right: set[str] = set()
    for name in letterboxd_directors:
        right |= _surnames(name)
    left.discard("")
    return bool(left & right)


def _name_tokens(name: str) -> list[str]:
    return [token for token in _name_fold(name).split() if len(token) > 1]


def people_overlap_count(left: list[str], right: list[str]) -> int:
    """How many people appear on both credit lists.

    A shared surname counts when at least one other name token also matches,
    so two different people who only share a last name are not treated as one.
    """
    used: set[int] = set()
    count = 0
    for name in left:
        tokens = _name_tokens(name)
        if not tokens:
            continue
        surname = tokens[-1]
        for index, other in enumerate(right):
            if index in used:
                continue
            other_tokens = _name_tokens(other)
            if not other_tokens or other_tokens[-1] != surname:
                continue
            if len(tokens) >= 2 and len(other_tokens) >= 2:
                if not (set(tokens[:-1]) & set(other_tokens[:-1])):
                    continue
            used.add(index)
            count += 1
            break
    return count


def parse_iso_duration_minutes(value: str) -> int | None:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value.strip())
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    if hours == 0 and minutes == 0 and seconds == 0:
        return None
    total = hours * 60 + minutes
    if seconds >= 30:
        total += 1
    return total


def duration_relation(left: int | None, right: int | None) -> str:
    """'close', 'conflict', or 'unknown'."""
    if left is None or right is None:
        return "unknown"
    delta = abs(left - right)
    if delta <= DURATION_CLOSE_MINUTES:
        return "close"
    if delta >= DURATION_CONFLICT_MINUTES:
        return "conflict"
    return "unknown"


def _director_status(
    nonstop_director: str | None,
    letterboxd_directors: list[str],
) -> bool | None:
    if not nonstop_director or not letterboxd_directors:
        return None
    return director_overlaps(nonstop_director, letterboxd_directors)


def film_identity_confirms(
    nonstop_year: int | None,
    nonstop_director: str | None,
    nonstop_cast: list[str],
    nonstop_duration: int | None,
    letterboxd_year: int | None,
    letterboxd_directors: list[str],
    letterboxd_actors: list[str],
    letterboxd_duration: int | None,
) -> bool:
    """True when director, runtime, or cast show this is the Nonstop film."""
    director = _director_status(nonstop_director, letterboxd_directors)
    cast_count = (
        people_overlap_count(nonstop_cast, letterboxd_actors)
        if nonstop_cast and letterboxd_actors
        else 0
    )
    duration = duration_relation(nonstop_duration, letterboxd_duration)
    year_delta = (
        abs(nonstop_year - letterboxd_year)
        if nonstop_year is not None and letterboxd_year is not None
        else None
    )
    if director is False and cast_count < 2:
        return False
    if cast_count >= 2 and duration != "conflict":
        return True
    if cast_count >= 1 and duration == "close" and director is not False:
        return True
    if director is True and duration == "close":
        return True
    if (
        director is True
        and duration != "conflict"
        and year_delta is not None
        and year_delta <= 1
    ):
        return True
    if (
        director is None
        and not nonstop_director
        and not nonstop_cast
        and duration != "conflict"
        and year_delta == 0
    ):
        return True
    if (
        director is None
        and duration == "close"
        and year_delta is not None
        and year_delta <= 1
    ):
        return True
    return False


def film_identity_conflicts(
    nonstop_year: int | None,
    nonstop_director: str | None,
    nonstop_cast: list[str],
    nonstop_duration: int | None,
    letterboxd_year: int | None,
    letterboxd_directors: list[str],
    letterboxd_actors: list[str],
    letterboxd_duration: int | None,
) -> bool:
    """True when this Letterboxd film is a different movie with a similar title."""
    if film_identity_confirms(
        nonstop_year,
        nonstop_director,
        nonstop_cast,
        nonstop_duration,
        letterboxd_year,
        letterboxd_directors,
        letterboxd_actors,
        letterboxd_duration,
    ):
        return False
    director = _director_status(nonstop_director, letterboxd_directors)
    cast_count = (
        people_overlap_count(nonstop_cast, letterboxd_actors)
        if nonstop_cast and letterboxd_actors
        else 0
    )
    duration = duration_relation(nonstop_duration, letterboxd_duration)
    year_delta = (
        abs(nonstop_year - letterboxd_year)
        if nonstop_year is not None and letterboxd_year is not None
        else None
    )
    if director is False:
        return True
    if duration == "conflict" and cast_count == 0:
        return True
    if nonstop_cast and letterboxd_actors and cast_count == 0 and director is not True:
        return True
    # Same director credit, but a distant year and no runtime or cast support.
    if (
        director is True
        and year_delta is not None
        and year_delta > 1
        and duration != "close"
        and cast_count == 0
        and (nonstop_duration is not None or bool(nonstop_cast))
    ):
        return True
    return False
