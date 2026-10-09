"""Paths and defaults for the Nonstop program lookup."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED_HTML = REPO_ROOT / "generated" / "program-ranked.html"
DEFAULT_CACHE = REPO_ROOT / "cache" / "ratings.json"
GOOGLE_CLIENT_PATH = REPO_ROOT / "cache" / "google-client.json"
GOOGLE_TOKEN_PATH = REPO_ROOT / "cache" / "google-token.json"
DEFAULT_PROGRAM_URL = (
    "https://nonstopkino.at/en/program/?weekday=all&time=all&location=wien"
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_LETTERBOXD_USER = "hcanon"
DEFAULT_KNOWN_LANGUAGES = ("English", "French")
DEFAULT_KNOWN_LANGUAGE_KEYS = frozenset(name.casefold() for name in DEFAULT_KNOWN_LANGUAGES)
DEFAULT_SERVE_PORT = 8765
