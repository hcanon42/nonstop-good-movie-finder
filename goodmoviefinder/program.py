"""Group the program and decide which versions are followable."""

from __future__ import annotations

import urllib.parse
from datetime import date

from goodmoviefinder.config import DEFAULT_KNOWN_LANGUAGE_KEYS
from goodmoviefinder.matching import director_person_keys
from goodmoviefinder.models import Movie, ProgramFilm, Screening

# A film at or above this score makes its director a recommendation source.
LOVED_RATING = 4.0

UNDERSTOOD_AUDIO_VERSIONS = frozenset({"OV", "OmdU", "OmeU"})
ENGLISH_SUBTITLE_VERSIONS = frozenset({"OmeU"})
VERSION_ORDER = ("OV", "OmdU", "OmeU", "DF")
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_MONTHS_SHORT = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def letterboxd_slug_from_url(url: str | None) -> str | None:
    if not url:
        return None
    path = urllib.parse.urlparse(url).path.rstrip("/")
    parts = path.split("/film/")
    if len(parts) < 2 or not parts[-1]:
        return None
    return parts[-1].split("/")[0] or None


def ordered_versions(codes: set[str]) -> list[str]:
    known = [code for code in VERSION_ORDER if code in codes]
    extra = sorted(codes - set(VERSION_ORDER))
    return known + extra


def film_title(movie: Movie) -> str:
    """English Letterboxd title, or the French original for French and Quebec films."""
    language = (movie.original_language or "").casefold()
    if language.startswith("fr") and movie.original_title:
        return movie.original_title
    return movie.letterboxd_title or movie.title


def attach_program(movie: Movie, film: ProgramFilm) -> None:
    movie.versions = ordered_versions(set(film.versions))
    movie.screenings = list(film.screenings)
    movie.poster_url = film.poster_url


def acceptable_versions(
    movie: Movie,
    known_languages: frozenset[str] | None = None,
) -> list[str]:
    """Nonstop versions that count for this film's primary language."""
    known = DEFAULT_KNOWN_LANGUAGE_KEYS if known_languages is None else known_languages
    listed = set(movie.versions)
    if movie.primary_language is None:
        return ordered_versions(listed)
    if movie.primary_language.casefold() in known:
        return ordered_versions(listed & UNDERSTOOD_AUDIO_VERSIONS)
    return ordered_versions(listed & ENGLISH_SUBTITLE_VERSIONS)


def movie_is_watchable(
    movie: Movie,
    known_languages: frozenset[str] | None = None,
) -> bool:
    """Unknown primary language is not filtered. Other languages need a matching version."""
    if movie.primary_language is None:
        return True
    return bool(acceptable_versions(movie, known_languages))


def watchable_screenings(
    movie: Movie,
    known_languages: frozenset[str] | None = None,
) -> list[Screening]:
    """Screenings whose version matches the same language rules as the film list."""
    if movie.primary_language is None:
        return list(movie.screenings)
    allowed = set(acceptable_versions(movie, known_languages))
    return [screening for screening in movie.screenings if screening.language in allowed]


def language_filter_blurb(languages: list[str]) -> str:
    """Subtitle for the main program, from the languages the viewer knows."""
    names = [name.strip() for name in languages if name.strip()]
    if not names:
        phrase = "English subtitles"
    elif len(names) == 1:
        phrase = f"{names[0]} original, or English subtitles"
    elif len(names) == 2:
        phrase = f"{names[0]} or {names[1]} original, or English subtitles"
    else:
        phrase = f"{', '.join(names[:-1])}, or {names[-1]} original, or English subtitles"
    return f"{phrase} — sorted by Letterboxd rating"


def venue_label(screening: Screening) -> str:
    if screening.venue_name and screening.city:
        return f"{screening.venue_name}, {screening.city}"
    return screening.venue_name or screening.city or screening.venue_slug or "Cinema"


def format_screening_date(weekday: str) -> str:
    try:
        day = date.fromisoformat(weekday)
    except ValueError:
        return weekday
    return f"{_WEEKDAYS[day.weekday()]} {day.day:02d} {_MONTHS_SHORT[day.month]}"


def format_language(movie: Movie) -> str:
    return movie.primary_language or "—"


def format_language_versions(movie: Movie) -> str:
    language = format_language(movie)
    codes = acceptable_versions(movie)
    if not codes:
        return language
    return f"{language} · {', '.join(codes)}"


def sort_movies(movies: list[Movie]) -> list[Movie]:
    def sort_key(m: Movie) -> tuple[int, float, str]:
        if m.rating is None:
            return (1, 0.0, film_title(m).casefold())
        return (0, -m.rating, film_title(m).casefold())

    return sorted(movies, key=sort_key)


def _loved_director_index(
    watched_ratings: dict[str, float | None],
    loved_credits: dict[str, dict],
) -> dict[str, list[tuple[float, str, int | None, str]]]:
    """Director name → films this viewer rated at least LOVED_RATING."""
    index: dict[str, list[tuple[float, str, int | None, str]]] = {}
    for slug, rating in watched_ratings.items():
        if not isinstance(rating, (int, float)) or rating < LOVED_RATING:
            continue
        credit = loved_credits.get(slug) or {}
        title = credit.get("title")
        directors = credit.get("directors") or []
        if not isinstance(title, str) or not title.strip() or not directors:
            continue
        year = credit.get("year")
        film_year = year if isinstance(year, int) else None
        item = (float(rating), title.strip(), film_year, slug)
        for key in director_person_keys([str(name) for name in directors]):
            index.setdefault(key, []).append(item)
    return index


def _recommendation_because(
    movie: Movie,
    index: dict[str, list[tuple[float, str, int | None, str]]],
    own_slug: str | None,
) -> str | None:
    """The highest-rated loved film that shares a director with this one."""
    best: tuple[float, str, int | None, str] | None = None
    seen: set[str] = set()
    for key in director_person_keys(movie.directors):
        for rating, title, year, slug in index.get(key, ()):
            if slug == own_slug or slug in seen:
                continue
            seen.add(slug)
            if best is None or rating > best[0] or (
                rating == best[0] and title.casefold() < best[1].casefold()
            ):
                best = (rating, title, year, slug)
    if best is None:
        return None
    rating, title, year, _slug = best
    film = f"{title} ({year})" if year is not None else title
    return f"Same director as {film}, which you rated {rating:.1f}"


def partition_program_movies(
    movies: list[Movie],
    watched_ratings: dict[str, float | None],
    watchlist_slugs: set[str],
    loved_credits: dict[str, dict] | None = None,
) -> tuple[list[Movie], list[Movie], list[Movie], list[Movie]]:
    """Watchlist, recommendations, already watched, and the full program.

    Watchlist and recommendation titles are listed in those sections and again
    in the full program. Already watched titles stay out of the full program.
    """
    watchlist: list[Movie] = []
    recommendations: list[Movie] = []
    watched: list[Movie] = []
    program: list[Movie] = []
    index = _loved_director_index(watched_ratings, loved_credits or {})

    for movie in movies:
        movie.because = None
        slug = letterboxd_slug_from_url(movie.letterboxd_url)
        if slug and slug in watched_ratings:
            movie.user_rating = watched_ratings[slug]
            watched.append(movie)
            continue
        because = _recommendation_because(movie, index, slug)
        if slug and slug in watchlist_slugs:
            movie.because = because
            watchlist.append(movie)
            program.append(movie)
            continue
        if because:
            movie.because = because
            recommendations.append(movie)
            program.append(movie)
            continue
        program.append(movie)

    return (
        sort_movies(watchlist),
        sort_movies(recommendations),
        sort_movies(watched),
        sort_movies(program),
    )


def split_program_groups(movies: list[Movie]) -> tuple[list[Movie], list[Movie], list[Movie]]:
    """Rated films, Letterboxd matches with no score, and titles with no match."""
    rated: list[Movie] = []
    unrated: list[Movie] = []
    unfound: list[Movie] = []
    for movie in movies:
        if movie.rating is not None:
            rated.append(movie)
        elif movie.letterboxd_url:
            unrated.append(movie)
        else:
            unfound.append(movie)
    return sort_movies(rated), sort_movies(unrated), sort_movies(unfound)


def _format_year(m: Movie) -> str:
    return str(m.year) if m.year is not None else "—"


def _format_directors(m: Movie) -> str:
    return ", ".join(m.directors) if m.directors else "—"


def _format_genres(m: Movie) -> str:
    return ", ".join(m.genres) if m.genres else "—"


def _format_user_rating(rating: float | None) -> str:
    if rating is None:
        return "—"
    return f"{rating:.1f}"
