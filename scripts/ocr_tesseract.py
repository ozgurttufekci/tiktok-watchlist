"""Fallback OCR using tesseract (used by run.sh only if Apple Vision is unavailable).

Produces the same data/ocr.json format as ocr.swift.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp"}


def fingerprint(p: Path) -> str:
    st = p.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


def main() -> None:
    in_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "screenshots"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "data" / "ocr.json"
    results = json.loads(out.read_text()) if out.exists() else {}
    images = sorted(p for p in in_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    processed = skipped = failed = 0
    for p in images:
        fp = fingerprint(p)
        if results.get(p.name, {}).get("fingerprint") == fp:
            skipped += 1
            continue
        try:
            text = subprocess.run(
                ["tesseract", str(p), "-", "--psm", "6", "-l", "eng"],
                check=True, capture_output=True, text=True,
            ).stdout
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            results[p.name] = {"fingerprint": fp, "lines": lines, "text": "\n".join(lines)}
            processed += 1
            print(f"OCR  {p.name}  ({len(lines)} lines)")
        except (subprocess.CalledProcessError, FileNotFoundError) as err:
            results[p.name] = {"fingerprint": fp, "lines": [], "text": "", "error": str(err)}
            failed += 1
            print(f"FAIL {p.name}: {err}", file=sys.stderr)
    present = {p.name for p in images}
    for k in list(results):
        if k not in present:
            del results[k]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False, sort_keys=True))
    print(f"done: {processed} processed, {skipped} cached, {failed} failed -> {out}")


if __name__ == "__main__":
    main()
