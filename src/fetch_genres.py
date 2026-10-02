"""
Enrich the streaming history with genre data pulled from Spotify's Web API.

Genre isn't in the streaming history export at all — it has to be fetched
per-artist from Spotify's API. This script:

1. Authenticates using the Client Credentials flow (app-only auth — no
   user login, no scopes, just a Client ID + Secret from a Spotify
   Developer app at developer.spotify.com/dashboard).
2. For every unique artist name in the cleaned dataset, resolves it to a
   Spotify artist ID via the Search endpoint (`/search?type=artist`,
   taking the top-ranked match) and fetches that artist's genre tags.
3. Caches the result to disk (data/artist_genres.json) so re-running the
   pipeline doesn't re-hit the API for artists it has already resolved.

A NOTE ON API DESIGN (documented here because it shaped this script and
is worth knowing if you re-run this later): the brief originally called
for Spotify's batched "Get Several Artists" / "Get Several Tracks"
endpoints (up to 50 IDs per call, via a `?ids=...` query param), which
would have been more efficient and let genre resolution go through exact
track->artist ID matching rather than name search. Testing against this
project's actual app credentials showed those specific batch endpoints
return 403 Forbidden for this app's access tier — confirmed by testing
the *same* resource both ways: `GET /v1/artists/{id}` (singular, by
path) returns 200; `GET /v1/artists?ids={id}` (the "several" endpoint,
even with just one ID) returns 403 on the same token. Singular lookups
and `/search` are unaffected. This looks like a Spotify-side restriction
on the batch "several" endpoints for apps below some quota/extension
tier, not anything wrong with these credentials specifically. Given that,
this script uses Search (already known to work) to resolve each artist
directly by the name already present in the streaming history, then a
singular `/artists/{id}` call for genres — more requests than the
batched design would have needed (one Search + one lookup per unique
artist, rather than 50 per call), but each request is small and the
retry/backoff logic below handles the resulting rate-limit pacing.

LIMITATION (also documented in REPORT.md): resolving by Search-then-top-
match means a handful of ambiguous or unusual artist-name strings (e.g.
a few joined multi-artist credits like "David Guetta, French Montana,
Charli XCX, Afrojack" — 5 out of 2,577 unique names in this dataset)
may fail to resolve or resolve to the wrong same-named artist. Any name
that doesn't resolve is recorded with an empty genre list rather than
guessed at.

LIMITATION (also in REPORT.md): Spotify assigns genre tags at the
*artist* level, not the *track* level. Every track by an artist gets
that artist's full genre list, regardless of what that specific track
actually sounds like.
"""

import argparse
import json
import os
import sys
import time

import requests
from dotenv import load_dotenv

import pandas as pd

load_dotenv()
sys.stdout.reconfigure(line_buffering=True)  # so progress is visible in real time, even when redirected to a file

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
PROCESSED_DIR = os.path.join(DATA_DIR, "processed")

ARTIST_GENRES_PATH = os.path.join(DATA_DIR, "artist_genres.json")  # keyed by artist_name

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
MAX_RETRIES = 3  # lowered from 6 — empirically, once this app is walled, every retry fails anyway;
                 # a smaller budget per artist means the circuit breaker below reacts faster
REQUEST_PACE_SECONDS = 0.3  # delay between requests — widened after an earlier run at 0.05s
                             # triggered a sustained rate-limit penalty (every request failing
                             # after 6 retries), suggesting the app-level quota is stricter than
                             # a brief 429-and-retry pattern assumes


def get_access_token() -> str:
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Missing Spotify API credentials.\n\n"
            "Create a free app at https://developer.spotify.com/dashboard "
            "(takes ~2 minutes, no cost), then set:\n"
            "  SPOTIFY_CLIENT_ID=<your client id>\n"
            "  SPOTIFY_CLIENT_SECRET=<your client secret>\n"
            "either as environment variables or in a .env file in the "
            "project root (see README.md)."
        )
    resp = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


MAX_BACKOFF_SECONDS = 20
COOLDOWN_SECONDS = 300   # pause when hitting a sustained rate-limit wall (see circuit breaker below) —
                          # widened from 180s, which empirically wasn't long enough to clear the window
MAX_COOLDOWN_CYCLES = 4  # ~20 min of total cooldown budget before giving up for good — bounded
                         # deliberately: past this point, chasing full coverage against an
                         # undocumented platform rate limit stops being worth it (see REPORT.md)


def _request_with_backoff(url: str, headers: dict, params: dict) -> requests.Response | None:
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=(5, 15))
        except requests.exceptions.RequestException as e:
            wait = min(2 ** attempt, MAX_BACKOFF_SECONDS)
            print(f"    network error ({e.__class__.__name__}), retrying in {wait}s...", flush=True)
            time.sleep(wait)
            continue
        if resp.status_code == 429:
            wait = min(int(resp.headers.get("Retry-After", 2 ** attempt)), MAX_BACKOFF_SECONDS)
            print(f"    rate limited, waiting {wait}s...", flush=True)
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


def resolve_artist(name: str, token: str):
    """Search for `name`, take the top match. Its genres come directly
    from the search response — Spotify includes a `genres` field on
    every artist object returned by /search, so no follow-up /artists
    call is needed (an earlier version of this script made one anyway;
    it was pure wasted request volume that made rate limiting worse for
    no data benefit — removed).

    Returns:
      dict            - resolved successfully
      NOT_FOUND       - search ran fine but matched nothing (a real,
                        cacheable negative)
      None            - the request itself failed/exhausted retries
                        (e.g. sustained rate limiting) — caller should
                        NOT cache this, so it's retried on the next run
                        instead of being permanently mislabeled as
                        "not found".
    """
    headers = {"Authorization": f"Bearer {token}"}

    search_resp = _request_with_backoff(
        f"{API_BASE}/search", headers, {"q": name, "type": "artist", "limit": 1}
    )
    if search_resp is None:
        return None
    items = search_resp.json().get("artists", {}).get("items", [])
    if not items:
        return NOT_FOUND
    top = items[0]
    return {"artist_id": top["id"], "matched_name": top["name"], "genres": top.get("genres", [])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--batch-size", type=int, default=None,
        help="Process at most this many not-yet-cached artists, then save and exit cleanly. "
             "Useful for running genre resolution in short, manually-paced windows instead of "
             "one long unattended run, if Spotify's rate limit turns out to be a cumulative "
             "daily quota rather than a short burst window."
    )
    args = parser.parse_args()

    df = pd.read_parquet(os.path.join(PROCESSED_DIR, "streaming_history_clean.parquet"))

    # Resolve by DESCENDING listening hours, not alphabetically. Spotify's
    # rate limit on this app tier caps sustained throughput (confirmed:
    # repeatable sustained-failure walls every ~75-100 requests, needing a
    # real cooldown, not just brief backoff) — if a long run has to stop
    # partway through, this ordering guarantees the artists that actually
    # matter for the analysis (by hours listened) get resolved first. This
    # dataset's listening time is a classic long tail: the top 500 of 2,577
    # artists already account for 95.8% of all hours.
    hours_by_artist = df.groupby("artist_name")["ms_played"].sum().sort_values(ascending=False)
    artist_names = hours_by_artist.index.tolist()
    print(f"Resolving genres for {len(artist_names):,} unique artist names "
          f"(ordered by listening hours, most-played first)...")

    token = get_access_token()
    print("Authenticated with Spotify (Client Credentials flow).")

    os.makedirs(DATA_DIR, exist_ok=True)

    cache = {}
    if os.path.exists(ARTIST_GENRES_PATH):
        with open(ARTIST_GENRES_PATH) as f:
            cache = json.load(f)

    missing = [a for a in artist_names if a not in cache]
    print(f"  {len(cache)} already cached, {len(missing)} to resolve")

    target = missing[: args.batch_size] if args.batch_size else missing
    if args.batch_size:
        print(f"  --batch-size {args.batch_size}: this run will attempt {len(target)} artists, "
              f"then save and exit (re-run the same command for the next window)")

    unresolved = 0
    failed = 0
    consecutive_failures = 0
    cooldown_cycles = 0
    resolved_count = 0
    start_time = time.time()
    for i, name in enumerate(target, 1):
        result = resolve_artist(name, token)
        if result is None:
            # Request failed/exhausted retries — do NOT cache. Leave it
            # missing so a future run retries it, rather than permanently
            # mislabeling a rate-limit failure as "no genres".
            failed += 1
            consecutive_failures += 1
        elif result == NOT_FOUND:
            cache[name] = {"artist_id": None, "matched_name": None, "genres": []}
            unresolved += 1
            resolved_count += 1
            consecutive_failures = 0
        else:
            cache[name] = result
            resolved_count += 1
            consecutive_failures = 0
        time.sleep(REQUEST_PACE_SECONDS)

        # Circuit breaker: if we're failing over and over, the app is
        # almost certainly in a sustained rate-limit penalty window (this
        # happened once already — every request failed after 6 retries
        # apiece, wasting ~2 minutes per artist for zero result). Stop
        # entirely rather than keep burning through retries.
        if consecutive_failures >= 3:
            cooldown_cycles += 1
            if cooldown_cycles > MAX_COOLDOWN_CYCLES:
                print(f"\n  Hit {MAX_COOLDOWN_CYCLES} sustained rate-limit walls in this run — "
                      f"stopping for good rather than continuing indefinitely. Progress so far is "
                      f"saved; re-run the script later to pick up where it left off.", flush=True)
                break
            cooldown = COOLDOWN_SECONDS
            print(f"\n  {consecutive_failures} consecutive failures — sustained rate-limit penalty "
                  f"(cooldown {cooldown_cycles}/{MAX_COOLDOWN_CYCLES}). Pausing {cooldown // 60} min "
                  f"before resuming automatically...", flush=True)
            time.sleep(cooldown)
            consecutive_failures = 0

        if i % 25 == 0 or i == len(target):
            elapsed = time.time() - start_time
            rate = resolved_count / elapsed if elapsed > 0 else 0
            remaining = (len(target) - i) / rate / 60 if rate > 0 else float("inf")
            print(f"    {i}/{len(target)} attempted this run, {resolved_count} resolved "
                  f"({unresolved} confirmed not-found, {failed} failed/will retry) — "
                  f"~{remaining:.1f} min remaining in this batch at current rate", flush=True)
            with open(ARTIST_GENRES_PATH, "w") as f:
                json.dump(cache, f, indent=1)

    with open(ARTIST_GENRES_PATH, "w") as f:
        json.dump(cache, f, indent=1)

    tagged = sum(1 for v in cache.values() if v["genres"])
    not_found = sum(1 for v in cache.values() if v["artist_id"] is None)
    still_missing = len(artist_names) - len(cache)
    hours_covered = hours_by_artist[hours_by_artist.index.isin(cache.keys())].sum()
    hours_total = hours_by_artist.sum()
    print(f"\nBatch done. {len(cache):,} / {len(artist_names):,} artist names resolved so far "
          f"({still_missing:,} still to go).")
    print(f"  {tagged:,} have at least one genre tag ({tagged / len(cache):.1%})")
    print(f"  {not_found:,} could not be resolved via search ({not_found / len(cache):.1%})")
    print(f"  Listening-hours coverage: {hours_covered / hours_total:.1%} of total hours "
          f"(this is the number that matters for the analysis — artist count doesn't, "
          f"since it's a long-tail distribution)")
    print(f"Saved: {ARTIST_GENRES_PATH}")
    if args.batch_size and still_missing > 0:
        print(f"\nRe-run the same command for the next window of up to {args.batch_size} artists.")


if __name__ == "__main__":
    main()
