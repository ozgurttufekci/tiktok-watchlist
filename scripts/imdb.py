"""IMDb ratings lookup backed by IMDb's official free dataset.

Downloads https://datasets.imdbws.com/title.ratings.tsv.gz (~7 MB) once into
data/imdb/ and refreshes it when it is older than MAX_AGE_DAYS. No API key.

Information courtesy of IMDb (https://www.imdb.com). Used with permission.
"""

from __future__ import annotations

import gzip
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMDB_DIR = ROOT / "data" / "imdb"
RATINGS_FILE = IMDB_DIR / "title.ratings.tsv.gz"
RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
MAX_AGE_DAYS = 7

_RATINGS: dict[str, tuple[float, int]] | None = None


def ensure_dataset(force: bool = False) -> Path:
    """Download the ratings dataset if missing or stale; return its path."""
    IMDB_DIR.mkdir(parents=True, exist_ok=True)
    stale = (
        force
        or not RATINGS_FILE.exists()
        or time.time() - RATINGS_FILE.stat().st_mtime > MAX_AGE_DAYS * 86400
    )
    if stale:
        print(f"Downloading IMDb ratings dataset -> {RATINGS_FILE.relative_to(ROOT)} ...", file=sys.stderr)
        tmp = RATINGS_FILE.with_suffix(".gz.part")
        req = urllib.request.Request(RATINGS_URL, headers={"User-Agent": "tiktok-watchlist/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp, tmp.open("wb") as out:
            while chunk := resp.read(1 << 16):
                out.write(chunk)
        tmp.replace(RATINGS_FILE)
    return RATINGS_FILE


def load_ratings() -> dict[str, tuple[float, int]]:
    """Return {tconst: (averageRating, numVotes)} for the whole dataset (cached in memory)."""
    global _RATINGS
    if _RATINGS is None:
        path = ensure_dataset()
        ratings: dict[str, tuple[float, int]] = {}
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            next(fh)  # header: tconst\taverageRating\tnumVotes
            for line in fh:
                tconst, rating, votes = line.rstrip("\n").split("\t")
                ratings[tconst] = (float(rating), int(votes))
        _RATINGS = ratings
    return _RATINGS


def lookup(imdb_id: str | None) -> tuple[float | None, int | None]:
    """(rating, votes) for an IMDb id like 'tt1204975'; (None, None) if unknown."""
    if not imdb_id:
        return None, None
    return load_ratings().get(imdb_id, (None, None))


def imdb_url(imdb_id: str | None) -> str:
    return f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else ""


if __name__ == "__main__":
    # Smoke test: python3 scripts/imdb.py tt1204975
    ids = sys.argv[1:] or ["tt1204975"]
    for i in ids:
        rating, votes = lookup(i)
        print(i, rating, votes, imdb_url(i))
