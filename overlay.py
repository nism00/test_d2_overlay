import ctypes
import json
import os
import threading
import time
import logging
import webview
from flask import Flask, request, jsonify

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "gpm_p99.json")
HTML_FILE = os.path.join(BASE_DIR, "overlay.html")

def is_shift_pressed():
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000)

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

WIDTH = 320
HEIGHT = 235

class WindowApi:
    def close(self):
        if win:
            win.destroy()

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

def is_lmb_pressed():
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)

def get_cursor_pos():
    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y

def drag_loop():
    # Перетаскивание опросом клавиш и курсора, а не событиями WebView:
    # окно с цветовым ключом не передаёт клики в WebView, поэтому JS-обработчики не срабатывают.
    dragging = False
    off_x = off_y = 0
    while True:
        time.sleep(0.01)
        if win is None:
            continue
        active = is_shift_pressed() and is_lmb_pressed()
        cx, cy = get_cursor_pos()
        if not dragging:
            if active:
                wx, wy = win.x, win.y
                if wx <= cx < wx + WIDTH and wy <= cy < wy + HEIGHT:
                    dragging = True
                    off_x, off_y = cx - wx, cy - wy
        elif active:
            win.move(cx - off_x, cy - off_y)
        else:
            dragging = False

VK_NUMPAD0 = 0x60

def hotkey_loop():
    # Numpad 0 переключает роль; клавиша не блокируется и доходит до игры.
    was_down = False
    while True:
        time.sleep(0.02)
        if win is None:
            continue
        down = bool(ctypes.windll.user32.GetAsyncKeyState(VK_NUMPAD0) & 0x8000)
        if down and not was_down:
            win.evaluate_js("cycleRole()")
        was_down = down

KEY_COLOR = (255, 0, 255)

def apply_color_key(window):
    # Импорт внутри функции: System.Drawing доступен только после того, как webview.start()
    # поднимет .NET-рантайм (pythonnet), поэтому на уровне модуля он не загрузится.
    from System import Action
    from System.Drawing import Color
    key = Color.FromArgb(*KEY_COLOR)

    def apply():
        window.native.BackColor = key
        window.native.TransparencyKey = key

    # Обработчики событий pywebview запускаются в отдельном потоке, а WinForms-контрол
    # нужно менять из GUI-потока, иначе окно зависает.
    window.native.Invoke(Action(apply))

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
        width=WIDTH,
        height=HEIGHT,
        x=20,
        y=60,
        frameless=True,
        easy_drag=False,
        transparent=True,
        on_top=True
    )
    win.events.shown += lambda: apply_color_key(win)
    threading.Thread(target=drag_loop, daemon=True).start()
    threading.Thread(target=hotkey_loop, daemon=True).start()
    webview.start()
