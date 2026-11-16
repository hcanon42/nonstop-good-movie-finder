---
target: Nonstop Kino and My Letterboxd
total_score: 21
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 4
p2_count: 1
target_identity: "file:/Users/h.canon/script/goodmoviefinder/nonstop-good-movie-finder/generated/program-ranked.html"
target_fingerprint: "sha256:eff4928a6db1c011835763a654199a15949c8efc50edc9d2b1dd0d689ffeb068"
target_path: /Users/h.canon/script/goodmoviefinder/nonstop-good-movie-finder/generated/program-ranked.html
timestamp: 2026-11-09T16-01-57Z
slug: generated-program-ranked-html
---
Method: dual-agent (A: c0d0d7da-7a96-496f-bec3-615928312470 · B: 65bc6449-939f-42c5-9e72-8042d3f7f2fc)

Reviewed live, signed in as @hcanon: **Nonstop Kino** (`generated/program-ranked.html`) and **My Letterboxd** (`generated/account.html`). Mode: Operate. The job of both pages is to decide what to watch, and when.

#### Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 2 | Plan count, a pressed Apple Calendar, and “Last built” are clear. Check for new films never says when the program was last read, and failed lists end as one sentence. |
| 2 | Match System / Real World | 3 | Showtimes, cinemas, and Leaving soon speak the viewer’s language. OV, OmdU, Unfound, and “nanogenre” do not, and one title prints the characters `\u2013`. |
| 3 | User Control and Freedom | 2 | Tabs, section toggles, search clear, and Escape from the account panel work. Sign out and Clear all commit immediately. |
| 4 | Consistency and Standards | 2 | The same seven pills are Jump to on List and Show only on Calendar. Watchlist counts stay on screen on Recommendations and For friends. |
| 5 | Error Prevention | 2 | The 12-film plan cap and the language hint are real guards. Sign out, Clear all, and Load are not. |
| 6 | Recognition Rather Than Recall | 2 | Tabs and section lines are labeled. Add to plan is a 23px plus, and the wrong-match control is a pencil with no words. |
| 7 | Flexibility and Efficiency | 2 | Search and column sort exist. There is no keyboard path, and a night is built one plus at a time. |
| 8 | Aesthetic and Minimalist Design | 2 | An opened film is calm. The first screen is a stack of equal controls, and a calendar day stamps Leaving soon on almost every title. |
| 9 | Error Recovery | 2 | “Friend ratings could not be loaded.” and “Those lists could not be loaded.” name a failure and then stop. |
| 10 | Help and Documentation | 2 | Section lines teach the personal groups. Nothing on the page defines OV, OmdU, Unfound versus Unavailable, or the pencil. |
| **Total** | | **21/40** | **Acceptable** |

21/40 is the low end of acceptable. Significant improvements are needed before the pages feel decided. The film detail and the taste card are ahead of the shell.

#### Design Specificity Verdict

**LLM assessment.** The shell could belong to any dark ranked-list tool: system font, a gold pill for the current page, ghost buttons, sticky tabs, and a data table. What is actually this product shows up inside a row, then gets buried. A community score sits next to your score. Leaving soon marks a Wien run. A line says “Same director as The Red Shoes (1948), which you rated 4.5.” The taste card names “Graphic violence and brutal revenge” beside “Dreamlike, quirky, and surreal storytelling.” Opening You Can See Everything or The Lighthouse is the one moment that feels like cinema: a still, a poster, a synopsis. Everything above that is navigation.

**Deterministic scan.** `impeccable detect` exited 2 with **4,737** findings: 4,736 warnings and 1 advisory. Almost all of that count is one decision repeated, not thousands of unrelated bugs.

| Rule | Count | Where it actually is |
|---|---:|---|
| Undersized functional text (10.88px, from `0.68rem`) | 4,337 | Weekday letters Mo–Su, day counts, and the jump labels Program / Unrated / Unfound in `program-ranked.html`. Taste labels Rated highly, Sits lower, Your ratings in `account.html`. |
| Cramped padding | 393 | Mostly `.detail { padding: 0 }` repeated per row. The visible hit is the “no followable version” chip: 2.6px above and below the text. Also `.table-wrap`, `.account-chip`, `.tabs-bar`. |
| Low contrast | 3 | Your-rating badges at 1.0 and 2.0: about 3.9:1 and 4.1:1 for `#df6a5f` on the dark red row, plus one 4.49:1 advisory. |
| Tight leading | 1 | `.month-day-title` / plan cell titles at `line-height: 1.25`. |
| Skipped heading | 1 | `account.html`: `<h1>My Letterboxd</h1>` is followed by `<h3>Letterboxd friends</h3>` before any h2. |
| Marketing buzzword | 1 | False positive. A quoted event title, “Future-proofing the AV Archive in Uncertain Times.” |
| Aphoristic cadence | 1 | False positive. Two hidden empty-state sentences concatenated, counted 444 times because the markup repeats them. |

The scan caught what the visual pass under-weighted: the type scale is systematically too small, the low “you” scores fail contrast, and My Letterboxd skips a heading level. `.detail { padding: 0 }` is mostly structural (the panel’s inner blocks carry the padding) and should not be read as 300 separate spacing bugs.

**Visual overlays.** Injection succeeded. The browser tool cannot rename a tab, so there is no tab titled [Human]. If those tabs are still open, the marks are on the page: **23** overlays on Nonstop Calendar (weekday letters, day counts, and one cramped day title), **6** on Nonstop List (the “no followable version” chips and the low-contrast 1.0 / 2.0 badges), and **1** banner on My Letterboxd for the skipped heading. The detector’s live server on port 8400 is stopped. Reloading clears the marks. They were not written into the HTML files.

#### Overall Impression

Both pages know a great deal about this viewer and then hide the answer under the controls used to ask the question. Nonstop opens on the wordmark, the page switch, @hcanon, four stat chips, List / Calendar / Plan, Check for new films, search, Languages: English, Turkish, and seven pills before You Can See Everything. On a 390px screen that title starts about 760px down. My Letterboxd opens on films you cannot stream, with the service logos in a sideways scroll, and Recommendations will suggest Twin Peaks: Fire Walk with Me with no cinema and no Austrian service.

Cognitive load is high: 6 of 8 checks failed (single focus, chunking, hierarchy, one decision at a time, minimal choices, progressive disclosure). Grouping and working memory pass. Decision points over the limit of 4: the 7 jump pills, 22 language checkboxes, 6 sort headers, the Plan toolbar (Check for new films, two calendars, Refresh, the film chips, Clear all, Add plan to calendar), and a single calendar day with 28 films. List / Calendar / Plan, the three watchlist filters, and See this first / More like what you love / A step outside are the decisions that are actually shaped.

The emotional peak is the opened film. The valley is the chrome required to reach it, and the visit can end on “Those lists could not be loaded.” with the search box still asking you to filter films that are not there.

The single biggest opportunity: lead with the next film you can actually see. Poster, title, your score beside the community score, the reason it is here, and the next showtime with a cinema. Let the chrome recede.

#### What's Working

**The opened row is the product.** The still sits behind the type, the poster is a real poster, and the Letterboxd title is a cyan link. That crop is doing more brand work than the header.

**Two scores, kept apart.** 4.15 beside a green 5.0 on Kiki’s Delivery Service, and 4.36 beside a purple 4.6 “for you” on Twin Peaks, is the whole idea of the tool. “Same director as Fight Club (1999), which you rated 5.0” is a better recommendation than a badge that says Recommended.

**See this first has an editorial order.** One film under “The strongest prediction that clears the bar,” then More like what you love (12), then A step outside (5). The taste card’s two columns, rated highly and sits lower, give that order a reason.

#### Priority Issues

**[P1] The first screen is navigation, on both pages.**
- **Why it matters:** The primary task is “what should I watch.” The first viewport answers “where are the controls.” The four stat chips repeat the section counts (1 watchlist, 4 recommended, 18 already seen). On a phone the first title is below the fold.
- **Fix:** Put the next followable screening at the top of Nonstop: poster, title, scores, why it is here, time, cinema, version. Drop the stat chips. Move Check for new films into the account panel, and show “Program read …” beside it. Keep search in the bar. Fold languages and the seven sections behind one Filters control. On My Letterboxd, lead the Watchlist with films that are streaming or playing, and put “Not available to stream in Austria” behind the filter that already exists.
- **Suggested command:** `/impeccable layout`, then `/impeccable distill`

**[P1] A phone cannot finish the task.**
- **Why it matters:** For friends renders as “For frie.” The tab row scrolls, and the scrollbar is hidden (`.tabs { overflow-x: auto; scrollbar-width: none }`), so the third tab looks cut off. The watchlist table is wider than the screen, so Streaming in Austria hides the logos the filter just promised (The Lighthouse: Prime, Apple TV, maxdome, Rakuten, Sky). Calendar days are about 43px wide and read “NAZA”, “Viridian”, “Kiki’s Delive”. Add to plan is 23×23. Account controls are 32–35px tall. The detector measured the same scale: weekday letters, day counts, jump labels, and taste labels are 10.88px, and day titles are set at line-height 1.25.
- **Fix:** Let the three account tabs fit, or rename the third to Friends and show a scroll affordance. On a narrow screen, replace the table with a card: poster, title, your score, logos or the next showtime, all inside the width. In the month, show the date, the count, and one full title. Raise the 0.68rem labels to at least 12px, and make Add to plan a labeled control at least 44px.
- **Suggested command:** `/impeccable adapt`, then `/impeccable typeset`

**[P1] Seven pills do two jobs, and the calendar job has no selected state.**
- **Why it matters:** On List they are Jump to, and Watchlist is current. On Calendar the label becomes Show only and none of the seven is pressed, so a filtered month and a full month look the same. Seven equal links plus a 28-film day is how Leaving soon turns into wallpaper.
- **Fix:** Keep jump links on List only. On Calendar, use one control with an explicit All that stays pressed until you choose Watchlist, Recommended, Watched, or Program. Put Unrated, Unfound, and Unavailable in a second group, with one line each that says what the word means (no Letterboxd score, no Letterboxd page, no screening in a language you follow).
- **Suggested command:** `/impeccable clarify`

**[P1] “Where do I watch this?” leaves the recommendation, and failures just stop.**
- **Why it matters:** See this first is a table of rating, for-you, film, director, genres, year, language. Twin Peaks has no streaming logo and no Nonstop time, so the strongest prediction cannot be acted on. The services that do exist stay up in the collapsed watchlist row and disappear in the detail, which repeats the synopsis and then an error. For friends shows the search field, then “Those lists could not be loaded.” The friend-rating failure in this pass was triggered by the static preview (no `/api/friends`); the copy and the dead end are in `page.js` either way. The fetch retries on its own, and the screen still offers no Retry.
- **Fix:** Put availability on every recommendation row: an Austrian service, a Nonstop date, or an explicit “Not streaming in Austria.” In the detail, repeat the logos or the next screening beside the synopsis. Replace both error lines with what failed and a Retry button. Remove the search field when the list failed to load.
- **Suggested command:** `/impeccable harden`, then `/impeccable clarify`

**[P2] The plan is a month you cannot read, and two destructive controls are unguarded.**
- **Why it matters:** 27 November is a gold cell reading “15:30”, “Kiki’s Deliver…”, “Filmhaus, Wien …”. Clear all can wipe Nausicaä of the Valley of the Wind and Kiki’s Delivery Service in one tap. Sign out sits as a ghost button under the language cloud and fires immediately. Refresh calendar and Clear all sit at the far end of the same toolbar.
- **Fix:** Make the plan a short list of the chosen screenings (time, cinema, version, title) and keep the month as a locator. Confirm Clear all and Sign out. Give Add to plan a visible label.
- **Suggested command:** `/impeccable layout`, then `/impeccable harden`

#### Persona Red Flags

**Alex (power user).** He can search and sort, and Plan 2 tells him a night is already started. He still cannot get a screening in one pass. The plus on You Can See Everything is 23px and unlabeled. Calendar on 9 November opens 28 rows with no grouping by time or cinema. Clear all can wipe the night with one tap. There is no key for add-to-plan, next row, or switch tab.

**Jordan (first-timer).** The first action is not obvious: Check for new films, Languages: English, Turkish, and Jump to all look like the product. Unfound, Unavailable, nanogenre, OV, and OmdU are never defined on the page. The pencil beside the Letterboxd link does not say it fixes a wrong match. After See this first, nothing tells him whether Twin Peaks is playing or streaming.

**Casey (phone, one thumb).** The page switch, @hcanon, and the tabs sit in the top band. For friends is clipped. Streaming in Austria (26) wraps under All (49), and the logos are off the card to the right. Day cells are 43px wide, so a thumb hits the next day. Add to plan is 23px.

**Sam (keyboard and screen reader).** A calendar day is announced as “9 28 NAZA,” which is the date, a count, and a chopped title. The watchlist’s streaming logos sit in a horizontal scroller, so the column the filter is about is easy to miss. My Letterboxd jumps from h1 to h3. Your-rating badges at 1.0 and 2.0 fall under 4.5:1. The gold focus ring and the ordinary body text are fine.

#### Minor Observations

- The gold Nonstop Kino pill is the loudest thing on a page whose job is a film.
- The sticky List / Calendar / Plan bar covers the month name and clips the previous row as you scroll.
- Genre chips stack to three lines and inflate every row.
- Leaving soon on a whole day is decoration. It means something on a single film whose last screening is inside seven days.
- maxdome’s dark mark disappears on the dark row.
- The language list is an alphabetical dump of 22 names, including Hebrew (modern) and Pashto, Pushto, while the chip already says English, Turkish.
- My Letterboxd’s default order is neither rating nor “what you can watch tonight.”
- *Ingeborg Bachmann \u2013 Someone Who Was Once Me* is stored and shown with the six characters `\u2013` instead of an en dash. The German title in the same row uses a real dash.
- There is no PRODUCT.md yet. `/impeccable init` would pin who this is for, and the tone, before a visual redesign.

#### Questions to Consider

- If the first screen were only the next followable screening, which of Check for new films, the four stat chips, and the seven pills would you actually miss?
- Should See this first be allowed to recommend Twin Peaks with no cinema time and no Austrian service?
- Is the calendar a month of 28 titles, or the two or three evenings you are free?
