"""Thin TMDB v3 client with on-disk caching and provider-name normalisation.

Only the stdlib is used. Every GET is cached under data/tmdb-cache/ so reruns
cost zero API calls; delete that folder to refresh availability data.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "tmdb-cache"
BASE_URL = "https://api.themoviedb.org/3"
REGION = "US"

# Canonical provider buckets, in the order they appear in the outputs.
PROVIDER_ORDER = [
    "Netflix",
    "Hulu",
    "Max",
    "Disney+",
    "Prime Video",
    "Apple TV+",
    "Paramount+",
    "Peacock",
    "Free (ads)",
    "Other",
]
NOT_STREAMING = "Not on a subscription service"

# TMDB provider display names -> canonical bucket. Matching is case-insensitive
# and tolerant of suffixes like "Amazon Channel" / "Apple TV Channel" / "with Ads".
_PROVIDER_PREFIXES = {
    "netflix": "Netflix",
    "hulu": "Hulu",
    "max": "Max",
    "hbo max": "Max",
    "hbo": "Max",
    "disney plus": "Disney+",
    "disney+": "Disney+",
    "amazon prime video": "Prime Video",
    "prime video": "Prime Video",
    "amazon video": "Prime Video",
    "apple tv plus": "Apple TV+",
    "apple tv+": "Apple TV+",
    "apple tv": "Apple TV+",
    "paramount plus": "Paramount+",
    "paramount+": "Paramount+",
    "paramount": "Paramount+",
    "peacock": "Peacock",
    "tubi": "Free (ads)",
    "pluto tv": "Free (ads)",
    "the roku channel": "Free (ads)",
    "roku channel": "Free (ads)",
    "plex": "Free (ads)",
    "crackle": "Free (ads)",
    "freevee": "Free (ads)",
    "amazon freevee": "Free (ads)",
    "kanopy": "Free (ads)",
    "hoopla": "Free (ads)",
    "fandango at home free": "Free (ads)",
    "vudu free": "Free (ads)",
    "xumo": "Free (ads)",
    "redbox free": "Free (ads)",
    "youtube free": "Free (ads)",
    "fawesome": "Free (ads)",
    "amazon mx player": "Free (ads)",
    "mx player": "Free (ads)",
    "cineverse": "Free (ads)",
    "justwatch tv": "Free (ads)",
    "mometu": "Free (ads)",
    "filmrise": "Free (ads)",
    "popcornflix": "Free (ads)",
    "freevee": "Free (ads)",
}

# Live-TV / cable-authenticated services: having a channel in a bundle is not a catalog
# you can browse, so these never count as "available".
IGNORED_PROVIDERS = {
    "fubotv", "youtube tv", "philo", "spectrum on demand", "the cw", "fxnow", "sling tv", "directv",
    "directv stream", "hulu live tv", "frndly tv", "xfinity stream", "tbs", "tnt", "trutv", "usa network",
    "abc", "nbc", "cbs", "fox", "amc", "fx", "syfy", "bravo tv", "e! online", "comedy central", "mtv",
    "vh1", "paramount network", "nickelodeon", "cartoon network", "adult swim", "history", "a&e", "lifetime",
    "food network", "hgtv", "discovery", "tlc", "bet+", "bet", "own", "freeform", "cw seed", "pbs", "pbs kids",
}


def is_ignored_provider(name: str) -> bool:
    return name.strip().lower() in IGNORED_PROVIDERS


def normalise_provider(name: str) -> str:
    """Map a TMDB provider display name to one of PROVIDER_ORDER."""
    key = name.strip().lower()
    # Longest prefix first so "hbo max" beats "hbo", "apple tv plus" beats "apple tv".
    for prefix in sorted(_PROVIDER_PREFIXES, key=len, reverse=True):
        if key == prefix or key.startswith(prefix + " ") or key.startswith(prefix + "("):
            return _PROVIDER_PREFIXES[prefix]
    return "Other"


def load_api_key() -> str:
    key = os.environ.get("TMDB_API_KEY", "").strip()
    env_file = ROOT / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("TMDB_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not key:
        sys.exit(
            "TMDB_API_KEY is not set. Copy .env.example to .env and paste your key "
            "(free at https://www.themoviedb.org/settings/api)."
        )
    return key


_API_KEY: str | None = None


def _api_key() -> str:
    global _API_KEY
    if _API_KEY is None:
        _API_KEY = load_api_key()
    return _API_KEY


def _cache_path(path: str, params: dict) -> Path:
    raw = path + "?" + urllib.parse.urlencode(sorted(params.items()))
    digest = hashlib.sha1(raw.encode()).hexdigest()[:16]
    safe = path.strip("/").replace("/", "_")
    return CACHE_DIR / f"{safe}_{digest}.json"


def get(path: str, use_cache: bool = True, **params) -> dict:
    """GET /3/<path> with the API key, returning parsed JSON (cached)."""
    params = {k: v for k, v in params.items() if v not in (None, "")}
    cache_file = _cache_path(path, params)
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text())

    query = dict(params, api_key=_api_key())
    url = f"{BASE_URL}/{path.lstrip('/')}?{urllib.parse.urlencode(query)}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                data = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as err:
            if err.code == 429 and attempt < 3:
                time.sleep(float(err.headers.get("Retry-After", 2)))
                continue
            if err.code == 404:
                data = {}
                break
            if err.code == 401:
                sys.exit("TMDB rejected the API key (401). Check TMDB_API_KEY in .env.")
            raise
        except urllib.error.URLError:
            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = cache_file.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(cache_file)
    return data


# --- search -----------------------------------------------------------------

def search_multi(query: str, year: int | None = None) -> list[dict]:
    """Movies + TV in one call (people are filtered out)."""
    data = get("search/multi", query=query, include_adult="false", language="en-US")
    results = [r for r in data.get("results", []) if r.get("media_type") in ("movie", "tv")]
    if year:
        results = [r for r in results if _year_of(r) in (year, year - 1, year + 1)] or results
    return results


def search_movie(query: str, year: int | None = None) -> list[dict]:
    data = get("search/movie", query=query, year=year, include_adult="false", language="en-US")
    return [dict(r, media_type="movie") for r in data.get("results", [])]


def search_tv(query: str, year: int | None = None) -> list[dict]:
    data = get("search/tv", query=query, first_air_date_year=year, include_adult="false", language="en-US")
    return [dict(r, media_type="tv") for r in data.get("results", [])]


# --- details ----------------------------------------------------------------

def movie_details(movie_id: int) -> dict:
    return get(f"movie/{movie_id}", append_to_response="external_ids", language="en-US")


def tv_details(tv_id: int) -> dict:
    return get(f"tv/{tv_id}", append_to_response="external_ids", language="en-US")


def tv_season(tv_id: int, season_number: int) -> dict:
    return get(f"tv/{tv_id}/season/{season_number}", language="en-US")


def watch_providers(media_type: str, tmdb_id: int) -> dict:
    """Return the raw US block of watch/providers ({} if none)."""
    data = get(f"{media_type}/{tmdb_id}/watch/providers")
    return data.get("results", {}).get(REGION, {})


def us_streaming_providers(media_type: str, tmdb_id: int) -> tuple[list[str], list[str]]:
    """(canonical buckets, raw TMDB names) for subscription/free/ad-supported US availability.

    Rent/buy offers are deliberately ignored.
    """
    block = watch_providers(media_type, tmdb_id)
    raw: list[str] = []
    for kind in ("flatrate", "free", "ads"):
        for p in block.get(kind, []):
            name = p.get("provider_name", "")
            if name and name not in raw and not is_ignored_provider(name):
                raw.append(name)
    buckets: list[str] = []
    for name in raw:
        bucket = normalise_provider(name)
        if bucket not in buckets:
            buckets.append(bucket)
    buckets.sort(key=PROVIDER_ORDER.index)
    return buckets, raw


# --- helpers ----------------------------------------------------------------

def _year_of(result: dict) -> int | None:
    date = result.get("release_date") or result.get("first_air_date") or ""
    return int(date[:4]) if date[:4].isdigit() else None


def title_of(result: dict) -> str:
    return result.get("title") or result.get("name") or ""


def tmdb_url(media_type: str, tmdb_id: int) -> str:
    return f"https://www.themoviedb.org/{media_type}/{tmdb_id}"


if __name__ == "__main__":
    # Smoke test: python3 scripts/tmdb.py "last vegas"
    q = " ".join(sys.argv[1:]) or "last vegas"
    for r in search_multi(q)[:5]:
        print(r["media_type"], r["id"], title_of(r), _year_of(r))
