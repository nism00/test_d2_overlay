import ctypes
import json
import os
import threading
import logging
import webview
from flask import Flask, request, jsonify

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "gpm_p99.json")
HTML_FILE = os.path.join(BASE_DIR, "overlay.html")

def is_alt_pressed():
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x12) & 0x8000)

def load_benchmarks():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

BENCHMARKS = load_benchmarks()
app = Flask(__name__)

# Разрешаем запросы от локального HTML-файла к серверу 127.0.0.1:3000
@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

game_state = {
    "hero_id": None,
    "hero_name": "Ожидание Dota 2...",
    "clock_time": 0,
    "gold": 0,
    "gpm": 0,
    "role_key": "all"
}

def calculate_total_gold(gpm, clock_time, current_gold):
    if clock_time <= 0:
        return current_gold
    earned = int(round(600 + (gpm * (clock_time / 60.0))))
    return max(earned, current_gold)

def get_interpolated_gold(gold_t, clock_time):
    if not gold_t:
        return 0
    if clock_time <= 0:
        return int(gold_t)
    minute = int(clock_time // 60)
    sec_rem = clock_time % 60
    if minute >= len(gold_t) - 1:
        return int(gold_t[-1])
    g_start = gold_t[minute]
    g_end = gold_t[minute + 1]
    return int(round(g_start + (g_end - g_start) * (sec_rem / 60.0)))

@app.route('/', methods=['POST'])
def gsi_listener():
    data = request.json
    if not data:
        return "OK", 200
    map_data = data.get("map", {})
    player = data.get("player", {})
    hero = data.get("hero", {})

    if "clock_time" in map_data:
        game_state["clock_time"] = map_data["clock_time"]
    if "gold" in player:
        game_state["gold"] = player["gold"]
    if "gpm" in player:
        game_state["gpm"] = player["gpm"]

    new_hero_id = hero.get("id")
    if new_hero_id:
        hid_str = str(new_hero_id)
        if hid_str != game_state["hero_id"]:
            game_state["hero_id"] = hid_str
            if hid_str in BENCHMARKS:
                game_state["hero_name"] = BENCHMARKS[hid_str]["name"]
            else:
                raw = hero.get("name", "").replace("npc_dota_hero_", "")
                game_state["hero_name"] = raw.replace("_", " ").title()
    return "OK", 200

@app.route('/api/state', methods=['GET'])
def get_state():
    role = request.args.get("role")
    if role:
        game_state["role_key"] = role

    hid = game_state["hero_id"]
    role_key = game_state["role_key"]
    cur_gpm = game_state["gpm"]
    cur_gold = game_state["gold"]
    clock = game_state["clock_time"]

    total_gold = calculate_total_gold(cur_gpm, clock, cur_gold)
    target_gpm = 0
    target_gold = 0

    if hid and hid in BENCHMARKS:
        role_data = BENCHMARKS[hid]["roles"].get(role_key, {})
        target_gpm = role_data.get("p99_gpm", 0)
        target_gold = get_interpolated_gold(role_data.get("gold_t", []), clock)

    return jsonify({
        "hero_name": game_state["hero_name"],
        "clock_time": clock,
        "gpm": cur_gpm,
        "target_gpm": target_gpm,
        "total_gold": total_gold,
        "target_gold": target_gold,
        "has_data": bool(hid and hid in BENCHMARKS)
    })

win = None

class WindowApi:
    def __init__(self):
        self.dragging = False
        self.offset_x = 0
        self.offset_y = 0

    def start_drag(self, screen_x, screen_y):
        if is_alt_pressed() and win:
            self.dragging = True
            self.offset_x = screen_x - win.x
            self.offset_y = screen_y - win.y

    def do_drag(self, screen_x, screen_y):
        if self.dragging and is_alt_pressed() and win:
            win.move(int(screen_x - self.offset_x), int(screen_y - self.offset_y))

    def stop_drag(self):
        self.dragging = False

    def close(self):
        if win:
            win.destroy()

def start_flask():
    # Слушаем все интерфейсы (и 127.0.0.1, и localhost) на порту 3000
    app.run(host="0.0.0.0", port=3000, threaded=True)

if __name__ == "__main__":
    threading.Thread(target=start_flask, daemon=True).start()
    api = WindowApi()
    win = webview.create_window(
        "Dota 2 Overlay",
        url=HTML_FILE,
        js_api=api,
        width=320,
        height=235,
        x=20,
        y=60,
        frameless=True,
        on_top=True
    )
    webview.start()
