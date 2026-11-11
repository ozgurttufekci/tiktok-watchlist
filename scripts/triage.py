"""Sort screenshots into review buckets and build the manual-review sheet.

For every screenshot that is not yet "reviewed":
  0 identified titles  -> review/unrelated/   (copy of the image)
  2+ identified titles -> review/multiple/    (copy of the image)
  exactly 1            -> proceeds through the pipeline as-is

review/review.csv lists each pending screenshot with the titles/candidates
found so far and an empty `answer` column. Fill it in and run
`python3 scripts/apply_review.py` (or `./run.sh`, which does it for you).

Answer syntax (one cell, several titles separated by ";"):
  skip                           not a movie/show, drop it
  all                            every suggested title is correct
  Last Vegas (2013)              one title; year optional
  Fargo (tv); Fargo (1996)       "(tv)" / "(movie)" force the type; a year pins the release
  tmdb:movie:158011              pin an exact TMDB entry
  Title (tv, 2026, imdb:tt123, 49eps, 70min, on:NetShort)   not on TMDB: rating from IMDb, rest from tokens
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCREENSHOTS = ROOT / "screenshots"
TITLES_FILE = ROOT / "data" / "titles.json"
REVIEW_DIR = ROOT / "review"
REVIEW_CSV = REVIEW_DIR / "review.csv"
BUCKETS = ("unrelated", "multiple")
CANDIDATES_SHOWN = 6


def describe_title(t: dict) -> str:
    kind = {"movie": "movie", "tv": "tv"}.get(t.get("media_type") or "", "?")
    year = f", {t['year']}" if t.get("year") else ""
    return f"{t['query']} ({kind}{year})"


def describe_candidate(c: dict) -> str:
    sug = c.get("tmdb")
    if sug:
        year = f", {sug['year']}" if sug.get("year") else ""
        return f"{sug['title']} ({sug['media_type']}{year}) [{c['score']:.2f}]"
    return f"{c['text']} [no TMDB match]"


def main() -> None:
    if not TITLES_FILE.exists():
        sys.exit(f"{TITLES_FILE} not found - run extract_candidates.py first.")
    titles = json.loads(TITLES_FILE.read_text())
    existing_answers: dict[str, str] = {}
    if REVIEW_CSV.exists():
        with REVIEW_CSV.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("answer", "").strip():
                    existing_answers[row["screenshot"]] = row["answer"].strip()

    rows: list[dict] = []
    counts = {"unrelated": 0, "multiple": 0, "clear": 0, "reviewed": 0}
    wanted: dict[str, set[str]] = {b: set() for b in BUCKETS}

    for name in sorted(titles):
        entry = titles[name]
        if entry.get("reviewed"):
            entry.pop("review", None)
            counts["reviewed"] += 1
            continue
        n = len(entry.get("titles", []))
        bucket = "unrelated" if n == 0 else "multiple" if n >= 2 else None
        if bucket is None and entry.get("agent_note"):
            bucket = "multiple"  # the assistant was unsure even though only one title is suggested
        entry["review"] = bucket
        if bucket is None:
            counts["clear"] += 1
            continue
        counts[bucket] += 1
        wanted[bucket].add(name)
        src = SCREENSHOTS / name
        dst = REVIEW_DIR / bucket / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists() and (not dst.exists() or dst.stat().st_size != src.stat().st_size):
            shutil.copy2(src, dst)
        suggested = " | ".join(describe_title(t) for t in entry.get("titles", []))
        cands = " | ".join(describe_candidate(c) for c in entry.get("candidates", [])[:CANDIDATES_SHOWN])
        rows.append(
            {
                "screenshot": name,
                "folder": f"review/{bucket}",
                "suggested_titles": suggested,
                "assistant_note": entry.get("agent_note", ""),
                "other_candidates": cands,
                "ocr_excerpt": entry.get("ocr_excerpt", ""),
                "answer": existing_answers.get(name, ""),
            }
        )

    TITLES_FILE.write_text(json.dumps(titles, indent=2, ensure_ascii=False) + "\n")

    # Remove copies of screenshots that no longer need review, and the whole review/
    # folder once nothing is pending - it only exists while there is work for a human.
    for bucket in BUCKETS:
        d = REVIEW_DIR / bucket
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.is_file() and (f.name.startswith(".") or f.name not in wanted[bucket]):
                f.unlink()
        if not any(d.iterdir()):
            d.rmdir()
    if not rows:
        REVIEW_CSV.unlink(missing_ok=True)
        if REVIEW_DIR.exists() and not any(p for p in REVIEW_DIR.iterdir() if not p.name.startswith(".")):
            shutil.rmtree(REVIEW_DIR)
        print(f"clear: {counts['clear']}, reviewed: {counts['reviewed']}, nothing needs review")
        return

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    with REVIEW_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["screenshot", "folder", "suggested_titles", "assistant_note", "other_candidates", "ocr_excerpt", "answer"])
        w.writeheader()
        w.writerows(rows)

    print(
        f"clear: {counts['clear']}, reviewed: {counts['reviewed']}, "
        f"needs review -> unrelated: {counts['unrelated']}, multiple: {counts['multiple']}  ({REVIEW_CSV.relative_to(ROOT)})"
    )


if __name__ == "__main__":
    main()
