# Fetch durasi film dan episode Oktober 2026 via TMDB API v3.
# Hasil disimpan ke tmdb_runtime_cache.json.

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
except ImportError:
    pass

API_KEY = os.environ.get("TMDB_API_KEY", "").strip()
if not API_KEY and os.path.exists(os.path.join(BASE, "tmdb_api_key.txt")):
    API_KEY = open(os.path.join(BASE, "tmdb_api_key.txt"), "r", encoding="utf-8").read().strip()

CACHE = os.path.join(BASE, "tmdb_runtime_cache.json")
WIB = timezone(timedelta(hours=7))
YEAR, MONTH = 2026, 10


def get_tmdb(path):
    url = f"https://api.themoviedb.org/3/{path}?api_key={API_KEY}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def to_wib(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(WIB)


def in_month(iso):
    dt = to_wib(iso)
    return dt.year == YEAR and dt.month == MONTH


def gather_items():
    history = []
    for i in range(1, 12):
        p = os.path.join(BASE, f"watched-history-{i}.json")
        if os.path.exists(p):
            history.extend(json.load(open(p, encoding="utf-8")))
    hist = [h for h in history if in_month(h["watched_at"])]

    movies, episodes = [], []
    for h in hist:
        if h["type"] == "movie":
            movies.append({
                "title": h["movie"]["title"],
                "tmdb": h["movie"]["ids"].get("tmdb"),
                "imdb": h["movie"]["ids"].get("imdb"),
            })
        elif h["type"] == "episode":
            episodes.append({
                "show_title": h["show"]["title"],
                "show_tmdb": h["show"]["ids"].get("tmdb"),
                "season": h["episode"]["season"],
                "episode": h["episode"]["number"],
                "ep_title": h["episode"].get("title"),
            })
    return movies, episodes


def main():
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {
        "movies": {}, "episodes": {}, "show_avg": {}}

    movies, episodes = gather_items()
    print(f"Total film: {len(movies)}, episode: {len(episodes)}")

    # 1. Fetch film
    for m in movies:
        mid = str(m["tmdb"])
        if mid not in cache["movies"]:
            print(f"Fetching film: {m['title']} (tmdb {mid})...")
            try:
                data = get_tmdb(f"movie/{mid}")
                rt = data.get("runtime")
                cache["movies"][mid] = {
                    "title": m["title"],
                    "runtime": rt,
                }
            except Exception as e:
                print(f"  Gagal: {e}")
                cache["movies"][mid] = {"title": m["title"], "runtime": None}
            time.sleep(0.2)

    # 2. Fetch episodes (coba per episode dulu, fallback ke show runtime)
    for e in episodes:
        sid = str(e["show_tmdb"])
        s, ep = e["season"], e["episode"]
        ep_key = f"{sid}-s{s}e{ep}"
        if ep_key not in cache["episodes"]:
            print(f"Fetching ep: {e['show_title']} S{s}E{ep} (show tmdb {sid})...")
            try:
                data = get_tmdb(f"tv/{sid}/season/{s}/episode/{ep}")
                rt = data.get("runtime")
                cache["episodes"][ep_key] = {
                    "show": e["show_title"],
                    "season": s,
                    "episode": ep,
                    "runtime": rt,
                }
            except Exception as ex:
                print(f"  Episode detail gagal ({ex}), fallback cek show info...")
                cache["episodes"][ep_key] = {
                    "show": e["show_title"],
                    "season": s,
                    "episode": ep,
                    "runtime": None,
                }
            time.sleep(0.2)

    # 3. Untuk episode yang runtime-nya None/0, coba ambil episode_run_time dari show
    shows_needed = set()
    for e in episodes:
        sid = str(e["show_tmdb"])
        s, ep = e["season"], e["episode"]
        ep_key = f"{sid}-s{s}e{ep}"
        if not cache["episodes"].get(ep_key, {}).get("runtime"):
            shows_needed.add(sid)

    for sid in shows_needed:
        if sid not in cache["show_avg"]:
            print(f"Fetching show runtime fallback for tmdb {sid}...")
            try:
                data = get_tmdb(f"tv/{sid}")
                rts = data.get("episode_run_time", [])
                avg = rts[0] if rts else None
                cache["show_avg"][sid] = avg
            except Exception as ex:
                print(f"  Show info gagal: {ex}")
                cache["show_avg"][sid] = None
            time.sleep(0.2)

    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # Hitung total
    movie_min = 0
    print("\n--- DETAIL FILM ---")
    for m in movies:
        mid = str(m["tmdb"])
        rt = cache["movies"].get(mid, {}).get("runtime") or 0
        movie_min += rt
        print(f"  {rt:>3} min  {m['title']}")

    ep_min = 0
    print("\n--- DETAIL EPISODE ---")
    for e in episodes:
        sid = str(e["show_tmdb"])
        s, ep = e["season"], e["episode"]
        ep_key = f"{sid}-s{s}e{ep}"
        rt = cache["episodes"].get(ep_key, {}).get("runtime")
        if not rt:
            rt = cache["show_avg"].get(sid) or 0
        ep_min += rt
        print(f"  {rt:>3} min  {e['show_title']} S{s}E{ep}")

    total_min = movie_min + ep_min
    jam = total_min // 60
    sisa = total_min % 60
    print("\n====================")
    print(f"Film    : {movie_min} min ({movie_min // 60}j {movie_min % 60}m)")
    print(f"Episode : {ep_min} min ({ep_min // 60}j {ep_min % 60}m)")
    print(f"TOTAL   : {total_min} menit = {jam} jam {sisa} menit ({total_min / 60:.1f} jam)")


if __name__ == "__main__":
    main()
