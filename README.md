# goodmoviefinder

Lists films from the [Nonstop Kino program](https://nonstopkino.at/en/program/?weekday=all&time=all&location=wien) (Vienna, all days/times by default), looks up each title on [Letterboxd](https://letterboxd.com), and writes them sorted from highest to lowest rating.

## Requirements

- Python 3.10+ (stdlib only). The first Apple Calendar run also needs the Xcode command line tools (`xcode-select --install`).

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

The HTML page keeps each film on one line, under its English title. A List / Calendar / Plan switch across the top of the page stays in view while you scroll. Calendar opens the current month and the next one; click a day to open the films playing then, in the same order as the list. Plan turns a short list of films into a calendar. Up to three schedules are offered: the best mix of evenings and weekends, the soonest, and one that waits for weekends. Add up to eight with the + on a list row or a calendar day. That list stays in this browser. The section links in that bar jump to a group in the list. In the calendar they show only that group; click the same link again to show every film. Both the display choice and folded categories are remembered in this browser. Click a category heading to fold or unfold that table. A search that matches films in a folded category opens it until the search is cleared. Click a column heading to sort that table; click it again to reverse the order. Films with no value in that column stay at the bottom. French and Quebec films keep their French original title. The **@username** button in the toolbar is the viewer: English and French, and [@hcanon](https://letterboxd.com/hcanon/), unless you change them. Films in a language you know count in the original version (including subtitled originals). Every other film needs English subtitles. The main program hides titles with no matching showtime. On your watchlist or in already watched, that line stays, tinted and marked **no followable version**. Open a title to see its poster, synopsis, and a calendar of matching showtimes. Each showtime links to that cinema’s website, whether the film plays once or several times that day.

Results are cached in `cache/ratings.json` so re-runs are much quicker. Use `--no-cache` to refresh Letterboxd data. Synopsis and cinema websites are cached with the film and filled in on the next run when they are missing. Runtime comes from the Letterboxd page, or from the Nonstop film page when Letterboxd has none, and is filled in on the next run when it is missing. Showtimes always come from the current program page.

By default, program titles that match your [watchlist](https://letterboxd.com/hcanon/watchlist/) are listed first under **Watchlist — now in Nonstop program**. **Recommendations** lists other films now playing whose director also made a film you rated 4.0 or higher. Each row names that film. Those directors are looked up on Letterboxd the first time and saved in the ratings cache. Films you have already logged ([@hcanon’s films](https://letterboxd.com/hcanon/films/)) appear in a separate **Already watched — also in program** section, with your Letterboxd rating in a **You** column next to the community score. Films you logged without a rating show a dash there. The full program lists every other film, split into three groups: rated films, Letterboxd matches that have no rating yet, and titles that could not be found on Letterboxd. Watchlist and recommendation titles appear in that listing as well as in their own sections. Profile data is cached for six hours; use `--refresh-profile` to update it.

Open **@username** to tick the languages you know. That choice stays in this browser. Loading another Letterboxd profile from that panel needs the local server (`--serve`), which reloads the page with that person’s watchlist and ratings. The same settings can be passed on the command line, and are remembered in `viewer.json`.

```bash
# Another Letterboxd account
python3 goodmoviefinder.py --letterboxd-user otheruser

# Languages you can watch without English subtitles
python3 goodmoviefinder.py --languages English,French,German

# Serve the page so Settings can load a profile
python3 goodmoviefinder.py --serve

# Disable watched/watchlist filtering
python3 goodmoviefinder.py --no-letterboxd-profile

# Keep the plan off times you are already busy
python3 goodmoviefinder.py --calendar google
python3 goodmoviefinder.py --calendar apple
python3 goodmoviefinder.py --calendar apple --calendar google
```

## Calendar

`--calendar` is remembered, including for `--serve`. Pass `--no-calendar` to stop. The plan then skips a showtime that overlaps a timed event on that calendar. Pass the flag twice to use Apple and Google together. The plan board marks busy hours in red. **Add plan to calendar** writes the scheduled films onto the connected calendar. Apple uses the calendar it keeps for new events. Clicking again does not duplicate them. The page stores only the busy start and end, in Vienna local time, from the moment you run the script. Re-run it when the calendar changes. All-day events, events marked free, cancelled events, and events you declined are ignored. A meeting that ends when the film starts does not block it. If a calendar cannot be read, the page is still written and the plan says that source was skipped.

Google Calendar, once:

1. In Google Cloud, create a project and enable the Google Calendar API.
2. On the OAuth consent screen, choose External, add your Gmail address as a test user, and add the scopes `https://www.googleapis.com/auth/calendar.readonly` and `https://www.googleapis.com/auth/calendar.events`.
3. Publish the app so the login lasts. The first approval shows an unverified-app warning. That is expected for a private script. Leaving the app in Testing makes Google drop the login after 7 days.
4. Create an OAuth client of type Desktop. Save the downloaded JSON as `cache/google-client.json`.

The first `--calendar google` run opens a browser to approve access and stores the login in `cache/google-token.json`.

Apple Calendar, once:

The first `--calendar apple` run builds a small helper and macOS asks to allow **goodmoviefinder** to read your calendars. Allow it. That prompt is for the helper, not for Python. The script stores times and discards titles. If you already denied access, turn goodmoviefinder on in System Settings → Privacy & Security → Calendars and run the command again.

Selected calendars on each account are included. A holiday calendar is all-day, so it does not wipe a day of films.

## How matching works

1. Unique films are taken from the program page (one row per movie, not per screening).
2. Each film’s Nonstop detail page is read for title, release year, director, cast, and runtime.
3. Letterboxd is resolved by trying slugs derived from the Nonstop URL and titles (including alternate names in parentheses). If no film page loads, or the page is a different movie with the same title, the program title and the Nonstop page title are searched with Letterboxd’s autocomplete: the full title, names in parentheses, then pieces split on dashes and colons (e.g. `E.T. – Der Außerirdische` → `E.T. the Extra-Terrestrial`, or `Brust oder Keule (L’aile ou la cuisse)` → `L’aile ou la cuisse`). Nearby years and Letterboxd’s numeric suffixes (`title-2025`, `title-2026-1`) are tried too. A page is kept when the director, runtime, or cast agrees with Nonstop. Another film with the same name is skipped when those disagree, even if the year matches. A one-year gap is still accepted when the director and runtime agree.

Some obscure or very new titles may not match Letterboxd. Those sit in **Unfound**. Matches that exist but have no community score sit in **Unrated**.

## Notes

- Please be polite to Letterboxd’s servers: the default `--delay` adds a short pause between film page requests.
- Ratings are Letterboxd’s weighted average (0.5–5 stars).
