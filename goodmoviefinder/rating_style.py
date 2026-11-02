"""CSS for a Letterboxd score, shared by the page and friend ratings."""

from __future__ import annotations

# Text and badge colors blend between these scores. Stops past 4.2 and below
# 2.6 keep the ends of the Letterboxd scale from collapsing to one shade.
_RATING_COLOR_STOPS: tuple[tuple[float, tuple[int, int, int], tuple[int, int, int, float]], ...] = (
    (0.5, (220, 96, 86), (176, 58, 48, 0.28)),
    (2.6, (232, 138, 122), (196, 92, 74, 0.22)),
    (3.0, (232, 200, 90), (201, 162, 39, 0.20)),
    (3.4, (212, 222, 122), (168, 184, 74, 0.20)),
    (3.8, (159, 216, 106), (106, 171, 74, 0.22)),
    (4.2, (111, 212, 168), (61, 154, 110, 0.25)),
    (5.0, (126, 232, 214), (72, 186, 168, 0.32)),
)


def _lerp(start: float, end: float, t: float) -> float:
    return start + (end - start) * t


def _rating_colors(
    rating: float,
) -> tuple[tuple[int, int, int], tuple[int, int, int, float]]:
    stops = _RATING_COLOR_STOPS
    if rating <= stops[0][0]:
        return stops[0][1], stops[0][2]
    if rating >= stops[-1][0]:
        return stops[-1][1], stops[-1][2]
    for index in range(len(stops) - 1):
        low_rating, low_text, low_bg = stops[index]
        high_rating, high_text, high_bg = stops[index + 1]
        if low_rating <= rating <= high_rating:
            t = (rating - low_rating) / (high_rating - low_rating)
            text = tuple(round(_lerp(a, b, t)) for a, b in zip(low_text, high_text))
            background = (
                round(_lerp(low_bg[0], high_bg[0], t)),
                round(_lerp(low_bg[1], high_bg[1], t)),
                round(_lerp(low_bg[2], high_bg[2], t)),
                _lerp(low_bg[3], high_bg[3], t),
            )
            return text, background
    return stops[-1][1], stops[-1][2]


def rating_style(rating: float | None) -> str:
    """Inline CSS for a score badge. Empty when the film has no rating."""
    if rating is None:
        return ""
    (red, green, blue), (bg_red, bg_green, bg_blue, bg_alpha) = _rating_colors(rating)
    return (
        f"color: rgb({red},{green},{blue}); "
        f"background: rgba({bg_red},{bg_green},{bg_blue},{bg_alpha:.3f});"
    )
