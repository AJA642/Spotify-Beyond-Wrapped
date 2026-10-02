"""
Core listening analysis (genre-independent).

Covers: total listening time, top artists/tracks/albums (lifetime + by
year), monthly volume trend, time-of-day / day-of-week patterns, skip
behavior, discovery-vs-repeat behavior, and platform/device patterns.

Design decisions:
- All "top N" rankings use total listening time (ms_played summed), not
  raw play count, as the primary metric — a track played twice at full
  length represents more genuine listening than one played five times
  and skipped after 35 seconds. Play count is reported alongside it.
- Time-of-day / day-of-week analysis converts timestamps from UTC to
  Asia/Kolkata (IST). 92.5% of all plays in this dataset have
  conn_country == 'IN'; the next largest is AE (UAE, ~5%, corresponding
  to the Dubai MSc period). A single fixed timezone is a simplification
  — the ~7.5% of plays from other countries will show at a shifted local
  hour — but converting per-row by conn_country would imply a precision
  the data doesn't really support (conn_country is the connection at
  play time, not a reliable travel log), so one dominant-timezone
  assumption is used and documented here and in REPORT.md.
- "Discovery" for a given period = artists/tracks whose *first-ever*
  appearance in the whole dataset falls in that period. This is a
  lifetime-relative definition, not "new to the last N months".
"""

import json
import os

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
PROCESSED_DIR = os.path.join(PROJECT_ROOT, "data", "processed")
LOCAL_TZ = "Asia/Kolkata"


def load_clean() -> pd.DataFrame:
    df = pd.read_parquet(os.path.join(PROCESSED_DIR, "streaming_history_clean.parquet"))
    df["ts_local"] = df["ts"].dt.tz_convert(LOCAL_TZ)
    df["year"] = df["ts_local"].dt.year
    df["month"] = df["ts_local"].dt.tz_localize(None).dt.to_period("M").astype(str)
    df["hour"] = df["ts_local"].dt.hour
    df["day_of_week"] = df["ts_local"].dt.day_name()
    df["hours_played"] = df["ms_played"] / 3_600_000
    return df


def total_listening_time(df: pd.DataFrame) -> dict:
    lifetime_hours = df["hours_played"].sum()
    by_year = df.groupby("year")["hours_played"].sum().round(1).to_dict()
    return {"lifetime_hours": round(lifetime_hours, 1), "by_year_hours": by_year}


def top_n(df: pd.DataFrame, group_col: str, n: int = 15) -> pd.DataFrame:
    g = df.groupby(group_col).agg(
        hours=("hours_played", "sum"),
        plays=("ms_played", "count"),
    ).sort_values("hours", ascending=False)
    g["hours"] = g["hours"].round(1)
    return g.head(n).reset_index()


def top_lists(df: pd.DataFrame) -> dict:
    result = {
        "top_artists_lifetime": top_n(df, "artist_name").to_dict("records"),
        "top_tracks_lifetime": top_n(
            df.assign(track_label=df["track_name"] + " — " + df["artist_name"]), "track_label"
        ).to_dict("records"),
        "top_albums_lifetime": top_n(
            df[df["album_name"].notna()].assign(
                album_label=df["album_name"] + " — " + df["artist_name"]
            ),
            "album_label",
        ).to_dict("records"),
        "top_artists_by_year": {},
    }
    for year, group in df.groupby("year"):
        result["top_artists_by_year"][int(year)] = top_n(group, "artist_name", n=5).to_dict("records")
    return result


def monthly_trend(df: pd.DataFrame) -> dict:
    monthly = df.groupby("month")["hours_played"].sum().round(1)
    return monthly.to_dict()


def time_patterns(df: pd.DataFrame) -> dict:
    by_hour = df.groupby("hour")["hours_played"].sum().round(1).to_dict()
    by_dow = df.groupby("day_of_week")["hours_played"].sum().round(1).to_dict()
    # hour x day-of-week matrix for the heatmap
    heatmap = df.pivot_table(
        index="day_of_week", columns="hour", values="hours_played", aggfunc="sum", fill_value=0
    )
    dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    heatmap = heatmap.reindex(dow_order)
    return {
        "by_hour": by_hour,
        "by_day_of_week": by_dow,
        "heatmap": heatmap.to_dict(),
    }


def skip_behavior(df_full: pd.DataFrame, df_clean: pd.DataFrame) -> dict:
    # Use the FULL (unfiltered) dataset for skip behavior — "skipped" and
    # very-short plays are exactly the signal the 30s filter removes from
    # everything else, so skip analysis needs the raw plays to be meaningful.
    df_full = df_full.copy()
    df_full["ts_local"] = df_full["ts"].dt.tz_convert(LOCAL_TZ)
    df_full["year"] = df_full["ts_local"].dt.year

    overall_skip_rate = df_full["skipped"].mean()
    by_year = df_full.groupby("year")["skipped"].mean().round(3).to_dict()

    # "Cut short" = ended before 30s regardless of the `skipped` flag
    # (Spotify's own flag is somewhat inconsistent about what counts).
    cut_short_rate = (df_full["ms_played"] < 30_000).mean()

    return {
        "overall_skip_flag_rate": round(overall_skip_rate, 3),
        "skip_flag_rate_by_year": by_year,
        "cut_short_under_30s_rate": round(cut_short_rate, 3),
        "total_raw_plays": len(df_full),
    }


def discovery_vs_repeat(df: pd.DataFrame) -> dict:
    df = df.sort_values("ts")
    first_seen_artist = df.groupby("artist_name")["ts"].min()
    first_seen_track = df.groupby("track_uri")["ts"].min()

    df = df.copy()
    df["artist_first_seen"] = df["artist_name"].map(first_seen_artist)
    df["track_first_seen"] = df["track_uri"].map(first_seen_track)
    df["is_artist_discovery_play"] = df["ts"] == df["artist_first_seen"]
    df["is_track_discovery_play"] = df["ts"] == df["track_first_seen"]

    # New distinct artists/tracks appearing per year (by first-ever play).
    new_artists_by_year = (
        first_seen_artist.dt.tz_convert(LOCAL_TZ).dt.year.value_counts().sort_index().to_dict()
    )
    new_tracks_by_year = (
        first_seen_track.dt.tz_convert(LOCAL_TZ).dt.year.value_counts().sort_index().to_dict()
    )

    # Of all plays in a given year, what share are of an artist/track
    # first encountered that same year (discovery) vs. an earlier year (repeat)?
    df["year"] = df["ts"].dt.tz_convert(LOCAL_TZ).dt.year
    df["artist_first_year"] = df["artist_first_seen"].dt.tz_convert(LOCAL_TZ).dt.year
    df["track_first_year"] = df["track_first_seen"].dt.tz_convert(LOCAL_TZ).dt.year
    df["is_new_artist_play"] = df["artist_first_year"] == df["year"]
    df["is_new_track_play"] = df["track_first_year"] == df["year"]
    plays_new_artist_share = df.groupby("year")["is_new_artist_play"].mean().round(3).to_dict()
    plays_new_track_share = df.groupby("year")["is_new_track_play"].mean().round(3).to_dict()

    return {
        "new_distinct_artists_by_year": {int(k): int(v) for k, v in new_artists_by_year.items()},
        "new_distinct_tracks_by_year": {int(k): int(v) for k, v in new_tracks_by_year.items()},
        "plays_that_are_new_artist_share_by_year": {int(k): v for k, v in plays_new_artist_share.items()},
        "plays_that_are_new_track_share_by_year": {int(k): v for k, v in plays_new_track_share.items()},
    }


def platform_patterns(df: pd.DataFrame) -> dict:
    by_platform = df.groupby("platform_group")["hours_played"].sum().round(1).sort_values(ascending=False)
    share = (by_platform / by_platform.sum()).round(3)
    return {
        "hours_by_platform": by_platform.to_dict(),
        "share_by_platform": share.to_dict(),
    }


def main():
    df = load_clean()
    df_full = pd.read_parquet(os.path.join(PROCESSED_DIR, "streaming_history_full.parquet"))

    results = {
        "total_listening_time": total_listening_time(df),
        "top_lists": top_lists(df),
        "monthly_trend_hours": monthly_trend(df),
        "time_patterns": time_patterns(df),
        "skip_behavior": skip_behavior(df_full, df),
        "discovery_vs_repeat": discovery_vs_repeat(df),
        "platform_patterns": platform_patterns(df),
    }

    out_path = os.path.join(PROCESSED_DIR, "analysis_results.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"Lifetime listening: {results['total_listening_time']['lifetime_hours']:,} hours")
    print(f"By year: {results['total_listening_time']['by_year_hours']}")
    print(f"\nTop 5 artists lifetime:")
    for row in results["top_lists"]["top_artists_lifetime"][:5]:
        print(f"  {row['artist_name']}: {row['hours']}h ({row['plays']} plays)")
    print(f"\nOverall skip-flag rate: {results['skip_behavior']['overall_skip_flag_rate']:.1%}")
    print(f"Plays cut short (<30s): {results['skip_behavior']['cut_short_under_30s_rate']:.1%}")
    print(f"\nSaved full results to {out_path}")

    # Also save the enriched clean dataframe (with local tz columns) for
    # visualize.py / edm_analysis.py to reuse without recomputing.
    df.to_parquet(os.path.join(PROCESSED_DIR, "streaming_history_enriched.parquet"), index=False)

    return results


if __name__ == "__main__":
    main()
