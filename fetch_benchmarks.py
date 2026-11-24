import json
import time
import requests

OUTPUT_FILE = "gpm_p99.json"
MAX_MINUTES = 60
HEADERS = {"User-Agent": "Dota2OverlayScript/1.0"}

# Собираем адрес API по частям, чтобы браузер не обрезал ссылку при копировании
API_BASE = "https://" + "api.opendota.com" + "/api/"
HEROES_URL = API_BASE + "heroes"
EXPLORER_URL = API_BASE + "explorer"

ROLE_QUERIES = {
    "all": "",
    "carry": "AND player_matches.lane_role = 1",
    "mid": "AND player_matches.lane_role = 2",
    "offlane": "AND player_matches.lane_role = 3",
    "support": "AND (player_matches.lane_role = 4 OR player_matches.is_roaming = true)"
}

def fetch_all_benchmarks():
    database = {}

    print("1. Загружаю список героев с:", HEROES_URL)
    r_heroes = requests.get(HEROES_URL, headers=HEADERS, timeout=15)
    r_heroes.raise_for_status()
    heroes_resp = r_heroes.json()

    for h in heroes_resp:
        hid = str(h["id"])
        database[hid] = {
            "name": h["localized_name"],
            "roles": {
                role: {"p99_gpm": 0, "gold_t": []}
                for role in ROLE_QUERIES
            }
        }

    for role_key, condition in ROLE_QUERIES.items():
        print(f"\n2. Собираю данные для роли: {role_key}...")

        sql_gpm = f"""
        SELECT 
            player_matches.hero_id,
            percentile_cont(0.99) WITHIN GROUP (ORDER BY gold_per_min) AS p99_gpm
        FROM player_matches
        JOIN matches ON player_matches.match_id = matches.match_id
        WHERE matches.start_time > extract(epoch from now() - interval '14 days')
          AND matches.duration > 900
          {condition}
        GROUP BY player_matches.hero_id
        """

        try:
            resp = requests.get(
                EXPLORER_URL,
                params={"sql": sql_gpm},
                headers=HEADERS,
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            for row in data.get("rows", []):
                hid = str(row.get("hero_id"))
                if hid in database and row.get("p99_gpm") is not None:
                    database[hid]["roles"][role_key]["p99_gpm"] = int(round(row["p99_gpm"]))
            print(f" -> GPM загружен ({len(data.get('rows', []))} героев)")
        except Exception as e:
            print(f" -> Ошибка загрузки GPM ({role_key}): {e}")

        time.sleep(1.5)

        sql_gold = f"""
        SELECT 
            pm.hero_id,
            gs.minute,
            percentile_cont(0.99) WITHIN GROUP (ORDER BY pm.gold_t[gs.minute + 1]) AS p99_gold
        FROM player_matches pm
        JOIN matches m ON pm.match_id = m.match_id
        CROSS JOIN generate_series(0, {MAX_MINUTES}) AS gs(minute)
        WHERE m.start_time > extract(epoch from now() - interval '14 days')
          AND m.duration > 900
          AND pm.gold_t IS NOT NULL
          AND array_length(pm.gold_t, 1) > gs.minute
          {condition.replace('player_matches.', 'pm.')}
        GROUP BY pm.hero_id, gs.minute
        ORDER BY pm.hero_id, gs.minute
        """

        try:
            resp = requests.get(
                EXPLORER_URL,
                params={"sql": sql_gold},
                headers=HEADERS,
                timeout=45
            )
            resp.raise_for_status()
            rows = resp.json().get("rows", [])

            temp_gold = {}
            for row in rows:
                hid = str(row.get("hero_id"))
                minute = row.get("minute")
                val = row.get("p99_gold")
                if hid in database and val is not None:
                    temp_gold.setdefault(hid, {})[minute] = int(round(val))

            for hid, minutes_map in temp_gold.items():
                max_m = max(minutes_map.keys())
                gold_list = [minutes_map.get(m, 0) for m in range(max_m + 1)]
                database[hid]["roles"][role_key]["gold_t"] = gold_list

            print(f" -> Поминутное золото загружено ({len(temp_gold)} героев)")
        except Exception as e:
            print(f" -> Ошибка загрузки gold_t ({role_key}): {e}")

        time.sleep(1.5)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(database, f, ensure_ascii=False, indent=2)

    print(f"\nГотово! База с GPM и поминутным золотом сохранена в {OUTPUT_FILE}")

if __name__ == "__main__":
    fetch_all_benchmarks()
