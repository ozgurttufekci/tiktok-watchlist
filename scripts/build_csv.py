"""Build the CSV deliverables from data/resolved.json (+ data/titles.json).

  output/watchlist.csv   one row per (provider, title); grouped by provider in a
                         fixed order, shortest total watch time first
  output/titles.csv      one row per title with all providers joined
  output/unresolved.csv  screenshots that yielded no title, and queries TMDB
                         could not match
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tmdb  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RESOLVED_FILE = ROOT / "data" / "resolved.json"
TITLES_FILE = ROOT / "data" / "titles.json"
OUTPUT_DIR = ROOT / "output"

GROUP_ORDER = tmdb.PROVIDER_ORDER + [tmdb.NOT_STREAMING]

WATCHLIST_COLUMNS = [
    "provider", "title", "type", "year", "imdb_rating", "imdb_votes", "total_watch_time",
    "total_minutes", "seasons", "episodes", "status", "watch_time_estimated", "imdb_url",
    "tmdb_url", "source_screenshots", "availability_checked_on",
]
TITLES_COLUMNS = [
    "title", "type", "year", "imdb_rating", "imdb_votes", "total_watch_time", "total_minutes",
    "seasons", "episodes", "status", "providers", "providers_raw", "watch_time_estimated",
    "low_confidence", "match_similarity", "imdb_url", "tmdb_url", "source_screenshots",
    "availability_checked_on",
]


def fmt_minutes(total: int | None) -> str:
    if total is None:
        return ""
    h, m = divmod(int(total), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def sort_key(rec: dict) -> tuple:
    # Unknown runtimes go last; ties broken by title.
    t = rec.get("total_minutes")
    return (t is None, t if t is not None else 0, (rec.get("title") or "").lower())


def base_row(rec: dict) -> dict:
    return {
        "title": rec.get("title", ""),
        "type": rec.get("type", ""),
        "year": rec.get("year") or "",
        "imdb_rating": "" if rec.get("imdb_rating") is None else rec["imdb_rating"],
        "imdb_votes": "" if rec.get("imdb_votes") is None else rec["imdb_votes"],
        "total_watch_time": fmt_minutes(rec.get("total_minutes")),
        "total_minutes": "" if rec.get("total_minutes") is None else rec["total_minutes"],
        "seasons": rec.get("seasons") or "",
        "episodes": rec.get("episodes") or "",
        "status": rec.get("status") or "",
        "watch_time_estimated": "yes" if rec.get("watch_time_estimated") else "",
        "imdb_url": rec.get("imdb_url", ""),
        "tmdb_url": rec.get("tmdb_url", ""),
        "source_screenshots": "; ".join(rec.get("sources", [])),
        "availability_checked_on": rec.get("checked_on", ""),
    }


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolved", type=Path, default=RESOLVED_FILE)
    ap.add_argument("--titles", type=Path, default=TITLES_FILE)
    ap.add_argument("--out-dir", type=Path, default=OUTPUT_DIR)
    args = ap.parse_args()

    if not args.resolved.exists():
        sys.exit(f"{args.resolved} not found - run resolve_tmdb.py first.")
    data = json.loads(args.resolved.read_text())
    records = list(data.get("titles", {}).values())
    titles_json = json.loads(args.titles.read_text()) if args.titles.exists() else {}

    # watchlist.csv: grouped by provider, shortest first within each group.
    watch_rows: list[dict] = []
    for provider in GROUP_ORDER:
        group = [r for r in records if provider in r.get("providers", [])]
        for rec in sorted(group, key=sort_key):
            watch_rows.append(dict(base_row(rec), provider=provider))
    write_csv(args.out_dir / "watchlist.csv", WATCHLIST_COLUMNS, watch_rows)

    # titles.csv: master list, one row per title.
    title_rows = []
    for rec in sorted(records, key=lambda r: (r.get("title") or "").lower()):
        row = base_row(rec)
        row.update(
            providers="; ".join(rec.get("providers", [])),
            providers_raw="; ".join(rec.get("providers_raw", [])),
            low_confidence="yes" if rec.get("low_confidence") else "",
            match_similarity=rec.get("match_similarity", ""),
        )
        title_rows.append(row)
    write_csv(args.out_dir / "titles.csv", TITLES_COLUMNS, title_rows)

    # unresolved.csv
    unresolved = []
    for shot in sorted(titles_json):
        e = titles_json[shot]
        if e.get("review") and not e.get("reviewed"):
            unresolved.append({"screenshot": shot, "query": "", "reason": f"pending manual review (review/{e['review']}/)", "ocr_excerpt": e.get("ocr_excerpt", "")})
        elif not e.get("titles"):
            reason = "marked unrelated in review" if e.get("reviewed") else "no title identified"
            unresolved.append({"screenshot": shot, "query": "", "reason": reason, "ocr_excerpt": e.get("ocr_excerpt", "")})
    for u in data.get("unmatched", []):
        unresolved.append({"screenshot": u.get("screenshot", ""), "query": u.get("query", ""), "reason": u.get("reason", ""), "ocr_excerpt": titles_json.get(u.get("screenshot", ""), {}).get("ocr_excerpt", "")})
    write_csv(args.out_dir / "unresolved.csv", ["screenshot", "query", "reason", "ocr_excerpt"], unresolved)

    counts = {p: sum(1 for r in records if p in r.get("providers", [])) for p in GROUP_ORDER}
    summary = ", ".join(f"{p}: {n}" for p, n in counts.items() if n)
    print(f"{len(records)} titles, {len(watch_rows)} watchlist rows ({summary}); {len(unresolved)} unresolved -> {args.out_dir}")


if __name__ == "__main__":
    main()
