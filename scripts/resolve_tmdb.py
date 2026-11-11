"""Resolve every identified title against TMDB (+ IMDb ratings) -> data/resolved.json.

For each title in data/titles.json:
  * pick the TMDB entry (explicit tmdb_id wins; otherwise best search match)
  * Movie  -> runtime
  * TV     -> sum of every released episode's runtime across all seasons
              (specials excluded; gaps filled with the show's median episode
              length and flagged watch_time_estimated)
  * US subscription / free / ad-supported providers (no rent/buy)
  * IMDb id -> rating + votes from the IMDb dataset

resolved.json is fully regenerated each run; TMDB responses are cached so this
is cheap. Delete data/tmdb-cache/ to refresh availability.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import imdb  # noqa: E402
import tmdb  # noqa: E402
from extract_candidates import similarity  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TITLES_FILE = ROOT / "data" / "titles.json"
RESOLVED_FILE = ROOT / "data" / "resolved.json"
LOW_CONFIDENCE = 0.75


def pick_match(entry: dict) -> tuple[dict | None, float]:
    """Return (search result, similarity) for a titles.json entry."""
    query, year, hint = entry.get("query", ""), entry.get("year"), entry.get("media_type")
    if not query:
        return None, 0.0
    if hint == "movie":
        results = tmdb.search_movie(query, year) or tmdb.search_movie(query)
    elif hint == "tv":
        results = tmdb.search_tv(query, year) or tmdb.search_tv(query)
    else:
        results = tmdb.search_multi(query, year) or tmdb.search_multi(query)
        if not results:  # search/multi occasionally drops titles that search/movie|tv return
            results = tmdb.search_movie(query, year) + tmdb.search_tv(query, year)
    best, best_key, best_sim = None, (-1.0, -1.0), 0.0
    for r in results[:10]:
        sim = similarity(query, tmdb.title_of(r))
        orig = r.get("original_title") or r.get("original_name")
        if orig:
            sim = max(sim, similarity(query, orig))
        ryear = tmdb._year_of(r)
        if year and ryear:
            sim = min(1.0, sim + 0.1) if abs(ryear - year) <= 1 else sim - 0.15
        key = (round(sim, 3), min(r.get("vote_count", 0) or 0, 5000))
        if key > best_key:
            best, best_key, best_sim = r, key, sim
    return best, best_sim


def movie_record(movie_id: int) -> dict | None:
    d = tmdb.movie_details(movie_id)
    if not d:
        return None
    runtime = d.get("runtime") or None
    return {
        "type": "Movie",
        "title": d.get("title") or d.get("original_title"),
        "year": int(d["release_date"][:4]) if (d.get("release_date") or "")[:4].isdigit() else None,
        "total_minutes": runtime,
        "watch_time_estimated": runtime is None,
        "seasons": None,
        "episodes": None,
        "status": None,
        "imdb_id": (d.get("external_ids") or {}).get("imdb_id") or d.get("imdb_id"),
        "genres": [g["name"] for g in d.get("genres", [])],
    }


def tv_record(tv_id: int) -> dict | None:
    d = tmdb.tv_details(tv_id)
    if not d:
        return None
    today = dt.date.today().isoformat()
    known: list[int] = []
    released = 0
    seasons_counted = 0
    for s in d.get("seasons", []):
        if (s.get("season_number") or 0) < 1:
            continue  # season 0 = specials
        season = tmdb.tv_season(tv_id, s["season_number"])
        eps = [e for e in season.get("episodes", []) if (e.get("air_date") or "9999") <= today]
        if not eps:
            continue
        seasons_counted += 1
        released += len(eps)
        known.extend(e["runtime"] for e in eps if e.get("runtime"))
    missing = released - len(known)
    if known:
        fill = int(statistics.median(known))
    elif d.get("episode_run_time"):
        fill = int(d["episode_run_time"][0])
    else:
        fill = None
    total = (sum(known) + missing * fill) if (released and fill is not None) else (sum(known) or None)
    return {
        "type": "TV Show",
        "title": d.get("name") or d.get("original_name"),
        "year": int(d["first_air_date"][:4]) if (d.get("first_air_date") or "")[:4].isdigit() else None,
        "total_minutes": total,
        "watch_time_estimated": missing > 0 or total is None,
        "episodes_missing_runtime": missing,
        "seasons": seasons_counted or d.get("number_of_seasons"),
        "episodes": released or d.get("number_of_episodes"),
        "status": d.get("status"),
        "imdb_id": (d.get("external_ids") or {}).get("imdb_id"),
        "genres": [g["name"] for g in d.get("genres", [])],
    }


def tmdb_find_by_imdb(imdb_id: str) -> tuple[str, int] | None:
    """(media_type, tmdb_id) for an IMDb id TMDB knows about, else None."""
    data = tmdb.get(f"find/{imdb_id}", external_source="imdb_id")
    for kind, media_type in (("movie_results", "movie"), ("tv_results", "tv")):
        if data.get(kind):
            return media_type, data[kind][0]["id"]
    return None


def imdb_only_record(entry: dict, shot: str, today: str) -> dict:
    """Record for a title TMDB does not have (e.g. vertical short-dramas), built from the
    review answer plus IMDb's ratings dataset."""
    rating, votes = imdb.lookup(entry["imdb_id"])
    media_type = entry.get("media_type") or "movie"
    provider = entry.get("provider")
    buckets = [tmdb.normalise_provider(provider)] if provider else [tmdb.NOT_STREAMING]
    return {
        "type": "TV Show" if media_type == "tv" else "Movie",
        "title": entry.get("query"),
        "year": entry.get("year"),
        "total_minutes": entry.get("minutes"),
        "watch_time_estimated": entry.get("minutes") is None,
        "seasons": 1 if media_type == "tv" and entry.get("episodes") else None,
        "episodes": entry.get("episodes"),
        "status": None,
        "imdb_id": entry["imdb_id"],
        "genres": [],
        "tmdb_id": None,
        "media_type": media_type,
        "providers": buckets,
        "providers_raw": [provider] if provider else [],
        "imdb_rating": rating,
        "imdb_votes": votes,
        "imdb_url": imdb.imdb_url(entry["imdb_id"]),
        "tmdb_url": "",
        "sources": [shot],
        "match_similarity": 1.0,
        "query": entry.get("query"),
        "checked_on": today,
        "note": "not on TMDB - entered manually from IMDb",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--titles", type=Path, default=TITLES_FILE)
    ap.add_argument("--out", type=Path, default=RESOLVED_FILE)
    args = ap.parse_args()

    if not args.titles.exists():
        sys.exit(f"{args.titles} not found - run extract_candidates.py first.")
    titles = json.loads(args.titles.read_text())
    tmdb.load_api_key()

    today = dt.date.today().isoformat()
    resolved: dict[str, dict] = {}
    unmatched: list[dict] = []

    for shot in sorted(titles):
        if titles[shot].get("review") and not titles[shot].get("reviewed"):
            continue  # waiting for manual review (see review/review.csv)
        for entry in titles[shot].get("titles", []):
            media_type, tmdb_id = entry.get("media_type"), entry.get("tmdb_id")
            sim = 1.0
            if not (tmdb_id and media_type in ("movie", "tv")) and entry.get("imdb_id"):
                found = tmdb_find_by_imdb(entry["imdb_id"])
                if found:
                    media_type, tmdb_id = found
                else:
                    rec = imdb_only_record(entry, shot, today)
                    key = f"imdb:{entry['imdb_id']}"
                    if key in resolved:
                        if shot not in resolved[key]["sources"]:
                            resolved[key]["sources"].append(shot)
                    else:
                        print(f"  {entry.get('query')!r:40s} -> {key} (not on TMDB)", file=sys.stderr)
                        resolved[key] = rec
                    continue
            if not (tmdb_id and media_type in ("movie", "tv")):
                match, sim = pick_match(entry)
                if not match:
                    unmatched.append({"screenshot": shot, "query": entry.get("query"), "reason": "no TMDB result"})
                    continue
                media_type, tmdb_id = match["media_type"], match["id"]
            key = f"{media_type}:{tmdb_id}"
            if key in resolved:
                rec = resolved[key]
                if shot not in rec["sources"]:
                    rec["sources"].append(shot)
                rec["match_similarity"] = max(rec["match_similarity"], sim)
                continue

            print(f"  {entry.get('query') or key!r:40s} -> {key}", file=sys.stderr)
            rec = movie_record(tmdb_id) if media_type == "movie" else tv_record(tmdb_id)
            if not rec:
                unmatched.append({"screenshot": shot, "query": entry.get("query"), "reason": f"TMDB {key} not found"})
                continue
            buckets, raw = tmdb.us_streaming_providers(media_type, tmdb_id)
            rating, votes = imdb.lookup(rec["imdb_id"])
            rec.update(
                {
                    "tmdb_id": tmdb_id,
                    "media_type": media_type,
                    "providers": buckets or [tmdb.NOT_STREAMING],
                    "providers_raw": raw,
                    "imdb_rating": rating,
                    "imdb_votes": votes,
                    "imdb_url": imdb.imdb_url(rec["imdb_id"]),
                    "tmdb_url": tmdb.tmdb_url(media_type, tmdb_id),
                    "sources": [shot],
                    "match_similarity": sim,
                    "query": entry.get("query"),
                    "checked_on": today,
                }
            )
            resolved[key] = rec

    for rec in resolved.values():
        rec["low_confidence"] = rec["match_similarity"] < LOW_CONFIDENCE

    out = {"generated_on": today, "titles": resolved, "unmatched": unmatched}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")

    n_low = sum(1 for r in resolved.values() if r["low_confidence"])
    n_est = sum(1 for r in resolved.values() if r["watch_time_estimated"])
    n_noimdb = sum(1 for r in resolved.values() if r["imdb_rating"] is None)
    print(
        f"{len(resolved)} titles resolved ({n_low} low-confidence, {n_est} estimated watch time, "
        f"{n_noimdb} without IMDb rating), {len(unmatched)} unmatched -> {args.out}"
    )


if __name__ == "__main__":
    main()
