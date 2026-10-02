"""
The EDM-specific deep dive — the distinctive section of this project.

Requires data/artist_genres.json (from fetch_genres.py) to already exist.
Produces:
  - data/processed/genre_share_by_year.csv   (general top-level genre mix, for the overall genre chart)
  - data/processed/edm_results.json          (everything EDM-specific)

See src/genre_mapping.py for how raw Spotify genre tags get bucketed
into "EDM / Dance" vs everything else, and into EDM sub-genres.
"""

import json
import os

import pandas as pd

from genre_mapping import classify_bucket, is_edm, edm_subgenres, load_artist_genre_lookup

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
PROCESSED_DIR = os.path.join(PROJECT_ROOT, "data", "processed")
LOCAL_TZ = "Asia/Kolkata"


def _attach_genre_columns(df: pd.DataFrame, lookup: dict) -> pd.DataFrame:
    df = df.copy()
    df["raw_genres"] = df["artist_name"].map(lambda a: lookup.get(a, []))
    df["genre_bucket"] = df["raw_genres"].map(classify_bucket)
    df["is_edm"] = df["raw_genres"].map(is_edm)
    return df


def general_genre_share_by_year(df: pd.DataFrame) -> pd.DataFrame:
    hours_by_year_bucket = df.groupby(["year", "genre_bucket"])["hours_played"].sum().unstack(fill_value=0)
    share = hours_by_year_bucket.div(hours_by_year_bucket.sum(axis=1), axis=0) * 100
    # Keep only buckets that are ever >=1% in some year, to avoid a legend of tiny slivers.
    keep_cols = share.columns[(share.max(axis=0) >= 1.0)]
    share = share[keep_cols].round(2)
    return share.sort_index()


def edm_share_by_year(df: pd.DataFrame) -> dict:
    total = df.groupby("year")["hours_played"].sum()
    edm = df[df["is_edm"]].groupby("year")["hours_played"].sum()
    share = (edm.reindex(total.index, fill_value=0) / total).round(4)
    return share.to_dict()


def top_edm_artists_and_tracks(df: pd.DataFrame) -> dict:
    edm_df = df[df["is_edm"]]
    top_artists = (
        edm_df.groupby("artist_name")
        .agg(hours=("hours_played", "sum"), plays=("ms_played", "count"))
        .sort_values("hours", ascending=False)
        .round(1)
        .head(15)
        .reset_index()
        .to_dict("records")
    )
    edm_df = edm_df.assign(track_label=edm_df["track_name"] + " — " + edm_df["artist_name"])
    top_tracks = (
        edm_df.groupby("track_label")
        .agg(hours=("hours_played", "sum"), plays=("ms_played", "count"))
        .sort_values("hours", ascending=False)
        .round(1)
        .head(15)
        .reset_index()
        .to_dict("records")
    )
    return {"top_edm_artists": top_artists, "top_edm_tracks": top_tracks}


def edm_subgenre_breakdown(df: pd.DataFrame) -> dict:
    edm_df = df[df["is_edm"]].copy()
    edm_df["subgenres"] = edm_df["raw_genres"].map(edm_subgenres)
    exploded = edm_df.explode("subgenres")
    exploded = exploded[exploded["subgenres"].notna()]
    by_subgenre = (
        exploded.groupby("subgenres")["hours_played"].sum().sort_values(ascending=False).round(1)
    )
    return by_subgenre.to_dict()


def edm_vs_overall_time_patterns(df: pd.DataFrame) -> dict:
    edm_df = df[df["is_edm"]]

    def normalized_dist(sub_df, col):
        s = sub_df.groupby(col)["hours_played"].sum()
        return (s / s.sum() * 100).round(2).to_dict()

    return {
        "edm_hour_distribution_pct": normalized_dist(edm_df, "hour"),
        "overall_hour_distribution_pct": normalized_dist(df, "hour"),
        "edm_dow_distribution_pct": normalized_dist(edm_df, "day_of_week"),
        "overall_dow_distribution_pct": normalized_dist(df, "day_of_week"),
    }


def concentration_comparison(df: pd.DataFrame) -> dict:
    """What share of listening time (within EDM, and within everything)
    is concentrated in the top 5 artists? Higher = more concentrated /
    less varied taste within that slice."""
    def top5_share(sub_df):
        by_artist = sub_df.groupby("artist_name")["hours_played"].sum().sort_values(ascending=False)
        if by_artist.sum() == 0:
            return None
        return round(by_artist.head(5).sum() / by_artist.sum(), 3)

    edm_df = df[df["is_edm"]]
    return {
        "edm_top5_artist_share_of_edm_hours": top5_share(edm_df),
        "overall_top5_artist_share_of_all_hours": top5_share(df),
        "edm_unique_artists": int(edm_df["artist_name"].nunique()),
        "overall_unique_artists": int(df["artist_name"].nunique()),
    }


def skip_rate_by_bucket(df_full: pd.DataFrame, lookup: dict) -> dict:
    df_full = _attach_genre_columns(df_full, lookup)
    by_bucket = df_full.groupby("genre_bucket")["skipped"].agg(["mean", "count"]).round(3)
    by_bucket.columns = ["skip_rate", "raw_play_count"]
    return by_bucket.sort_values("skip_rate", ascending=False).reset_index().to_dict("records")


def edm_share_by_month(df: pd.DataFrame) -> dict:
    total = df.groupby("month")["hours_played"].sum()
    edm = df[df["is_edm"]].groupby("month")["hours_played"].sum()
    share = (edm.reindex(total.index, fill_value=0) / total).round(4)
    return share.to_dict()


def main():
    lookup = load_artist_genre_lookup()
    resolved = sum(1 for g in lookup.values() if g)
    print(f"Loaded genre tags for {len(lookup):,} artist names ({resolved:,} with at least one tag)")

    df = pd.read_parquet(os.path.join(PROCESSED_DIR, "streaming_history_enriched.parquet"))
    df = _attach_genre_columns(df, lookup)

    tagged_names = {a for a, g in lookup.items() if g}
    coverage = df.loc[df["artist_name"].isin(tagged_names), "hours_played"].sum() / df["hours_played"].sum()
    print(f"Genre coverage: {coverage:.1%} of listening hours are by an artist with >=1 genre tag")

    # 1. General genre mix over time
    genre_share = general_genre_share_by_year(df)
    genre_share.to_csv(os.path.join(PROCESSED_DIR, "genre_share_by_year.csv"))
    print("\nGenre share by year (top-level buckets, %):")
    print(genre_share.round(1))

    # 2. EDM deep dive
    edm_share_year = edm_share_by_year(df)
    edm_share_month = edm_share_by_month(df)
    top_lists = top_edm_artists_and_tracks(df)
    subgenres = edm_subgenre_breakdown(df)
    time_patterns = edm_vs_overall_time_patterns(df)
    concentration = concentration_comparison(df)

    df_full = pd.read_parquet(os.path.join(PROCESSED_DIR, "streaming_history_full.parquet"))
    skip_by_bucket = skip_rate_by_bucket(df_full, lookup)

    total_edm_hours = df[df["is_edm"]]["hours_played"].sum()
    edm_results = {
        "genre_coverage_share_of_hours": round(coverage, 4),
        "total_edm_hours": round(total_edm_hours, 1),
        "total_edm_hours_share_lifetime": round(total_edm_hours / df["hours_played"].sum(), 4),
        "edm_share_by_year": edm_share_year,
        "edm_share_by_month": edm_share_month,
        **top_lists,
        "edm_subgenre_hours": subgenres,
        **time_patterns,
        "concentration": concentration,
        "skip_rate_by_genre_bucket": skip_by_bucket,
    }

    out_path = os.path.join(PROCESSED_DIR, "edm_results.json")
    with open(out_path, "w") as f:
        json.dump(edm_results, f, indent=2, default=str)

    print(f"\nLifetime EDM share: {edm_results['total_edm_hours_share_lifetime']:.1%} "
          f"({edm_results['total_edm_hours']:.0f}h)")
    print(f"EDM share by year: { {k: f'{v:.1%}' for k, v in edm_share_year.items()} }")
    print(f"\nTop 5 EDM artists:")
    for row in top_lists["top_edm_artists"][:5]:
        print(f"  {row['artist_name']}: {row['hours']}h")
    print(f"\nSaved: {out_path}")
    print(f"Saved: {os.path.join(PROCESSED_DIR, 'genre_share_by_year.csv')}")


if __name__ == "__main__":
    main()
