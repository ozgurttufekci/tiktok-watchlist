# tiktok-watchlist

Turn a folder of TikTok screenshots (movie/show recommendations, "what film is
this?" comment threads, listicles) into a watchlist grouped by **US streaming
provider**, sorted by **total watch time**, labelled **Movie / TV Show**, with
**IMDb ratings**.

No dependencies beyond macOS + Python 3 (Apple Vision does the OCR; `tesseract`
is used as a fallback if Vision isn't available).

## Setup (one time)

1. Get a free TMDB API key: <https://www.themoviedb.org/settings/api> (v3 auth key).
2. `cp .env.example .env` and paste the key after `TMDB_API_KEY=`.
   `.env` is git-ignored and the key never ends up in any output.

## Usage

```bash
# 1. drop screenshots here
cp ~/Desktop/IMG_*.jpeg screenshots/

# 2. run the pipeline
./run.sh
```

Then open **`output/watchlist.html`** (click any column header to sort, filter by
provider / type, search) or **`output/watchlist.csv`**.

Adding more screenshots later: drop them in `screenshots/` and run `./run.sh`
again — only new/changed images are OCR'd and TMDB responses are cached, so
reruns take seconds.

### Manual review (`review/`)

Screenshots the pipeline can't settle on its own are copied into two folders
and listed in **`review/review.csv`**. The `review/` folder only exists while
something is pending — it disappears once everything is answered:

| Folder | Meaning |
|---|---|
| `review/unrelated/` | no movie/show could be identified — probably not a recommendation at all |
| `review/multiple/` | several different valid titles were found (listicles, threads where people suggest different things, or an uncertain guess flagged by the assistant) |

Open the images, then type into the `answer` column of `review.csv` and rerun
`./run.sh`. Answer syntax (several titles separated by `;`):

| Answer | Effect |
|---|---|
| `skip` | not a movie/show, drop it |
| `all` | every title in `suggested_titles` is correct |
| `Last Vegas (2013)` | one title; year optional |
| `Fargo (tv); Fargo (1996)` | `(tv)` / `(movie)` force the type, a year pins the release |
| `tmdb:movie:158011` | pin an exact TMDB entry |
| `Lost & Found (tv, 2026, imdb:tt40062294, 49eps, 70min, on:NetShort)` | a title TMDB doesn't have (vertical short-dramas, etc.): rating comes from IMDb, the rest from the tokens you give — `eps`/`min`/`on:` are optional |

Answered screenshots disappear from the folders on the next run; the sheet keeps
only what's still pending. `data/agent_answers.json` holds the decisions the
assistant made while reading the OCR text (same syntax; a leading `?` means
"suggestion only, keep it pending") — your answers in `review.csv` always win.

### Options

| Flag | Effect |
|---|---|
| `./run.sh --refresh` | clear the TMDB cache first → fresh streaming availability |
| `./run.sh --skip-ocr` | reuse `data/ocr.json` as-is |
| `./run.sh --no-tmdb` | OCR + candidate extraction only (no key needed) |

## Outputs

| File | Contents |
|---|---|
| `output/watchlist.csv` | **One row per (provider, title)**. Grouped by provider in this order: Netflix, Hulu, Max, Disney+, Prime Video, Apple TV+, Paramount+, Peacock, Free (ads), Other, Not on a subscription service. Within each group: shortest total watch time first. A title on three services appears three times. |
| `output/watchlist.html` | Same data, one row per title with provider chips, plus a **✓ watched** checkbox per row and a *Hide watched* toggle. Ticks are saved in the browser (localStorage, keyed by TMDB/IMDb id) so they survive regenerating the file — but they live in that one browser only. Self-contained — works offline from `file://`. |
| `output/titles.csv` | Master list, one row per title, all providers joined. Includes `low_confidence` / `match_similarity` for auditing the TMDB match. |
| `output/unresolved.csv` | Screenshots marked unrelated, plus anything still pending in `review/`, with an OCR excerpt. |

Columns: `provider, title, type (Movie|TV Show), year, imdb_rating, imdb_votes,
total_watch_time (e.g. 1h 45m), total_minutes, seasons, episodes, status,
watch_time_estimated, imdb_url, tmdb_url, source_screenshots, availability_checked_on`.

## How it works

```
screenshots/*.jpeg
   │  scripts/ocr.swift            Apple Vision OCR (cached per file)
   ▼
data/ocr.json
   │  scripts/extract_candidates.py  "Search: …" bar, comment threads ("what movie is this?" replies,
   ▼                                 "Margin Call. Fantastic movie."), "Title (2013)", "1. Title",
data/titles.json                     #CamelCaseHashtags, video captions → TMDB suggest + scoring
   │  scripts/apply_review.py       apply data/agent_answers.json + review/review.csv answers
   │  scripts/triage.py             copy undecided screenshots to review/unrelated|multiple, write review.csv
   │  scripts/resolve_tmdb.py       runtime / all-season episode runtimes / US providers / IMDb id → rating
   ▼
data/resolved.json
   │  scripts/build_csv.py + build_html.py
   ▼
output/
```

### Fixing a wrong title that is *not* in the review sheet

Edit `data/titles.json`: set the screenshot's `"titles"` and `"reviewed": true`,
then `./run.sh --skip-ocr`. Minimal entry:

```json
"IMG_0042.jpeg": {
  "reviewed": true,
  "titles": [{ "query": "Last Vegas", "year": 2013, "media_type": "movie" }]
}
```

`tmdb_id` can be given instead of (or in addition to) `query`/`year` to pin an
exact TMDB entry. One screenshot may list several titles.

## Data notes

- **Watch time** — movies: TMDB runtime. TV: sum of the runtime of every *released*
  episode across all seasons (specials excluded). If some episodes lack a runtime
  on TMDB they're filled with the show's median episode length and the row is
  flagged `watch_time_estimated` (shown as `≈` in the HTML). Still-airing shows
  count episodes aired so far; see the `status` column.
- **Providers** — only subscription, free, and ad-supported offers (no rent/buy).
  Channel add-ons are folded into the base service (e.g. "Max Amazon Channel" → Max,
  "Paramount+ with Showtime" → Paramount+). Tubi / Pluto / Roku Channel / Plex /
  Crackle / Kanopy / Fawesome etc. are grouped as **Free (ads)**. Services outside the
  main list (Starz, AMC+, MGM+, Shudder, Crunchyroll, …) land in **Other**; the exact
  names are in `titles.csv` → `providers_raw`. Live-TV bundles (fuboTV, YouTube TV,
  Philo, Spectrum, network apps) are ignored — a channel in a bundle isn't a catalog.
- **Availability is a snapshot** — catalogs change constantly. The
  `availability_checked_on` column records the date; run `./run.sh --refresh` to update.
- **IMDb rating** comes from IMDb's official daily dataset
  (`title.ratings.tsv.gz`, cached in `data/imdb/`, auto-refreshed weekly). Titles
  with no IMDb id or no rating yet are left blank — TMDB's score is never substituted.
- **Region** is fixed to US (`REGION` in `scripts/tmdb.py`).

## Attribution

- This product uses the TMDB API but is not endorsed or certified by TMDB.
- Streaming availability data provided by [JustWatch](https://www.justwatch.com/) via TMDB.
- Ratings: information courtesy of [IMDb](https://www.imdb.com). Used with permission.
  (IMDb datasets are for personal, non-commercial use.)
