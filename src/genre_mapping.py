"""
Maps Spotify's raw, highly granular artist genre tags (e.g. "future
house", "brostep", "pop rap", "corridos") into two levels used by the
rest of the analysis:

1. TOP-LEVEL BUCKETS — a small set of broad categories (EDM/Dance,
   Hip-Hop/Rap, Pop, Rock/Alternative, R&B/Soul, Indie, Other) used for
   the general "genre share over time" chart. Spotify's raw genre list
   for any real listening history runs into the hundreds of distinct
   micro-genre tags, which isn't chartable directly — the buckets below
   collapse them into something a stacked-area chart can show.

   Each artist maps to exactly ONE top-level bucket, even if they have
   multiple raw genre tags, to keep the chart's shares summing to 100%
   without double-counting. The bucket is chosen by checking the
   artist's tags against BUCKET_KEYWORDS in priority order (EDM checked
   first) and taking the first bucket with any match. This is a
   simplification — an artist tagged both "pop" and "edm" lands in
   EDM/Dance, not split between the two — documented as a limitation in
   REPORT.md.

2. EDM SUB-GENRES — a finer breakdown used only within the EDM deep
   dive (house, techno, dubstep, trance, drum & bass, etc.), via
   substring match against EDM_SUBGENRE_KEYWORDS. An artist can count
   toward multiple sub-genres here (e.g. an artist tagged both "house"
   and "big room" contributes to both) since this section isn't a
   100%-stacked chart, just "which EDM sub-genres show up and how much."

This taxonomy was built from general knowledge of Spotify's genre
vocabulary, then checked against the actual genre tags returned for
this dataset's artists (see the "unmapped genres" printout from
`python src/genre_mapping.py --inspect`) and adjusted where a real,
frequently-played artist was landing in the wrong bucket.
"""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
ARTIST_GENRES_PATH = os.path.join(DATA_DIR, "artist_genres.json")

# Order matters: checked top to bottom, first match wins.
BUCKET_KEYWORDS = [
    ("EDM / Dance", [
        "edm", "house", "techno", "trance", "dubstep", "drum and bass", "dnb",
        "hardstyle", "big room", "electro house", "electro pop house",
        "future bass", "moombahton", "garage", "jungle", "breakbeat",
        "electronica", "electro", "dance pop", "pop dance", "eurodance",
        "complextro", "drumstep", "riddim", "hardcore techno", "gabber",
        "uk dance", "bounce", "dance", "brostep", "melbourne bounce",
    ]),
    ("Hip-Hop / Rap", [
        "hip hop", "rap", "trap", "grime", "drill",
    ]),
    ("R&B / Soul", [
        "r&b", "rnb", "soul", "neo soul", "funk", "quiet storm",
    ]),
    ("Rock / Alternative", [
        "rock", "metal", "punk", "grunge", "emo", "post-hardcore",
    ]),
    ("Indie / Alternative Pop", [
        "indie", "alt z", "bedroom pop", "alternative", "singer-songwriter",
    ]),
    ("Pop", [
        "pop", "boy band", "girl group",
    ]),
    ("Classical / Instrumental", [
        "classical", "orchestra", "instrumental", "soundtrack", "score",
        "piano", "lo-fi", "lofi",
    ]),
    ("World / Regional", [
        "bollywood", "desi", "punjabi", "hindi", "k-pop", "kpop", "latin",
        "reggaeton", "afrobeat", "afrobeats", "arab", "filmi",
    ]),
    ("Country / Folk", [
        "country", "folk", "bluegrass", "americana",
    ]),
]

EDM_SUBGENRE_KEYWORDS = [
    ("House", ["house"]),
    ("Techno", ["techno"]),
    ("Trance", ["trance"]),
    ("Dubstep / Bass", ["dubstep", "brostep", "riddim", "bass music", "future bass"]),
    ("Drum & Bass / Jungle", ["drum and bass", "dnb", "jungle", "breakbeat", "drumstep"]),
    ("Hardstyle / Hardcore", ["hardstyle", "hardcore techno", "gabber"]),
    ("Big Room / Festival EDM", ["big room", "edm", "electro house"]),
    ("Moombahton / Global Bass", ["moombahton"]),
    ("Other Electronic / Dance", ["electronica", "electro", "dance", "eurodance", "complextro"]),
]


def classify_bucket(genres: list[str]) -> str:
    """Assigns a bucket by PLURALITY VOTE across all of an artist's raw
    genre/tag strings, not by "does any single tag match" — needed
    because MusicBrainz's tag data is a community folksonomy, not a
    curated genre list. A single stray or minority tag (e.g. Kanye West's
    tag list includes a couple of "house" / "future bass" tags alongside
    10+ hip-hop/rap tags, almost certainly from a one-off collab or
    mistagging) would otherwise hijack the whole classification under a
    simple first-match rule. Ties are broken by BUCKET_KEYWORDS' priority
    order (EDM first), matching the original tie-break behavior."""
    if not genres:
        return "Unknown / No Genre Data"
    lowered = [g.lower() for g in genres]

    votes = {bucket: 0 for bucket, _ in BUCKET_KEYWORDS}
    for g in lowered:
        for bucket, keywords in BUCKET_KEYWORDS:
            if any(kw in g for kw in keywords):
                votes[bucket] += 1
                break  # each tag counts toward its first-matching bucket only

    best_bucket, best_votes = max(votes.items(), key=lambda kv: kv[1])
    if best_votes == 0:
        return "Other"
    return best_bucket


def is_edm(genres: list[str]) -> bool:
    return classify_bucket(genres) == "EDM / Dance"


def edm_subgenres(genres: list[str]) -> list[str]:
    lowered = [g.lower() for g in genres]
    matches = []
    for subgenre, keywords in EDM_SUBGENRE_KEYWORDS:
        if any(kw in g for kw in keywords for g in lowered):
            matches.append(subgenre)
    return matches


def load_artist_genre_lookup() -> dict:
    """artist_name -> list[str] of raw Spotify genre tags."""
    with open(ARTIST_GENRES_PATH) as f:
        cache = json.load(f)
    return {name: v["genres"] for name, v in cache.items()}


if __name__ == "__main__":
    import sys

    lookup = load_artist_genre_lookup()
    all_raw_genres = sorted({g for genres in lookup.values() for g in genres})
    print(f"{len(lookup)} artists, {len(all_raw_genres)} distinct raw genre tags\n")

    bucket_counts = {}
    unmapped_examples = {}
    for name, genres in lookup.items():
        bucket = classify_bucket(genres)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        if bucket in ("Other", "Unknown / No Genre Data") and genres:
            unmapped_examples.setdefault(tuple(genres), []).append(name)

    print("Artist counts per bucket:")
    for bucket, count in sorted(bucket_counts.items(), key=lambda x: -x[1]):
        print(f"  {bucket}: {count}")

    if "--inspect" in sys.argv:
        print("\nArtists landing in 'Other' with their raw tags (review for taxonomy gaps):")
        for genres, names in sorted(unmapped_examples.items(), key=lambda x: -len(x[1]))[:40]:
            print(f"  {genres} -> {names[:5]}")
