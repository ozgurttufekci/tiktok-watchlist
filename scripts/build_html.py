"""Build output/watchlist.html - a self-contained, sortable/filterable view.

The data is embedded as JSON and rendered with ~60 lines of vanilla JS, so the
file works offline from file:// with no server, framework or network access.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tmdb  # noqa: E402
from build_csv import GROUP_ORDER, fmt_minutes  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RESOLVED_FILE = ROOT / "data" / "resolved.json"
OUTPUT_FILE = ROOT / "output" / "watchlist.html"

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TikTok watchlist</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 20px; max-width: 1400px; margin-inline: auto; }
  h1 { font-size: 20px; margin: 0 0 12px; }
  .controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 12px; }
  .controls input, .controls select { font: inherit; padding: 6px 8px; border: 1px solid #8884; border-radius: 6px; background: transparent; color: inherit; }
  .controls input { min-width: 240px; }
  .controls label.toggle input { min-width: 0; width: 16px; height: 16px; }
  .count { opacity: .7; margin-left: auto; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 7px 10px; text-align: left; border-bottom: 1px solid #8883; vertical-align: top; }
  td:nth-child(3) { white-space: nowrap; }
  th { position: sticky; top: 0; background: Canvas; cursor: pointer; user-select: none; white-space: nowrap; }
  th:hover { text-decoration: underline; }
  th.active::after { content: attr(data-arrow); margin-left: 4px; opacity: .7; }
  tbody tr:nth-child(even) { background: #8881; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .chip { display: inline-block; padding: 1px 7px; margin: 1px 3px 1px 0; border-radius: 10px; font-size: 12px; background: #8883; white-space: nowrap; }
  .chip.none { background: transparent; border: 1px dashed #8886; opacity: .7; }
  .est { opacity: .6; font-size: 11px; }
  a { color: inherit; }
  footer { margin-top: 20px; font-size: 12px; opacity: .6; }
  td.check { text-align: center; width: 28px; }
  td.check input { width: 16px; height: 16px; cursor: pointer; }
  tr.watched td { opacity: .45; }
  tr.watched td:first-child + td a { text-decoration: line-through; }
  label.toggle { display: inline-flex; align-items: center; gap: 5px; cursor: pointer; user-select: none; }
</style>
</head>
<body>
<h1>TikTok watchlist <span class="est">(data checked __DATE__)</span></h1>
<div class="controls">
  <input id="q" type="search" placeholder="Search titles…">
  <select id="provider"><option value="">All providers</option>__PROVIDER_OPTIONS__</select>
  <select id="type"><option value="">Movies &amp; TV</option><option value="Movie">Movies only</option><option value="TV Show">TV shows only</option></select>
  <label class="toggle"><input id="hide" type="checkbox"> Hide watched</label>
  <span class="count" id="count"></span>
</div>
<table>
  <thead><tr>
    <th title="Tick what you've watched (saved in this browser)">✓</th>
    <th data-key="title" data-type="str">Title</th>
    <th data-key="type" data-type="str">Type</th>
    <th data-key="year" data-type="num">Year</th>
    <th data-key="imdb_rating" data-type="num">IMDb</th>
    <th data-key="imdb_votes" data-type="num">Votes</th>
    <th data-key="total_minutes" data-type="num">Watch time</th>
    <th data-key="seasons" data-type="num">Seasons</th>
    <th data-key="episodes" data-type="num">Episodes</th>
    <th data-key="status" data-type="str">Status</th>
    <th data-key="providers_str" data-type="str">Where to watch</th>
  </tr></thead>
  <tbody id="rows"></tbody>
</table>
<footer>
  Click a column header to sort (click again to flip). Blank values always sort last.
  Ticks are saved in this browser only (localStorage) and survive regenerating this file.<br>
  This product uses the TMDB API but is not endorsed or certified by TMDB. Streaming availability data provided by JustWatch (US).
  Ratings: information courtesy of IMDb (https://www.imdb.com). Used with permission.
</footer>
<script id="data" type="application/json">__DATA__</script>
<script>
(() => {
  const data = JSON.parse(document.getElementById('data').textContent);
  data.forEach(r => { r.providers_str = r.providers.join(', '); });
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let sortKey = 'total_minutes', sortDir = 1, sortType = 'num';

  // Watched ticks: a set of stable ids (tmdb/imdb) kept in localStorage.
  const STORE = 'tiktok-watchlist:watched';
  let watched = new Set();
  try { watched = new Set(JSON.parse(localStorage.getItem(STORE) || '[]')); } catch (e) {}
  const saveWatched = () => { try { localStorage.setItem(STORE, JSON.stringify([...watched])); } catch (e) {} };

  const blank = v => v === null || v === undefined || v === '';
  function cmp(a, b) {
    const x = a[sortKey], y = b[sortKey];
    if (blank(x) && blank(y)) return 0;
    if (blank(x)) return 1;          // blanks last in either direction
    if (blank(y)) return -1;
    const r = sortType === 'num' ? x - y : String(x).localeCompare(String(y), undefined, {sensitivity: 'base'});
    return r * sortDir || String(a.title).localeCompare(String(b.title));
  }

  function render() {
    const q = $('q').value.trim().toLowerCase(), prov = $('provider').value, type = $('type').value, hide = $('hide').checked;
    const rows = data.filter(r =>
      (!q || r.title.toLowerCase().includes(q)) &&
      (!prov || r.providers.includes(prov)) &&
      (!type || r.type === type) &&
      (!hide || !watched.has(r.key))).sort(cmp);
    $('rows').innerHTML = rows.map(r => `<tr class="${watched.has(r.key) ? 'watched' : ''}">
      <td class="check"><input type="checkbox" data-key="${esc(r.key)}"${watched.has(r.key) ? ' checked' : ''} title="watched"></td>
      <td><a href="${esc(r.tmdb_url || r.imdb_url)}" target="_blank" rel="noopener">${esc(r.title)}</a></td>
      <td>${esc(r.type)}</td>
      <td class="num">${esc(r.year)}</td>
      <td class="num">${blank(r.imdb_rating) ? '' : `<a href="${esc(r.imdb_url)}" target="_blank" rel="noopener">${r.imdb_rating.toFixed(1)}</a>`}</td>
      <td class="num">${blank(r.imdb_votes) ? '' : r.imdb_votes.toLocaleString()}</td>
      <td class="num">${esc(r.total_watch_time)}${r.watch_time_estimated && !blank(r.total_minutes) ? ' <span class="est" title="some episode runtimes were estimated">≈</span>' : ''}</td>
      <td class="num">${esc(r.seasons)}</td>
      <td class="num">${esc(r.episodes)}</td>
      <td>${esc(r.status)}</td>
      <td>${r.providers.map(p => `<span class="chip${p === '__NOT_STREAMING__' ? ' none' : ''}">${esc(p)}</span>`).join('')}</td>
    </tr>`).join('');
    const n = data.filter(r => watched.has(r.key)).length;
    $('count').textContent = `${rows.length} of ${data.length} titles` + (n ? ` · ${n} watched` : '');
    document.querySelectorAll('th[data-key]').forEach(th => {
      th.classList.toggle('active', th.dataset.key === sortKey);
      th.dataset.arrow = sortDir === 1 ? '▲' : '▼';
    });
  }

  document.querySelectorAll('th[data-key]').forEach(th => th.addEventListener('click', () => {
    if (sortKey === th.dataset.key) sortDir = -sortDir;
    else { sortKey = th.dataset.key; sortType = th.dataset.type; sortDir = th.dataset.key === 'imdb_rating' || th.dataset.key === 'imdb_votes' ? -1 : 1; }
    render();
  }));
  ['q', 'provider', 'type', 'hide'].forEach(id => $(id).addEventListener('input', render));
  $('rows').addEventListener('change', e => {
    const box = e.target.closest('input[type=checkbox]');
    if (!box) return;
    box.checked ? watched.add(box.dataset.key) : watched.delete(box.dataset.key);
    saveWatched();
    render();
  });
  render();
})();
</script>
</body>
</html>
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolved", type=Path, default=RESOLVED_FILE)
    ap.add_argument("--out", type=Path, default=OUTPUT_FILE)
    args = ap.parse_args()

    if not args.resolved.exists():
        sys.exit(f"{args.resolved} not found - run resolve_tmdb.py first.")
    data = json.loads(args.resolved.read_text())

    rows = []
    for key, r in data.get("titles", {}).items():
        rows.append(
            {
                "key": key,  # stable id (movie:158011 / tv:1396 / imdb:tt...) for the watched ticks
                "title": r.get("title") or "",
                "type": r.get("type") or "",
                "year": r.get("year"),
                "imdb_rating": r.get("imdb_rating"),
                "imdb_votes": r.get("imdb_votes"),
                "total_minutes": r.get("total_minutes"),
                "total_watch_time": fmt_minutes(r.get("total_minutes")),
                "watch_time_estimated": bool(r.get("watch_time_estimated")),
                "seasons": r.get("seasons"),
                "episodes": r.get("episodes"),
                "status": r.get("status") or "",
                "providers": r.get("providers") or [],
                "imdb_url": r.get("imdb_url") or "",
                "tmdb_url": r.get("tmdb_url") or "",
            }
        )

    counts = {p: sum(1 for r in rows if p in r["providers"]) for p in GROUP_ORDER}
    options = "".join(
        f'<option value="{html.escape(p)}">{html.escape(p)} ({n})</option>' for p, n in counts.items() if n
    )
    # "</" inside JSON must not terminate the <script> block.
    payload = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")

    page = (
        TEMPLATE.replace("__DATE__", html.escape(data.get("generated_on", "")))
        .replace("__PROVIDER_OPTIONS__", options)
        .replace("__NOT_STREAMING__", tmdb.NOT_STREAMING)
        .replace("__DATA__", payload)
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")
    print(f"{len(rows)} titles -> {args.out}")


if __name__ == "__main__":
    main()
