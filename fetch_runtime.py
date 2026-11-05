# Ambil durasi (runtime) judul yang ditonton Oktober 2026.
# Film: Wikidata (properti P2047) via IMDb ID. Episode: TVMaze via IMDb ID show.
# Hasil disimpan ke runtime_cache.json agar tidak perlu fetch ulang.

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "runtime_cache.json")
UA = "trakt-recap-script/1.0 (personal use; contact: fikimaul)"
WIB = timezone(timedelta(hours=7))
YEAR, MONTH = 2026, 10


def get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode()


def to_wib(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(WIB)


def in_month(iso):
    dt = to_wib(iso)
    return dt.year == YEAR and dt.month == MONTH


def october_titles():
    history = []
    for i in range(1, 12):
        p = os.path.join(BASE, f"watched-history-{i}.json")
        if os.path.exists(p):
            history.extend(json.load(open(p, encoding="utf-8")))
    hist = [h for h in history if in_month(h["watched_at"])]
    movies, episodes = {}, {}
    for h in hist:
        if h["type"] == "movie":
            imdb = h["movie"]["ids"].get("imdb")
            if imdb:
                movies[imdb] = h["movie"]["title"]
        else:
            show = h["show"]
            key = (show["ids"].get("imdb") or show["slug"],
                   h["episode"]["season"], h["episode"]["number"])
            episodes[key] = show["title"]
    return movies, episodes


def fetch_movies_wikidata(imdb_ids):
    ids = " ".join(f'"{i}"' for i in imdb_ids)
    query = (f"SELECT ?id ?min WHERE {{ VALUES ?id {{ {ids} }} "
             f"?item wdt:P345 ?id. OPTIONAL {{ ?item wdt:P2047 ?min. }} }}")
    url = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(query)
    out = json.loads(get(url))
    res = {}
    for b in out["results"]["bindings"]:
        res[b["id"]["value"]] = int(float(b["min"]["value"])) if "min" in b else None
    return res


def fetch_show_episodes(imdb_id):
    """Kembalikan dict (season, number) -> menit dari TVMaze."""
    show = json.loads(get(f"https://api.tvmaze.com/lookup/shows?imdb={imdb_id}"))
    eps = json.loads(get(f"https://api.tvmaze.com/shows/{show['id']}/episodes"))
    runtimes = {}
    for e in eps:
        runtimes[f"{e['season']}-{e['number']}"] = e.get("runtime")
    avg = show.get("runtime")
    return {"name": show.get("name"), "avg": avg, "episodes": runtimes}


def main():
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {
        "movies": {}, "shows": {}}

    movies, episodes = october_titles()
    need_movies = [i for i in movies if i not in cache["movies"]]
    if need_movies:
        print("fetch wikidata:", len(need_movies), "film")
        got = fetch_movies_wikidata(need_movies)
        for k, v in got.items():
            cache["movies"][k] = v
        # tandai yang memang tidak ada di wikidata
        for i in need_movies:
            cache["movies"].setdefault(i, None)
        time.sleep(1)

    show_keys = sorted({k[0] for k in episodes})
    for imdb in show_keys:
        if imdb not in cache["shows"]:
            print("fetch tvmaze:", imdb)
            try:
                cache["shows"][imdb] = fetch_show_episodes(imdb)
            except Exception as e:
                print("  gagal:", e)
                cache["shows"][imdb] = None
            time.sleep(1)

    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ---- rekap durasi
    total, detail = 0, []
    for imdb, title in movies.items():
        mins = cache["movies"].get(imdb)
        detail.append((title, mins))
        total += mins or 0
    ep_total, ep_unknown = 0, 0
    for (imdb, s, n), show in episodes.items():
        info = cache["shows"].get(imdb) or {}
        mins = (info.get("episodes", {}).get(f"{s}-{n}")
                or info.get("avg"))
        if mins:
            ep_total += int(mins)
        else:
            ep_unknown += 1

    print("\n--- film (menit)")
    for t, v in sorted(detail, key=lambda x: (x[1] or 0), reverse=True):
        print(f"{v if v is not None else '?':>4}  {t}")
    print("film tanpa runtime:", sum(1 for _, v in detail if v is None))
    print("\nepisode terpakai runtime:", len(episodes) - ep_unknown,
          "tanpa:", ep_unknown, "-> total menit:", ep_total)
    print("\nTOTAL MENIT:", total + ep_total,
          "=", round((total + ep_total) / 60, 1), "jam")


if __name__ == "__main__":
    main()
