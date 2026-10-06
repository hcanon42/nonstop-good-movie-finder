# goodmoviefinder

Lists films from the [Nonstop Kino program](https://nonstopkino.at/en/program/?weekday=all&time=all&location=wien) (Vienna, all days/times by default), looks up each title on [Letterboxd](https://letterboxd.com), and prints them sorted from highest to lowest rating.

## Requirements

- Python 3.10+ (stdlib only)

## Usage

```bash
python3 goodmoviefinder.py
```

Common options:

```bash
# Styled HTML page (search, stats, color-coded ratings)
python3 goodmoviefinder.py --html -o program-ranked.html

# Markdown table with links
python3 goodmoviefinder.py --markdown -o program-ranked.md

# Try only a few films first (faster)
python3 goodmoviefinder.py --limit 10

# Different program filters (same as on the website)
python3 goodmoviefinder.py --program-url 'https://nonstopkino.at/en/program/?weekday=2026-10-06&time=20'
```

Results are cached in `~/.cache/goodmoviefinder/ratings.json` so re-runs are much quicker. Use `--no-cache` to refresh Letterboxd data.

By default, program titles that match your [watchlist](https://letterboxd.com/hcanon/watchlist/) are listed first under **Watchlist — now in Nonstop program**. Films you have already logged ([@hcanon’s films](https://letterboxd.com/hcanon/films/)) appear in a separate **Already watched — also in program** section. Everything else stays in the main ranked program list. Profile data is cached for six hours; use `--refresh-profile` to update it.

```bash
# Another Letterboxd account
python3 goodmoviefinder.py --letterboxd-user otheruser

# Disable watched/watchlist filtering
python3 goodmoviefinder.py --no-letterboxd-profile
```

## How matching works

1. Unique films are taken from the program page (one row per movie, not per screening).
2. Each film’s Nonstop detail page is read for title and release year.
3. Letterboxd is resolved by trying slugs derived from the Nonstop URL and titles (including alternate names in parentheses). If nothing matches, parenthetical titles are searched via Letterboxd’s autocomplete API (e.g. `Brust oder Keule (L’aile ou la cuisse)` → `L’aile ou la cuisse`). When several pages match, the release year from Nonstop is preferred.

Some obscure or very new titles may not match Letterboxd; those appear at the bottom with `no rating`.

## Notes

- Please be polite to Letterboxd’s servers: the default `--delay` adds a short pause between film page requests.
- Ratings are Letterboxd’s weighted average (0.5–5 stars).
