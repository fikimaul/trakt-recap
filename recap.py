"""Trakt.tv Recap Infographic Generator (Neo-Brutalism Instagram Story 1080x1920)

Can be executed dynamically for any month or year:
  python recap.py --period=2026-09
  python recap.py --period=2026-10
  python recap.py -p 2025-12
  python recap.py 2026
"""

import argparse
import calendar
import concurrent.futures
import csv
import glob
import json
import os
import shutil
import sys
import threading
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from collections import Counter, defaultdict

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

# ==============================================================================
# DIRECTORY & PATH CONFIGURATION
# ==============================================================================
BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
OUTPUT_DIR = os.path.join(BASE, "output")
POSTERS_DIR = os.path.join(BASE, "posters")
BACKDROPS_DIR = os.path.join(BASE, "backdrops")
LOGOS_DIR = os.path.join(BASE, "logos")
TMDB_CACHE = os.path.join(BASE, "tmdb_runtime_cache.json")
POSTER_FALLBACK = os.path.join(BASE, "top1_poster.jpg")

W, H = 1080, 1920
WIB = timezone(timedelta(hours=7))

FONTS_DIR = os.path.join(BASE, "fonts")
FONT_FILES = {
    "impact": os.path.join(FONTS_DIR, "Anton-Regular.ttf"),
    "arialb": os.path.join(FONTS_DIR, "Barlow-Bold.ttf"),
    "bahnschrift": os.path.join(FONTS_DIR, "Barlow-SemiBold.ttf"),
    "mono": os.path.join(FONTS_DIR, "SpaceMono-Bold.ttf"),
}
_fcache = {}

MONTH_NAMES = {
    1: "JANUARY", 2: "FEBRUARY", 3: "MARCH", 4: "APRIL",
    5: "MAY", 6: "JUNE", 7: "JULY", 8: "AUGUST",
    9: "SEPTEMBER", 10: "OCTOBER", 11: "NOVEMBER", 12: "DECEMBER"
}

COUNTRY_NAMES = {
    "US": "United States", "JP": "Japan", "ID": "Indonesia", "GB": "United Kingdom",
    "KR": "South Korea", "CN": "China", "LV": "Latvia", "PL": "Poland",
    "FR": "France", "DE": "Germany", "IT": "Italy", "ES": "Spain",
    "CA": "Canada", "AU": "Australia", "TH": "Thailand", "IN": "India",
    "HK": "Hong Kong", "TW": "Taiwan", "MX": "Mexico", "BR": "Brazil",
    "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "FI": "Finland",
    "NL": "Netherlands", "BE": "Belgium", "NZ": "New Zealand", "IE": "Ireland",
    "PH": "Philippines", "MY": "Malaysia", "SG": "Singapore", "VN": "Vietnam",
    "TR": "Turkey", "RU": "Russia", "UA": "Ukraine", "AR": "Argentina",
    "CL": "Chile", "CO": "Colombia", "ZA": "South Africa", "EG": "Egypt",
    "NG": "Nigeria", "AT": "Austria", "CH": "Switzerland", "GR": "Greece",
    "PT": "Portugal", "CZ": "Czech Republic", "HU": "Hungary", "RO": "Romania",
}


def get_font(name, size):
    key = (name, size)
    if key not in _fcache:
        path = FONT_FILES.get(name, FONT_FILES["arialb"])
        if not os.path.exists(path):
            path = FONT_FILES["arialb"]
        _fcache[key] = ImageFont.truetype(path, size)
    return _fcache[key]


def to_wib(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(WIB)


def get_username(cli_username=None):
    """Dynamically determines the Trakt username:
    1. CLI argument (--username / -u)
    2. Environment variable TRAKT_USERNAME
    3. File data/user-profile.json (field 'username')
    4. File data/user-settings.json (field 'user.username')
    5. Fallback to 'TRAKT.TV' if none is found
    """
    if cli_username and str(cli_username).strip():
        return str(cli_username).strip()

    env_user = os.environ.get("TRAKT_USERNAME", "").strip()
    if env_user:
        return env_user

    # Check user-profile.json
    for search_dir in [DATA_DIR, BASE]:
        p = os.path.join(search_dir, "user-profile.json")
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    u = json.load(f).get("username")
                    if u and isinstance(u, str) and u.strip():
                        return u.strip()
            except Exception:
                pass

    # Check user-settings.json
    for search_dir in [DATA_DIR, BASE]:
        p = os.path.join(search_dir, "user-settings.json")
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    u = json.load(f).get("user", {}).get("username")
                    if u and isinstance(u, str) and u.strip():
                        return u.strip()
            except Exception:
                pass

    return "TRAKT.TV"


# ==============================================================================
# TMDB API & CACHE SYNC
# ==============================================================================
def load_tmdb_cache():
    cache = {"movies": {}, "episodes": {}, "show_avg": {}, "meta_movies": {}, "meta_shows": {}}
    if os.path.exists(TMDB_CACHE):
        try:
            loaded = json.load(open(TMDB_CACHE, encoding="utf-8"))
            cache.update(loaded)
        except Exception:
            pass
    if "meta_movies" not in cache:
        cache["meta_movies"] = {}
    if "meta_shows" not in cache:
        cache["meta_shows"] = {}
    return cache


def save_tmdb_cache(cache):
    temp = TMDB_CACHE + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
    os.replace(temp, TMDB_CACHE)


def get_tmdb_api_key():
    """Retrieves TMDB API key from TMDB_API_KEY environment variable or .env file."""
    key = os.environ.get("TMDB_API_KEY", "").strip()
    if key:
        return key

    # Fallback to direct parsing of local .env file
    env_file = os.path.join(BASE, ".env")
    if os.path.exists(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() == "TMDB_API_KEY":
                            val = v.strip().strip("'\"")
                            if val:
                                return val
        except Exception:
            pass

    # Fallback legacy tmdb_api_key.txt if present
    legacy_file = os.path.join(BASE, "tmdb_api_key.txt")
    if os.path.exists(legacy_file):
        try:
            return open(legacy_file, "r", encoding="utf-8").read().strip()
        except Exception:
            pass

    return ""


def sync_tmdb_runtimes(movies_to_check, episodes_to_check):
    """Checks and downloads missing metadata & runtimes from TMDB API in parallel."""
    api_key = get_tmdb_api_key()
    if not api_key:
        return load_tmdb_cache()

    cache = load_tmdb_cache()
    lock = threading.Lock()
    changed = False

    # 1. Filter movies that need TMDB sync
    movies_to_fetch = []
    seen_m = set()
    for m in movies_to_check:
        mid = m.get("tmdb_id")
        if not mid or mid in seen_m:
            continue
        seen_m.add(mid)
        smid = str(mid)
        need_rt = smid not in cache["movies"] or not cache["movies"][smid].get("runtime")
        need_meta = smid not in cache.get("meta_movies", {}) or "studios" not in cache["meta_movies"][smid]
        if need_rt or need_meta:
            movies_to_fetch.append((mid, smid, m.get("title", "")))

    def fetch_movie(item):
        nonlocal changed
        mid, smid, title = item
        url = f"https://api.themoviedb.org/3/movie/{mid}?api_key={api_key}&append_to_response=credits"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                d = json.loads(resp.read().decode())
                rt = d.get("runtime") or 0
                genres = [g["name"] for g in d.get("genres", [])]
                crew = d.get("credits", {}).get("crew", [])
                dirs = list(dict.fromkeys([c["name"] for c in crew if c.get("job") == "Director"]))
                writers = list(dict.fromkeys([c["name"] for c in crew if c.get("job") in ("Writer", "Screenplay", "Author")]))
                orig_c = d.get("origin_country", [])
                if not orig_c:
                    orig_c = [p.get("iso_3166_1") for p in d.get("production_countries", []) if p.get("iso_3166_1")]
                studios = [p["name"] for p in d.get("production_companies", [])[:2] if p.get("name")]
                with lock:
                    cache["movies"][smid] = {"title": title, "runtime": rt}
                    cache["meta_movies"][smid] = {
                        "genres": genres,
                        "directors": dirs,
                        "writers": writers,
                        "countries": orig_c,
                        "studios": studios,
                    }
                    changed = True
        except Exception:
            pass

    # 2. Filter episodes & shows that need TMDB sync
    episodes_to_fetch = []
    shows_to_fetch = set()
    seen_e = set()
    for e in episodes_to_check:
        sid = e.get("show_tmdb_id")
        s = e.get("season")
        ep = e.get("number")
        if not sid or s is None or ep is None:
            continue
        ssid = str(sid)
        ekey = f"{ssid}-s{s}e{ep}"
        if ekey not in seen_e:
            seen_e.add(ekey)
            if ekey not in cache["episodes"] or not cache["episodes"][ekey].get("runtime"):
                episodes_to_fetch.append((sid, s, ep, ssid, ekey, e.get("show_title", "")))

        need_show_avg = ssid not in cache["show_avg"]
        need_show_meta = ssid not in cache.get("meta_shows", {}) or "studios" not in cache["meta_shows"][ssid]
        if need_show_avg or need_show_meta:
            shows_to_fetch.add((sid, ssid))

    def fetch_episode(item):
        nonlocal changed
        sid, s, ep, ssid, ekey, title = item
        url = f"https://api.themoviedb.org/3/tv/{sid}/season/{s}/episode/{ep}?api_key={api_key}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                d = json.loads(resp.read().decode())
                rt = d.get("runtime") or 0
                with lock:
                    cache["episodes"][ekey] = {"show": title, "runtime": rt}
                    changed = True
        except Exception:
            pass

    def fetch_show(item):
        nonlocal changed
        sid, ssid = item
        url = f"https://api.themoviedb.org/3/tv/{sid}?api_key={api_key}&append_to_response=credits,aggregate_credits"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                d = json.loads(resp.read().decode())
                ert = d.get("episode_run_time") or []
                avg = int(sum(ert) / len(ert)) if ert else 0
                genres = [g["name"] for g in d.get("genres", [])]
                cb = [c["name"] for c in d.get("created_by", [])]
                agg_crew = d.get("aggregate_credits", {}).get("crew", []) or d.get("credits", {}).get("crew", [])
                dirs = list(dict.fromkeys([c["name"] for c in agg_crew if any(j.get("job") == "Director" for j in c.get("jobs", [])) or c.get("job") == "Director"]))
                writers = list(dict.fromkeys([c["name"] for c in agg_crew if any("Writer" in j.get("job", "") or "Creator" in j.get("job", "") for j in c.get("jobs", [])) or c.get("job") in ("Writer", "Screenplay")]))
                orig_c = d.get("origin_country", [])
                if not orig_c:
                    orig_c = [p.get("iso_3166_1") for p in d.get("production_countries", []) if p.get("iso_3166_1")]
                studios = [p["name"] for p in d.get("production_companies", [])[:2] if p.get("name")]
                with lock:
                    cache["show_avg"][ssid] = avg
                    cache["meta_shows"][ssid] = {
                        "genres": genres,
                        "creators": cb,
                        "directors": dirs,
                        "writers": writers,
                        "countries": orig_c,
                        "studios": studios,
                    }
                    changed = True
        except Exception:
            pass

    tasks = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        for item in movies_to_fetch:
            tasks.append(executor.submit(fetch_movie, item))
        for item in shows_to_fetch:
            tasks.append(executor.submit(fetch_show, item))
        for item in episodes_to_fetch:
            tasks.append(executor.submit(fetch_episode, item))
        concurrent.futures.wait(tasks)

    if changed:
        save_tmdb_cache(cache)
    return cache


# ==============================================================================
# DATA COLLECTION FOR PERIOD
# ==============================================================================
def gather(year, month=None, username=None):
    is_yearly = (month is None)
    active_user = get_username(username)
    history_files = glob.glob(os.path.join(DATA_DIR, "watched-history-*.json"))
    if not history_files:
        history_files = glob.glob(os.path.join(BASE, "watched-history-*.json"))

    all_history = []
    for p in history_files:
        try:
            all_history.extend(json.load(open(p, encoding="utf-8")))
        except Exception:
            pass

    # Filter by period (year and month if provided)
    hist = []
    for h in all_history:
        dt = to_wib(h["watched_at"])
        if dt.year == year and (month is None or dt.month == month):
            hist.append(h)

    # Sort from newest to oldest
    hist.sort(key=lambda x: x["watched_at"], reverse=True)

    # Ratings
    ratings = []
    rating_patterns = ["ratings-movies-*.json", "ratings-episodes-*.json", "ratings-shows.json"]
    for pat in rating_patterns:
        for p in glob.glob(os.path.join(DATA_DIR, pat)) + glob.glob(os.path.join(BASE, pat)):
            try:
                ratings.extend(json.load(open(p, encoding="utf-8")))
            except Exception:
                pass

    rate = []
    for r in ratings:
        ra = r.get("rated_at")
        if ra:
            dt = to_wib(ra)
            if dt.year == year and (month is None or dt.month == month):
                rate.append(r)

    movies = [h for h in hist if h["type"] == "movie"]
    eps = [h for h in hist if h["type"] == "episode"]

    # Sync TMDB cache for this period
    m_check = [{"tmdb_id": m["movie"]["ids"].get("tmdb"), "title": m["movie"]["title"]} for m in movies]
    e_check = [{
        "show_tmdb_id": e["show"]["ids"].get("tmdb"),
        "season": e["episode"]["season"],
        "number": e["episode"]["number"],
        "show_title": e["show"]["title"]
    } for e in eps]
    tc = sync_tmdb_runtimes(m_check, e_check)

    # Aggregate shows sorted by highest total runtime (grouped by show title)
    shows_eps = {}
    shows_rt = {}
    show_tmdb = {}
    for e in eps:
        t = e["show"]["title"]
        shows_eps[t] = shows_eps.get(t, 0) + 1
        sid = str(e["show"]["ids"].get("tmdb"))
        s, ep = e["episode"]["season"], e["episode"]["number"]
        k = f"{sid}-s{s}e{ep}"
        rt = tc.get("episodes", {}).get(k, {}).get("runtime")
        if not rt:
            rt = tc.get("show_avg", {}).get(sid) or 0
        shows_rt[t] = shows_rt.get(t, 0) + rt
        if t not in show_tmdb and e["show"]["ids"].get("tmdb"):
            show_tmdb[t] = e["show"]["ids"]["tmdb"]

    shows_list = []
    for t, rt in shows_rt.items():
        cnt = shows_eps.get(t, 0)
        tmdb_id = show_tmdb.get(t)
        shows_list.append((t, rt, cnt, tmdb_id))

    shows_list.sort(key=lambda x: x[1], reverse=True)
    top_shows = shows_list[:5]

    # Map movie ratings
    movie_rating = {}
    for r in rate:
        if r.get("movie"):
            movie_rating[r["movie"]["title"]] = r["rating"]
    # Fallback to all ratings if current month has no rated_at
    if not movie_rating:
        for r in ratings:
            if r.get("movie") and r["movie"]["title"] not in movie_rating:
                movie_rating[r["movie"]["title"]] = r.get("rating")

    # Map ratings per episode/show
    rate_map = {}
    for r in ratings:
        if r.get("movie"):
            rate_map[("movie", r["movie"]["ids"].get("tmdb"))] = r.get("rating")
        elif r.get("episode") and r.get("show"):
            rate_map[("episode", r["show"]["ids"].get("tmdb"), r["episode"]["season"], r["episode"]["number"])] = r.get("rating")

    # Aggregate movies sorted by highest runtime
    tc_movies = tc.get("movies", {})
    movie_runtime_list = []
    seen_movies = set()
    for m in movies:
        t = m["movie"]["title"]
        if t in seen_movies:
            continue
        seen_movies.add(t)
        mid = str(m["movie"]["ids"].get("tmdb"))
        rt = tc_movies.get(mid, {}).get("runtime") or 0
        yr = m["movie"].get("year")
        rtg = movie_rating.get(t)
        tmdb_id = m["movie"]["ids"].get("tmdb")
        movie_runtime_list.append((t, rt, rtg, yr, tmdb_id))

    movie_runtime_list.sort(key=lambda x: x[1], reverse=True)
    top_movies = movie_runtime_list[:5]

    # Calculate total runtime and daily watch duration
    total_minutes = 0
    by_day_minutes = {}
    by_day_counts = {}

    by_month_counts = Counter()
    by_month_minutes = Counter()
    by_month_days = defaultdict(set)

    for m in movies:
        mid = str(m["movie"]["ids"].get("tmdb"))
        rt = tc.get("movies", {}).get(mid, {}).get("runtime") or 0
        total_minutes += rt
        dt_m = to_wib(m["watched_at"])
        d = dt_m.day
        by_day_minutes[d] = by_day_minutes.get(d, 0) + rt
        by_day_counts[d] = by_day_counts.get(d, 0) + 1
        by_month_counts[dt_m.month] += 1
        by_month_minutes[dt_m.month] += rt
        by_month_days[dt_m.month].add(d)

    for e in eps:
        sid = str(e["show"]["ids"].get("tmdb"))
        s, ep = e["episode"]["season"], e["episode"]["number"]
        k = f"{sid}-s{s}e{ep}"
        rt = tc.get("episodes", {}).get(k, {}).get("runtime")
        if not rt:
            rt = tc.get("show_avg", {}).get(sid) or 0
        total_minutes += rt
        dt_e = to_wib(e["watched_at"])
        d = dt_e.day
        by_day_minutes[d] = by_day_minutes.get(d, 0) + rt
        by_day_counts[d] = by_day_counts.get(d, 0) + 1
        by_month_counts[dt_e.month] += 1
        by_month_minutes[dt_e.month] += rt
        by_month_days[dt_e.month].add(d)

    # Average rating
    all_month_ratings = [r["rating"] for r in rate if "rating" in r]
    avg_rate = sum(all_month_ratings) / len(all_month_ratings) if all_month_ratings else 0

    # Top 5 Genres, Top 5 Studios & Top 5 Origin Countries (grouped by show)
    genre_counter = Counter()
    country_counter = Counter()
    studio_counter = Counter()
    creator_counter = Counter()
    meta_m = tc.get("meta_movies", {})
    meta_s = tc.get("meta_shows", {})

    # 1. Unique movies
    for m in movies:
        mid = str(m["movie"]["ids"].get("tmdb"))
        mm = meta_m.get(mid, {})
        for g in mm.get("genres", []):
            genre_counter[g] += 1
        for c in mm.get("countries", []):
            c_name = COUNTRY_NAMES.get(c, c)
            country_counter[c_name] += 1
        for s in mm.get("studios", []):
            studio_counter[s] += 1
        c_people = list(dict.fromkeys(mm.get("directors", []) + mm.get("writers", [])))
        for p in c_people:
            creator_counter[p] += 1

    # 2. Shows (grouped by show title)
    for t, tmdb_id in show_tmdb.items():
        sid = str(tmdb_id)
        sm = meta_s.get(sid, {})
        for g in sm.get("genres", []):
            genre_counter[g] += 1
        for c in sm.get("countries", []):
            c_name = COUNTRY_NAMES.get(c, c)
            country_counter[c_name] += 1
        for s in sm.get("studios", []):
            studio_counter[s] += 1
        s_people = sm.get("creators", [])
        if not s_people:
            s_people = sm.get("writers", [])[:2] or sm.get("directors", [])[:2]
        s_people = list(dict.fromkeys(s_people))
        for p in s_people:
            creator_counter[p] += 1

    top_genres = genre_counter.most_common(5)
    top_countries = country_counter.most_common(5)
    top_studios = studio_counter.most_common(5)
    top_creators = creator_counter.most_common(5)

    peak_month = None
    if is_yearly and by_month_counts:
        pm = max(by_month_counts.keys(), key=lambda x: (by_month_counts[x], by_month_minutes[x]))
        peak_month = {
            "month": pm,
            "watches": by_month_counts[pm],
            "minutes": by_month_minutes[pm],
            "active_days": len(by_month_days[pm]),
        }

    return {
        "is_yearly": is_yearly,
        "year": year,
        "month": month,
        "hist": hist,
        "total": len(hist),
        "movies": len(movies),
        "episodes": len(eps),
        "shows": len(shows_eps),
        "active_days": len(by_day_counts),
        "top_shows": top_shows,
        "top_movies": top_movies,
        "top_genres": top_genres,
        "top_countries": top_countries,
        "top_studios": top_studios,
        "top_creators": top_creators,
        "peak_month": peak_month,
        "by_day_minutes": by_day_minutes,
        "by_day_counts": by_day_counts,
        "avg_rating": avg_rate,
        "total_minutes": total_minutes,
        "rate_map": rate_map,
        "tc": tc,
        "username": active_user,
    }


# ==============================================================================
# EXPORT WATCH HISTORY TO CSV
# ==============================================================================
def export_csv(data, out_csv_path):
    rows = []
    rate_map = data.get("rate_map", {})
    tc = data.get("tc", {})

    for idx, h in enumerate(data["hist"], start=1):
        dt = to_wib(h["watched_at"])
        w_type = h["type"]
        if w_type == "movie":
            title = h["movie"]["title"]
            year = h["movie"].get("year", "")
            mid = str(h["movie"]["ids"].get("tmdb"))
            runtime = tc.get("movies", {}).get(mid, {}).get("runtime") or 0
            rating = rate_map.get(("movie", h["movie"]["ids"].get("tmdb")), "")
            details = f"Movie ({year})"
        else:
            show_title = h["show"]["title"]
            s = h["episode"]["season"]
            ep = h["episode"]["number"]
            ep_title = h["episode"].get("title", "")
            sid = str(h["show"]["ids"].get("tmdb"))
            k = f"{sid}-s{s}e{ep}"
            runtime = tc.get("episodes", {}).get(k, {}).get("runtime")
            if not runtime:
                runtime = tc.get("show_avg", {}).get(sid) or 0
            rating = rate_map.get(("episode", h["show"]["ids"].get("tmdb"), s, ep), "")
            title = show_title
            details = f"S{s:02d}E{ep:02d} - {ep_title}"

        rows.append({
            "No": idx,
            "Watched At (WIB)": dt.strftime("%Y-%m-%d %H:%M"),
            "Type": w_type.capitalize(),
            "Title": title,
            "Episode / Detail": details,
            "Runtime (Minutes)": runtime,
            "Rating": rating,
            "Action": h.get("action", ""),
            "Trakt ID": h.get("id", ""),
        })

    if rows:
        with open(out_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)


# ==============================================================================
# NEO-BRUTALISM TRANSPARENT COLOR PALETTE
# ==============================================================================
BG_CREAM = (245, 240, 230)
BLACK = (16, 16, 16, 255)
BLACK_SHADOW = (16, 16, 16, 175)

WHITE_TRANSPARENT = (255, 255, 255, 135)
LIME_TRANSPARENT = (195, 255, 60, 145)
CYAN_TRANSPARENT = (88, 226, 255, 145)
YELLOW_TRANSPARENT = (255, 224, 60, 155)
CORAL_TRANSPARENT = (255, 92, 57, 165)
PURPLE_TRANSPARENT = (212, 178, 255, 145)
GRAY_BAR = (160, 155, 145, 140)

BORDER = 5
SHADOW_OFFSET = (8, 8)


def draw_brutal_box(draw, xy, fill=WHITE_TRANSPARENT, outline=BLACK, width=BORDER,
                     shadow_offset=SHADOW_OFFSET, radius=0):
    x0, y0, x1, y1 = xy
    sx, sy = shadow_offset
    if sx != 0 or sy != 0:
        if radius > 0:
            draw.rounded_rectangle([x0 + sx, y0 + sy, x1 + sx, y1 + sy],
                                   radius=radius, fill=BLACK_SHADOW)
        else:
            draw.rectangle([x0 + sx, y0 + sy, x1 + sx, y1 + sy], fill=BLACK_SHADOW)

    if radius > 0:
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius,
                               fill=fill, outline=outline, width=width)
    else:
        draw.rectangle([x0, y0, x1, y1], fill=fill, outline=outline, width=width)


def fit_text_width(draw, text, font_name, max_size, min_size, max_w):
    size = max_size
    while size > min_size:
        f = get_font(font_name, size)
        b = draw.textbbox((0, 0), text, font=f)
        if b[2] - b[0] <= max_w:
            return f
        size -= 2
    return get_font(font_name, min_size)


def fit_text_and_truncate(draw, text, font_name, max_size, min_size, max_w):
    size = max_size
    while size >= min_size:
        f = get_font(font_name, size)
        b = draw.textbbox((0, 0), text, font=f)
        if b[2] - b[0] <= max_w:
            return f, text
        size -= 1
    f = get_font(font_name, min_size)
    cur = text
    while len(cur) > 3:
        cur = cur[:-1]
        b = draw.textbbox((0, 0), cur + "...", font=f)
        if b[2] - b[0] <= max_w:
            return f, cur + "..."
    return f, text


# ==============================================================================
# POSTER & BACKDROP COLLAGE GENERATOR (2x5: Top 5 Movies & Top 5 Shows)
# ==============================================================================
def get_collage_items(data):
    """Collects and downloads backdrop images and official PNG logos for Top 5 Movies and Top 5 Shows.
    Falls back to poster if backdrop is unavailable.
    If no logo is found, logo_path is set to None.
    Assets are downloaded in parallel via TMDB API if not already cached locally.
    """
    os.makedirs(POSTERS_DIR, exist_ok=True)
    os.makedirs(BACKDROPS_DIR, exist_ok=True)
    os.makedirs(LOGOS_DIR, exist_ok=True)
    api_key = get_tmdb_api_key()

    top_m = [("movie", m[4]) for m in data.get("top_movies", [])[:5] if len(m) > 4 and m[4]]
    top_s = [("tv", s[3]) for s in data.get("top_shows", [])[:5] if len(s) > 3 and s[3]]

    # Arrange alternately (M1, S1, M2, S2, ...)
    combined = []
    idx_m, idx_s = 0, 0
    while idx_m < len(top_m) or idx_s < len(top_s):
        if idx_m < len(top_m):
            combined.append(top_m[idx_m])
            idx_m += 1
        if idx_s < len(top_s):
            combined.append(top_s[idx_s])
            idx_s += 1

    # If collage has fewer than 10 items, cycle back from item #1 (index 0)
    slot_items = []
    if combined:
        for i in range(10):
            slot_items.append(combined[i % len(combined)])

    # List of unique items to fetch
    unique_items = list({itm for itm in slot_items if itm is not None})

    def fetch_item_assets(item):
        item_type, tmdb_id = item
        bg_path = os.path.join(BACKDROPS_DIR, f"{item_type}_{tmdb_id}.jpg")
        p_path = os.path.join(POSTERS_DIR, f"{item_type}_{tmdb_id}.jpg")
        logo_path = os.path.join(LOGOS_DIR, f"{item_type}_{tmdb_id}.png")

        have_bg = os.path.exists(bg_path) and os.path.getsize(bg_path) > 1000
        have_p = os.path.exists(p_path) and os.path.getsize(p_path) > 1000
        have_logo = os.path.exists(logo_path) and os.path.getsize(logo_path) > 1000

        # Fetch from TMDB API if needed
        if (not have_bg or not have_p or not have_logo) and api_key:
            endpoint = (
                f"https://api.themoviedb.org/3/{item_type}/{tmdb_id}"
                f"?api_key={api_key}&append_to_response=images"
            )
            try:
                req = urllib.request.Request(
                    endpoint,
                    headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
                )
                with urllib.request.urlopen(req, timeout=12) as resp:
                    res = json.loads(resp.read().decode())
                    bg = res.get("backdrop_path")
                    p = res.get("poster_path")
                    logos = res.get("images", {}).get("logos", [])

                    if not have_bg and bg:
                        urllib.request.urlretrieve(f"https://image.tmdb.org/t/p/w780{bg}", bg_path)
                    if not have_p and p:
                        urllib.request.urlretrieve(f"https://image.tmdb.org/t/p/w780{p}", p_path)

                    if not have_logo:
                        png_logos = [l for l in logos if l.get("file_path", "").endswith(".png")]
                        if png_logos:
                            orig_lang = res.get("original_language")
                            # Priority 1: English, Indonesian, or untagged
                            pref_logos = [
                                l for l in png_logos
                                if l.get("iso_639_1") in ("en", "id", None, "")
                            ]
                            # Priority 2: Original spoken language (e.g. ja, zh, ko, fr, etc.)
                            orig_logos = [
                                l for l in png_logos
                                if orig_lang and l.get("iso_639_1") == orig_lang
                            ]

                            candidates = pref_logos if pref_logos else (orig_logos if orig_logos else png_logos)
                            best_entry = max(
                                candidates,
                                key=lambda x: (x.get("vote_average", 0), x.get("vote_count", 0))
                            )
                            best_logo = best_entry["file_path"]
                            urllib.request.urlretrieve(f"https://image.tmdb.org/t/p/w500{best_logo}", logo_path)
            except Exception:
                pass

        # Select primary image: backdrop -> poster -> fallback
        final_img = None
        if os.path.exists(bg_path) and os.path.getsize(bg_path) > 1000:
            final_img = bg_path
        elif os.path.exists(p_path) and os.path.getsize(p_path) > 1000:
            final_img = p_path
        elif os.path.exists(POSTER_FALLBACK):
            final_img = POSTER_FALLBACK

        final_logo = logo_path if (os.path.exists(logo_path) and os.path.getsize(logo_path) > 1000) else None
        return item, {"image_path": final_img, "logo_path": final_logo}

    asset_map = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_item_assets, itm) for itm in unique_items]
        for f in futures:
            itm_key, res = f.result()
            if res.get("image_path"):
                asset_map[itm_key] = res

    # Assemble exactly 10 slots
    results = []
    for itm in slot_items:
        if itm and itm in asset_map:
            results.append(asset_map[itm])
        elif os.path.exists(POSTER_FALLBACK):
            results.append({"image_path": POSTER_FALLBACK, "logo_path": None})

    # Emergency fallback if no images found
    if not results and os.path.exists(POSTER_FALLBACK):
        results = [{"image_path": POSTER_FALLBACK, "logo_path": None}] * 10

    return results


def create_collage_background(collage_items):
    """Creates a 2x5 collage background (Top 5 Movies & Top 5 Shows) with landscape backdrops + centered logos:
    - Overlapping grid cells with smooth cosine alpha mask
    - Centered official PNG logo with soft drop shadow
    - Gaussian blur (2.0px) to seamlessly blend boundaries
    - Subtle dark tint and 72% blend over Neo-Brutalism cream background
    """
    base_bg = Image.new("RGBA", (W, H), (*BG_CREAM, 255))
    if not collage_items:
        return base_bg

    cols, rows = 2, 5
    overlap_x = 100
    overlap_y = 80
    cw = (W + (cols - 1) * overlap_x) // cols
    ch = (H + (rows - 1) * overlap_y) // rows

    # Generate cosine smooth mask for seamless edge blending
    try:
        x = np.linspace(-1, 1, cw)
        y = np.linspace(-1, 1, ch)
        xx, yy = np.meshgrid(x, y)
        dist_x = np.abs(xx)
        dist_y = np.abs(yy)
        fade_w = 0.35
        ax = np.clip((1.0 - dist_x) / fade_w, 0, 1)
        ay = np.clip((1.0 - dist_y) / fade_w, 0, 1)
        ax = 0.5 - 0.5 * np.cos(np.pi * ax)
        ay = 0.5 - 0.5 * np.cos(np.pi * ay)
        alpha = (ax * ay * 255).astype(np.uint8)
        cell_mask = Image.fromarray(alpha, mode="L")
    except Exception:
        cell_mask = Image.new("L", (cw, ch), 255)

    canvas = Image.new("RGBA", (W, H), (18, 20, 24, 255))
    total_slots = cols * rows
    items = list(collage_items)
    while len(items) < total_slots:
        items.extend(collage_items)
    items = items[:total_slots]

    for idx, itm in enumerate(items):
        r = idx // cols
        c = idx % cols
        x0 = c * (cw - overlap_x)
        y0 = r * (ch - overlap_y)

        img_path = itm.get("image_path")
        logo_path = itm.get("logo_path")

        try:
            cell_img = Image.open(img_path).convert("RGBA")
            iw, ih = cell_img.size
            scale = max(cw / iw, ch / ih)
            nw, nh = int(iw * scale), int(ih * scale)
            cell_img = cell_img.resize((nw, nh), Image.Resampling.LANCZOS)
            cx, cy = (nw - cw) // 2, (nh - ch) // 2
            cell_img = cell_img.crop((cx, cy, cx + cw, cy + ch))

            # Center official logo if file exists
            if logo_path and os.path.exists(logo_path):
                try:
                    logo = Image.open(logo_path).convert("RGBA")
                    lw, lh = logo.size
                    max_lw = int(cw * 0.68)
                    max_lh = int(ch * 0.44)
                    lscale = min(max_lw / lw, max_lh / lh, 1.0)
                    flw, flh = int(lw * lscale), int(lh * lscale)
                    if flw > 10 and flh > 10:
                        logo = logo.resize((flw, flh), Image.Resampling.LANCZOS)
                        lx = (cw - flw) // 2
                        ly = (ch - flh) // 2

                        # Soft drop shadow for logo
                        shadow_layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                        l_alpha = logo.split()[3]
                        shadow_mask = l_alpha.filter(ImageFilter.GaussianBlur(6))
                        shadow_black = Image.new("RGBA", (flw, flh), (0, 0, 0, 210))
                        shadow_black.putalpha(shadow_mask)
                        shadow_layer.alpha_composite(shadow_black, (lx, ly + 3))

                        cell_img.alpha_composite(shadow_layer)
                        cell_img.alpha_composite(logo, (lx, ly))
                except Exception:
                    pass

            cell_img.putalpha(cell_mask)
            canvas.alpha_composite(cell_img, (x0, y0))
        except Exception:
            pass

    # 1. Soft blur to blend cell boundaries
    blended = canvas.filter(ImageFilter.GaussianBlur(2.0))

    # 2. Subtle dark cinematic tint for contrast
    dark_tint = Image.new("RGBA", (W, H), (12, 14, 18, 50))
    blended = Image.alpha_composite(blended, dark_tint)

    # 3. Blend with Neo-Brutalism cream background
    final_bg = Image.blend(base_bg, blended, 0.72)
    return final_bg


# ==============================================================================
# MAIN INFOGRAPHIC RENDERING
# ==============================================================================
def render(data):
    collage_items = get_collage_items(data)
    canvas = create_collage_background(collage_items)

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    M = 64
    BW = W - 2 * M  # 952 px

    # 1. TOP BAR: Header Strip
    t_box = [M, 80, W - M, 144]
    draw_brutal_box(d, t_box, fill=YELLOW_TRANSPARENT, outline=BLACK, width=4,
                    shadow_offset=(6, 6), radius=12)

    box_center_y = (t_box[1] + t_box[3]) / 2
    is_yearly = data.get("is_yearly", False)
    top_label = "TRAKT.TV YEARLY RECAP" if is_yearly else "TRAKT.TV MONTHLY RECAP"
    d.text((M + 24, box_center_y), top_label,
           font=get_font("impact", 32), fill=BLACK, anchor="lm")
    raw_user = data.get("username", "TRAKT.TV").strip()
    user_handle = f"@{raw_user.lstrip('@').upper()}"
    f_user = fit_text_width(d, user_handle, "arialb", 26, 16, 380)
    d.text((W - M - 24, box_center_y), user_handle,
           font=f_user, fill=BLACK, anchor="rm")

    # 2. TITLE SECTION (Dynamic: Year or Month & Year)
    if is_yearly:
        title_text = f"YEAR {data['year']}"
    else:
        title_text = f"{MONTH_NAMES.get(data['month'], 'MONTH')} {data['year']}"
    title_y = 164
    d.text((M + 3, title_y + 3), title_text, font=get_font("impact", 112), fill=(255, 255, 255, 200))
    d.text((M, title_y), title_text, font=get_font("impact", 112), fill=BLACK)

    # Dynamic line position calculated from actual text bounding box with clean breathing room
    b_title = d.textbbox((M, title_y), title_text, font=get_font("impact", 112))
    line_y = b_title[3] + 16
    d.line([(M, line_y), (W - M, line_y)], fill=BLACK, width=5)

    # 3. HERO STAT: Total Watches + Total Time
    hero_y = line_y + 20
    hero_h = 220
    gap = 20
    w_left = int(BW * 0.54)

    # Left Box: TOTAL LOGS
    box_left = [M, hero_y, M + w_left, hero_y + hero_h]
    draw_brutal_box(d, box_left, fill=LIME_TRANSPARENT, outline=BLACK, width=BORDER,
                    shadow_offset=(8, 8), radius=16)

    d.text((M + 28, hero_y + 18), "TOTAL LOGS", font=get_font("bahnschrift", 24), fill=BLACK)
    tot_str = str(data["total"])
    f_tot = get_font("impact", 136 if len(tot_str) <= 2 else (124 if len(tot_str) == 3 else 108))
    d.text((M + 24, hero_y + 36), tot_str, font=f_tot, fill=BLACK)
    bbox_tot = d.textbbox((M + 24, hero_y + 36), tot_str, font=f_tot)
    watches_x = max(M + 210, bbox_tot[2] + 16)
    d.text((watches_x, hero_y + 78), "WATCHES", font=get_font("impact", 48), fill=BLACK)
    d.text((watches_x, hero_y + 134), f"{data['movies']} MOVIES · {data['episodes']} EPS",
           font=get_font("arialb", 20), fill=BLACK)

    # Right Box: TOTAL TIME
    box_right = [M + w_left + gap, hero_y, W - M, hero_y + hero_h]
    draw_brutal_box(d, box_right, fill=CYAN_TRANSPARENT, outline=BLACK, width=BORDER,
                    shadow_offset=(8, 8), radius=16)

    d.text((box_right[0] + 24, hero_y + 18), "TOTAL TIME", font=get_font("bahnschrift", 24), fill=BLACK)
    tot_min = data["total_minutes"]
    hours = tot_min // 60
    rem_min = tot_min % 60
    d.text((box_right[0] + 22, hero_y + 54), f"{hours}h {rem_min}m",
           font=get_font("impact", 76), fill=BLACK)
    d.text((box_right[0] + 24, hero_y + 152), f"={tot_min:,} MINUTES",
           font=get_font("mono", 30), fill=BLACK)

    # 4. STAT CARDS GRID (Movies, Episodes, Shows, Avg Rating)
    grid_y = hero_y + hero_h + 20  # 564
    grid_h = 104
    col_w = (BW - 3 * 16) // 4
    stats_data = [
        (str(data["movies"]), "MOVIES", WHITE_TRANSPARENT),
        (str(data["episodes"]), "EPISODES", YELLOW_TRANSPARENT),
        (str(data["shows"]), "SHOWS", WHITE_TRANSPARENT),
        (f"{data['avg_rating']:.1f}" if data['avg_rating'] else "0.0", "AVG RATING", CORAL_TRANSPARENT),
    ]
    for i, (val, label, bg_col) in enumerate(stats_data):
        cx0 = M + i * (col_w + 16)
        cx1 = cx0 + col_w
        cbox = [cx0, grid_y, cx1, grid_y + grid_h]
        fg_col = (255, 255, 255, 255) if bg_col == CORAL_TRANSPARENT else BLACK
        draw_brutal_box(d, cbox, fill=bg_col, outline=BLACK, width=4,
                        shadow_offset=(5, 5), radius=12)
        d.text((cx0 + 16, grid_y + 12), val, font=get_font("impact", 46), fill=fg_col)
        d.text((cx0 + 16, grid_y + 66), label, font=get_font("bahnschrift", 19),
               fill=(255, 255, 255, 255) if bg_col == CORAL_TRANSPARENT else (20, 20, 20, 255))

    m_colors = [CORAL_TRANSPARENT, YELLOW_TRANSPARENT, CYAN_TRANSPARENT, LIME_TRANSPARENT, PURPLE_TRANSPARENT]
    pill_w = 98
    pill_x0 = W - M - 24 - pill_w
    c_badge_w = 46
    c_badge_x = pill_x0 - c_badge_w - 10
    max_label_w = c_badge_x - (M + 68) - 12

    if is_yearly:
        strip_y = grid_y + grid_h + 24   # 692
        strip_h = 66
        mov_y = strip_y + strip_h + 24   # 782
        mov_h = 340
        shows_y = mov_y + mov_h + 24     # 1146
        shows_h = 340
        bottom_y = shows_y + shows_h + 24 # 1510
        bottom_h = 340
    else:
        chart_box_y = grid_y + grid_h + 20  # 688
        chart_box_h = 384
        mov_y = chart_box_y + chart_box_h + 20  # 1092
        mov_h = 340
        shows_y = mov_y + mov_h + 20  # 1452
        shows_h = 340

    # 5. DAILY WATCHED MINUTES (Monthly Mode Only)
    if not is_yearly:
        c_box = [M, chart_box_y, W - M, chart_box_y + chart_box_h]
        draw_brutal_box(d, c_box, fill=WHITE_TRANSPARENT, outline=BLACK, width=BORDER,
                        shadow_offset=(8, 8), radius=16)

        d.text((M + 24, chart_box_y + 20), "DAILY WATCHED MINUTES",
               font=get_font("impact", 30), fill=BLACK)

        col_w = 120
        col_gap = 10
        grid_w = 7 * col_w + 6 * col_gap  # 900 px
        grid_left = M + (BW - grid_w) // 2  # 90 px
        header_y = chart_box_y + 72

        dows = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
        for c, dow in enumerate(dows):
            cx = grid_left + c * (col_w + col_gap) + col_w // 2
            d.text((cx, header_y), dow, font=get_font("bahnschrift", 16),
                   fill=(90, 90, 90, 255), anchor="mm")

        # Dynamically calculate number of weeks and cell height
        year, month = data["year"], data["month"]
        start_dow = date(year, month, 1).weekday()  # Monday = 0
        _, num_days = calendar.monthrange(year, month)
        weeks_count = (start_dow + num_days + 6) // 7

        grid_start_y = chart_box_y + 94
        if weeks_count == 6:
            cell_h = 40
            row_gap = 6
        elif weeks_count == 4:
            cell_h = 60
            row_gap = 8
        else:  # 5 weeks
            cell_h = 48
            row_gap = 8

        by_day_min = data.get("by_day_minutes", {})
        max_m = max(by_day_min.values()) if by_day_min and any(v > 0 for v in by_day_min.values()) else 0

        for day in range(1, num_days + 1):
            idx = (day - 1) + start_dow
            r = idx // 7
            c = idx % 7
            bx0 = grid_left + c * (col_w + col_gap)
            by0 = grid_start_y + r * (cell_h + row_gap)
            bx1 = bx0 + col_w
            by1 = by0 + cell_h

            m_count = by_day_min.get(day, 0)
            if m_count == 0:
                bg_col = (255, 255, 255, 80)
                date_col = (130, 130, 130, 255)
                text_col = (160, 160, 160, 255)
                cnt_font = get_font("impact", 20 if cell_h >= 46 else 18)
                label_text = "0m"
            else:
                # Dynamic scaling proportional to maximum minutes
                ratio = (m_count / max_m) if max_m > 0 else 0
                if ratio >= 0.75:  # Peak intensity (Top 25% daily volume)
                    bg_col = CORAL_TRANSPARENT
                    date_col = (20, 20, 20, 255)
                    text_col = BLACK
                    cnt_font = get_font("impact", 23 if cell_h >= 46 else 20)
                elif ratio >= 0.50:  # High intensity (50% - 75%)
                    bg_col = YELLOW_TRANSPARENT
                    date_col = (40, 40, 40, 255)
                    text_col = BLACK
                    cnt_font = get_font("impact", 22 if cell_h >= 46 else 19)
                elif ratio >= 0.25:  # Medium intensity (25% - 50%)
                    bg_col = CYAN_TRANSPARENT
                    date_col = (40, 40, 40, 255)
                    text_col = BLACK
                    cnt_font = get_font("impact", 22 if cell_h >= 46 else 19)
                else:  # Low intensity (< 25%)
                    bg_col = LIME_TRANSPARENT
                    date_col = (50, 50, 50, 255)
                    text_col = BLACK
                    cnt_font = get_font("impact", 22 if cell_h >= 46 else 19)
                label_text = f"{m_count}m"

            d.rounded_rectangle([bx0, by0, bx1, by1], radius=6, fill=bg_col,
                                outline=BLACK, width=2)
            d.text((bx0 + 8, by0 + 4), str(day), font=get_font("bahnschrift", 12 if cell_h < 45 else 13), fill=date_col)
            d.text((bx0 + col_w // 2, by0 + cell_h // 2 + 1), label_text,
                   font=cnt_font, fill=text_col, anchor="mm")

    # 5.5 PEAK MONTH STRIP (Yearly Mode Only)
    if is_yearly and data.get("peak_month"):
        pm_data = data["peak_month"]
        strip_box = [M, strip_y, W - M, strip_y + strip_h]
        draw_brutal_box(d, strip_box, fill=WHITE_TRANSPARENT, outline=BLACK, width=BORDER,
                        shadow_offset=(8, 8), radius=14)

        cy = strip_y + strip_h // 2
        tag_w = 148
        tag_box = [M + 24, strip_y + 14, M + 24 + tag_w, strip_y + strip_h - 14]
        draw_brutal_box(d, tag_box, fill=CORAL_TRANSPARENT, outline=BLACK, width=2,
                        shadow_offset=(2, 2), radius=8)
        d.text((tag_box[0] + tag_w // 2, cy), "PEAK MONTH",
               font=get_font("impact", 20), fill=BLACK, anchor="mm")

        m_name = MONTH_NAMES.get(pm_data["month"], "MONTH")
        d.text((tag_box[2] + 16, cy), m_name,
               font=get_font("impact", 30), fill=BLACK, anchor="lm")

        pills = [
            (f"{pm_data['watches']} WATCHES", YELLOW_TRANSPARENT),
            (f"{pm_data['minutes'] // 60}h {pm_data['minutes'] % 60}m", CYAN_TRANSPARENT),
            (f"{pm_data['active_days']} ACTIVE DAYS", LIME_TRANSPARENT),
        ]
        cur_rx = W - M - 24
        f_pill = get_font("impact", 17)
        for p_text, p_bg in reversed(pills):
            p_bbox = d.textbbox((0, 0), p_text, font=f_pill)
            pw = (p_bbox[2] - p_bbox[0]) + 20
            p_bx0 = cur_rx - pw
            p_box = [p_bx0, strip_y + 14, cur_rx, strip_y + strip_h - 14]
            draw_brutal_box(d, p_box, fill=p_bg, outline=BLACK, width=2,
                            shadow_offset=(2, 2), radius=6)
            d.text((p_bx0 + pw // 2, cy), p_text, font=f_pill, fill=BLACK, anchor="mm")
            cur_rx = p_bx0 - 10

    # 6. TOP MOVIES (Fixed 5 Slots, Rank on Left, Runtime on Right)
    m_box = [M, mov_y, W - M, mov_y + mov_h]
    draw_brutal_box(d, m_box, fill=WHITE_TRANSPARENT, outline=BLACK, width=BORDER,
                    shadow_offset=(8, 8), radius=16)

    d.text((M + 24, mov_y + 20), "TOP MOVIES",
           font=get_font("impact", 30), fill=BLACK)

    top_m = data["top_movies"][:5]
    m_row_start = mov_y + 64
    m_row_h = 52

    for i in range(5):
        mry = m_row_start + i * m_row_h
        if i < len(top_m):
            m_title, m_runtime, m_rate, m_yr, m_mid = top_m[i]

            # 1. Rank Badge (Left)
            badge_box = [M + 24, mry, M + 24 + 32, mry + 32]
            draw_brutal_box(d, badge_box, fill=m_colors[i], outline=BLACK, width=2,
                            shadow_offset=(2, 2), radius=6)
            d.text((M + 40, mry + 16), str(i + 1), font=get_font("impact", 20), fill=BLACK, anchor="mm")

            # 2. Movie Title + (Year)
            label = f"{m_title} ({m_yr})" if m_yr else m_title
            f_lbl = fit_text_width(d, label, "arialb", 22, 16, max_label_w)
            d.text((M + 68, mry + 3), label, font=f_lbl, fill=BLACK)

            # 3. Origin Country Code Badge (if available)
            tc_meta_m = data.get("tc", {}).get("meta_movies", {})
            m_countries = tc_meta_m.get(str(m_mid), {}).get("countries", [])
            m_ccode = m_countries[0].upper() if m_countries and m_countries[0] else ""
            if m_ccode:
                c_pill_box = [c_badge_x, mry, c_badge_x + c_badge_w, mry + 34]
                draw_brutal_box(d, c_pill_box, fill=WHITE_TRANSPARENT, outline=BLACK, width=2,
                                shadow_offset=(3, 3), radius=8)
                d.text((c_badge_x + c_badge_w // 2, mry + 17), m_ccode,
                       font=get_font("impact", 18), fill=BLACK, anchor="mm")

            # 4. Total Runtime (Right Pill)
            h = m_runtime // 60
            rem_m = m_runtime % 60
            runtime_str = f"{h}h {rem_m:02d}m"
            pill_box = [pill_x0, mry, pill_x0 + pill_w, mry + 34]
            draw_brutal_box(d, pill_box, fill=m_colors[i], outline=BLACK, width=2,
                            shadow_offset=(3, 3), radius=8)
            d.text((pill_x0 + pill_w // 2, mry + 17), runtime_str,
                   font=get_font("impact", 21), fill=BLACK, anchor="mm")

            # Divider line between movies
            if i < 4 and i < len(top_m) - 1:
                d.line([(M + 24, mry + 44), (W - M - 24, mry + 44)], fill=GRAY_BAR, width=2)

    # 7. TOP TV SHOWS (Fixed 5 Slots, Rank on Left, Runtime on Right)
    s_box = [M, shows_y, W - M, shows_y + shows_h]
    draw_brutal_box(d, s_box, fill=WHITE_TRANSPARENT, outline=BLACK, width=BORDER,
                    shadow_offset=(8, 8), radius=16)

    d.text((M + 24, shows_y + 20), "TOP TV SHOWS",
           font=get_font("impact", 30), fill=BLACK)

    top_shows = data["top_shows"][:5]
    row_start_y = shows_y + 64
    row_height = 52
    colors = [CORAL_TRANSPARENT, YELLOW_TRANSPARENT, CYAN_TRANSPARENT, PURPLE_TRANSPARENT, LIME_TRANSPARENT]

    for i in range(5):
        ry = row_start_y + i * row_height
        if i < len(top_shows):
            s_name, s_runtime, ep_count, s_sid = top_shows[i]

            # 1. Rank Badge (Left)
            badge_box = [M + 24, ry, M + 24 + 32, ry + 32]
            draw_brutal_box(d, badge_box, fill=colors[i], outline=BLACK, width=2,
                            shadow_offset=(2, 2), radius=6)
            d.text((M + 40, ry + 16), str(i + 1), font=get_font("impact", 20), fill=BLACK, anchor="mm")

            # 2. Show Title + (Total Eps)
            label = f"{s_name} ({ep_count} Eps)"
            f_title = fit_text_width(d, label, "arialb", 22, 16, max_label_w)
            d.text((M + 68, ry + 3), label, font=f_title, fill=BLACK)

            # 3. Origin Country Code Badge (if available)
            tc_meta_s = data.get("tc", {}).get("meta_shows", {})
            s_countries = tc_meta_s.get(str(s_sid), {}).get("countries", [])
            s_ccode = s_countries[0].upper() if s_countries and s_countries[0] else ""
            if s_ccode:
                c_pill_box = [c_badge_x, ry, c_badge_x + c_badge_w, ry + 34]
                draw_brutal_box(d, c_pill_box, fill=WHITE_TRANSPARENT, outline=BLACK, width=2,
                                shadow_offset=(3, 3), radius=8)
                d.text((c_badge_x + c_badge_w // 2, ry + 17), s_ccode,
                       font=get_font("impact", 18), fill=BLACK, anchor="mm")

            # 4. Total Runtime (Right Pill)
            h = s_runtime // 60
            rem_m = s_runtime % 60
            runtime_str = f"{h}h {rem_m:02d}m"
            pill_box = [pill_x0, ry, pill_x0 + pill_w, ry + 34]
            draw_brutal_box(d, pill_box, fill=colors[i], outline=BLACK, width=2,
                            shadow_offset=(3, 3), radius=8)
            d.text((pill_x0 + pill_w // 2, ry + 17), runtime_str,
                   font=get_font("impact", 21), fill=BLACK, anchor="mm")

            # Divider line between shows
            if i < 4 and i < len(top_shows) - 1:
                d.line([(M + 24, ry + 44), (W - M - 24, ry + 44)], fill=GRAY_BAR, width=2)

    # 8. YEARLY MODE: TOP GENRES, TOP STUDIOS & TOP COUNTRIES (3 Columns)
    if is_yearly:
        b_box = [M, bottom_y, W - M, bottom_y + bottom_h]
        draw_brutal_box(d, b_box, fill=WHITE_TRANSPARENT, outline=BLACK, width=BORDER,
                        shadow_offset=(8, 8), radius=16)

        inner_x0 = M + 24
        inner_x1 = W - M - 24
        inner_w = inner_x1 - inner_x0  # 904 px
        col_w = inner_w // 3           # 301 px

        div1_x = inner_x0 + col_w      # 389
        div2_x = inner_x0 + col_w * 2  # 690
        d.line([(div1_x, bottom_y + 16), (div1_x, bottom_y + bottom_h - 16)], fill=BLACK, width=2)
        d.line([(div2_x, bottom_y + 16), (div2_x, bottom_y + bottom_h - 16)], fill=BLACK, width=2)

        columns = [
            ("TOP GENRES", data.get("top_genres", [])[:5], inner_x0, div1_x - 14),
            ("TOP STUDIOS", data.get("top_studios", [])[:5], div1_x + 14, div2_x - 14),
            ("TOP COUNTRIES", data.get("top_countries", [])[:5], div2_x + 14, inner_x1),
        ]

        row_start_b = bottom_y + 64
        row_h_b = 52
        pill_w_col = 44
        badge_w = 32

        for col_title, items, c_start, c_end in columns:
            col_x0 = c_start
            col_x1 = c_end
            d.text((col_x0, bottom_y + 20), col_title, font=get_font("impact", 28), fill=BLACK)

            max_text_w = (col_x1 - col_x0) - badge_w - 10 - pill_w_col - 8

            for i in range(5):
                ry = row_start_b + i * row_h_b
                if i < len(items):
                    name, cnt = items[i]
                    badge_box = [col_x0, ry, col_x0 + badge_w, ry + badge_w]
                    draw_brutal_box(d, badge_box, fill=m_colors[i], outline=BLACK, width=2,
                                    shadow_offset=(2, 2), radius=6)
                    d.text((col_x0 + 16, ry + 16), str(i + 1),
                           font=get_font("impact", 20), fill=BLACK, anchor="mm")

                    f_item, item_disp = fit_text_and_truncate(d, name, "arialb", 20, 14, max_text_w)
                    d.text((col_x0 + badge_w + 10, ry + 3), item_disp, font=f_item, fill=BLACK)

                    px = col_x1 - pill_w_col
                    pill_box = [px, ry, px + pill_w_col, ry + 34]
                    draw_brutal_box(d, pill_box, fill=m_colors[i], outline=BLACK, width=2,
                                    shadow_offset=(2, 2), radius=6)
                    d.text((px + pill_w_col // 2, ry + 17), str(cnt),
                           font=get_font("impact", 20), fill=BLACK, anchor="mm")

                    if i < 4 and i < len(items) - 1:
                        d.line([(col_x0, ry + 44), (col_x1, ry + 44)], fill=GRAY_BAR, width=2)

    final_img = Image.alpha_composite(canvas, overlay)
    return final_img.convert("RGB")


# ==============================================================================
# CLI ENTRY POINT
# ==============================================================================
def parse_period(period_str):
    """Supports formats:
    - 'YYYY' (e.g. 2026) -> Yearly recap mode
    - 'YYYY-MM', 'YYYY/MM', or 'YYYY-M' -> Monthly recap mode
    """
    clean = period_str.strip().replace("/", "-")
    if clean.isdigit() and len(clean) == 4:
        return int(clean), None
    parts = clean.split("-")
    if len(parts) == 2:
        return int(parts[0]), int(parts[1])
    raise ValueError(f"Invalid period format: '{period_str}'. Use format YYYY (e.g. 2026) or YYYY-MM (e.g. 2026-10)")


def main():
    parser = argparse.ArgumentParser(description="Trakt.tv Recap Infographic Generator (Instagram Story)")
    parser.add_argument("period_pos", nargs="?", default=None,
                        help="Watch period in YYYY or YYYY-MM format (e.g. 2026 or 2026-10)")
    parser.add_argument("--period", "-p", "--periode", dest="period_flag", default=None,
                        help="Watch period in YYYY or YYYY-MM format (e.g. 2026 or 2026-10)")
    parser.add_argument("--month", "-m", type=int, default=None, help="Month number (1-12)")
    parser.add_argument("--year", "-y", type=int, default=None, help="Year (e.g. 2026)")
    parser.add_argument("--username", "-u", dest="username", default=None,
                        help="Custom Trakt username to display on header (e.g. --username=fikimaul)")

    args = parser.parse_args()

    # Determine period
    raw_p = args.period_flag or args.period_pos
    if raw_p:
        year, month = parse_period(raw_p)
    elif args.year and args.month:
        year, month = args.year, args.month
    elif args.year and not args.month:
        year, month = args.year, None
    else:
        # Default if no arguments provided: 2026-10
        year, month = 2026, 10

    print("=" * 60)
    if month is None:
        print(f" GENERATING TRAKT RECAP: YEAR {year}")
    else:
        print(f" GENERATING TRAKT RECAP: {MONTH_NAMES.get(month, '')} {year}")
    print("=" * 60)

    data = gather(year, month, username=args.username)
    if data["total"] == 0:
        period_desc = f"{year}" if month is None else f"{year}-{month:02d}"
        print(f"[Warning] No watch history found for period {period_desc}!")
        print("Please ensure Trakt export files exist in data/ or the current directory.")
        return

    print(f"User           : @{data['username'].lstrip('@')}")
    print(f"Total Watches  : {data['total']} ({data['movies']} Movies, {data['episodes']} Episodes)")
    print(f"Total Time     : {data['total_minutes'] // 60}h {data['total_minutes'] % 60}m ({data['total_minutes']:,} minutes)")
    print(f"Active Days    : {data['active_days']} days")
    if data["top_movies"]:
        print(f"Top 1 Movie    : {data['top_movies'][0][0]} ({data['top_movies'][0][1]}m)")
    if data["top_shows"]:
        print(f"Top 1 Show     : {data['top_shows'][0][0]} ({data['top_shows'][0][1]}m, {data['top_shows'][0][2]} eps)")
    if data["is_yearly"]:
        if data.get("peak_month"):
            pm = data["peak_month"]
            print(f"Peak Month     : {MONTH_NAMES.get(pm['month'], '')} ({pm['watches']} watches, {pm['minutes'] // 60}h {pm['minutes'] % 60}m, {pm['active_days']} active days)")
        if data.get("top_genres"):
            print(f"Top 1 Genre    : {data['top_genres'][0][0]} ({data['top_genres'][0][1]} titles)")
        if data.get("top_studios"):
            print(f"Top 1 Studio   : {data['top_studios'][0][0]} ({data['top_studios'][0][1]} titles)")
        if data.get("top_countries"):
            print(f"Top 1 Country  : {data['top_countries'][0][0]} ({data['top_countries'][0][1]} titles)")

    # Render Image
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if month is None:
        out_img = os.path.join(OUTPUT_DIR, f"recap-{year}.png")
        temp_img = os.path.join(OUTPUT_DIR, f"recap_temp_{year}.png")
    else:
        out_img = os.path.join(OUTPUT_DIR, f"recap-{year}-{month:02d}.png")
        temp_img = os.path.join(OUTPUT_DIR, f"recap_temp_{year}_{month:02d}.png")

    img = render(data)
    img.save(temp_img, "PNG")

    if os.path.exists(out_img):
        try:
            os.remove(out_img)
        except Exception:
            pass
    try:
        os.replace(temp_img, out_img)
    except Exception:
        shutil.copy2(temp_img, out_img)
        try:
            os.remove(temp_img)
        except Exception:
            pass

    print(f"[OK] Infographic successfully saved to: {out_img}")
    print("=" * 60)


if __name__ == "__main__":
    main()
