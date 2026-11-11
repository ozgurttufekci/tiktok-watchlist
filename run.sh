#!/usr/bin/env bash
# Full pipeline: OCR -> candidates -> TMDB/IMDb resolve -> CSV + HTML.
#
#   ./run.sh                 run everything (OCR only touches new/changed screenshots)
#   ./run.sh --skip-ocr      reuse data/ocr.json as-is
#   ./run.sh --refresh       drop the TMDB cache first (fresh streaming availability)
#   ./run.sh --no-tmdb       OCR + candidate extraction only (no API key needed)
set -euo pipefail
cd "$(dirname "$0")"

SKIP_OCR=0; REFRESH=0; NO_TMDB=0
for arg in "$@"; do
  case "$arg" in
    --skip-ocr) SKIP_OCR=1 ;;
    --refresh)  REFRESH=1 ;;
    --no-tmdb)  NO_TMDB=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }

if [[ $SKIP_OCR -eq 0 ]]; then
  step "1/7 OCR screenshots"
  if [[ -z "$(find screenshots -maxdepth 1 -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.heic' -o -iname '*.webp' \) | head -1)" ]]; then
    echo "No screenshots found in screenshots/ - drop your TikTok screenshots there first." >&2
    exit 1
  fi
  OCR_BIN=data/ocr-bin
  if command -v swiftc >/dev/null 2>&1; then
    if [[ ! -x $OCR_BIN || scripts/ocr.swift -nt $OCR_BIN ]]; then
      echo "compiling scripts/ocr.swift ..."
      swiftc -O scripts/ocr.swift -o "$OCR_BIN" 2>/dev/null || rm -f "$OCR_BIN"
    fi
  fi
  if [[ -x $OCR_BIN ]]; then
    "$OCR_BIN" screenshots data/ocr.json
  elif command -v swift >/dev/null 2>&1 && swift scripts/ocr.swift screenshots data/ocr.json; then
    :
  elif command -v tesseract >/dev/null 2>&1; then
    echo "Apple Vision unavailable - falling back to tesseract" >&2
    python3 scripts/ocr_tesseract.py screenshots data/ocr.json
  else
    echo "No OCR engine available (need Xcode Command Line Tools or tesseract)." >&2
    exit 1
  fi
fi

step "2/7 Extract title candidates"
if [[ $NO_TMDB -eq 1 ]]; then
  python3 scripts/extract_candidates.py --no-tmdb
  echo; echo "Stopped after candidate extraction (--no-tmdb). Review data/titles.json."
  exit 0
fi
python3 scripts/extract_candidates.py

step "3/7 Apply manual review answers + triage"
python3 scripts/apply_review.py
python3 scripts/triage.py

if [[ $REFRESH -eq 1 ]]; then
  echo "clearing TMDB cache ..."
  find data/tmdb-cache -name '*.json' -delete
fi

step "4/7 Resolve on TMDB + IMDb"
python3 scripts/resolve_tmdb.py

step "5/7 Build CSVs"
python3 scripts/build_csv.py

step "6/7 Build HTML"
python3 scripts/build_html.py

step "7/7 Summary"
echo "Outputs: output/watchlist.html, output/watchlist.csv, output/titles.csv, output/unresolved.csv"
[[ -f review/review.csv ]] && echo "Pending manual review: review/review.csv (images in review/unrelated/ and review/multiple/)" || echo "Nothing pending manual review."
