"""
Extended listening statistics — a follow-up pass requested after the main
analysis (analysis.py) and EDM deep dive (edm_analysis.py) were already
complete. Covers four questions the original analysis didn't answer:
unique album count, listening consistency (streaks/gaps), a concentration
curve beyond the top 5, and session-level stats.

A fifth candidate — "era of music" via MusicBrainz release-year resolution
per track — was investigated and rejected as unreliable, not just
incomplete. Multiple recording candidates per track from MusicBrainz's
own search routinely tie at the maximum relevance score while spanning
release dates a decade or more apart (remixes, reissues, deluxe
editions) — e.g. "Wake Me Up" by Avicii returned 10 same-artist,
same-title candidates scored identically, dated 2013 through 2021. A
naive top-1 search returned a release date for "Levels" six years off
from its actual 2011 release. This isn't a coverage gap fixable by
querying harder; picking the true original release among tied candidates
isn't reliably possible from title-based search alone. Not built.

Design decisions (see REPORT.md's new "Extended Analysis" section for
full reasoning on each):
- Unique album count and the concentration curve use the CLEAN (30s-filtered)
  dataset, matching every other "top N" / hours-based figure in this project.
- Listening-consistency (calendar-day presence, streaks, gaps) uses the
  FULL (unfiltered) dataset, on the same logic already established for
  skip_behavior() in analysis.py: a single short/accidental play still
  means the app was opened and used that day, which is what "consistency"
  is measuring — filtering it out would remove exactly the signal of interest.
- Sessions are built from the CLEAN dataset (a session is a run of *real*
  listening, consistent with how this project treats short plays
  everywhere else) using start-to-start gaps between consecutive plays;
  a new session begins whenever that gap exceeds 30 minutes.
"""

import json
import os

import pandas as pd

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
PROCESSED_DIR = os.path.join(PROJECT_ROOT, "data", "processed")
LOCAL_TZ = "Asia/Kolkata"
SESSION_GAP_MINUTES = 30


def load_clean() -> pd.DataFrame:
    df = pd.read_parquet(os.path.join(PROCESSED_DIR, "streaming_history_clean.parquet"))
    df["ts_local"] = df["ts"].dt.tz_convert(LOCAL_TZ)
    df["hours_played"] = df["ms_played"] / 3_600_000
    return df.sort_values("ts").reset_index(drop=True)


def load_full() -> pd.DataFrame:
    df = pd.read_parquet(os.path.join(PROCESSED_DIR, "streaming_history_full.parquet"))
    df["ts_local"] = df["ts"].dt.tz_convert(LOCAL_TZ)
    return df


def unique_album_count(df_clean: pd.DataFrame) -> dict:
    n_albums = df_clean["album_name"].nunique()
    n_album_artist_pairs = df_clean.dropna(subset=["album_name"]).groupby(
        ["album_name", "artist_name"]
    ).ngroups
    return {
        "unique_album_names": int(n_albums),
        "unique_album_artist_pairs": int(n_album_artist_pairs),
    }


def listening_consistency(df_full: pd.DataFrame) -> dict:
    dates_with_play = pd.to_datetime(df_full["ts_local"].dt.date.unique())
    dates_with_play = pd.Series(sorted(dates_with_play))

    full_range = pd.date_range(dates_with_play.iloc[0], dates_with_play.iloc[-1], freq="D")
    total_days_in_range = len(full_range)
    days_with_play = len(dates_with_play)

    # Longest streak of consecutive calendar days with >=1 play.
    day_diffs = dates_with_play.diff().dt.days.fillna(1)
    streak_id = (day_diffs != 1).cumsum()
    streak_lengths = dates_with_play.groupby(streak_id).size()
    longest_streak = int(streak_lengths.max())
    longest_streak_end = dates_with_play[streak_id == streak_lengths.idxmax()].iloc[-1]
    longest_streak_start = dates_with_play[streak_id == streak_lengths.idxmax()].iloc[0]

    # Longest gap (calendar days) between two consecutive listened-to days.
    gaps = dates_with_play.diff().dt.days.dropna()
    longest_gap_days = int(gaps.max() - 1) if len(gaps) else 0
    gap_end_idx = gaps.idxmax()
    gap_end_date = dates_with_play.iloc[gap_end_idx]
    gap_start_date = dates_with_play.iloc[gap_end_idx - 1]

    return {
        "total_days_in_date_range": int(total_days_in_range),
        "days_with_at_least_one_play": int(days_with_play),
        "share_of_days_with_a_play": round(days_with_play / total_days_in_range, 4),
        "longest_consecutive_day_streak": longest_streak,
        "longest_streak_start": str(longest_streak_start.date()),
        "longest_streak_end": str(longest_streak_end.date()),
        "longest_gap_days": longest_gap_days,
        "longest_gap_start": str(gap_start_date.date()),
        "longest_gap_end": str(gap_end_date.date()),
    }


def concentration_curve(df_clean: pd.DataFrame) -> dict:
    hours_by_artist = df_clean.groupby("artist_name")["hours_played"].sum().sort_values(ascending=False)
    total_hours = hours_by_artist.sum()
    cum_share = (hours_by_artist.cumsum() / total_hours)

    checkpoints = [5, 10, 25, 50, 100, 250]
    result = {}
    for n in checkpoints:
        if n <= len(cum_share):
            result[f"top_{n}_share_of_hours"] = round(float(cum_share.iloc[n - 1]), 4)
    result["total_unique_artists"] = int(len(hours_by_artist))
    return result


def session_stats(df_clean: pd.DataFrame) -> dict:
    df = df_clean.sort_values("ts").reset_index(drop=True)
    gap_minutes = df["ts"].diff().dt.total_seconds().div(60)
    new_session = (gap_minutes.isna()) | (gap_minutes > SESSION_GAP_MINUTES)
    df["session_id"] = new_session.cumsum()

    sessions = df.groupby("session_id").agg(
        n_tracks=("track_name", "count"),
        hours=("hours_played", "sum"),
        start=("ts_local", "min"),
        end=("ts_local", "max"),
    )
    # Session wall-clock span (end of last track's play, not just start-to-start).
    last_track_ms = df.groupby("session_id")["ms_played"].last()
    sessions["span_minutes"] = (
        (sessions["end"] - sessions["start"]).dt.total_seconds() / 60
        + last_track_ms.reindex(sessions.index).fillna(0) / 60_000
    )

    longest = sessions.loc[sessions["span_minutes"].idxmax()]
    most_active_day = (
        df.groupby(df["ts_local"].dt.date)["hours_played"].sum().sort_values(ascending=False)
    )

    return {
        "session_gap_threshold_minutes": SESSION_GAP_MINUTES,
        "total_sessions": int(len(sessions)),
        "median_tracks_per_session": float(sessions["n_tracks"].median()),
        "mean_tracks_per_session": round(float(sessions["n_tracks"].mean()), 2),
        "median_session_minutes": round(float(sessions["span_minutes"].median()), 1),
        "mean_session_minutes": round(float(sessions["span_minutes"].mean()), 1),
        "longest_session": {
            "start": str(longest["start"]),
            "end": str(longest["end"]),
            "span_minutes": round(float(longest["span_minutes"]), 1),
            "n_tracks": int(longest["n_tracks"]),
            "hours": round(float(longest["hours"]), 2),
        },
        "most_active_day": {
            "date": str(most_active_day.index[0]),
            "hours": round(float(most_active_day.iloc[0]), 2),
        },
    }


def main():
    df_clean = load_clean()
    df_full = load_full()

    results = {
        "unique_albums": unique_album_count(df_clean),
        "listening_consistency": listening_consistency(df_full),
        "concentration_curve": concentration_curve(df_clean),
        "session_stats": session_stats(df_clean),
    }

    out_path = os.path.join(PROCESSED_DIR, "extended_stats.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    print("Unique albums:", results["unique_albums"])
    print("\nListening consistency:", results["listening_consistency"])
    print("\nConcentration curve:", results["concentration_curve"])
    print("\nSession stats:", results["session_stats"])
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
