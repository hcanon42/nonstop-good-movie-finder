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
    # Venue slugs present on the film page when cinema websites were last read.
    venues: frozenset[str] = field(default_factory=frozenset)
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
    letterboxd_poster_url: str | None = None
    # True after a Letterboxd page was read for a poster, including when it has none.
    poster_checked: bool = False
    letterboxd_backdrop_url: str | None = None
    synopsis: str | None = None
    cinema_sites: dict[str, str] = field(default_factory=dict)
    # Venue slugs seen on the Nonstop film page, including ones with no website.
    cinema_venues: list[str] = field(default_factory=list)
    cinema_venues_known: bool = False
    details_known: bool = False
    duration_minutes: int | None = None
    duration_known: bool = False
    screenings: list[Screening] = field(default_factory=list)
    note: str | None = None
    user_rating: float | None = None
    # Why this screening is recommended.
    because: str | None = None
    predicted_rating: float | None = None
    lower_estimate: float | None = None
    member_average: float | None = None
    member_support: int = 0
    # False when a cached Letterboxd link was stored by an older matcher.
    match_current: bool = True
    # True when the viewer chose the Letterboxd page; keep it across matcher bumps.
    manual_match: bool = False
    # True when this program title was looked up fresh (not from cache) this run.
    new_in_program: bool = False


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
