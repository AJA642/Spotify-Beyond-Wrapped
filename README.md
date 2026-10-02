# Spotify Listening History Analysis

A personal analytics case study built from my own Spotify Extended Streaming
History export — real listening patterns, genre evolution, and a deep dive
into my EDM listening specifically. The goal was to go meaningfully deeper
than Spotify Wrapped: full 7+ year history, real numbers, and an honest
account of where the data (and the analysis) has limits.

Findings and charts live in [`REPORT.md`](REPORT.md). This file covers what
the project is, how it's built, and how to re-run it.

## Data source & privacy

This project analyzes a personal Spotify "Extended Streaming History" export
(requested from Spotify's privacy settings), covering **Feb 2019 – Sept
2026**. That raw export — and the cleaned/intermediate files this pipeline
produces from it — contains genuinely personal data (every track played,
down-to-the-second timestamps, IP addresses, device info) and **is not
included in this repository**. `.gitignore` excludes the parquet/cache files
under `data/`; only the aggregated, already-summarized outputs (`REPORT.md`,
the PNGs in `charts/`, and the small JSON/CSV result files needed to
regenerate charts) are meant to be shared or published. The `ip_addr` field
present in the raw export is dropped during loading and never written to
disk in any processed file.

If you're looking at this as a portfolio piece: what you should be looking
at is `REPORT.md` and `charts/`, not `data/`.

## Tech stack

- **Python** (pandas, numpy) for data loading and analysis
- **MusicBrainz's open public API** for genre enrichment (originally built
  against Spotify's Web API — see "Known issues resolved" below for why
  that was abandoned mid-project)
- **matplotlib** / **seaborn** for charts
- **Parquet** for intermediate storage (faster + smaller than CSV for ~140K rows)

## Project structure

```
data/
  processed/                cleaned datasets + analysis result JSON/CSV (gitignored)
  artist_genres.json        genre lookup cache, keyed by artist name (MusicBrainz-sourced)
  artist_genres_spotify_partial.json   archived — the abandoned Spotify attempt, kept for the debugging story
  tableau_export.csv        row-per-play export for building a Tableau dashboard (gitignored — see "Tableau export" below)
src/
  load_data.py               Step 1: parse + clean the raw export
  fetch_genres.py             Spotify Web API genre enrichment — SUPERSEDED, kept for its debugging story (see below); not part of the working pipeline
  fetch_genres_musicbrainz.py Step 2: genre enrichment via MusicBrainz (the actual data source used)
  genre_mapping.py            genre taxonomy: raw tags -> top-level buckets / EDM sub-genres
  analysis.py                 Step 3: core listening analysis (genre-independent)
  edm_analysis.py             Step 4: EDM-specific deep dive (needs genre data)
  edm_tail_coverage_check.py  sanity check: is EDM disproportionately in the unresolved genre-coverage tail?
  visualize.py                Step 5: chart generation
  export_tableau.py           Step 6: exports data/tableau_export.csv for the Tableau phase (see below)
  extended_stats.py           follow-up pass: unique album count, listening consistency, concentration curve, session stats (see REPORT.md's "Extended analysis" section)
charts/                  generated PNGs, embedded in REPORT.md
README.md                this file
REPORT.md                the write-up: findings, charts, limitations
requirements.txt
```

## How to run it

### 1. Set up the environment

```bash
cd AJA_Spotify-Listening-Analysis
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

### 2. Run the pipeline in order

```bash
./venv/bin/python src/load_data.py                  # parse + clean the raw export
./venv/bin/python src/fetch_genres_musicbrainz.py   # genre enrichment via MusicBrainz — no API key needed, but is slow (1 req/sec limit): pass --batch-size N to run it in short, resumable windows instead of one long sitting
./venv/bin/python src/analysis.py                   # core listening analysis
./venv/bin/python src/edm_analysis.py               # EDM deep dive (needs fetch_genres_musicbrainz.py output)
./venv/bin/python src/edm_tail_coverage_check.py    # optional: re-run the EDM-tail-coverage sanity check
./venv/bin/python src/visualize.py                  # generate all charts
./venv/bin/python src/export_tableau.py             # optional: export data/tableau_export.csv for the Tableau phase
./venv/bin/python src/extended_stats.py             # optional: unique albums, listening consistency, concentration curve, session stats
```

Each step reads/writes to `data/processed/` and is independently re-runnable.
`fetch_genres_musicbrainz.py` caches everything it fetches to
`data/artist_genres.json`, so re-running it only fetches artists it hasn't
already resolved. No API key, developer account, or credentials of any
kind are needed — MusicBrainz's API is fully open and public. (An earlier
version of this project used Spotify's Web API instead, which *does* need
a free developer app; that path is kept in `src/fetch_genres.py` purely
for its debugging story — see "Known issues resolved" below — and isn't
needed to run the pipeline.)

## Key methodology notes

(Full detail and reasoning in `REPORT.md`'s Limitations section — this is
the short version.)

- **Short-play filter**: plays under 30 seconds are excluded from all
  analysis except skip-behavior itself, as the standard threshold for
  filtering out accidental taps / near-instant skips.
- **Genre resolution**: genre/tag data is assigned at the *artist* level,
  not the *track* level, and is resolved via MusicBrainz's public API —
  search by artist name, then look up the top match's `genres` (curated)
  and `tags` (community folksonomy), merged together. This project
  originally used Spotify's Web API for this; that was abandoned mid-way
  for reasons detailed in "Known issues resolved" below.
- **Genre coverage is partial, by deliberate choice**: resolution was
  prioritized by listening hours (most-played artists first) and stopped
  once it covered the large majority of total listening time, rather than
  chased to 100% — see `REPORT.md` for the exact percentage. This
  dataset's listening time is a long-tail distribution (the top 500 of
  2,577 artists already account for 95.8% of all hours), so the
  unresolved tail is, by construction, each artist's most rarely played —
  the coverage tradeoff this implies is spelled out with exact numbers in
  `REPORT.md`, including a check that EDM-tagged listening specifically
  isn't disproportionately sitting in the uncovered tail.
- **Timezone**: time-of-day/day-of-week analysis assumes Asia/Kolkata (IST)
  throughout, since 92.5% of all plays connect from India.
- **Genre bucketing**: the raw genre/tag vocabulary runs into hundreds of
  micro-genre tags (e.g. "brostep", "corridos", "alt z") — not chartable
  directly. `src/genre_mapping.py` collapses these into a small set of
  top-level buckets (EDM/Dance, Hip-Hop/Rap, Pop, etc.) for the general
  genre-share chart. Because MusicBrainz's tag data is a noisier community
  folksonomy than Spotify's algorithmic genre lists, bucket assignment
  uses a **plurality vote** across an artist's full tag list (whichever
  bucket has the most matching tags wins) rather than "any match wins" —
  needed after a stray "house" tag on Kanye West's profile was initially
  outvoting his 10+ genuine hip-hop/rap tags under a simpler rule (see
  below). Each artist still lands in exactly one bucket, even with
  multiple raw tags. The EDM deep dive additionally breaks EDM itself
  into sub-genres (house, techno, dubstep, trance, ...).

## Tableau export

The Python phase of this project (this repo) is done; the next phase is a
Tableau dashboard, built separately. `src/export_tableau.py` produces the
one file that phase needs: **`data/tableau_export.csv`** — 236,487 rows,
27 columns, ~92MB.

**Grain: one row per raw play**, not per artist-month or any other
pre-aggregated grain — deliberately, for two reasons. First, the charts
already built in Python each need a different slice of the data (by
month, by hour-of-day, by artist, by genre bucket, by skip status), and
row-per-play is the only grain flexible enough for Tableau to reproduce
all of them via its own aggregation, rather than locking in one
pre-chosen rollup that fits some charts and not others. Second, the
skip-behavior analysis specifically needs the **unfiltered** data (that's
the entire point of the 30-second filter — it removes exactly the plays
that signal a skip), so this export uses the full, unfiltered dataset
(236,487 rows) rather than the 140,161-row filtered one, with a boolean
flag (`is_short_play`) so Tableau can filter to the "clean" subset with
one click for every chart *except* skip behavior.

Genre/EDM classification and the discovery-status flags are pre-computed
in this file specifically because they require logic Tableau can't
replicate from raw rows alone — genre bucketing needs this project's
MusicBrainz lookup plus the plurality-vote taxonomy in
`src/genre_mapping.py`, and "is this artist new" needs a groupby-min +
join across the whole dataset. Everything else (year, month, hour,
day-of-week) is included as a convenience even though Tableau could
derive it from a timestamp, specifically so no one has to re-implement
the Asia/Kolkata timezone conversion this project uses throughout.

### Column reference

| Column | Type | Meaning |
|---|---|---|
| `ts_utc` | datetime | Raw play timestamp, UTC, as recorded by Spotify. |
| `ts_local` | datetime | Same timestamp converted to Asia/Kolkata (IST) and written as a plain local time (no UTC offset in the string) — this is the timestamp used throughout this project's own time-based analysis. See `REPORT.md`'s Limitations: IST is used dataset-wide since 92.5% of plays connect from India; the ~7.5% that don't (e.g. periods abroad) are still shown at their IST-equivalent hour, not their true local hour. |
| `year` | int | Calendar year of `ts_local`. |
| `month` | string | `"YYYY-MM"` label of `ts_local`'s month, e.g. `"2022-03"`. |
| `hour` | int | Hour of day (0-23) of `ts_local`. |
| `day_of_week` | string | Full day name (e.g. `"Wednesday"`) of `ts_local`. |
| `artist_name` | string | Artist as recorded in the Spotify export. |
| `track_name` | string | Track title. |
| `album_name` | string | Album title. |
| `ms_played` | int | Milliseconds of this track played in this session — Spotify's raw play-duration field. |
| `hours_played` | float | `ms_played / 3,600,000` — the hour-weighted metric used for every "listening time" chart in this project (preferred over raw play count throughout). |
| `is_short_play` | bool | `True` if `ms_played < 30,000` (under 30 seconds) — this project's short-play filter. Every chart in `REPORT.md` **except** skip behavior is built on rows where this is `False`; filter this to `False` in Tableau to match those charts exactly. Skip-behavior charts intentionally use the full, unfiltered data. |
| `genre_bucket` | string | Top-level genre bucket from `src/genre_mapping.py` (`"EDM / Dance"`, `"Hip-Hop / Rap"`, `"Pop"`, etc.), or **`"Unknown / No Genre Data"`** when the artist isn't genre-resolved or MusicBrainz has no tag data for them (see coverage numbers below). One bucket per artist, applied to every one of that artist's plays (an artist-level, not track-level, classification — see `REPORT.md`'s Limitations). |
| `raw_genres` | string | The artist's raw MusicBrainz genre/tag strings, `;`-joined (e.g. `"edm;house;progressive house"`), or empty string if none. This is the input `genre_bucket` was derived from — kept for anyone who wants to re-bucket differently. |
| `is_edm` | bool | `True` if `genre_bucket == "EDM / Dance"`. The flag used for every EDM-specific chart. |
| `edm_subgenres` | string | Sub-genre tags (House, Techno, Trance, Dubstep/Bass, etc. — see `src/genre_mapping.py`) that matched this artist's raw tags, `;`-joined. Only meaningful when `is_edm` is `True`; a single artist can match multiple sub-genres (these aren't mutually exclusive, unlike `genre_bucket`). |
| `artist_first_seen_year` | int | The calendar year (IST) of this artist's very first play anywhere in the dataset. Constant across all of that artist's rows. |
| `is_artist_first_play` | bool | `True` only on the single row that *is* that artist's first-ever play chronologically. Use `year == artist_first_seen_year` (row-level, many `True`s per artist per year) for the "discovery share" chart's yearly aggregate, or filter `is_artist_first_play == True` to get exactly one row per artist (their debut play). |
| `skipped` | bool | Spotify's own skip flag, straight from the export. **Caveat, detailed in `REPORT.md` Finding 4**: this field appears to be unreliable/unpopulated before 2022 (it's a flat `False` for every single 2019-2021 row) — treat it as meaningful only from 2022 onward. `is_short_play` is the more reliable cross-era skip-like signal. |
| `reason_start` / `reason_end` | string | Spotify's raw reason codes for why a play started/ended (e.g. `"clickrow"`, `"trackdone"`, `"fwdbtn"`). Included as-is in case a dashboard wants finer skip/behavior detail than the `skipped` flag gives. |
| `shuffle` / `offline` / `incognito_mode` | bool | Raw playback-context flags from the Spotify export. |
| `platform` | string | Raw device/platform string from the export (e.g. `"Android OS 6.0.1 API 23 (samsung, SM-N910C)"`). |
| `platform_group` | string | This project's cleaned-up platform category (`"mobile (Android)"`, `"mobile (iOS)"`, `"desktop (Windows)"`, etc.) — used for the platform-share chart. |
| `conn_country` | string | Two-letter country code of the connection at play time (e.g. `"IN"`). Not a travel log — reflects the connection, not necessarily a fixed home location. |

### Regenerating it

```bash
./venv/bin/python src/export_tableau.py
```

Re-run any time after `fetch_genres_musicbrainz.py` resolves more artists
to refresh the genre columns; everything else only depends on
`load_data.py`'s output and won't change unless the raw export itself is
re-processed.

## Known issues resolved during development

Kept here because several of these were real bugs, not just design
decisions, and a couple specifically would have silently corrupted
downstream results if they hadn't been caught before scaling up:

- **Spotify's Web API was abandoned entirely, mid-project, for two
  independent reasons — this is the biggest issue on this list.** The
  project originally used Spotify's Web API for genre data (see the
  entries below for the debugging work that went into that attempt).
  Two separate problems surfaced:
  1. A sustained rate-limit wall that kept recurring after every attempt
     to resume, and did not clear even after a genuine 24+ hour wait —
     ruling out both a UTC-midnight and an IST-midnight reset as the
     mechanism.
  2. Inspecting the artists that *did* resolve before hitting that wall
     showed **every single one** had an empty genre list — including
     globally famous, normally heavily-tagged acts (*NSYNC, 21 Savage,
     2 Chainz). That isn't artist obscurity; getting zero genre tags
     across dozens of mainstream artists isn't statistically plausible
     as coincidence. Checking the Spotify Developer Dashboard confirmed
     this app is registered in **"Development mode"** — Spotify's most
     restricted access tier — which explains both symptoms: a stricter
     quota, and (very plausibly) reduced data access on certain fields.
  No amount of waiting or smarter pacing was going to fix a tier
  restriction. Genre resolution was rebuilt from scratch around
  **MusicBrainz** — a fully open, public, Creative-Commons-licensed music
  database with no API key, no developer account, and a simple, stable,
  *documented* rate limit (1 request/second) instead of Spotify's opaque
  one. `src/fetch_genres.py` (the Spotify version) is kept in the repo
  specifically because the debugging work below is a real, worthwhile
  story — it's just no longer part of the working pipeline.
- **A single stray tag could hijack an artist's entire genre
  classification.** MusicBrainz's tag data is a community folksonomy —
  broader than Spotify's algorithmic genre lists, but noisier. Under the
  original "any keyword match wins" bucketing rule, **Kanye West**
  (370.6 hours — the #2 artist in the entire dataset) was classified as
  EDM/Dance, because a couple of stray "house" and "future bass" tags on
  his profile matched before his 10+ genuine hip-hop/rap tags ever got
  checked. Caught by spot-checking *why* a specific high-impact artist
  landed in a given bucket, rather than just trusting the aggregate
  numbers. Fixed by switching to a plurality vote across an artist's full
  tag list — whichever genre bucket has the most matching tags wins,
  rather than any single match.
- **Automated artist-name search occasionally matched the wrong
  same-named entity.** Search-based resolution twice matched a fake or
  irrelevant entity instead of the real artist: "Kanye West" matched a
  tribute-band entry, then (after adding a tribute-band filter) a fan
  mashup project called "Kanye West & Hatsune Miku" — because Kanye
  legally renamed to "Ye" on MusicBrainz, with "Kanye West" kept only as
  an alias that ranks below literal-name joke entries in search results.
  Separately, "Axwell /\\ Ingrosso" (78.3 hours) failed to resolve at all,
  because Spotify's export stores the duo's name with `/\\` as a
  plaintext stand-in for the stylized "Λ" character they actually use.
  Both were confirmed and corrected by hand via direct MusicBrainz
  queries (checking the `disambiguation` field to verify the real
  artist), rather than silently accepted or silently dropped.
- **Batch endpoints return 403 on this app's tier.** The original design
  called for Spotify's "Get Several Artists" / "Get Several Tracks"
  endpoints (up to 50 IDs per call). Testing showed they return `403
  Forbidden` for this app's credentials — confirmed by requesting the exact
  same artist both ways: `GET /v1/artists/{id}` (singular) → `200`;
  `GET /v1/artists?ids={id}` (the "several" endpoint, even with one ID) →
  `403`, same token, same artist. Rebuilt genre resolution around Search +
  singular lookups instead, which cost more total requests but actually
  work.
- **A rate-limited failure was silently miscached as "artist has no
  genre."** Early in development, when a request exhausted its retries
  (typically because the app was mid rate-limit penalty), the script cached
  that as `{"genres": []}` — identical to a real "Spotify has no genre data
  for this artist" result. Because the cache-loading logic treats anything
  already in the cache as "done, don't refetch," every one of those
  artists would have stayed permanently and invisibly mislabeled on every
  future run — quietly understating genre coverage (and specifically EDM
  coverage) with no error or warning anywhere. Caught before it reached
  the analysis stage by noticing the "not found" count climbing suspiciously
  in step with rate-limit log lines, rather than with real artist obscurity.
  Fixed by giving a failed request its own distinct return value, separate
  from "searched and genuinely found nothing" — only the latter gets
  cached; a failed request is left unresolved so a later run retries it.
- **A redundant second API call per artist.** An earlier version made a
  Search call *and* a follow-up singular `/artists/{id}` call per artist,
  on the assumption the singular lookup was needed for "fresh" data.
  Spotify's Search response already includes each artist's `genres` field
  directly — the second call bought nothing and doubled total request
  volume, making the rate-limit problem above worse than it needed to be.
  Removed.
- **Sustained rate-limit walls needed a slower, patient response, not a
  faster one.** The first retry/backoff design assumed 429s were brief and
  worth retrying quickly (small capped backoff, 6 retries per request).
  In practice, once this app hit its throughput ceiling, every retry
  failed regardless of backoff length — the fix was recognizing the
  pattern faster (fail after fewer retries) and then backing off for
  real minutes, not seconds, before resuming.
- **A too-short read timeout on MusicBrainz misread "slow" as "failed."**
  During a period of elevated latency on MusicBrainz's (volunteer-run,
  public) servers, requests that would have succeeded if given more time
  were instead timing out at the original 15-second limit and retrying
  from scratch — wasting far more time than just waiting a bit longer
  would have. Confirmed by checking the ratio of true failures (requests
  that never succeeded, even after retries) against timeouts that
  succeeded on the very next attempt: almost all of them were the latter.
  Fixed by widening the read timeout to 40 seconds, which turned mostly-
  wasted retry cycles back into single successful requests.
