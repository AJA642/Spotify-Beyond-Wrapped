"""
Genre enrichment via MusicBrainz's open public API — the fallback path
after Spotify's Web API turned out to be a dead end for this project (see
README.md's "Known issues resolved" section for the full story).

WHY THE SWITCH: Spotify's Web API hit two separate, independent blockers:
1. A sustained rate-limit wall that did not clear even after a genuine
   24+ hour wait, ruling out both a UTC-midnight and an IST-midnight
   calendar-day reset.
2. Inspecting the 575 artists that *did* resolve before the wall hit
   showed EVERY one had an empty `genres` list — including globally
   famous, heavily-genre-tagged acts on Spotify normally (*NSYNC, 21
   Savage, 2 Chainz). All 575 had a correctly-matched artist ID and name,
   just no genre data. That isn't artist obscurity — it's strong evidence
   the `genres` field isn't returned at all for this app's API access
   tier, independent of the rate limit. No amount of waiting fixes that.

MusicBrainz was chosen as the replacement because:
- It's fully open, public, Creative-Commons-licensed data — no API key,
  no developer account, no approval process to wait on.
- Its rate limit (1 request/second) is simple, documented, and stable —
  unlike Spotify's undocumented sustained-wall behavior.
- It returns genre data in a shape that maps directly onto this
  project's existing artist_genres.json cache format, so
  genre_mapping.py, edm_analysis.py, and visualize.py all work
  completely unchanged regardless of which fetch script produced the
  cache.

METHOD: for each artist name (ordered by listening hours, most-played
first — same reasoning as the original Spotify script: this dataset's
listening time is a long-tail distribution, so this ordering maximizes
analytical value if a run has to stop partway through), search
MusicBrainz for the artist, take the top match's MBID, then look that
MBID up with `inc=genres+tags` to get both MusicBrainz's curated genre
list and its broader community tag folksonomy. Tags are merged in as a
supplement, since the curated "genres" field is often sparser than the
tag data — genre_mapping.py's keyword-based bucketing already handles
noisy, non-genre tag words safely (they simply match no bucket and fall
into "Other" rather than being misclassified).

LIMITATION (documented in REPORT.md): switching data sources means genre
coverage and granularity may differ from what Spotify would have
returned. MusicBrainz's tagging is a community folksonomy — generally
broad, but occasionally sparse or inconsistent for lesser-known or
regional artists, compared to a platform-maintained genre taxonomy.
"""

import argparse
import json
import os
import sys
import time

import requests
import pandas as pd

sys.stdout.reconfigure(line_buffering=True)

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
PROCESSED_DIR = os.path.join(DATA_DIR, "processed")
ARTIST_GENRES_PATH = os.path.join(DATA_DIR, "artist_genres.json")

API_BASE = "https://musicbrainz.org/ws/2"
# MusicBrainz's usage policy requires an identifying User-Agent with contact info.
USER_AGENT = "AJA-Spotify-Listening-Analysis/1.0 (ajabraham.642@gmail.com)"
REQUEST_PACE_SECONDS = 1.8  # documented limit is 1 req/sec; wider margin than the minimum needed,
                             # since this is a volunteer-run public service and there's no reason to
                             # run it close to the edge of its stated limit when there's no time pressure
MAX_RETRIES = 4
MAX_BACKOFF_SECONDS = 20

LUCENE_SPECIAL = set('+-!(){}[]^"~*?:\\/')


def _escape_lucene(name: str) -> str:
    """Escape Lucene special characters so artist names like
    'Axwell /\\ Ingrosso' don't break MusicBrainz's query parser."""
    return "".join(("\\" + ch) if ch in LUCENE_SPECIAL else ch for ch in name)


def _request_with_backoff(url: str, params: dict):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=(10, 40))
        except requests.exceptions.RequestException as e:
            wait = min(2 ** attempt, MAX_BACKOFF_SECONDS)
            print(f"    network error ({e.__class__.__name__}), retrying in {wait}s...", flush=True)
            time.sleep(wait)
            continue
        if resp.status_code == 503:  # MusicBrainz's rate-limit response
            wait = min(2 ** (attempt + 1), MAX_BACKOFF_SECONDS)
            print(f"    503 (rate limited), waiting {wait}s...", flush=True)
            time.sleep(wait)
            continue
        if resp.status_code >= 500:
            wait = min(2 ** attempt, MAX_BACKOFF_SECONDS)
            print(f"    server error {resp.status_code}, retrying in {wait}s...", flush=True)
            time.sleep(wait)
            continue
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp
    print(f"    giving up after {MAX_RETRIES} retries: {url} params={params}", flush=True)
    return None


NOT_FOUND = "NOT_FOUND"  # sentinel: search succeeded, genuinely no match — safe to cache permanently

# Manual MBID overrides for a small number of high-impact artists where
# automated search-and-match genuinely can't win, confirmed by hand:
#   - "Kanye West" (370.6h, #2 by hours in this dataset): legally renamed
#     to "Ye" on MusicBrainz, with "Kanye West" kept only as an alias.
#     Searching the literal string "Kanye West" ranks joke/mashup entries
#     (a tribute band, a "Kanye West & Hatsune Miku" fan project) above
#     the real alias match. Verified via a direct MusicBrainz search for
#     "Ye" — top result: disambiguation "formerly Kanye West", score 100.
#   - "Axwell /\\ Ingrosso" (78.3h): Spotify's streaming history stores
#     this duo's name using "/\\" as a plaintext stand-in for the actual
#     stylized "Λ" (Lambda) character they use, which doesn't match
#     MusicBrainz's real entry at all as a plain-text search. Verified via
#     search for "Axwell Ingrosso" — top result "Axwell Λ Ingrosso",
#     disambiguation "2/3 of Swedish House Mafia", score 100.
MANUAL_MBID_OVERRIDES = {
    "Kanye West": "164f0d73-1234-4e2c-8743-d77bf2191051",
    "Axwell /\\ Ingrosso": "00323ee1-05b6-4cf6-98c4-94f0701645d3",
}


def _is_tribute_or_cover(candidate: dict) -> bool:
    """MusicBrainz search sometimes ranks a tribute/cover-band entry above
    the real artist (e.g. searching "Kanye West" — who now goes by "Ye"
    on MusicBrainz, with "Kanye West" stored only as an alias — returned
    "Kanye West Tribute Band" as the #1 result). Filter these out rather
    than trusting rank-1 blindly."""
    label = f"{candidate.get('name', '')} {candidate.get('disambiguation', '')}".lower()
    return "tribute" in label or "cover band" in label


def resolve_artist(name: str):
    """Returns dict (resolved), NOT_FOUND (confirmed no match — cacheable),
    or None (request failed — do NOT cache, retry on a later run)."""
    if name in MANUAL_MBID_OVERRIDES:
        mbid = MANUAL_MBID_OVERRIDES[name]
        matched_name = name
    else:
        query = f'artist:"{_escape_lucene(name)}"'
        search_resp = _request_with_backoff(f"{API_BASE}/artist/", {"query": query, "fmt": "json", "limit": 5})
        if search_resp is None:
            return None
        candidates = search_resp.json().get("artists", [])
        if not candidates:
            return NOT_FOUND

        # Prefer an exact case-insensitive name match among non-tribute candidates;
        # otherwise fall back to the top-ranked non-tribute candidate, then to
        # rank 1 overall if every candidate looks like a tribute/cover act.
        exact = [c for c in candidates if c["name"].lower() == name.lower() and not _is_tribute_or_cover(c)]
        non_tribute = [c for c in candidates if not _is_tribute_or_cover(c)]
        top = exact[0] if exact else (non_tribute[0] if non_tribute else candidates[0])
        mbid = top["id"]
        matched_name = top.get("name")
        time.sleep(REQUEST_PACE_SECONDS)

    lookup_resp = _request_with_backoff(f"{API_BASE}/artist/{mbid}", {"inc": "genres+tags", "fmt": "json"})
    if lookup_resp is None:
        return None
    data = lookup_resp.json()
    genres = [g["name"] for g in data.get("genres", [])]
    tags = [t["name"] for t in data.get("tags", [])]
    merged = sorted(set(genres) | set(tags))
    return {"artist_id": mbid, "matched_name": matched_name, "genres": merged}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--batch-size", type=int, default=None,
        help="Process at most this many not-yet-cached artists, then save and exit."
    )
    args = parser.parse_args()

    df = pd.read_parquet(os.path.join(PROCESSED_DIR, "streaming_history_clean.parquet"))
    hours_by_artist = df.groupby("artist_name")["ms_played"].sum().sort_values(ascending=False)
    artist_names = hours_by_artist.index.tolist()
    print(f"Resolving genres for {len(artist_names):,} unique artist names "
          f"(ordered by listening hours, most-played first) via MusicBrainz...")

    os.makedirs(DATA_DIR, exist_ok=True)
    cache = {}
    if os.path.exists(ARTIST_GENRES_PATH):
        with open(ARTIST_GENRES_PATH) as f:
            cache = json.load(f)

    missing = [a for a in artist_names if a not in cache]
    target = missing[: args.batch_size] if args.batch_size else missing
    print(f"  {len(cache)} already cached, {len(missing)} to resolve"
          + (f" (this run: {len(target)})" if args.batch_size else ""))

    unresolved = 0
    failed = 0
    resolved_count = 0
    start_time = time.time()
    for i, name in enumerate(target, 1):
        result = resolve_artist(name)
        if result is None:
            failed += 1
        elif result == NOT_FOUND:
            cache[name] = {"artist_id": None, "matched_name": None, "genres": []}
            unresolved += 1
            resolved_count += 1
        else:
            cache[name] = result
            resolved_count += 1
        time.sleep(REQUEST_PACE_SECONDS)

        if i % 25 == 0 or i == len(target):
            elapsed = time.time() - start_time
            rate = resolved_count / elapsed if elapsed > 0 else 0
            remaining = (len(target) - i) / rate / 60 if rate > 0 else float("inf")
            print(f"    {i}/{len(target)} attempted, {resolved_count} resolved "
                  f"({unresolved} confirmed not-found, {failed} failed/will retry) — "
                  f"~{remaining:.1f} min remaining", flush=True)
            with open(ARTIST_GENRES_PATH, "w") as f:
                json.dump(cache, f, indent=1)

    with open(ARTIST_GENRES_PATH, "w") as f:
        json.dump(cache, f, indent=1)

    tagged = sum(1 for v in cache.values() if v["genres"])
    still_missing = len(artist_names) - len(cache)
    hours_covered = hours_by_artist[hours_by_artist.index.isin(cache.keys())].sum()
    hours_total = hours_by_artist.sum()
    print(f"\nBatch done. {len(cache):,} / {len(artist_names):,} artist names resolved so far "
          f"({still_missing:,} still to go).")
    print(f"  {tagged:,} have at least one genre/tag ({tagged / len(cache):.1%})")
    print(f"  Listening-hours coverage: {hours_covered / hours_total:.1%}")
    print(f"Saved: {ARTIST_GENRES_PATH}")
    if args.batch_size and still_missing > 0:
        print(f"\nRe-run the same command for the next window of up to {args.batch_size} artists.")


if __name__ == "__main__":
    main()
