"""
Sanity check on genre coverage: is EDM-relevant listening disproportionately
sitting in the unresolved long tail of artists (the ~1.8% of hours not
covered by the 758 artists genre-resolution reached)?

If EDM listening tends to happen across many low-play artists rather than
a few heavy-rotation ones, the tail could be denser in EDM than the
resolved set, which would mean the EDM share reported in REPORT.md
understates the true figure. This checks that empirically rather than
assuming it away.

METHOD: take a random sample of currently-unresolved artists (fixed seed
for reproducibility), resolve their genres via the same MusicBrainz path
used everywhere else in this project, and compare the sample's EDM share
against the EDM share among the already-resolved (covered) set. This is a
sanity check, not a rigorous statistical test — the sample is modest in
size, so treat the comparison as directional, not exact.
"""

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd

from fetch_genres_musicbrainz import resolve_artist, NOT_FOUND, ARTIST_GENRES_PATH, REQUEST_PACE_SECONDS
from genre_mapping import is_edm
import time

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
PROCESSED_DIR = os.path.join(PROJECT_ROOT, "data", "processed")
OUT_PATH = os.path.join(PROCESSED_DIR, "edm_tail_coverage_check.json")

SAMPLE_SIZE = 80
RANDOM_SEED = 42


def main():
    with open(ARTIST_GENRES_PATH) as f:
        cache = json.load(f)

    df = pd.read_parquet(os.path.join(PROCESSED_DIR, "streaming_history_clean.parquet"))
    hours_by_artist = df.groupby("artist_name")["ms_played"].sum().sort_values(ascending=False)
    all_artists = hours_by_artist.index.tolist()

    missing = [a for a in all_artists if a not in cache]
    print(f"{len(cache)} artists already resolved (covered set), {len(missing)} still unresolved (tail)")

    random.seed(RANDOM_SEED)
    sample = random.sample(missing, min(SAMPLE_SIZE, len(missing)))
    print(f"Sampling {len(sample)} artists at random from the unresolved tail (seed={RANDOM_SEED})...")

    sample_genres = {}
    for i, name in enumerate(sample, 1):
        result = resolve_artist(name)
        if result is None:
            print(f"    [{i}/{len(sample)}] {name!r}: failed, skipping from sample")
        elif result == NOT_FOUND:
            sample_genres[name] = []
        else:
            sample_genres[name] = result["genres"]
            cache[name] = result  # fold into the main cache too — legitimate resolution, free extra coverage
        time.sleep(REQUEST_PACE_SECONDS)
        if i % 20 == 0:
            print(f"    [{i}/{len(sample)}] resolved so far...")

    with open(ARTIST_GENRES_PATH, "w") as f:
        json.dump(cache, f, indent=1)

    # EDM share within the sample (tail)
    sample_edm_count = sum(1 for g in sample_genres.values() if is_edm(g))
    sample_edm_share = sample_edm_count / len(sample_genres) if sample_genres else None

    # EDM share within the already-covered set (excluding this sample, to keep the two groups distinct)
    covered_names = [a for a in cache if a not in sample_genres]
    covered_genres = {a: cache[a]["genres"] for a in covered_names}
    covered_edm_count = sum(1 for g in covered_genres.values() if is_edm(g))
    covered_edm_share = covered_edm_count / len(covered_genres) if covered_genres else None

    result = {
        "sample_size": len(sample_genres),
        "sample_edm_count": sample_edm_count,
        "sample_edm_share": round(sample_edm_share, 4) if sample_edm_share is not None else None,
        "covered_set_size": len(covered_genres),
        "covered_edm_count": covered_edm_count,
        "covered_edm_share": round(covered_edm_share, 4) if covered_edm_share is not None else None,
        "random_seed": RANDOM_SEED,
    }
    with open(OUT_PATH, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\nCovered-set EDM share: {covered_edm_share:.1%} ({covered_edm_count}/{len(covered_genres)})")
    print(f"Tail-sample EDM share: {sample_edm_share:.1%} ({sample_edm_count}/{len(sample_genres)})")
    print(f"Saved: {OUT_PATH}")


if __name__ == "__main__":
    main()
