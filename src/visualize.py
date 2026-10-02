"""
Chart generation for the Spotify listening analysis.

Split into genre-independent charts (run immediately after analysis.py)
and genre-dependent charts (run after fetch_genres.py + edm_analysis.py
have produced genre-tagged data). main() runs whichever inputs are
available and prints a note about anything skipped.
"""

import json
import os

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
PROCESSED_DIR = os.path.join(PROJECT_ROOT, "data", "processed")
CHARTS_DIR = os.path.join(PROJECT_ROOT, "charts")

sns.set_theme(style="whitegrid", context="talk", font_scale=0.7)
ACCENT = "#1DB954"  # Spotify green, used sparingly as a single accent
PALETTE = "viridis"


def _load_results() -> dict:
    with open(os.path.join(PROCESSED_DIR, "analysis_results.json")) as f:
        return json.load(f)


def chart_monthly_trend(results: dict):
    monthly = results["monthly_trend_hours"]
    s = pd.Series(monthly)
    s.index = pd.PeriodIndex(s.index, freq="M").to_timestamp()
    s = s.sort_index()

    fig, ax = plt.subplots(figsize=(13, 5))
    ax.plot(s.index, s.values, color=ACCENT, linewidth=2)
    ax.fill_between(s.index, s.values, color=ACCENT, alpha=0.15)
    ax.set_title("Monthly Listening Time", fontsize=16, fontweight="bold")
    ax.set_ylabel("Hours played")
    ax.set_xlabel("")
    fig.tight_layout()
    fig.savefig(os.path.join(CHARTS_DIR, "monthly_listening_trend.png"), dpi=150)
    plt.close(fig)


def chart_top_artists(results: dict):
    top = pd.DataFrame(results["top_lists"]["top_artists_lifetime"][:15])
    top = top.sort_values("hours")

    fig, ax = plt.subplots(figsize=(10, 8))
    bars = ax.barh(top["artist_name"], top["hours"], color=sns.color_palette(PALETTE, len(top)))
    ax.set_title("Top 15 Artists — Lifetime Listening Time", fontsize=16, fontweight="bold")
    ax.set_xlabel("Hours played")
    for bar, hours in zip(bars, top["hours"]):
        ax.text(bar.get_width() + 3, bar.get_y() + bar.get_height() / 2,
                 f"{hours:.0f}h", va="center", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(CHARTS_DIR, "top_artists.png"), dpi=150)
    plt.close(fig)


def chart_time_heatmap(results: dict):
    heatmap_data = pd.DataFrame(results["time_patterns"]["heatmap"])
    heatmap_data = heatmap_data.reindex(
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    )
    heatmap_data.columns = [int(c) for c in heatmap_data.columns]
    heatmap_data = heatmap_data.reindex(sorted(heatmap_data.columns), axis=1)

    fig, ax = plt.subplots(figsize=(14, 6))
    sns.heatmap(heatmap_data, cmap="mako", ax=ax, cbar_kws={"label": "Hours played"})
    ax.set_title("Listening Activity by Hour of Day and Day of Week (IST)", fontsize=16, fontweight="bold")
    ax.set_xlabel("Hour of day (local)")
    ax.set_ylabel("")
    fig.tight_layout()
    fig.savefig(os.path.join(CHARTS_DIR, "time_of_day_heatmap.png"), dpi=150)
    plt.close(fig)


def chart_discovery_vs_repeat(results: dict):
    d = results["discovery_vs_repeat"]
    years = sorted(int(y) for y in d["plays_that_are_new_artist_share_by_year"].keys())
    new_share = [d["plays_that_are_new_artist_share_by_year"][str(y)] * 100 for y in years]
    repeat_share = [100 - v for v in new_share]

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.bar(years, repeat_share, label="Repeat listens (artist heard in an earlier year)", color="#4C72B0")
    ax.bar(years, new_share, bottom=repeat_share, label="Discovery (new artist that year)", color=ACCENT)
    ax.set_title("Discovery vs. Repeat Listening, by Year", fontsize=16, fontweight="bold")
    ax.set_ylabel("Share of plays (%)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2, fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(CHARTS_DIR, "discovery_vs_repeat.png"), dpi=150)
    plt.close(fig)


def chart_platform_share(results: dict):
    share = results["platform_patterns"]["share_by_platform"]
    s = pd.Series(share).sort_values(ascending=False) * 100

    fig, ax = plt.subplots(figsize=(9, 6))
    ax.pie(s.values, labels=s.index, autopct="%1.0f%%", colors=sns.color_palette(PALETTE, len(s)),
           startangle=90, textprops={"fontsize": 10})
    ax.set_title("Listening Time by Platform", fontsize=16, fontweight="bold")
    fig.tight_layout()
    fig.savefig(os.path.join(CHARTS_DIR, "platform_share.png"), dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------
# Genre-dependent charts — require data/artist_genres.json +
# data/processed/edm_results.json (produced by fetch_genres.py and
# edm_analysis.py respectively).
# ---------------------------------------------------------------------

def chart_genre_share_over_time():
    path = os.path.join(PROCESSED_DIR, "genre_share_by_year.csv")
    df = pd.read_csv(path, index_col=0)

    fig, ax = plt.subplots(figsize=(12, 6))
    df.plot.area(ax=ax, cmap="tab20", linewidth=0)
    ax.set_title("Genre Share of Listening Time, by Year", fontsize=16, fontweight="bold")
    ax.set_ylabel("Share of listening hours (%)")
    ax.set_xlabel("")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=4, fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(CHARTS_DIR, "genre_share_over_time.png"), dpi=150)
    plt.close(fig)


def chart_edm_share_trend():
    path = os.path.join(PROCESSED_DIR, "edm_results.json")
    with open(path) as f:
        edm = json.load(f)

    s = pd.Series(edm["edm_share_by_year"]).sort_index() * 100

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(s.index.astype(str), s.values, marker="o", color="#8B5CF6", linewidth=2.5, markersize=8)
    ax.fill_between(range(len(s)), s.values, color="#8B5CF6", alpha=0.15)
    ax.set_title("EDM Share of Total Listening Time, by Year", fontsize=16, fontweight="bold")
    ax.set_ylabel("Share of listening hours (%)")
    for i, v in enumerate(s.values):
        ax.text(i, v + 0.5, f"{v:.1f}%", ha="center", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(CHARTS_DIR, "edm_share_trend.png"), dpi=150)
    plt.close(fig)


def chart_top_edm_artists():
    path = os.path.join(PROCESSED_DIR, "edm_results.json")
    with open(path) as f:
        edm = json.load(f)

    top = pd.DataFrame(edm["top_edm_artists"][:12]).sort_values("hours")

    fig, ax = plt.subplots(figsize=(10, 7))
    bars = ax.barh(top["artist_name"], top["hours"], color=sns.color_palette("plasma", len(top)))
    ax.set_title("Top EDM Artists — Lifetime Listening Time", fontsize=16, fontweight="bold")
    ax.set_xlabel("Hours played")
    for bar, hours in zip(bars, top["hours"]):
        ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
                 f"{hours:.0f}h", va="center", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(CHARTS_DIR, "top_edm_artists.png"), dpi=150)
    plt.close(fig)


def main():
    os.makedirs(CHARTS_DIR, exist_ok=True)
    results = _load_results()

    print("Generating genre-independent charts...")
    chart_monthly_trend(results)
    chart_top_artists(results)
    chart_time_heatmap(results)
    chart_discovery_vs_repeat(results)
    chart_platform_share(results)
    print("  Saved: monthly_listening_trend.png, top_artists.png, time_of_day_heatmap.png, "
          "discovery_vs_repeat.png, platform_share.png")

    genre_share_path = os.path.join(PROCESSED_DIR, "genre_share_by_year.csv")
    edm_results_path = os.path.join(PROCESSED_DIR, "edm_results.json")
    if os.path.exists(genre_share_path) and os.path.exists(edm_results_path):
        print("Generating genre-dependent charts...")
        chart_genre_share_over_time()
        chart_edm_share_trend()
        chart_top_edm_artists()
        print("  Saved: genre_share_over_time.png, edm_share_trend.png, top_edm_artists.png")
    else:
        print("Skipping genre-dependent charts — run fetch_genres.py and edm_analysis.py first "
              "(needs Spotify API credentials, see README.md).")


if __name__ == "__main__":
    main()
