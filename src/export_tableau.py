"""
Exports a single tidy, row-per-play CSV for building a Tableau dashboard
from this project's already-computed data — genre/EDM classification,
timezone-local time fields, the short-play filter flag, and discovery
status are all pre-computed here so Tableau never needs to re-derive
anything that required Python-side logic (genre bucketing, timezone
conversion, or a groupby/join for "is this artist new").

GRAIN: one row per raw play (the full, UNFILTERED dataset — 236,487 rows),
not per artist-month or any other pre-aggregated grain. Two reasons:
1. Every chart already built in this project needs a different slice or
   aggregation of the data (by month, by hour, by artist, by genre-bucket,
   by skip status...) — row-per-play is the only grain that lets Tableau
   recreate all of them via its own drag-and-drop aggregation, rather than
   locking in one pre-chosen aggregation that would fit some charts and
   not others.
2. Skip-behavior analysis specifically needs the UNFILTERED data (that's
   the whole point of the 30-second filter — it removes exactly the plays
   that signal a skip). Exporting only the filtered/"clean" data would
   make that analysis impossible to reproduce in Tableau. Instead, an
   `is_short_play` boolean flag is included so Tableau can filter to the
   "clean" 140,161-row subset with one click when recreating any chart
   that used the filtered data (i.e. everything except skip behavior).

See README.md's "Tableau export" section for the full column reference.
"""

import os

import pandas as pd

import sys
sys.path.insert(0, os.path.dirname(__file__))
from genre_mapping import classify_bucket, is_edm, edm_subgenres, load_artist_genre_lookup

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
PROCESSED_DIR = os.path.join(DATA_DIR, "processed")
LOCAL_TZ = "Asia/Kolkata"
SHORT_PLAY_THRESHOLD_MS = 30_000
OUT_PATH = os.path.join(DATA_DIR, "tableau_export.csv")


def main():
    df = pd.read_parquet(os.path.join(PROCESSED_DIR, "streaming_history_full.parquet"))
    df = df.sort_values("ts").reset_index(drop=True)

    # Timezone-local time fields — same derivation as analysis.py, applied
    # here to the full (unfiltered) dataset instead of just the clean one.
    ts_local = df["ts"].dt.tz_convert(LOCAL_TZ)
    df["ts_local"] = ts_local.dt.tz_localize(None)  # drop tz info: plain local wall-clock time, unambiguous in a CSV
    df["year"] = ts_local.dt.year
    df["month"] = ts_local.dt.tz_localize(None).dt.to_period("M").astype(str)  # "YYYY-MM"
    df["hour"] = ts_local.dt.hour
    df["day_of_week"] = ts_local.dt.day_name()
    df["hours_played"] = df["ms_played"] / 3_600_000

    # The project's own short-play filter, exposed as a flag rather than
    # applied — lets Tableau reproduce either the "clean" or "full" view.
    df["is_short_play"] = df["ms_played"] < SHORT_PLAY_THRESHOLD_MS

    # Discovery status — precomputed exactly as in analysis.py's
    # discovery_vs_repeat(), since "first-ever play of this artist" needs
    # a groupby-min + join that Tableau can't do on raw CSV rows alone.
    first_seen_artist = df.groupby("artist_name")["ts"].transform("min")
    df["artist_first_seen_year"] = first_seen_artist.dt.tz_convert(LOCAL_TZ).dt.year
    df["is_artist_first_play"] = df["ts"] == first_seen_artist

    # Genre / EDM classification — requires this project's MusicBrainz
    # lookup + genre_mapping.py's plurality-vote bucketing logic, which
    # Tableau has no way to replicate. Unresolved or tag-less artists
    # fall through to "Unknown / No Genre Data" consistently, matching
    # every chart and the coverage disclosure in REPORT.md exactly.
    lookup = load_artist_genre_lookup()
    df["raw_genres"] = df["artist_name"].map(lambda a: lookup.get(a, []))
    df["genre_bucket"] = df["raw_genres"].map(classify_bucket)
    df["is_edm"] = df["raw_genres"].map(is_edm)
    df["edm_subgenres"] = df["raw_genres"].map(lambda g: ";".join(edm_subgenres(g)))
    df["raw_genres"] = df["raw_genres"].map(lambda g: ";".join(g))

    export = df[[
        "ts", "ts_local", "year", "month", "hour", "day_of_week",
        "artist_name", "track_name", "album_name",
        "ms_played", "hours_played", "is_short_play",
        "genre_bucket", "raw_genres", "is_edm", "edm_subgenres",
        "artist_first_seen_year", "is_artist_first_play",
        "skipped", "reason_start", "reason_end", "shuffle", "offline", "incognito_mode",
        "platform", "platform_group", "conn_country",
    ]].rename(columns={"ts": "ts_utc"})

    export.to_csv(OUT_PATH, index=False)
    print(f"Exported {len(export):,} rows, {len(export.columns)} columns to {OUT_PATH}")
    print(f"  Unknown/no-genre-data rows: {(export['genre_bucket'] == 'Unknown / No Genre Data').mean():.1%}")
    print(f"  EDM rows: {export['is_edm'].mean():.1%}")


if __name__ == "__main__":
    main()
