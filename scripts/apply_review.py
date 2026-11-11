"""Apply manual-review answers to data/titles.json.

Sources, in order (later wins):
  data/agent_answers.json   {"IMG_0001.PNG": "Last Vegas (2013)", ...} - decisions the
                            assistant made from the OCR text
  review/review.csv         the user's `answer` column

Answer syntax (see triage.py): "skip", "all", or titles separated by ";"
such as "Last Vegas (2013); Fargo (tv); tmdb:movie:158011".
Rows with an empty answer are left pending.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TITLES_FILE = ROOT / "data" / "titles.json"
REVIEW_CSV = ROOT / "review" / "review.csv"
AGENT_ANSWERS = ROOT / "data" / "agent_answers.json"

PAREN_RE = re.compile(r"\s*\(([^)]*)\)\s*$")


def parse_title(spec: str) -> dict | None:
    """'Last Vegas (2013)' / 'Fargo (tv)' / 'tmdb:movie:158011' /
    'Lost & Found (tv, 2026, imdb:tt40062294, 49eps, 70min, on:NetShort)' -> title dict."""
    spec = spec.strip().strip('"').strip()
    if not spec:
        return None
    m = re.match(r"^tmdb:(movie|tv):(\d+)$", spec, re.I)
    if m:
        return {"query": None, "media_type": m.group(1).lower(), "tmdb_id": int(m.group(2)), "note": "manual review"}
    title: dict = {"query": spec, "year": None, "media_type": None, "note": "manual review"}
    while (m := PAREN_RE.search(title["query"])):
        for part in m.group(1).split(","):
            part = part.strip()
            low = part.lower()
            if re.fullmatch(r"(19|20)\d{2}", low):
                title["year"] = int(low)
            elif low in ("tv", "show", "series"):
                title["media_type"] = "tv"
            elif low in ("movie", "film"):
                title["media_type"] = "movie"
            elif re.fullmatch(r"imdb:tt\d+", low):
                title["imdb_id"] = low.split(":", 1)[1]
            elif re.fullmatch(r"\d+\s*eps?", low):
                title["episodes"] = int(re.match(r"\d+", low).group())
            elif re.fullmatch(r"\d+\s*min", low):
                title["minutes"] = int(re.match(r"\d+", low).group())
            elif low.startswith("on:"):
                title["provider"] = part[3:].strip()
            else:
                break
        else:
            title["query"] = title["query"][: m.start()].strip()
            continue
        break
    return title


def apply_answer(entry: dict, answer: str, who: str) -> bool:
    # "? Title A; Title B" = a suggestion only: pre-fill titles but keep the screenshot pending.
    if answer.startswith("?"):
        parsed = [t for t in (parse_title(s) for s in answer[1:].split(";")) if t]
        entry["titles"] = parsed  # bare "?" clears the automatic guess
        entry["reviewed"] = False
        entry["agent_note"] = answer[1:].strip() or "needs a human look"
        return True
    low = answer.lower()
    if low in ("skip", "none", "unrelated", "no", "x", "-"):
        entry["titles"] = []
    elif low == "all":
        pass  # keep every suggested title
    else:
        parsed = [t for t in (parse_title(s) for s in answer.split(";")) if t]
        if not parsed:
            return False
        entry["titles"] = parsed
    entry["reviewed"] = True
    entry["review_answer"] = answer
    entry["reviewed_by"] = who
    entry.pop("review", None)
    entry.pop("agent_note", None)
    return True


def main() -> None:
    titles = json.loads(TITLES_FILE.read_text())
    applied = skipped = 0

    # 1. Decisions made by the assistant while reading OCR text (reproducible on reruns).
    if AGENT_ANSWERS.exists():
        for name, answer in json.loads(AGENT_ANSWERS.read_text()).items():
            if name in titles and answer.strip() and apply_answer(titles[name], answer.strip(), "agent"):
                applied += 1
                skipped += titles[name]["titles"] == []

    # 2. The user's answers in review/review.csv override everything.
    if REVIEW_CSV.exists():
        with REVIEW_CSV.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                name, answer = row["screenshot"], row.get("answer", "").strip()
                if not answer or name not in titles:
                    continue
                if apply_answer(titles[name], answer, "user"):
                    applied += 1
                    skipped += titles[name]["titles"] == []
                else:
                    print(f"warning: could not parse answer for {name}: {answer!r}", file=sys.stderr)

    TITLES_FILE.write_text(json.dumps(titles, indent=2, ensure_ascii=False) + "\n")
    print(f"applied {applied} answers ({skipped} marked unrelated)")


if __name__ == "__main__":
    main()
