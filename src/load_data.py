"""
Load and clean Spotify Extended Streaming History.

Reads every Streaming_History_Audio_*.json file from the source export
folder, combines them into one chronological dataset, drops fields that
are either irrelevant (podcast/audiobook-only columns once we've filtered
to music) or privacy-sensitive (ip_addr), and applies the short-play
filter used by the rest of the analysis.

Design decisions (documented per the project brief):
- Podcast/audiobook rows (no track name) are dropped entirely — this
  project is about music listening.
- ip_addr is dropped immediately on load and never written to disk. It's
  the one field in the export that identifies *where* the listening
  happened rather than *what* was listened to, and has no analytical use
  here.
- The short-play filter threshold is 30 seconds (30_000 ms), the standard
  rule of thumb for excluding accidental taps / near-instant skips that
  would otherwise inflate play counts. Both the filtered and unfiltered
  row counts are preserved (via `ms_played` itself, still present in the
  cleaned file) so downstream analysis can report either.
"""

import glob
import json
import os

import pandas as pd

SOURCE_DIR = "/Users/ashish/Desktop/Spotify Extended Streaming History"
PROCESSED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "processed")
SHORT_PLAY_THRESHOLD_MS = 30_000

# Columns we keep from the raw export. Notably excludes ip_addr (privacy)
# and the audiobook_*/episode_* columns (out of scope — music only).
KEEP_COLUMNS = [
    "ts",
    "platform",
    "ms_played",
    "conn_country",
    "master_metadata_track_name",
    "master_metadata_album_artist_name",
    "master_metadata_album_album_name",
    "spotify_track_uri",
    "reason_start",
    "reason_end",
    "shuffle",
    "skipped",
    "offline",
    "incognito_mode",
]

RENAME_MAP = {
    "master_metadata_track_name": "track_name",
    "master_metadata_album_artist_name": "artist_name",
    "master_metadata_album_album_name": "album_name",
    "spotify_track_uri": "track_uri",
}


def load_raw_records() -> list[dict]:
    pattern = os.path.join(SOURCE_DIR, "Streaming_History_Audio_*.json")
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(f"No Streaming_History_Audio_*.json files found in {SOURCE_DIR}")

    records = []
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            records.extend(json.load(f))
    return records


def build_dataframe(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame.from_records(records)

    # Music only: rows with no track name are podcast episodes or
    # audiobook chapters, which this project doesn't analyze.
    df = df[df["master_metadata_track_name"].notna()].copy()

    df = df[KEEP_COLUMNS].rename(columns=RENAME_MAP)

    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.sort_values("ts").reset_index(drop=True)

    # Simplify the platform string into a coarse device category.
    # Raw values range from "ios" / "android" to verbose desktop OS
    # strings (e.g. "Windows 10 (10.0.19041; x64)") and browser user
    # agents — this buckets them into something chartable.
    df["platform_group"] = df["platform"].apply(_categorize_platform)

    return df


def _categorize_platform(raw: str) -> str:
    if not isinstance(raw, str):
        return "unknown"
    p = raw.lower()
    if "ios" in p or "iphone" in p or "ipad" in p:
        return "mobile (iOS)"
    if "android" in p:
        return "mobile (Android)"
    if "windows" in p:
        return "desktop (Windows)"
    if "osx" in p or "mac os" in p or "macos" in p:
        return "desktop (macOS)"
    if "linux" in p:
        return "desktop (Linux)"
    if "web_player" in p or "webplayer" in p or "browser" in p:
        return "web player"
    if "cast" in p or "speaker" in p or "tv" in p:
        return "cast / speaker / TV"
    return "other"


def main():
    print("Loading raw records...")
    records = load_raw_records()
    print(f"  {len(records):,} raw records across all files")

    df = build_dataframe(records)
    unfiltered_count = len(df)
    unfiltered_hours = df["ms_played"].sum() / 3_600_000

    df_clean = df[df["ms_played"] >= SHORT_PLAY_THRESHOLD_MS].copy()
    filtered_count = len(df_clean)
    filtered_hours = df_clean["ms_played"].sum() / 3_600_000

    print(f"  {unfiltered_count:,} music plays (podcast/audiobook rows dropped)")
    print(f"  {filtered_count:,} plays after removing plays under 30s "
          f"({unfiltered_count - filtered_count:,} dropped, "
          f"{(unfiltered_count - filtered_count) / unfiltered_count:.1%})")
    print(f"  Unfiltered listening time: {unfiltered_hours:,.0f} hours")
    print(f"  Filtered listening time:   {filtered_hours:,.0f} hours")
    print(f"  Date range: {df_clean['ts'].min()} to {df_clean['ts'].max()}")

    os.makedirs(PROCESSED_DIR, exist_ok=True)

    # Full (unfiltered) dataset — kept so any analysis that wants the raw
    # play count can get it without re-parsing the source JSON.
    df.to_parquet(os.path.join(PROCESSED_DIR, "streaming_history_full.parquet"), index=False)
    # Cleaned dataset (>=30s plays only) — what the rest of the pipeline uses.
    df_clean.to_parquet(os.path.join(PROCESSED_DIR, "streaming_history_clean.parquet"), index=False)

    summary = {
        "unfiltered_row_count": unfiltered_count,
        "filtered_row_count": filtered_count,
        "unfiltered_hours": round(unfiltered_hours, 1),
        "filtered_hours": round(filtered_hours, 1),
        "short_play_threshold_ms": SHORT_PLAY_THRESHOLD_MS,
        "date_range_start": str(df_clean["ts"].min()),
        "date_range_end": str(df_clean["ts"].max()),
        "unique_artists": int(df_clean["artist_name"].nunique()),
        "unique_tracks": int(df_clean["track_uri"].nunique()),
    }
    with open(os.path.join(PROCESSED_DIR, "load_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nSaved cleaned data to {PROCESSED_DIR}/")
    return df_clean


if __name__ == "__main__":
    main()
