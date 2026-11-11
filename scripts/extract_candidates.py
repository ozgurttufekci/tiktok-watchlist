"""Turn OCR text from TikTok screenshots into title candidates (data/titles.json).

Signals, strongest first:
  search_line   TikTok's "Search: <query>" / "Q Search • <query>" suggestion bar
  reply         comment replies to "what movie/show is this?"
  year          "Title (2013)" style lines
  listicle      "1. Title" style lines
  mention       comment bodies that look like a title ("Margin Call. Fantastic movie.",
                "movie name The King's Man", "go watch Devil Wears Prada again")
  hashtag       #LastVegas (camel-cased hashtags only)
  caption       Title-Case lines outside the comment section (video pages)

Each candidate is looked up on TMDB and scored by signal strength x title
similarity (+ a bonus when several independent signals agree). Screenshots
marked "reviewed": true keep their hand-edited "titles"; only "candidates"
are refreshed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tmdb  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OCR_FILE = ROOT / "data" / "ocr.json"
TITLES_FILE = ROOT / "data" / "titles.json"

AUTO_THRESHOLD = 0.60
MAX_AUTO_TITLES = 8
TMDB_THREADS = 8

BASE_SCORE = {
    "search_line": 1.00,
    "reply": 0.95,
    "year": 0.90,
    "listicle": 0.85,
    "mention": 0.75,
    "hashtag": 0.55,  # never enough alone; boosts other signals
    "caption": 0.50,  # never enough alone; surfaces in the review candidates
}
THIS_YEAR = __import__("datetime").date.today().year

# --- OCR line classifiers -----------------------------------------------------

TIME_RE = re.compile(
    r"^(?:repl[yv]\s+)?(?:\d+\s*(?:[smhdwy]|min|mins|hr|hrs|sec)\b\.?(?:\s+ago)?|\d{1,2}-\d{1,2}|\d{4}-\d{1,2}-\d{1,2})"
    r"(?:\s+repl[yv])?\.?$",
    re.I,
)
COUNT_RE = re.compile(r"^[\d,.]+\s*[kKmM]?$")
COMMENTS_HEADER_RE = re.compile(r"^\s*[\d,.]+\s*[kKmM]?\s+comments?\b", re.I)
UI_NOISE_RE = re.compile(
    r"^(repl[yv]|replies|hide|hide \^|view (\d+ )?(more )?(repl(y|ies|v)|replies)( ?v)?( \(\d+\))?|"
    r"view (\d+ )?more( v)?|add comment\.{0,3}|comments?|share|like|likes|follow|following( ®)?|for you|"
    r"explore|community|friends|profile|home|inbox|live|shop|less|more( v)?|search|see translation|"
    r"original (audio|sound).*|pinned|liked by creator|creator|author|sort by|top|newest|most liked|"
    r"\d{1,2}:\d{2}( [ap]m)?|5g\+?|lte|wifi|[a-z]|[\W_]+)$",
    re.I,
)
UI_WORDS = {
    "following", "for", "you", "explore", "community", "friends", "profile", "home", "inbox", "live",
    "shop", "search", "reposted", "more", "less", "stem", "lite", "®", "q", "o", "x", "d",
}
QUESTION_RE = re.compile(
    r"(what('?s| is| was)?\s+(the\s+)?(name|movie|film|show|series|title|this|it)|"
    r"name of (the|this)|where (can i|to|do i) (watch|find)|movie\s*\?|film\s*\?|show\s*\?|"
    r"title\s*\?|what movie|what show|what film|which (movie|show|film)|(movie|film|show) name\s*[?:]*$|"
    r"^name\s*[?!.]*$|sauce|source\s*\?|anyone know|does anyone)",
    re.I,
)
NON_ANSWER_RE = re.compile(
    r"^(thank(s| you)|thx|ty|same|idk|i don'?t know|me too|lol|lmao|omg|yes|no|yep|nope|facts|fr|real|"
    r"this|bump|w|l|following|commenting|song\s*\??|\?+|\W+)\b",
    re.I,
)
SEARCH_LINE_RE = re.compile(r"^\s*(?:q\s+)?search\s*[:•·\-]?\s+(.+?)\s*[?◦•·]*\s*$", re.I)
YEAR_RE = re.compile(r"^(.*?[A-Za-z]{2}.*?)\s*[\(\[\-–—,|]?\s*((?:19|20)\d{2})\s*[\)\]]?\s*$")
MONTH_DATE_RE = re.compile(
    r"^(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?$", re.I
)
LISTICLE_RE = re.compile(r"^\s*(?:\d{1,2}|[IVX]{1,4})\s*[.):\-–]\s+(.{2,60}?)\s*$")
HASHTAG_RE = re.compile(r"#([A-Za-z][A-Za-z0-9_]{2,40})")
SERIAL_RE = re.compile(r"^(episode|ep|part|pt|day|chapter|season|s)\.?\s*\d+$", re.I)

# Single words that match *some* TMDB title but are never what the comment meant.
GENERIC_WORDS = {
    "name", "song", "sauce", "title", "movie", "film", "show", "series", "same", "facts", "bump",
    "this", "that", "what", "where", "who", "why", "how", "yes", "no", "lol", "omg", "wow", "first",
    "second", "real", "true", "damn", "bro", "man", "dude", "sir", "king", "queen", "legend", "goat",
    "fire", "ratio", "cap", "nah", "yeah", "ok", "okay", "idk", "lmao", "me", "us", "him", "her",
    "them", "it", "you", "everything", "something", "anything", "nothing", "one", "all", "both",
    "these", "those", "there", "here", "then", "when", "which", "whom", "whose", "mine", "yours",
    "home", "friends", "inbox", "profile", "explore", "following", "live", "shop", "search", "reply",
    "more", "less", "view", "hide", "comment", "comments", "share", "like", "likes", "follow", "watch",
    "watching", "watched", "episode", "part", "scene", "clip", "edit", "trailer", "ending", "spoiler",
    "spoilers", "cast", "actor", "actress", "director", "soundtrack", "music", "audio", "sound",
    "original", "playlist", "video", "photo", "live", "love", "hate", "good", "bad", "best", "worst",
    "great", "classic", "masterpiece", "underrated", "overrated", "recommend", "recommendation",
    "today", "tonight", "tomorrow", "yesterday", "now", "later", "again", "please", "pls", "plz",
    "help", "thanks", "thank", "sorry", "hello", "hi", "hey", "bye", "yo", "sup", "cool", "nice",
    "crazy", "insane", "wild", "chill", "vibes", "mood", "energy", "goals", "relatable", "accurate",
    "agree", "disagree", "exactly", "literally", "actually", "honestly", "basically", "obviously",
}
GENERIC_HASHTAGS = {
    "fyp", "fy", "foryou", "foryoupage", "viral", "trending", "movie", "movies", "film", "films",
    "movietok", "filmtok", "cinema", "moviesrecommendation", "movierecommendation", "movierecommendations",
    "moviesrecommendations", "tvshow", "tvshows", "series", "netflix", "hulu", "hbo", "hbomax", "max",
    "disneyplus", "primevideo", "amazonprime", "appletv", "paramountplus", "peacock", "whattowatch",
    "watchlist", "mustwatch", "recommendation", "recommendations", "underrated", "comedy", "drama",
    "horror", "thriller", "romance", "romcom", "action", "scifi", "anime", "kdrama", "fypシ", "edit",
    "edits", "scene", "movieclips", "movieclip", "clips", "actor", "actress", "hollywood", "oscars",
    "tiktok", "foryourpage", "xyzbca", "capcut", "fypage", "explore", "explorepage", "viralvideo",
    "movieedit", "filmedit", "tvshowedit", "seriesedit", "moviescene", "movienight", "cinematography",
}
STOP_AFTER = (
    r"again|tonight|rn|right now|next|too|as well|and|or|if|is|was|last|yesterday|when|on|at|for|"
    r"with|lol|haha|omg|tho|though|instead|first|now|already|before|after|since|then|so|but|because"
)
WATCH_VERB_RE = re.compile(
    r"\b(?:watch(?:ed|ing)?|seen|see|try|called|recommend|loved|finished|started|"
    r"binged|binge|rewatched|rewatch)\s+(?:the\s+)?([A-Za-z0-9'’:&!\-\. ]{3,45}?)"
    rf"(?=\s+(?:{STOP_AFTER})\b|[.!?,;:]|$)",
    re.I,
)
CAP_RUN_RE = re.compile(
    r"\b((?:[A-Z][\w'’]*|\d+)(?:\s+(?:of|the|and|a|an|in|on|at|to|for|&|vs\.?|[A-Z][\w'’]*|\d+)){1,5})\b"
)
CAP_WORD_RE = re.compile(r"\b([A-Z][a-z]{3,}(?:[\-'’][A-Za-z]+)?)\b")
LEAD_STRIP_RE = re.compile(
    r"^(?:(?:the\s+)?(?:movie|film|show|series|anime)(?:\s+(?:name|title))?\s*(?:is|:|-)?\s+|"
    r"(?:it'?s|its|it is|this is|that'?s|thats|watch|go watch|try|called|name is|name:|i think(?: it'?s)?|"
    r"probably|maybe|it'?s called|its called|from|in|the (?:movie|film|show) is)\s+)+",
    re.I,
)
TAIL_STRIP_RE = re.compile(
    r"\s+(?:movie|film|show|series|lol|lmao|haha|fr|tho|though|imo|is (?:so )?good|is great|is amazing|"
    r"was (?:so )?good|i think|maybe|probably|for sure|100%|frfr|ong|no cap|obviously|definitely)[.!?]*$",
    re.I,
)


def norm(s: str) -> str:
    s = s.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"^(the|a|an) ", "", s)
    return re.sub(r"\s+", " ", s).strip()


def similarity(a: str, b: str) -> float:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    short, long_ = sorted((na, nb), key=len)
    # "truth or dare" vs "truth or dare ending jack 2 cabin": reward multi-word prefixes,
    # scaled by how much of the longer string they cover.
    if len(short.split()) >= 2 and long_.startswith(short + " "):
        ratio = max(ratio, 0.6 + 0.4 * len(short) / len(long_))
    return round(ratio, 3)


def clean_line(s: str) -> str:
    return re.sub(r"^[\s\-•·◦|>]+", "", s).strip()


def is_noise(line: str) -> bool:
    s = clean_line(line)
    if not s or UI_NOISE_RE.match(s) or TIME_RE.match(s) or COUNT_RE.match(s):
        return True
    words = re.findall(r"[\w®]+", s.lower())
    return bool(words) and all(w in UI_WORDS for w in words)


def is_all_caps(s: str) -> bool:
    letters = [c for c in s if c.isalpha()]
    return len(letters) >= 3 and all(c.isupper() for c in letters)


# --- structure ----------------------------------------------------------------

def split_sections(lines: list[str]) -> tuple[list[str], list[str]]:
    """(lines above the comment sheet, lines inside it). No sheet -> everything is 'above'."""
    for i, line in enumerate(lines):
        if COMMENTS_HEADER_RE.match(clean_line(line)):
            return lines[:i], lines[i + 1 :]
    return lines, []


def parse_comments(comment_lines: list[str]) -> list[dict]:
    """Group comment-sheet lines into {user, body, lone} using timestamps as separators.

    Layout per comment is: username / body line(s) / [like count] / timestamp / Reply.
    """
    comments: list[dict] = []
    buf: list[str] = []

    def flush():
        if len(buf) >= 2:
            comments.append({"user": buf[0], "body": " ".join(buf[1:]), "lone": False})
        elif len(buf) == 1:
            comments.append({"user": "", "body": buf[0], "lone": True})
        buf.clear()

    for raw in comment_lines:
        s = clean_line(raw)
        if not s:
            continue
        if TIME_RE.match(s):
            flush()
            continue
        if COUNT_RE.match(s) or UI_NOISE_RE.match(s):
            continue
        buf.append(s)
    flush()
    return comments


def body_candidates(body: str) -> list[tuple[str, float]]:
    """(text, weight) phrases inside one comment body that could be a title."""
    out: list[tuple[str, float]] = []
    b = body.strip()
    if not b or QUESTION_RE.search(b) or NON_ANSWER_RE.match(b):
        return out

    core = LEAD_STRIP_RE.sub("", b)
    core = re.sub(r"\s*[?!.]+$", "", core)
    first = re.split(r"[.!?;:(\[\n]|\s[-–—]\s|,\s", core, maxsplit=1)[0].strip()
    first = TAIL_STRIP_RE.sub("", first).strip(" \"'“”‘’")
    n = len(first.split())
    if 1 <= n <= 6 and re.search(r"[A-Za-z]{2}", first):
        weight = 1.0 if len(b.split()) <= 6 else 0.8
        if n == 1:
            weight = 0.0 if first.lower() in GENERIC_WORDS else 0.5
        if weight:
            out.append((first, weight))

    stop_words = set(STOP_AFTER.replace("right now", "").split("|"))
    for m in WATCH_VERB_RE.finditer(b):
        phrase = m.group(1).strip(" .")
        words = phrase.split()
        while words and words[0].lower() in stop_words:
            words = words[1:]
        phrase = " ".join(words)
        if 1 <= len(words) <= 6 and phrase.lower() not in GENERIC_WORDS:
            out.append((phrase, 0.9))

    for m in CAP_RUN_RE.finditer(b):
        phrase = m.group(1)
        words = phrase.split()
        if len(words) >= 2 and not re.match(r"^(I|I'm|I'll|I've|I'd|Im|Ill|Ive)$", words[0]):
            out.append((phrase, 0.9))

    for m in CAP_WORD_RE.finditer(b):
        word = m.group(1)
        mid_sentence = m.start() > 0 and not re.search(r"[.!?]\s*$", b[: m.start()])
        if mid_sentence and word.lower() not in GENERIC_WORDS:
            out.append((word, 0.5))
    return out


def search_variants(query: str) -> list[str]:
    """'truth or dare ending jack 2 cabin' -> every leading prefix, longest first (max 8)."""
    words = query.split()
    out: list[str] = []
    seen: set[str] = set()
    while words and len(out) < 8:
        q = " ".join(words)
        if q.lower() not in seen and len(q) >= 2:
            seen.add(q.lower())
            out.append(q)
        words = words[:-1]
    return out


def split_camel(tag: str) -> str:
    words = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", tag)
    return " ".join(words)


# --- extraction ---------------------------------------------------------------

def extract_candidates(lines: list[str]) -> list[dict]:
    cands: list[dict] = []

    def add(text, source, evidence, year=None, media_hint=None, weight=1.0):
        text = re.sub(r"\s+", " ", text).strip(" \"'“”‘’.,:;!?-–—#@()[]")
        if len(text) < 2 or len(text) > 80 or is_noise(text) or SERIAL_RE.match(text):
            return
        if not re.search(r"[A-Za-z]{2}", text) or MONTH_DATE_RE.match(text):
            return
        if len(text.split()) == 1 and text.lower() in GENERIC_WORDS:
            return
        cands.append({"text": text, "source": source, "evidence": evidence[:160], "year": year,
                      "media_hint": media_hint, "weight": weight})

    above, sheet = split_sections(lines)
    comments = parse_comments(sheet)

    # 1. "Search: …" bar (anywhere)
    for line in lines:
        m = SEARCH_LINE_RE.match(clean_line(line))
        if m:
            q = re.sub(r"[?◦•·\s]+$", "", m.group(1))
            variants = search_variants(q)
            for v in variants:
                weight = 0.6 if len(v.split()) == 1 and len(variants) > 1 else 1.0
                add(v, "search_line", line.strip(), weight=weight)

    # 2. Replies to "what movie is this?"
    for k, c in enumerate(comments):
        if not QUESTION_RE.search(c["body"]):
            continue
        hint = "tv" if re.search(r"\b(show|series)\b", c["body"], re.I) else (
            "movie" if re.search(r"\b(movie|film)\b", c["body"], re.I) else None)
        for ans in comments[k + 1 : k + 4]:
            for text, w in body_candidates(ans["body"]):
                add(text, "reply", f"{c['body']!r} -> {ans['body']!r}", media_hint=hint,
                    weight=w * (0.7 if ans["lone"] else 1.0))

    # 3. Any comment that reads like a title
    for c in comments:
        hint = "tv" if re.search(r"\b(shows?|series|season|episodes?|anime)\b", c["body"], re.I) else (
            "movie" if re.search(r"\b(movies?|films?)\b", c["body"], re.I) else None)
        for text, w in body_candidates(c["body"]):
            add(text, "mention", c["body"], media_hint=hint, weight=w * (0.7 if c["lone"] else 1.0))

    # 4. "Title (2013)"
    for line in lines:
        m = YEAR_RE.match(clean_line(line))
        if m and len(m.group(1)) <= 60 and not is_noise(m.group(1)) and not MONTH_DATE_RE.match(m.group(1).strip()):
            add(m.group(1), "year", line.strip(), year=int(m.group(2)))

    # 5. Listicles "1. Title"
    for line in lines:
        m = LISTICLE_RE.match(clean_line(line))
        if m:
            add(m.group(1), "listicle", line.strip())

    # 6. Hashtags (camel-cased only; all-lowercase tags can't be split reliably)
    for line in lines:
        for tag in HASHTAG_RE.findall(line):
            if tag.lower() in GENERIC_HASHTAGS or not re.search(r"[a-z][A-Z]", tag):
                continue
            add(split_camel(tag), "hashtag", f"#{tag}")

    # 7. Title-Case segments above the comment sheet (video pages, overlays)
    for line in above:
        for s in re.split(r"\s+[|•·]\s+", clean_line(line)):
            s = s.strip()
            words = s.split()
            if not (1 <= len(words) <= 6) or is_noise(s) or SEARCH_LINE_RE.match(s):
                continue
            if re.search(r"[#@•|]", s) or QUESTION_RE.search(s) or NON_ANSWER_RE.match(s) or s.endswith(("...", "…")):
                continue
            caps = sum(1 for w in words if w[:1].isupper())
            if caps < max(1, len(words) - 1) or not re.search(r"[A-Za-z]{2,}", s):
                continue
            weight = 1.0
            if is_all_caps(s):
                weight = 0.7  # burned-in subtitles are usually all caps
            if len(words) == 1:
                weight = min(weight, 0.6)
            add(s, "caption", s, weight=weight)

    # Merge duplicates (by normalised text): keep the strongest signal, remember all of them.
    best: dict[str, dict] = {}
    for c in cands:
        key = norm(c["text"])
        c["base"] = round(BASE_SCORE[c["source"]] * c.pop("weight"), 3)
        if key not in best:
            c["sources"], c["evidence"] = [c["source"]], [c["evidence"]]
            best[key] = c
            continue
        b = best[key]
        if c["source"] not in b["sources"]:
            b["sources"].append(c["source"])
            b["evidence"].append(c["evidence"])
        b["year"] = b["year"] or c["year"]
        b["media_hint"] = b["media_hint"] or c["media_hint"]
        if c["base"] > b["base"]:
            b["base"], b["source"] = c["base"], c["source"]
    return sorted(best.values(), key=lambda c: -c["base"])


def final_score(cand: dict, sim: float) -> float:
    """base x TMDB similarity, +0.05 for every extra independent signal that agreed."""
    return round(min(1.0, cand["base"] * sim + 0.05 * (len(cand["sources"]) - 1)), 3)


# --- TMDB ---------------------------------------------------------------------

def tmdb_suggest(cand: dict) -> dict | None:
    """Best TMDB match for a candidate. The media hint only nudges; people say "movie" for shows."""
    hint, year = cand.get("media_hint"), cand.get("year")
    fallback = False
    try:
        results = tmdb.search_multi(cand["text"], year)
        if not results:  # search/multi occasionally drops titles that search/movie|tv return
            results = tmdb.search_movie(cand["text"], year) + tmdb.search_tv(cand["text"], year)
            fallback = True
    except Exception as err:  # network hiccup: leave the candidate unscored
        print(f"warning: TMDB lookup failed for {cand['text']!r}: {err}", file=sys.stderr)
        return None
    single_word = len(cand["text"].split()) == 1
    scored = []
    for r in results[:10]:
        sim = similarity(cand["text"], tmdb.title_of(r))
        orig = r.get("original_title") or r.get("original_name")
        if orig:
            sim = max(sim, similarity(cand["text"], orig))
        votes = r.get("vote_count", 0) or 0
        ryear = tmdb._year_of(r)
        if ryear is None or ryear > THIS_YEAR:
            sim *= 0.7  # unreleased / undated entries are rarely what a TikTok is about
        if single_word and votes < 50:
            sim *= 0.7  # "terry", "horace": one word matching an obscure film is noise
        if fallback:
            sim *= 0.85  # multi-search found nothing; these are usually marginal matches
        if hint and r.get("media_type") == hint:
            sim = min(1.0, sim + 0.08)
        # Vote count breaks ties between same-named titles (TMDB "popularity" over-ranks TV).
        scored.append((sim + min(votes, 5000) / 100000, round(sim, 3), r))
    if not scored:
        return None
    _, sim, r = max(scored, key=lambda t: t[0])
    return {
        "id": r["id"],
        "media_type": r["media_type"],
        "title": tmdb.title_of(r),
        "year": tmdb._year_of(r),
        "votes": r.get("vote_count", 0) or 0,
        "similarity": sim,
    }


def auto_titles(cands: list[dict]) -> list[dict]:
    auto, seen = [], set()
    for c in cands:
        sug = c.get("tmdb")
        if not sug or c["score"] < AUTO_THRESHOLD:
            continue
        key = (sug["media_type"], sug["id"])
        if key in seen:
            continue
        seen.add(key)
        auto.append(
            {
                "query": sug["title"],
                "year": sug.get("year") or c.get("year"),
                "media_type": sug["media_type"],
                "tmdb_id": sug["id"],
                "confidence": c["score"],
                "note": "; ".join(f"{s}: {e}" for s, e in zip(c["sources"], c["evidence"])),
            }
        )
        if len(auto) >= MAX_AUTO_TITLES:
            break
    return auto


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-tmdb", action="store_true", help="skip TMDB lookups (no API key yet)")
    ap.add_argument("--ocr", type=Path, default=OCR_FILE)
    ap.add_argument("--out", type=Path, default=TITLES_FILE)
    args = ap.parse_args()

    if not args.ocr.exists():
        sys.exit(f"{args.ocr} not found - run the OCR step first.")
    ocr = json.loads(args.ocr.read_text())
    titles = json.loads(args.out.read_text()) if args.out.exists() else {}

    use_tmdb = not args.no_tmdb
    if use_tmdb:
        try:
            tmdb.load_api_key()
        except SystemExit as err:
            print(f"warning: {err}\n         continuing without TMDB suggestions", file=sys.stderr)
            use_tmdb = False

    per_shot: dict[str, list[dict]] = {}
    for name in sorted(ocr):
        lines = [l for l in ocr[name].get("lines", []) if l.strip()]
        per_shot[name] = extract_candidates(lines)

    all_cands = [c for cs in per_shot.values() for c in cs]
    if use_tmdb:
        print(f"looking up {len(all_cands)} candidates on TMDB ...", file=sys.stderr)
        with ThreadPoolExecutor(TMDB_THREADS) as pool:
            for c, sug in zip(all_cands, pool.map(tmdb_suggest, all_cands)):
                c["tmdb"] = sug
                c["score"] = final_score(c, sug["similarity"] if sug else 0.0)
    else:
        for c in all_cands:
            c["score"] = final_score(c, 1.0)

    for name, cands in per_shot.items():
        cands.sort(key=lambda c: (-c["score"], -len(c["sources"])))
        lines = [l for l in ocr[name].get("lines", []) if l.strip()]
        entry = titles.get(name, {})
        entry["candidates"] = cands
        entry["ocr_excerpt"] = " | ".join(lines[:14])[:400]
        if not entry.get("reviewed"):
            entry["titles"] = auto_titles(cands) if use_tmdb else []
            entry["reviewed"] = False
        titles[name] = entry

    for name in list(titles):
        if name not in ocr:
            del titles[name]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(titles, indent=2, ensure_ascii=False) + "\n")
    n_auto = sum(1 for e in titles.values() if e.get("titles"))
    n_rev = sum(1 for e in titles.values() if e.get("reviewed"))
    print(f"{len(titles)} screenshots -> {n_auto} with titles ({n_rev} reviewed), "
          f"{len(titles) - n_auto} without -> {args.out}")


if __name__ == "__main__":
    main()
