# goodmoviefinder

Lists films from the [Nonstop Kino program](https://nonstopkino.at/en/program/?weekday=all&time=all&location=wien) (Vienna, all days/times by default), looks up each title on [Letterboxd](https://letterboxd.com), and writes them sorted from highest to lowest rating.

## Requirements

- Python 3.10+ (stdlib only)

## Usage

```bash
python3 goodmoviefinder.py
```

This writes a styled HTML page (search, color-coded ratings) to `generated/program-ranked.html`. That path is fixed.

Common options:

```bash
# Try only a few films first (faster)
python3 goodmoviefinder.py --limit 10

# Different program filters (same as on the website)
python3 goodmoviefinder.py --program-url 'https://nonstopkino.at/en/program/?weekday=2026-10-06&time=20'
```

The HTML page keeps each film on one line, under its English title. A List / Calendar switch across the top of the page stays in view while you scroll and opens the current month and the next one; click a day to open the films playing then, in the same order as the list. Both the display choice and folded categories are remembered in this browser. Click a category heading to fold or unfold that table. A search that matches films in a folded category opens it until the search is cleared. Click a column heading to sort that table; click it again to reverse the order. Films with no value in that column stay at the bottom. French and Quebec films keep their French original title. If none of the showtimes are in a language you can follow (original English or French, or English subtitles), that line is tinted and marked **no followable version**. Open a title to see its poster, synopsis, and a calendar of matching showtimes. Each showtime links to that cinema’s website, whether the film plays once or several times that day.

Results are cached in `cache/ratings.json` so re-runs are much quicker. Use `--no-cache` to refresh Letterboxd data. Synopsis and cinema websites are cached with the film and filled in on the next run when they are missing. Showtimes always come from the current program page.

By default, program titles that match your [watchlist](https://letterboxd.com/hcanon/watchlist/) are listed first under **Watchlist — now in Nonstop program**. Films you have already logged ([@hcanon’s films](https://letterboxd.com/hcanon/films/)) appear in a separate **Already watched — also in program** section, with your Letterboxd rating in a **You** column next to the community score. Films you logged without a rating show a dash there. Everything else is the full program, split into three groups: rated films, Letterboxd matches that have no rating yet, and titles that could not be found on Letterboxd. Profile data is cached for six hours; use `--refresh-profile` to update it.

```bash
# Another Letterboxd account
python3 goodmoviefinder.py --letterboxd-user otheruser

# Disable watched/watchlist filtering
python3 goodmoviefinder.py --no-letterboxd-profile
```

## How matching works

1. Unique films are taken from the program page (one row per movie, not per screening).
2. Each film’s Nonstop detail page is read for title, release year, director, cast, and runtime.
3. Letterboxd is resolved by trying slugs derived from the Nonstop URL and titles (including alternate names in parentheses). If no film page loads, or the page is a different movie with the same title, the program title and the Nonstop page title are searched with Letterboxd’s autocomplete: the full title, names in parentheses, then pieces split on dashes and colons (e.g. `E.T. – Der Außerirdische` → `E.T. the Extra-Terrestrial`, or `Brust oder Keule (L’aile ou la cuisse)` → `L’aile ou la cuisse`). Nearby years and Letterboxd’s numeric suffixes (`title-2025`, `title-2026-1`) are tried too. A page is kept when the director, runtime, or cast agrees with Nonstop. Another film with the same name is skipped when those disagree, even if the year matches. A one-year gap is still accepted when the director and runtime agree.

Some obscure or very new titles may not match Letterboxd. Those sit in **Unfound**. Matches that exist but have no community score sit in **Unrated**.

## Notes

- Please be polite to Letterboxd’s servers: the default `--delay` adds a short pause between film page requests.
- Ratings are Letterboxd’s weighted average (0.5–5 stars).
