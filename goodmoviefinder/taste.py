"""Predict which films now playing fit one viewer's Letterboxd ratings."""

from __future__ import annotations

import hashlib
import json
import math
import pickle
import random
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from goodmoviefinder.matching import director_person_keys
from goodmoviefinder.models import Movie
from goodmoviefinder.program import letterboxd_slug_from_url

PREDICT_MIN = 4.2
LOWER_MIN = 3.8
LOVED_RATING = 4.0
LOW_RATING = 2.5
MIN_LABELS = 4
MIN_SHARED = 30
MIN_SIMILARITY = 0.25
MIN_NEIGHBORS = 4
MIN_CALLS = 10
MIN_PRECISION = 0.70
MIN_RATINGS = 500
OUTSIDE_CAP = 5
OUTSIDE_PREDICT_MIN = 3.2
OUTSIDE_TAG_MAX = 2
OUTSIDE_SOURCE_CAP = 4
OUTSIDE_PER_SOURCE = 12

COARSE_GENRES = {
    "action",
    "adventure",
    "animation",
    "comedy",
    "crime",
    "documentary",
    "drama",
    "family",
    "fantasy",
    "history",
    "horror",
    "music",
    "mystery",
    "romance",
    "science fiction",
    "tv movie",
    "thriller",
    "war",
    "western",
}

_COUNTRY_PHRASE = {
    "japan": "Japanese cinema",
    "france": "French cinema",
    "usa": "American cinema",
    "united states": "American cinema",
    "uk": "British cinema",
    "united kingdom": "British cinema",
    "south korea": "Korean cinema",
    "korea": "Korean cinema",
    "italy": "Italian cinema",
    "germany": "German cinema",
    "spain": "Spanish cinema",
    "sweden": "Swedish cinema",
    "denmark": "Danish cinema",
    "poland": "Polish cinema",
    "china": "Chinese cinema",
    "hong kong": "Hong Kong cinema",
    "taiwan": "Taiwanese cinema",
    "india": "Indian cinema",
    "iran": "Iranian cinema",
    "mexico": "Mexican cinema",
    "brazil": "Brazilian cinema",
    "canada": "Canadian cinema",
    "australia": "Australian cinema",
    "russia": "Russian cinema",
    "belgium": "Belgian cinema",
    "austria": "Austrian cinema",
    "romania": "Romanian cinema",
    "hungary": "Hungarian cinema",
    "czech republic": "Czech cinema",
    "czechia": "Czech cinema",
    "greece": "Greek cinema",
    "turkey": "Turkish cinema",
    "argentina": "Argentine cinema",
    "ireland": "Irish cinema",
    "netherlands": "Dutch cinema",
    "norway": "Norwegian cinema",
    "finland": "Finnish cinema",
    "portugal": "Portuguese cinema",
    "thailand": "Thai cinema",
}


@dataclass(frozen=True)
class OutsideTag:
    """A nanogenre the viewer has liked on only one or two films."""

    kind: str
    name: str
    slug: str
    example: str


@dataclass
class TasteView:
    paragraph: str = ""
    note: str = ""
    see_first: list[Movie] = field(default_factory=list)
    like_you: list[Movie] = field(default_factory=list)
    step_outside: list[Movie] = field(default_factory=list)


def _enough_ratings(feature: dict) -> bool:
    """Letterboxd recommendations need a stable community score."""
    members = feature.get("member_count")
    return isinstance(members, int) and members >= MIN_RATINGS


# Narrative musicals keep Drama/Comedy/etc. Filmed concerts are often Music alone.
_MUSIC_ONLY_GENRES = frozenset({"music", "tv movie"})
_LIVE_PERFORMANCE_MARKERS = (
    "concert",
    "live at ",
    "in concert",
    "in-concert",
    "live performance",
    "music festival",
    "live album",
)


def _folded_names(values: object) -> list[str]:
    names: list[str] = []
    if not isinstance(values, list):
        return names
    for value in values:
        if isinstance(value, str) and value.strip():
            names.append(value.strip())
    return names


def excluded_from_recommendations(
    movie: Movie | None = None,
    feature: dict | None = None,
) -> bool:
    """Skip documentaries and filmed concerts on Letterboxd For you lists."""
    genres = {
        name.casefold()
        for name in [
            *_folded_names(movie.genres if movie else None),
            *_folded_names((feature or {}).get("genres")),
        ]
    }
    if "documentary" in genres:
        return True
    titles: list[str] = []
    if movie is not None:
        titles.extend([movie.title, movie.letterboxd_title or ""])
    feature_title = (feature or {}).get("title")
    if isinstance(feature_title, str):
        titles.append(feature_title)
    themes = [
        *_folded_names((feature or {}).get("themes")),
        *_folded_names((feature or {}).get("mini_themes")),
        *_folded_names((feature or {}).get("nanogenres")),
    ]
    blob = " ".join(part for part in (*titles, *themes) if part).casefold()
    live = any(marker in blob for marker in _LIVE_PERFORMANCE_MARKERS)
    if "music" in genres and (live or not (genres - _MUSIC_ONLY_GENRES)):
        return True
    return False


def _theme_keys(feature: dict) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name in [
        *(feature.get("themes") or []),
        *(feature.get("mini_themes") or []),
    ]:
        if not isinstance(name, str) or not name.strip():
            continue
        key = ("theme", name.strip())
        if key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def _theme_overlap(left: dict, right: dict) -> float:
    """Jaccard on themes. Missing tags are not similarity — two untagged films can be unrelated."""
    mine = {(kind, name.casefold()) for kind, name in _theme_keys(left)}
    theirs = {(kind, name.casefold()) for kind, name in _theme_keys(right)}
    if not mine or not theirs:
        return 0.0
    return len(mine & theirs) / len(mine | theirs)


def _fine_keys(feature: dict) -> list[tuple[str, str]]:
    keys = list(_theme_keys(feature))
    seen = set(keys)
    for name in feature.get("countries") or []:
        if not isinstance(name, str) or not name.strip():
            continue
        key = ("country", name.strip())
        if key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def _phrase(key: tuple[str, str]) -> str:
    kind, name = key
    if kind == "country":
        return _COUNTRY_PHRASE.get(name.casefold(), f"films from {name}")
    return name


def _join(phrases: list[str]) -> str:
    if not phrases:
        return ""
    if len(phrases) == 1:
        return phrases[0]
    if len(phrases) == 2:
        return f"{phrases[0]} and {phrases[1]}"
    return f"{', '.join(phrases[:-1])}, and {phrases[-1]}"


def _label_stats(
    rows: list[tuple[str, float]],
    features: dict[str, dict],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], dict[tuple[str, str], int]]:
    sums: dict[tuple[str, str], float] = {}
    counts: dict[tuple[str, str], int] = {}
    for slug, rating in rows:
        feature = features.get(slug)
        if not feature:
            continue
        for key in _fine_keys(feature):
            counts[key] = counts.get(key, 0) + 1
            sums[key] = sums.get(key, 0.0) + rating
    high: list[tuple[float, int, tuple[str, str]]] = []
    low: list[tuple[float, int, tuple[str, str]]] = []
    for key, count in counts.items():
        if count < MIN_LABELS:
            continue
        mean = sums[key] / count
        if mean >= LOVED_RATING:
            high.append((mean, count, key))
        elif mean <= LOW_RATING:
            low.append((mean, count, key))
    high.sort(key=lambda item: (-item[0], -item[1], item[2][1].casefold()))
    low.sort(key=lambda item: (item[0], -item[1], item[2][1].casefold()))
    return [item[2] for item in high], [item[2] for item in low], counts


def _paragraph(high_phrases: list[str], low_phrases: list[str], *, themes: bool) -> str:
    sentences: list[str] = []
    if high_phrases and themes:
        sentences.append(f"The themes you rate most highly are {_join(high_phrases)}.")
    elif high_phrases:
        sentences.append(f"You tend to rate {_join(high_phrases)} highly.")
    if low_phrases:
        sentences.append(f"{_join(low_phrases)} sit lower in your diary.")
    if high_phrases and low_phrases:
        sentences.append(
            "A step outside follows nanogenres you have liked only once or twice, "
            "and skips the second group."
        )
    elif high_phrases:
        sentences.append(
            "A step outside follows nanogenres you have liked only once or twice."
        )
    elif not sentences:
        return (
            "There are not enough repeated themes in your ratings "
            "to describe a taste yet."
        )
    return " ".join(sentences)


def _bridge_mini_themes(
    rows: list[tuple[str, float]],
    features: dict[str, dict],
) -> list[str]:
    """Mini-themes rated well and logged often enough to describe a taste."""
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for slug, rating in rows:
        feature = features.get(slug)
        if not feature:
            continue
        for name in feature.get("mini_themes") or []:
            if not isinstance(name, str) or not name.strip():
                continue
            counts[name] = counts.get(name, 0) + 1
            sums[name] = sums.get(name, 0.0) + rating
    low = {
        name
        for name, count in counts.items()
        if count >= MIN_LABELS and sums[name] / count <= LOW_RATING
    }
    ranked: list[tuple[float, int, str]] = []
    for name, count in counts.items():
        if count < 8 or name in low:
            continue
        mean = sums[name] / count
        if mean < 3.6:
            continue
        ranked.append((mean, count, name))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2].casefold()))
    return [name for _mean, _count, name in ranked[:3]]


def _carried_by_low(
    feature: dict,
    low_keys: set[tuple[str, str]],
    high_keys: set[tuple[str, str]],
) -> bool:
    labels = _fine_keys(feature)
    low_hits = [key for key in labels if key in low_keys]
    high_hits = [key for key in labels if key in high_keys]
    return bool(low_hits) and not high_hits


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 3.0


class _Space:
    """Numeric features for one training fold. Affinities never include the film itself."""

    def __init__(self, known_languages: frozenset[str]) -> None:
        self.known_languages = known_languages
        self.fine_vocab: list[tuple[str, str]] = []
        self.genre_vocab: list[str] = []
        self.user_mean = 3.0
        self.directors: dict[str, list[tuple[str, float]]] = {}
        self.actors: dict[str, list[tuple[str, float]]] = {}
        self.rows: list[tuple[str, float, dict]] = []

    def fit(self, rows: list[tuple[str, float]], features: dict[str, dict]) -> None:
        self.user_mean = _mean([rating for _slug, rating in rows])
        fine_counts: dict[tuple[str, str], int] = {}
        genre_counts: dict[str, str] = {}
        genre_n: dict[str, int] = {}
        actor_films: dict[str, set[str]] = {}
        self.rows = []
        for slug, rating in rows:
            feature = features.get(slug)
            if not feature:
                continue
            self.rows.append((slug, rating, feature))
            for key in _fine_keys(feature):
                fine_counts[key] = fine_counts.get(key, 0) + 1
            for genre in feature.get("genres") or []:
                if not isinstance(genre, str) or not genre.strip():
                    continue
                folded = genre.strip().casefold()
                genre_counts.setdefault(folded, genre.strip())
                genre_n[folded] = genre_n.get(folded, 0) + 1
            for name in feature.get("directors") or []:
                for key in director_person_keys([str(name)]):
                    self.directors.setdefault(key, []).append((slug, rating))
            for name in feature.get("actors") or []:
                if isinstance(name, str) and name.strip():
                    actor_films.setdefault(name.strip().casefold(), set()).add(slug)
                    self.actors.setdefault(name.strip().casefold(), []).append((slug, rating))
        self.fine_vocab = sorted(
            (key for key, count in fine_counts.items() if count >= 3),
            key=lambda key: (key[0], key[1].casefold()),
        )
        self.genre_vocab = sorted(
            (genre_counts[key] for key, count in genre_n.items() if count >= 3),
            key=str.casefold,
        )
        self.actors = {
            name: pairs
            for name, pairs in self.actors.items()
            if len(actor_films.get(name, ())) >= 2
        }

    def _affinity(
        self,
        slug: str,
        names: list[str],
        index: dict[str, list[tuple[str, float]]],
        *,
        directors: bool,
    ) -> float:
        pooled: dict[str, float] = {}
        if directors:
            keys: list[str] = []
            for name in names:
                keys.extend(director_person_keys([name]))
            for key in keys:
                for other, rating in index.get(key, ()):
                    if other != slug:
                        pooled[other] = rating
        else:
            for name in names:
                for other, rating in index.get(name.casefold(), ()):
                    if other != slug:
                        pooled[other] = rating
        if not pooled:
            return self.user_mean
        values = list(pooled.values())
        return (sum(values) + 3 * self.user_mean) / (len(values) + 3)

    def _nearness(self, slug: str, feature: dict) -> tuple[float, float]:
        my_directors = set(director_person_keys([str(name) for name in feature.get("directors") or []]))
        my_actors = {
            name.strip().casefold()
            for name in feature.get("actors") or []
            if isinstance(name, str) and name.strip()
        }
        scored: list[tuple[float, float, str]] = []
        for other_slug, rating, other in self.rows:
            if other_slug == slug:
                continue
            overlap = _theme_overlap(feature, other)
            their_directors = set(
                director_person_keys([str(name) for name in other.get("directors") or []])
            )
            bonus = 1.0 if my_directors & their_directors else 0.0
            their_actors = {
                name.strip().casefold()
                for name in other.get("actors") or []
                if isinstance(name, str) and name.strip()
            }
            score = overlap + bonus + 0.15 * min(3, len(my_actors & their_actors))
            if score > 0:
                scored.append((score, rating, other_slug))
        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
        top = scored[:8]
        if not top:
            liked = sum(1 for _slug, rating in ((row[0], row[1]) for row in self.rows) if rating >= LOVED_RATING)
            base = liked / len(self.rows) if self.rows else 0.0
            return self.user_mean, base
        mean = sum(item[1] for item in top) / len(top)
        liked_frac = sum(1 for item in top if item[1] >= LOVED_RATING) / len(top)
        return mean, liked_frac

    def vector(self, slug: str, feature: dict) -> list[float]:
        fine = set(_fine_keys(feature))
        genres = {
            genre.strip().casefold()
            for genre in feature.get("genres") or []
            if isinstance(genre, str) and genre.strip()
        }
        values = [1.0 if key in fine else 0.0 for key in self.fine_vocab]
        values.extend(
            1.0 if genre.casefold() in genres else 0.0 for genre in self.genre_vocab
        )
        year = feature.get("year")
        runtime = feature.get("runtime")
        community = feature.get("community_rating")
        members = feature.get("member_count")
        language = feature.get("language")
        values.append(float(year) if isinstance(year, int) else math.nan)
        values.append(float(runtime) if isinstance(runtime, int) else math.nan)
        values.append(float(community) if isinstance(community, (int, float)) else math.nan)
        values.append(math.log1p(members) if isinstance(members, int) and members > 0 else math.nan)
        known = (
            1.0
            if isinstance(language, str) and language.casefold() in self.known_languages
            else 0.0
        )
        values.append(known)
        values.append(
            self._affinity(
                slug,
                [str(name) for name in feature.get("directors") or []],
                self.directors,
                directors=True,
            )
        )
        values.append(
            self._affinity(
                slug,
                [str(name) for name in feature.get("actors") or []],
                self.actors,
                directors=False,
            )
        )
        near_mean, near_liked = self._nearness(slug, feature)
        values.append(near_mean)
        values.append(near_liked)
        if not values:
            values.append(1.0)
        return values

    def nearest_titles(self, slug: str, feature: dict, limit: int = 2) -> list[str]:
        my_directors = set(director_person_keys([str(name) for name in feature.get("directors") or []]))
        scored: list[tuple[float, float, str]] = []
        for other_slug, rating, other in self.rows:
            if other_slug == slug or rating < LOVED_RATING:
                continue
            overlap = _theme_overlap(feature, other)
            their_directors = set(
                director_person_keys([str(name) for name in other.get("directors") or []])
            )
            score = overlap + (1.0 if my_directors & their_directors else 0.0)
            if score <= 0:
                continue
            title = other.get("title") or other_slug
            year = other.get("year")
            film = f"{title} ({year})" if isinstance(year, int) else str(title)
            scored.append((score, rating, f"{film}, which you rated {rating:.1f}"))
        scored.sort(key=lambda item: (-item[0], -item[1], item[2].casefold()))
        phrases: list[str] = []
        seen: set[str] = set()
        for _score, _rating, phrase in scored:
            if phrase in seen:
                continue
            seen.add(phrase)
            phrases.append(phrase)
            if len(phrases) >= limit:
                break
        return phrases


def _fit_models(space: _Space):
    from sklearn.ensemble import HistGradientBoostingRegressor

    matrix = [space.vector(slug, feature) for slug, _rating, feature in space.rows]
    target = [rating for _slug, rating, _feature in space.rows]
    leaf = max(2, min(8, len(target) // 10))
    shared = dict(
        learning_rate=0.06,
        max_iter=300,
        max_leaf_nodes=15,
        min_samples_leaf=leaf,
        l2_regularization=1.0,
        random_state=0,
    )
    mean_model = HistGradientBoostingRegressor(loss="squared_error", **shared)
    low_model = HistGradientBoostingRegressor(loss="quantile", quantile=0.25, **shared)
    mean_model.fit(matrix, target)
    low_model.fit(matrix, target)
    return mean_model, low_model


def _predict_pair(space: _Space, mean_model, low_model, slug: str, feature: dict) -> tuple[float, float]:
    row = [space.vector(slug, feature)]
    predicted = float(mean_model.predict(row)[0])
    lower = float(low_model.predict(row)[0])
    return predicted, lower


def _content_check(
    rows: list[tuple[str, float]],
    features: dict[str, dict],
    known_languages: frozenset[str],
) -> tuple[bool, float | None, int]:
    if len(rows) < 40:
        return False, None, 0
    order = list(rows)
    random.Random(0).shuffle(order)
    folds = [order[index::5] for index in range(5)]
    calls: list[bool] = []
    for index, test in enumerate(folds):
        train = [row for fold_index, fold in enumerate(folds) if fold_index != index for row in fold]
        if len(train) < 30 or not test:
            continue
        space = _Space(known_languages)
        space.fit(train, features)
        if len(space.rows) < 30:
            continue
        mean_model, low_model = _fit_models(space)
        high, low, _counts = _label_stats(train, features)
        high_keys = set(high)
        low_keys = set(low)
        for slug, actual in test:
            feature = features.get(slug)
            if not feature:
                continue
            predicted, lower = _predict_pair(space, mean_model, low_model, slug, feature)
            if (
                predicted >= PREDICT_MIN
                and lower >= LOWER_MIN
                and not _carried_by_low(feature, low_keys, high_keys)
            ):
                calls.append(actual >= LOVED_RATING)
    if not calls:
        return False, None, 0
    precision = sum(1 for call in calls if call) / len(calls)
    return len(calls) >= MIN_CALLS and precision >= MIN_PRECISION, precision, len(calls)


def _centered(ratings: dict[str, float]) -> tuple[float, dict[str, float]]:
    mean = _mean(list(ratings.values()))
    return mean, {slug: rating - mean for slug, rating in ratings.items()}


def _similarities(
    user_ratings: dict[str, float],
    neighbors: dict[str, dict[str, float]],
) -> dict[str, float]:
    _mean_value, centered = _centered(user_ratings)
    kept: dict[str, float] = {}
    for name, diary in neighbors.items():
        if len(diary) < 2:
            continue
        their_mean = _mean(list(diary.values()))
        shared = [slug for slug in diary if slug in centered]
        if len(shared) < MIN_SHARED:
            continue
        dot = norm_user = norm_theirs = 0.0
        for slug in shared:
            user_value = centered[slug]
            their_value = diary[slug] - their_mean
            dot += user_value * their_value
            norm_user += user_value * user_value
            norm_theirs += their_value * their_value
        if norm_user <= 0 or norm_theirs <= 0:
            continue
        cosine = dot / math.sqrt(norm_user * norm_theirs)
        if cosine >= MIN_SIMILARITY:
            kept[name] = cosine
    return kept


def _neighbor_score(
    slug: str,
    sims: dict[str, float],
    neighbors: dict[str, dict[str, float]],
    user_mean: float,
) -> tuple[float | None, int]:
    weighted = 0.0
    weight = 0.0
    support = 0
    for name, similarity in sims.items():
        rating = neighbors.get(name, {}).get(slug)
        if rating is None:
            continue
        weighted += similarity * rating
        weight += similarity
        support += 1
    if support < MIN_NEIGHBORS or weight <= 0:
        return None, support
    shrink = 8 / support
    predicted = (weighted + shrink * user_mean) / (weight + shrink)
    return predicted, support


def _neighbor_check(
    user_ratings: dict[str, float],
    neighbors: dict[str, dict[str, float]],
) -> tuple[bool, float | None, int]:
    slugs = list(user_ratings)
    if len(slugs) < 40 or not neighbors:
        return False, None, 0
    random.Random(1).shuffle(slugs)
    cut = max(1, len(slugs) // 5)
    holdout = slugs[:cut]
    train = {slug: user_ratings[slug] for slug in slugs[cut:]}
    if len(train) < 30:
        return False, None, 0
    sims = _similarities(train, neighbors)
    user_mean = _mean(list(train.values()))
    calls: list[bool] = []
    for slug in holdout:
        predicted, support = _neighbor_score(slug, sims, neighbors, user_mean)
        if predicted is not None and support >= MIN_NEIGHBORS and predicted >= PREDICT_MIN:
            calls.append(user_ratings[slug] >= LOVED_RATING)
    if not calls:
        return False, None, 0
    precision = sum(1 for call in calls if call) / len(calls)
    return len(calls) >= MIN_CALLS and precision >= MIN_PRECISION, precision, len(calls)


def _percent(precision: float | None) -> str:
    if precision is None:
        return ""
    return f"{round(precision * 100)}%"


def _note(
    *,
    content_ok: bool,
    content_precision: float | None,
    content_calls: int,
    neighbor_ok: bool,
    neighbor_precision: float | None,
    neighbor_calls: int,
    have_neighbors: bool,
    content_only: bool,
    use_content: bool,
) -> str:
    sentences: list[str] = []
    if content_precision is None:
        sentences.append(
            "The rating model did not make enough held-out predictions to check."
        )
    else:
        sentences.append(
            "When the rating model predicted you would like a film, you had rated it "
            f"4 or higher {_percent(content_precision)} of the time ({content_calls} films)."
        )
    if not have_neighbors:
        sentences.append("Similar members were not available.")
    elif neighbor_precision is None:
        sentences.append(
            "The member model did not make enough held-out predictions to check."
        )
    else:
        sentences.append(
            "When members like you pointed at a film, you had rated it 4 or higher "
            f"{_percent(neighbor_precision)} of the time ({neighbor_calls} films)."
        )
    if not use_content and not neighbor_ok:
        sentences.append("That is not confident enough to recommend a film.")
    elif use_content and not content_ok and not neighbor_ok:
        sentences.append(
            "That check is under the usual bar. These are still the strongest predictions from your ratings."
        )
    elif content_only:
        sentences.append("These picks use your ratings only.")
    return " ".join(sentences)


def _hash(
    ratings: dict[str, float],
    features: dict[str, dict],
    neighbors: dict[str, dict[str, float]],
    known_languages: frozenset[str],
) -> str:
    digest = hashlib.sha256()
    digest.update(b"near-themes-v2")
    digest.update(",".join(sorted(known_languages)).encode())
    for slug in sorted(ratings):
        digest.update(slug.encode())
        digest.update(str(ratings[slug]).encode())
        feature = {
            key: value
            for key, value in (features.get(slug) or {}).items()
            if key not in {"nanogenres", "nanogenre_slugs", "nanogenres_known"}
        }
        digest.update(json.dumps(feature, sort_keys=True, default=str).encode())
    for name in sorted(neighbors):
        diary = neighbors[name]
        digest.update(name.encode())
        digest.update(str(len(diary)).encode())
        digest.update(f"{sum(diary.values()):.3f}".encode())
    return digest.hexdigest()


def _bundle_path(cache_path: Path, user: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", user) or "viewer"
    return cache_path.parent / f"taste-{safe}.pkl"


def _loved_director_index(
    rows: list[tuple[str, float]],
    features: dict[str, dict],
) -> dict[str, tuple[float, str]]:
    index: dict[str, tuple[float, str]] = {}
    for slug, rating in rows:
        if rating < LOVED_RATING:
            continue
        feature = features.get(slug) or {}
        title = feature.get("title") or slug
        year = feature.get("year")
        film = f"{title} ({year})" if isinstance(year, int) else str(title)
        phrase = f"{film}, which you rated {rating:.1f}"
        for key in director_person_keys([str(name) for name in feature.get("directors") or []]):
            current = index.get(key)
            if current is None or rating > current[0]:
                index[key] = (rating, phrase)
    return index


def _earliest(movie: Movie) -> int:
    ordinals: list[int] = []
    for screening in movie.screenings:
        try:
            ordinals.append(date.fromisoformat(screening.weekday).toordinal())
        except ValueError:
            continue
    return min(ordinals) if ordinals else 10**9


def _median_loved_year(rows: list[tuple[str, float]], features: dict[str, dict]) -> int | None:
    years = sorted(
        features[slug]["year"]
        for slug, rating in rows
        if rating >= LOVED_RATING
        and slug in features
        and isinstance(features[slug].get("year"), int)
    )
    if not years:
        return None
    return years[len(years) // 2]


def recommend(
    movies: list[Movie],
    watched_ratings: dict[str, float | None],
    watchlist_slugs: set[str],
    features: dict[str, dict],
    neighbors: dict[str, dict[str, float]],
    *,
    known_languages: frozenset[str],
    cache_path: Path,
    user: str,
    like_cap: int = 12,
    outside_hits: dict[str, OutsideTag] | None = None,
) -> TasteView:
    """Score films. A trained rating model still recommends when its check is under 70%."""
    for movie in movies:
        movie.because = None
        movie.predicted_rating = None
        movie.lower_estimate = None
        movie.member_average = None
        movie.member_support = 0

    rated_rows = [
        (slug, float(rating))
        for slug, rating in watched_ratings.items()
        if isinstance(rating, (int, float)) and slug in features
    ]
    user_ratings = dict(rated_rows)
    high, low, _counts = _label_stats(rated_rows, features)
    bridge = _bridge_mini_themes(rated_rows, features)
    if bridge:
        paragraph = _paragraph(bridge, [_phrase(key) for key in low[:2]], themes=True)
    else:
        paragraph = _paragraph(
            [_phrase(key) for key in high[:3]],
            [_phrase(key) for key in low[:2]],
            themes=False,
        )
    try:
        import sklearn.ensemble  # noqa: F401
    except ImportError:
        return TasteView(
            paragraph=paragraph,
            note="Install scikit-learn to score films against your ratings.",
        )

    digest = _hash(user_ratings, features, neighbors, known_languages)
    path = _bundle_path(cache_path, user)
    bundle = None
    if path.is_file():
        try:
            loaded = pickle.loads(path.read_bytes())
        except (OSError, pickle.UnpicklingError, AttributeError, EOFError, ImportError):
            loaded = None
        if isinstance(loaded, dict) and loaded.get("hash") == digest:
            bundle = loaded

    if bundle is None:
        content_ok, content_precision, content_calls = _content_check(
            rated_rows, features, known_languages
        )
        neighbor_ok, neighbor_precision, neighbor_calls = _neighbor_check(
            user_ratings, neighbors
        )
        space = _Space(known_languages)
        space.fit(rated_rows, features)
        mean_model = low_model = None
        if len(space.rows) >= 30:
            mean_model, low_model = _fit_models(space)
        bundle = {
            "hash": digest,
            "content_ok": content_ok and mean_model is not None,
            "content_precision": content_precision,
            "content_calls": content_calls,
            "neighbor_ok": neighbor_ok,
            "neighbor_precision": neighbor_precision,
            "neighbor_calls": neighbor_calls,
            "space": space,
            "mean_model": mean_model,
            "low_model": low_model,
        }
        try:
            path.write_bytes(pickle.dumps(bundle))
        except OSError:
            pass

    use_content = bundle.get("mean_model") is not None and bundle.get("low_model") is not None
    content_ok = bool(bundle.get("content_ok")) and use_content
    neighbor_ok = bool(bundle.get("neighbor_ok"))
    content_only = content_ok and not neighbor_ok
    note = _note(
        content_ok=content_ok,
        content_precision=bundle.get("content_precision"),
        content_calls=int(bundle.get("content_calls") or 0),
        neighbor_ok=neighbor_ok,
        neighbor_precision=bundle.get("neighbor_precision"),
        neighbor_calls=int(bundle.get("neighbor_calls") or 0),
        have_neighbors=bool(neighbors),
        content_only=content_only,
        use_content=use_content,
    )
    view = TasteView(paragraph=paragraph, note=note)
    if not use_content and not neighbor_ok:
        return view

    space = bundle["space"]
    mean_model = bundle.get("mean_model")
    low_model = bundle.get("low_model")
    sims = _similarities(user_ratings, neighbors) if neighbor_ok else {}
    user_mean = _mean(list(user_ratings.values())) if user_ratings else 3.0
    high_keys = set(high)
    low_keys = set(low)
    director_index = _loved_director_index(rated_rows, features)
    median_year = _median_loved_year(rated_rows, features)
    hits = outside_hits or {}
    outside_label: dict[int, str] = {}
    like_you: list[Movie] = []
    step_outside: list[Movie] = []

    for movie in movies:
        slug = letterboxd_slug_from_url(movie.letterboxd_url)
        if not slug or slug in watched_ratings:
            continue
        feature = features.get(slug)
        if not feature:
            continue
        if excluded_from_recommendations(movie, feature):
            continue
        if not _enough_ratings(feature):
            continue
        predicted = lower = None
        if use_content and mean_model is not None and low_model is not None:
            predicted, lower = _predict_pair(space, mean_model, low_model, slug, feature)
        member_predicted, support = (None, 0)
        if neighbor_ok:
            member_predicted, support = _neighbor_score(slug, sims, neighbors, user_mean)
        if _carried_by_low(feature, low_keys, high_keys):
            continue
        if use_content and neighbor_ok and content_ok:
            passed = (
                predicted is not None
                and lower is not None
                and predicted >= PREDICT_MIN
                and lower >= LOWER_MIN
                and member_predicted is not None
                and member_predicted >= PREDICT_MIN
            )
        elif use_content:
            passed = (
                predicted is not None
                and lower is not None
                and predicted >= PREDICT_MIN
                and lower >= LOWER_MIN
            )
        else:
            passed = member_predicted is not None and member_predicted >= PREDICT_MIN
            predicted = member_predicted
            lower = None
        loved_phrase = None
        for key in director_person_keys([str(name) for name in feature.get("directors") or []]):
            found = director_index.get(key)
            if found is not None and (loved_phrase is None or found[0] > loved_phrase[0]):
                loved_phrase = found
        hit = hits.get(slug)
        has_low = any(key in low_keys for key in _fine_keys(feature))
        outside_ok = (
            hit is not None
            and not has_low
            and loved_phrase is None
            and predicted is not None
            and predicted >= OUTSIDE_PREDICT_MIN
        )
        if predicted is None or (not passed and not outside_ok):
            continue
        shown_rating = predicted
        movie.predicted_rating = shown_rating
        movie.lower_estimate = lower
        movie.member_average = member_predicted
        movie.member_support = support
        bits: list[str] = []
        community = feature.get("community_rating")
        if (
            not content_only
            and isinstance(community, (int, float))
            and community < 3.6
            and member_predicted is not None
            and member_predicted >= PREDICT_MIN
            and predicted >= PREDICT_MIN
        ):
            bits.append(
                f"Rated {float(community):.1f} overall. "
                f"Members close to you average {member_predicted:.1f}."
            )
        if lower is not None:
            bits.append(f"Predicted {predicted:.1f} (lower {lower:.1f}).")
        else:
            bits.append(f"Predicted {predicted:.1f}.")
        if not content_only and member_predicted is not None and support >= MIN_NEIGHBORS:
            bits.append(
                f"{support} members with taste close to yours average {member_predicted:.1f}."
            )
        if content_only:
            bits.append("From your own ratings only.")
        if loved_phrase is not None:
            bits.append(f"Same director as {loved_phrase[1]}.")
        elif space is not None:
            nearest = space.nearest_titles(slug, feature)
            if nearest:
                bits.append(f"Nearest of your own ratings: {_join(nearest)}.")
        year = feature.get("year")
        if isinstance(year, int) and median_year is not None and year < median_year:
            bits.append(f"{year}, older than most films you rate highly.")
        if outside_ok and hit is not None:
            bits.insert(0, f"{hit.name} is a nanogenre you have barely logged. It showed up on {hit.example}.")
            outside_label[id(movie)] = hit.name
        movie.because = " ".join(bits)
        if slug in watchlist_slugs:
            movie.because = f"On your watchlist. {movie.because}"
        if outside_ok:
            step_outside.append(movie)
        elif passed:
            like_you.append(movie)

    like_you.sort(key=lambda movie: (-(movie.predicted_rating or 0), film_sort(movie)))
    step_outside.sort(key=lambda movie: (-(movie.predicted_rating or 0), film_sort(movie)))
    step_outside = _spread_named(step_outside, outside_label, OUTSIDE_CAP)
    if like_you:
        best = min(
            like_you,
            key=lambda movie: (
                -(movie.predicted_rating or 0),
                -movie.member_support,
                _earliest(movie),
                film_sort(movie),
            ),
        )
        like_you = [movie for movie in like_you if movie is not best]
        view.see_first = [best]
    view.like_you = like_you[:like_cap]
    view.step_outside = step_outside
    return view


def film_sort(movie: Movie) -> str:
    from goodmoviefinder.program import film_title

    return film_title(movie).casefold()


def neighbor_candidate_slugs(
    watched: set[str],
    neighbors: dict[str, dict[str, float]],
    limit: int = 40,
) -> list[str]:
    """Unwatched films that several similar members rated highly."""
    buckets: dict[str, list[float]] = {}
    for diary in neighbors.values():
        for slug, rating in diary.items():
            if slug in watched or rating < LOVED_RATING:
                continue
            buckets.setdefault(slug, []).append(rating)
    ranked = [
        (sum(values) / len(values), len(values), slug)
        for slug, values in buckets.items()
        if len(values) >= 2
    ]
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [slug for _avg, _count, slug in ranked[:limit]]


def cached_category_slugs(
    cache: dict,
    sources: list[tuple[str, str]],
    watched: set[str],
) -> list[str]:
    """Unwatched films already on disk, one turn per category so Japan does not crowd the others out."""
    buckets: dict[tuple[str, str], list[str]] = {key: [] for key in sources}
    raw_features = cache.get("film_features", {})
    if not isinstance(raw_features, dict):
        return []
    for slug, raw in raw_features.items():
        if not isinstance(slug, str) or slug in watched or not isinstance(raw, dict):
            continue
        keys = set()
        for name in (raw.get("themes") or []) + (raw.get("mini_themes") or []):
            if isinstance(name, str) and name.strip():
                keys.add(("theme", name.strip()))
        for name in raw.get("countries") or []:
            if isinstance(name, str) and name.strip():
                keys.add(("country", name.strip()))
        matched = next((key for key in sources if key in keys), None)
        if matched is not None:
            buckets[matched].append(slug)
    slugs: list[str] = []
    seen: set[str] = set()
    while any(buckets.values()):
        for key in sources:
            films = buckets[key]
            if not films:
                continue
            film_slug = films.pop(0)
            if film_slug in seen:
                continue
            seen.add(film_slug)
            slugs.append(film_slug)
    return slugs


def catalog_sources(
    rows: list[tuple[str, float]],
    features: dict[str, dict],
) -> list[tuple[str, str]]:
    """The strongest countries and themes, not only the single label that averages 4.0."""
    sums: dict[tuple[str, str], float] = {}
    counts: dict[tuple[str, str], int] = {}
    for slug, rating in rows:
        feature = features.get(slug)
        if not feature:
            continue
        for key in _fine_keys(feature):
            counts[key] = counts.get(key, 0) + 1
            sums[key] = sums.get(key, 0.0) + rating
    low = {
        key
        for key, count in counts.items()
        if count >= MIN_LABELS and sums[key] / count <= LOW_RATING
    }
    countries: list[tuple[float, int, tuple[str, str]]] = []
    themes: list[tuple[float, int, tuple[str, str]]] = []
    for key, count in counts.items():
        if count < 12 or key in low:
            continue
        mean = sums[key] / count
        if mean < 3.6:
            continue
        bucket = countries if key[0] == "country" else themes
        bucket.append((mean, count, key))
    countries.sort(key=lambda item: (-item[0], -item[1], item[2][1].casefold()))
    themes.sort(key=lambda item: (-item[0], -item[1], item[2][1].casefold()))
    picked_countries = [item[2] for item in countries[:2]]
    picked_themes = [item[2] for item in themes[:3]]
    sources: list[tuple[str, str]] = []
    for index in range(max(len(picked_countries), len(picked_themes))):
        if index < len(picked_countries):
            sources.append(picked_countries[index])
        if index < len(picked_themes):
            sources.append(picked_themes[index])
    return sources


def choose_outside_tags(
    rows: list[tuple[str, float]],
    features: dict[str, dict],
) -> list[OutsideTag]:
    """Nanogenres liked on one or two films, spread across those films."""
    stats: dict[str, dict[str, object]] = {}
    for slug, rating in rows:
        feature = features.get(slug) or {}
        slug_map = feature.get("nanogenre_slugs") or {}
        if not isinstance(slug_map, dict):
            continue
        title = feature.get("title") if isinstance(feature.get("title"), str) else slug
        year = feature.get("year")
        film = f"{title} ({year})" if isinstance(year, int) else str(title)
        phrase = f"{film}, which you rated {rating:.1f}"
        for name in feature.get("nanogenres") or []:
            if not isinstance(name, str) or name not in slug_map:
                continue
            stat = stats.setdefault(
                name,
                {
                    "count": 0,
                    "sum": 0.0,
                    "slug": str(slug_map[name]),
                    "best": -1.0,
                    "example": phrase,
                    "film": slug,
                },
            )
            stat["count"] = int(stat["count"]) + 1
            stat["sum"] = float(stat["sum"]) + rating
            if rating > float(stat["best"]):
                stat["best"] = rating
                stat["example"] = phrase
                stat["film"] = slug
    ranked: list[tuple[float, float, str, OutsideTag]] = []
    for name, stat in stats.items():
        count = int(stat["count"])
        if count < 1 or count > OUTSIDE_TAG_MAX:
            continue
        mean = float(stat["sum"]) / count
        best = float(stat["best"])
        if mean < LOVED_RATING or best < LOVED_RATING:
            continue
        ranked.append(
            (
                best,
                mean,
                str(stat["film"]),
                OutsideTag(
                    kind="nanogenre",
                    name=name,
                    slug=str(stat["slug"]),
                    example=str(stat["example"]),
                ),
            )
        )
    ranked.sort(key=lambda item: (-item[0], -item[1], item[3].name.casefold()))
    picked: list[OutsideTag] = []
    used_films: set[str] = set()
    leftovers: list[OutsideTag] = []
    for _best, _mean, film, tag in ranked:
        if film in used_films:
            leftovers.append(tag)
            continue
        used_films.add(film)
        picked.append(tag)
        if len(picked) >= OUTSIDE_SOURCE_CAP:
            return picked
    for tag in leftovers:
        picked.append(tag)
        if len(picked) >= OUTSIDE_SOURCE_CAP:
            break
    return picked


def outside_candidate_slugs(
    tags: list[OutsideTag],
    watched: set[str],
    delay: float,
) -> dict[str, OutsideTag]:
    """Highest-rated unwatched films on each barely logged nanogenre."""
    import sys

    from goodmoviefinder.letterboxd import fetch_catalog_film_slugs

    if tags:
        print(
            "Step outside: " + ", ".join(tag.name for tag in tags),
            file=sys.stderr,
        )
    lists: list[tuple[OutsideTag, list[str]]] = []
    for tag in tags:
        found = [
            film_slug
            for film_slug in fetch_catalog_film_slugs(tag.kind, tag.slug, delay, pages=1)
            if film_slug not in watched
        ][:OUTSIDE_PER_SOURCE]
        if found:
            lists.append((tag, found))
    hits: dict[str, OutsideTag] = {}
    while any(films for _tag, films in lists):
        for tag, films in lists:
            if not films:
                continue
            film_slug = films.pop(0)
            hits.setdefault(film_slug, tag)
    return hits


def _spread_named(movies: list[Movie], labels: dict[int, str], cap: int) -> list[Movie]:
    """At most two films from each nanogenre."""
    buckets: dict[str, list[Movie]] = {}
    order: list[str] = []
    for movie in movies:
        name = labels.get(id(movie), "")
        if name not in buckets:
            order.append(name)
            buckets[name] = []
        buckets[name].append(movie)
    picked: list[Movie] = []
    while len(picked) < cap:
        progressed = False
        for name in order:
            bucket = buckets[name]
            if not bucket:
                continue
            if sum(1 for movie in picked if labels.get(id(movie)) == name) >= 2:
                bucket.clear()
                continue
            picked.append(bucket.pop(0))
            progressed = True
            if len(picked) >= cap:
                break
        if not progressed:
            break
    return picked


def catalog_candidate_slugs(
    sources: list[tuple[str, str]],
    watched: set[str],
    delay: float,
) -> list[str]:
    """Highest-rated Letterboxd films from each strong category, mixed together."""
    import sys

    from goodmoviefinder.letterboxd import fetch_catalog_film_slugs
    from goodmoviefinder.matching import slugify

    if sources:
        print(
            "Catalog categories: " + ", ".join(name for _kind, name in sources),
            file=sys.stderr,
        )
    lists: list[list[str]] = []
    for kind, name in sources:
        slug = slugify(name)
        if not slug:
            continue
        found = [
            film_slug
            for film_slug in fetch_catalog_film_slugs(kind, slug, delay, pages=1)
            if film_slug not in watched
        ]
        if found:
            lists.append(found)
    slugs: list[str] = []
    seen: set[str] = set()
    while any(lists):
        for films in lists:
            if not films:
                continue
            film_slug = films.pop(0)
            if film_slug in seen:
                continue
            seen.add(film_slug)
            slugs.append(film_slug)
    return slugs


def _spread_across_sources(
    movies: list[Movie],
    sources: list[tuple[str, str]],
    features: dict[str, dict],
    cap: int,
) -> list[Movie]:
    """Keep the best films from each category instead of letting one country fill the list."""
    from goodmoviefinder.program import letterboxd_slug_from_url

    buckets: dict[tuple[str, str], list[Movie]] = {key: [] for key in sources}
    other: list[Movie] = []
    for movie in movies:
        slug = letterboxd_slug_from_url(movie.letterboxd_url)
        feature = features.get(slug or "") or {}
        keys = set(_fine_keys(feature))
        matched = next((key for key in sources if key in keys), None)
        if matched is None:
            other.append(movie)
        else:
            buckets[matched].append(movie)
    picked: list[Movie] = []
    while len(picked) < cap:
        progressed = False
        for key in sources:
            if not buckets[key]:
                continue
            picked.append(buckets[key].pop(0))
            progressed = True
            if len(picked) >= cap:
                break
        if not progressed:
            break
    for movie in other:
        if len(picked) >= cap:
            break
        picked.append(movie)
    return picked


def movie_from_letterboxd(slug: str, feature: dict) -> Movie:
    title = feature.get("title") if isinstance(feature.get("title"), str) else None
    title = title or slug
    url = f"https://letterboxd.com/film/{slug}/"
    year = feature.get("year") if isinstance(feature.get("year"), int) else None
    community = feature.get("community_rating")
    language = feature.get("language") if isinstance(feature.get("language"), str) else None
    return Movie(
        title=title,
        nonstop_url=url,
        letterboxd_url=url,
        letterboxd_title=title,
        year=year,
        directors=list(feature.get("directors") or []),
        genres=list(feature.get("genres") or []),
        rating=float(community) if isinstance(community, (int, float)) else None,
        primary_language=language,
        primary_language_known=bool(language),
        synopsis=feature.get("synopsis") if isinstance(feature.get("synopsis"), str) else None,
        poster_url=feature.get("poster") if isinstance(feature.get("poster"), str) else None,
        details_known=bool(feature.get("card_known")),
    )


def build_for_you(
    cache: dict,
    cache_path: Path,
    watched_ratings: dict[str, float | None],
    neighbors: dict[str, dict[str, float]],
    watchlist_slugs: set[str],
    *,
    known_languages: frozenset[str],
    user: str,
    delay: float,
) -> TasteView:
    """Score unwatched Letterboxd films from similar members and from your highest themes."""
    from goodmoviefinder.cache import (
        ensure_film_cards,
        ensure_film_features,
        ensure_nanogenres,
        film_feature_get,
    )
    from goodmoviefinder.program import letterboxd_slug_from_url as slug_from_url

    rated_slugs = [
        slug
        for slug, rating in watched_ratings.items()
        if isinstance(rating, (int, float))
    ]
    features = ensure_film_features(cache, cache_path, rated_slugs, delay)
    ensure_nanogenres(cache, cache_path, rated_slugs, delay)
    for slug in rated_slugs:
        refreshed = film_feature_get(cache, slug)
        if refreshed is not None:
            features[slug] = refreshed
    rows = [(slug, float(watched_ratings[slug])) for slug in rated_slugs if slug in features]
    sources = catalog_sources(rows, features)
    watched = set(watched_ratings)
    outside_hits = outside_candidate_slugs(choose_outside_tags(rows, features), watched, delay)
    ordered: list[str] = []
    seen: set[str] = set()
    for slug in list(outside_hits) + cached_category_slugs(cache, sources, watched) + catalog_candidate_slugs(
        sources, watched, delay
    ) + neighbor_candidate_slugs(watched, neighbors):
        if slug in watched or slug in seen:
            continue
        seen.add(slug)
        ordered.append(slug)
    missing = [slug for slug in ordered if film_feature_get(cache, slug) is None]
    missing_outside = [slug for slug in missing if slug in outside_hits]
    missing_other = [slug for slug in missing if slug not in outside_hits]
    chosen = [
        slug for slug in ordered if slug not in set(missing)
    ] + missing_outside + missing_other[:120]
    features.update(ensure_film_features(cache, cache_path, chosen, delay))
    movies = [
        movie_from_letterboxd(slug, features[slug])
        for slug in chosen
        if slug in features
        and not excluded_from_recommendations(feature=features[slug])
        and _enough_ratings(features[slug])
    ]
    view = recommend(
        movies,
        watched_ratings,
        watchlist_slugs,
        features,
        neighbors,
        known_languages=known_languages,
        cache_path=cache_path,
        user=user,
        like_cap=80,
        outside_hits=outside_hits,
    )
    pool = [*view.see_first, *view.like_you]
    if pool and sources:
        best = pool[0]
        view.see_first = [best]
        view.like_you = _spread_across_sources(
            [movie for movie in pool if movie is not best],
            sources,
            features,
            12,
        )
    shown = [*view.see_first, *view.like_you, *view.step_outside]
    ensure_film_cards(
        cache,
        cache_path,
        [slug for movie in shown if (slug := slug_from_url(movie.letterboxd_url))],
        delay,
    )
    for movie in shown:
        slug = slug_from_url(movie.letterboxd_url)
        feature = film_feature_get(cache, slug) if slug else None
        if not feature:
            continue
        synopsis = feature.get("synopsis")
        poster = feature.get("poster")
        movie.synopsis = synopsis if isinstance(synopsis, str) and synopsis else None
        movie.poster_url = poster if isinstance(poster, str) else None
        movie.details_known = bool(feature.get("card_known"))
    return view
