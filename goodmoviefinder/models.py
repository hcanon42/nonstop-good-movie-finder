"""Program and film records shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

@dataclass
class Screening:
    weekday: str
    time: str
    language: str
    venue_slug: str
    venue_name: str
    city: str


@dataclass
class ProgramFilm:
    title: str
    url: str
    versions: frozenset[str]
    poster_url: str | None
    screenings: list[Screening]


@dataclass
class NonstopFilmPage:
    title: str | None
    year: int | None
    director: str | None
    synopsis: str | None
    cinema_sites: dict[str, str]
    cast: list[str] = field(default_factory=list)
    duration_minutes: int | None = None


@dataclass
class Movie:
    title: str
    nonstop_url: str
    letterboxd_url: str | None = None
    letterboxd_title: str | None = None
    original_title: str | None = None
    original_language: str | None = None
    titles_known: bool = False
    rating: float | None = None
    year: int | None = None
    directors: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    primary_language: str | None = None
    primary_language_known: bool = False
    versions: list[str] = field(default_factory=list)
    poster_url: str | None = None
    synopsis: str | None = None
    cinema_sites: dict[str, str] = field(default_factory=dict)
    details_known: bool = False
    duration_minutes: int | None = None
    duration_known: bool = False
    screenings: list[Screening] = field(default_factory=list)
    note: str | None = None
    user_rating: float | None = None
    # False when a cached Letterboxd link was stored by an older matcher.
    match_current: bool = True


LetterboxdCandidate = tuple[
    str,
    str | None,
    int | None,
    float | None,
    list[str],
    list[str],
    str | None,
    str | None,
    str | None,
    list[str],
    int | None,
]
