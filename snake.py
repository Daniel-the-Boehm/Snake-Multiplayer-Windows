#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import random
import select
import shutil
import socket
import sys
import threading
import time
import traceback
from collections import Counter, OrderedDict, deque
from pathlib import Path

APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def data_dir():
    base = os.environ.get("APPDATA") if sys.platform == "win32" else None
    if base:
        d = Path(base) / "Snake"
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "Snake"
    else:
        d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "snake"
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return d
    except OSError:
        return APP_DIR


BASE_DIR = data_dir()


def migrate_old_files():
    if BASE_DIR == APP_DIR:
        return
    try:
        old = APP_DIR / "snake_save.json"
        if old.exists() and not (BASE_DIR / "snake_save.json").exists():
            shutil.copy2(old, BASE_DIR / "snake_save.json")
        old_photos = APP_DIR / "snake_photos"
        if old_photos.is_dir() and not (BASE_DIR / "snake_photos").exists():
            shutil.copytree(old_photos, BASE_DIR / "snake_photos")
    except OSError:
        pass


migrate_old_files()


def report_fatal(title, message):
    try:
        (BASE_DIR / "snake_error.log").write_text(message, encoding="utf-8")
    except OSError:
        pass
    if sys.stderr:
        print(message, file=sys.stderr)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
        except Exception:
            pass


try:
    import faulthandler
    _CRASH_LOG = open(BASE_DIR / "snake_crash.log", "w")
    faulthandler.enable(_CRASH_LOG)
except Exception:
    _CRASH_LOG = None


def _close_crash_log():
    global _CRASH_LOG
    if _CRASH_LOG is None:
        return
    try:
        faulthandler.disable()
        _CRASH_LOG.flush()
        empty = _CRASH_LOG.tell() == 0
        _CRASH_LOG.close()
        if empty:
            (BASE_DIR / "snake_crash.log").unlink()
    except Exception:
        pass
    _CRASH_LOG = None


class WinPlatform:

    def __init__(self):
        self.ok = sys.platform == "win32"
        self.dpi = 1.0
        self.timer_set = False
        if not self.ok:
            return
        import ctypes
        self.ct = ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        try:
            self.dpi = ctypes.windll.user32.GetDpiForSystem() / 96.0
        except Exception:
            try:
                hdc = ctypes.windll.user32.GetDC(0)
                self.dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88) / 96.0
                ctypes.windll.user32.ReleaseDC(0, hdc)
            except Exception:
                self.dpi = 1.0
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
            self.timer_set = True
        except Exception:
            pass
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Snake.Game.Desktop")
        except Exception:
            pass

    def close(self):
        if self.ok and self.timer_set:
            try:
                self.ct.windll.winmm.timeEndPeriod(1)
            except Exception:
                pass
            self.timer_set = False

    def tint_title_bar(self, hwnd, rgb, dark):
        if not self.ok or not hwnd:
            return
        ct = self.ct
        try:
            dwm = ct.windll.dwmapi
            v = ct.c_int(1 if dark else 0)
            dwm.DwmSetWindowAttribute(ct.c_void_p(hwnd), 20, ct.byref(v), ct.sizeof(v))
            colorref = ct.c_uint(rgb[0] | (rgb[1] << 8) | (rgb[2] << 16))
            dwm.DwmSetWindowAttribute(ct.c_void_p(hwnd), 35, ct.byref(colorref), ct.sizeof(colorref))
            corner = ct.c_int(2)
            dwm.DwmSetWindowAttribute(ct.c_void_p(hwnd), 33, ct.byref(corner), ct.sizeof(corner))
        except Exception:
            pass


PLATFORM = WinPlatform()

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_WINDOWS_DPI_AWARENESS", "permonitorv2")

try:
    import pygame
except ImportError:
    if getattr(sys, "frozen", False):
        report_fatal("Snake can't start",
                     "This Snake.exe was built without pygame.\n\nOn the PC that builds it, open a terminal and run:\n"
                     "python -m pip install pygame\n\nThen build the exe again with the same python command.")
    else:
        py = sys.executable.replace("pythonw.exe", "python.exe")
        report_fatal("Snake can't start",
                     "pygame is not installed for this Python:\n" + sys.executable +
                     "\n\nInstall it by running this in a terminal:\n\"" + py + "\" -m pip install pygame")
    raise SystemExit(1)

FPS = 60
START_SIZE = (900, 720)
MIN_SIZE = (520, 480)
SAVE_PATH = BASE_DIR / "snake_save.json"
PHOTO_DIR = BASE_DIR / "snake_photos"
PHOTO_FILES = {"bg_photo": "background.png", "head_photo": "head.png", "fruit_photo": "fruit.png"}
PHOTO_LABELS = {"bg_photo": "Background photo", "head_photo": "Head photo", "fruit_photo": "Fruit photo"}

TWIN_INVERTS_CONTROLS = False

NET_PORT = 55055
DISCOVERY_PORT = 55056
NET_VERSION = 1
MAX_PLAYERS = 4
CORPSE_TIME = 1.2
APPLE_SNAKE_PAUSE = 7
APPLE_GROW_TICKS = 24
APPLE_MISTAKE = 0.10
APPLE_WIN_BONUS = 10
APPLE_TELEPORTS = 3
APPLE_TIME = 60.0
TELEPORT_MIN_DIST = 8
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))
LOCKED_FOR_CLIENT = ("map", "mode", "speed", "apples")

MODES = ["Classic", "No Borders", "Wall", "Twin", "Mirror", "Rush", "Apple"]
MODE_INFO = {
    "Classic": "Solid borders. Hit a wall or yourself and it's over.",
    "No Borders": "The edges wrap around: leave on one side, appear on the other.",
    "Wall": "Every apple turns the end of your tail into a permanent wall.",
    "Twin": "Eat an apple and head and tail swap, so you slither the other way.",
    "Mirror": "Left and right are swapped. Good luck.",
    "Rush": "The more you eat, the faster you go.",
    "Apple": "You are the apple: hold a key to run, R teleports (3x). Solo: an AI snake hunts you. "
             "With friends: they are the snakes, catch the apple or survive 60s.",
}

SPEEDS = {"Slow": 6, "Normal": 9, "Fast": 12, "Turbo": 16}
MAP_SIZES = {"Small": (12, 12), "Medium": (16, 16), "Large": (24, 24), "Huge": (32, 32)}
APPLE_COUNTS = [1, 2, 3, 4, 5, 10, 15, 25, 40, 144]
FRUITS = ["Apple", "Cherry", "Orange", "Lemon", "Grapes"]
FRUIT_COLORS = {
    "Apple": (226, 60, 52), "Cherry": (190, 24, 50), "Orange": (255, 152, 32),
    "Lemon": (250, 222, 64), "Grapes": (132, 66, 190),
}

THEMES = {
    "Classic": dict(
        window=(62, 99, 36), window2=(62, 99, 36), header=(74, 117, 44),
        board_a=(170, 215, 81), board_b=(162, 209, 73), frame=(87, 138, 52),
        text=(255, 255, 255), muted=(214, 235, 178), panel=(58, 94, 33), accent=(255, 214, 79),
        wall=(122, 104, 82), wall_hi=(168, 146, 116), wall_lo=(84, 70, 54)),
    "Retro Dark": dict(
        window=(12, 14, 20), window2=(12, 14, 20), header=(20, 24, 34),
        board_a=(27, 33, 45), board_b=(22, 27, 38), frame=(52, 211, 120),
        text=(214, 255, 224), muted=(120, 168, 142), panel=(24, 30, 42), accent=(52, 211, 120),
        wall=(96, 106, 124), wall_hi=(146, 158, 178), wall_lo=(56, 62, 76)),
    "Ocean Blue": dict(
        window=(10, 62, 104), window2=(4, 28, 60), header=(12, 70, 118),
        board_a=(44, 150, 204), board_b=(38, 140, 194), frame=(104, 196, 240),
        text=(240, 250, 255), muted=(170, 215, 240), panel=(10, 60, 104), accent=(255, 196, 77),
        wall=(22, 62, 100), wall_hi=(58, 110, 156), wall_lo=(10, 36, 62)),
    "Sunset Neon": dict(
        window=(24, 6, 48), window2=(120, 30, 92), header=(36, 11, 60),
        board_a=(66, 22, 96), board_b=(56, 17, 84), frame=(255, 74, 176),
        text=(255, 226, 245), muted=(214, 150, 220), panel=(34, 12, 56), accent=(255, 150, 60),
        wall=(255, 116, 74), wall_hi=(255, 178, 120), wall_lo=(196, 62, 66)),
}

SNAKE_STYLES = {
    "Classic Blue": dict(body=(66, 118, 244), head=(74, 128, 250), outline=(34, 74, 190),
                         shine=(132, 176, 255), seam=None, radius=0.30),
    "Retro Green": dict(body=(76, 190, 72), head=(92, 210, 86), outline=(16, 70, 28),
                        shine=None, seam=(40, 126, 46), radius=0.06),
    "Neon Purple": dict(body=(122, 64, 226), head=(142, 82, 244), outline=(236, 128, 255),
                        shine=(176, 122, 255), seam=None, radius=0.34),
    "Sunset Orange": dict(body=(240, 138, 36), head=(250, 160, 62), outline=(150, 72, 8),
                          shine=(255, 204, 124), seam=None, radius=0.30),
}

OPTIONS = [
    ("fruit", "Fruit Type", FRUITS),
    ("apples", "Apple Count", APPLE_COUNTS),
    ("speed", "Speed", list(SPEEDS)),
    ("map", "Map Size", list(MAP_SIZES)),
    ("mode", "Game Mode", MODES),
    ("snake", "Snake Style", list(SNAKE_STYLES)),
    ("theme", "Background Theme", list(THEMES)),
    ("bg_photo", "Background Photo", ["Off", "On"]),
    ("head_photo", "Head Photo", ["Off", "On"]),
    ("fruit_photo", "Fruit Photo", ["Off", "On"]),
]
OPTION_VALUES = {key: values for key, _label, values in OPTIONS}

KEYMAP = {
    pygame.K_UP: (0, -1), pygame.K_w: (0, -1),
    pygame.K_DOWN: (0, 1), pygame.K_s: (0, 1),
    pygame.K_LEFT: (-1, 0), pygame.K_a: (-1, 0),
    pygame.K_RIGHT: (1, 0), pygame.K_d: (1, 0),
}
ARROW_KEYS = (pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT)
WASD_KEYS = (pygame.K_w, pygame.K_a, pygame.K_s, pygame.K_d)
TELEPORT_KEYS = (pygame.K_r, pygame.K_RSHIFT, pygame.K_RCTRL, pygame.K_KP0)


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    return tuple(int(lerp(a, b, t)) for a, b in zip(c1, c2))


def smoothstep(t):
    t = clamp(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def ease_out_back(t):
    t = clamp(t, 0.0, 1.0)
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def mp_map_for(base, players):
    order = list(MAP_SIZES)
    return order[min(order.index(base) + max(0, players - 2), len(order) - 1)]


def wrap_step(d, n):
    return d - n * math.floor(d / n + 0.5)


def wrap_text(font, text, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if font.size(trial)[0] <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def ip(pt):
    return int(round(pt[0])), int(round(pt[1]))


class Config:
    DEFAULTS = {"fruit": "Apple", "apples": 1, "speed": "Normal", "map": "Medium",
                "mode": "Classic", "snake": "Classic Blue", "theme": "Classic",
                "bg_photo": "Off", "head_photo": "Off", "fruit_photo": "Off"}

    def __init__(self):
        self.v = dict(self.DEFAULTS)
        self.best = {}
        self._load()

    def __getitem__(self, key):
        return self.v[key]

    def _load(self):
        try:
            data = json.loads(SAVE_PATH.read_text(encoding="utf-8"))
            saved = data.get("settings", {})
            for key, _label, values in OPTIONS:
                if saved.get(key) in values:
                    self.v[key] = saved[key]
            for mode, score in data.get("best", {}).items():
                if mode in MODES and isinstance(score, int):
                    self.best[mode] = score
        except (OSError, ValueError, AttributeError):
            pass

    def save(self):
        try:
            SAVE_PATH.write_text(json.dumps({"settings": self.v, "best": self.best}, indent=2),
                                 encoding="utf-8")
        except OSError:
            pass

    def cycle(self, key, step):
        values = OPTION_VALUES[key]
        i = values.index(self.v[key])
        self.v[key] = values[(i + step) % len(values)]


class Snake:

    def __init__(self, head, direction, length=3):
        hx, hy = head
        dx, dy = direction
        self.body = [(hx - dx * i, hy - dy * i) for i in range(length)]
        self.dir = direction
        self.queue = deque()
        self.alive = True
        self.score = 0
        self.twin_flip = False
        self.grow = 0
        self.gone = False
        self.died = 0.0
        self.still()

    def still(self):
        b = self.body
        self.path_cells = list(b)
        self.h_from = self.h_to = b[0]
        self.t_from = self.t_to = b[-1]

    def to_dict(self):
        return {"body": self.body, "dir": self.dir, "path": self.path_cells, "hf": self.h_from,
                "ht": self.h_to, "tf": self.t_from, "tt": self.t_to, "alive": self.alive,
                "score": self.score, "q": list(self.queue), "gone": self.gone}

    def load(self, d):
        cells = lambda lst: [tuple(c) for c in lst]
        self.body, self.path_cells = cells(d["body"]), cells(d["path"])
        self.dir = tuple(d["dir"])
        self.h_from, self.h_to = tuple(d["hf"]), tuple(d["ht"])
        self.t_from, self.t_to = tuple(d["tf"]), tuple(d["tt"])
        self.alive, self.score, self.gone = d["alive"], d["score"], d.get("gone", False)
        self.queue = deque(tuple(q) for q in d["q"])


class Game:

    def __init__(self, cfg, remote=False):
        self.cfg = cfg
        self.remote = remote
        self.remote_rules = {k: cfg[k] for k in ("map", "mode", "apples", "speed")}
        self.iv = 1.0 / SPEEDS["Normal"]
        self.styles = None
        self.reset()

    def rule(self, key):
        return self.remote_rules[key] if self.remote else self.cfg[key]

    @property
    def score(self):
        if self.apple is not None:
            return self.apple.score
        return self.snakes[0].score

    def targets(self):
        cells = [f["cell"] for f in self.foods]
        if self.apple is not None and self.apple.alive:
            cells.append(self.apple.body[0])
        return cells

    def reset(self, players=1, countdown=3.0, apple_slot=None):
        self.players = players
        name = self.rule("map")
        if players > 1:
            name = mp_map_for(name, players)
        self.cols, self.rows = MAP_SIZES[name]
        self.mode = self.rule("mode")
        cols, rows = self.cols, self.rows
        self.apple, self.apple_slot = None, None
        self.held, self.apple_queue = [], deque()
        self.teleports = APPLE_TELEPORTS
        self.why = ""
        starts = [((cols // 4, rows // 3), (1, 0)),
                  ((cols - 1 - cols // 4, rows - 1 - rows // 3), (-1, 0)),
                  ((cols - 1 - cols // 3, rows // 4), (0, 1)),
                  ((cols // 3, rows - 1 - rows // 4), (0, -1))]
        self.slots = [("snake", i) for i in range(players)]
        if self.mode == "Apple" and players == 1:
            self.snakes = [Snake((cols - 4, rows // 2), (-1, 0))]
            self.apple = Snake((cols // 4, rows // 2), (1, 0), length=1)
        elif self.mode == "Apple":
            self.apple_slot = (players - 1) if apple_slot is None else apple_slot % players
            n = players - 1
            if n == 1:
                self.snakes = [Snake((cols - 4, rows // 2), (-1, 0))]
                self.apple = Snake((cols // 4, rows // 2), (1, 0), length=1)
            else:
                self.snakes = [Snake(h, d) for h, d in starts[:n]]
                self.apple = Snake((cols // 2, rows // 2), (1, 0), length=1)
            self.slots, k = [], 0
            for slot in range(players):
                if slot == self.apple_slot:
                    self.slots.append(("apple", 0))
                else:
                    self.slots.append(("snake", k))
                    k += 1
        elif players == 1:
            self.snakes = [Snake((cols // 3, rows // 2), (1, 0))]
        else:
            self.snakes = [Snake(h, d) for h, d in starts[:players]]
        self.walls = {}
        self.foods = []
        self.state = "idle" if players == 1 else "countdown"
        self.cd = countdown
        self.winner = None
        self.win = False
        self.new_best = False
        self.acc = 0.0
        self.clock = 0.0
        self.dead_at = 0.0
        self.tick_no = 0
        self.last_dying = []
        self.events = []
        if not self.remote:
            if players == 1 and self.mode != "Apple":
                self._spawn_food((cols * 2 // 3 + 1, rows // 2))
            self.fill_foods()

    def snake_of(self, slot):
        if self.apple is not None:
            if self.players == 1 or self.slots[slot][0] == "apple":
                return None
            return self.slots[slot][1]
        return slot if slot < len(self.snakes) else None

    def slot_of_snake(self, i):
        if self.apple is not None and self.players > 1:
            for slot, (kind, k) in enumerate(self.slots):
                if kind == "snake" and k == i:
                    return slot
            return None
        return i

    def time_left(self):
        return max(0.0, APPLE_TIME - self.tick_no * self.interval())

    def _spawn_food(self, cell=None):
        if cell is None:
            taken = set(self.walls) | {f["cell"] for f in self.foods}
            for s in self.snakes:
                if not s.gone:
                    taken.update(s.body)
            free = [(x, y) for x in range(self.cols) for y in range(self.rows) if (x, y) not in taken]
            if not free:
                return False
            cell = random.choice(free)
        self.foods.append({"cell": cell, "born": self.clock})
        return True

    def fill_foods(self):
        if self.remote or self.mode == "Apple":
            return
        target = self.cfg["apples"]
        if self.players > 1:
            target = max(target, self.players)
        while len(self.foods) < target:
            if not self._spawn_food():
                break
        while len(self.foods) > target:
            self.foods.pop()
        if not self.foods and self.state != "dead":
            self.win = True
            self._end_round()

    def interval(self):
        if self.remote:
            return self.iv
        cps = SPEEDS[self.cfg["speed"]]
        if self.mode == "Rush":
            cps = min(cps + 0.5 * max(s.score for s in self.snakes), cps * 2.5, 30.0)
        return 1.0 / cps

    @property
    def t(self):
        if self.state in ("idle", "countdown"):
            return 1.0
        return clamp(self.acc / self.interval(), 0.0, 1.0)

    def update(self, dt, simulate=True):
        self.clock += dt
        if self.state == "countdown":
            self.cd -= dt
            if self.cd <= 0 and not self.remote and simulate:
                self.cd = 0.0
                self.state = "running"
                self.acc = 0.0
                self.tick()
            else:
                self.cd = max(self.cd, 0.0)
            return
        if self.state == "idle" or (self.state == "running" and not simulate):
            return
        self.acc += dt
        iv = self.interval()
        if self.state == "running" and not self.remote:
            guard = 0
            while self.acc >= iv and self.state == "running" and guard < 6:
                self.acc -= iv
                self.tick()
                iv = self.interval()
                guard += 1
        self.acc = min(self.acc, iv)

    def steer(self, idx, raw):
        if self.remote or self.state == "dead":
            return
        si = idx
        if self.apple is not None:
            if self.players == 1 or self.slots[idx][0] == "apple":
                self._steer_apple(raw)
                return
            si = self.slots[idx][1]
        s = self.snakes[si]
        if not s.alive:
            return
        d = raw
        if self.mode == "Mirror":
            d = (-d[0], d[1])
        if TWIN_INVERTS_CONTROLS and self.mode == "Twin" and s.twin_flip:
            d = (-d[0], -d[1])

        if self.state == "idle":
            if d == (-1, 0):
                hx, hy = s.body[0]
                s.body = [(hx + i, hy) for i in range(len(s.body))]
            s.dir = d
            s.still()
            self.state = "running"
            self.acc = 0.0
            self.tick()
            return

        last = s.queue[-1] if s.queue else s.dir
        if d == last or d == (-last[0], -last[1]):
            return
        if len(s.queue) < 3:
            s.queue.append(d)

    def _steer_apple(self, raw):
        if raw in self.held:
            self.held.remove(raw)
        self.held.append(raw)
        if len(self.apple_queue) < 2:
            self.apple_queue.append(raw)
        if self.state == "idle":
            self.state = "running"
            self.acc = 0.0
            self.tick()

    def teleport(self):
        a = self.apple
        if a is None or not a.alive or self.state != "running" or self.teleports <= 0:
            return False
        heads = [s.body[0] for s in self.snakes if s.alive]
        if not heads:
            return False
        blocked = set(self.walls) | {c for s in self.snakes if not s.gone for c in s.body}
        reach, todo = set(heads), deque(heads)
        while todo:
            c = todo.popleft()
            for d in DIRS:
                n = (c[0] + d[0], c[1] + d[1])
                if 0 <= n[0] < self.cols and 0 <= n[1] < self.rows and n not in blocked and n not in reach:
                    reach.add(n)
                    todo.append(n)
        reach -= set(heads)
        far = [c for c in reach if c != a.body[0]
               and all(abs(c[0] - h[0]) + abs(c[1] - h[1]) >= TELEPORT_MIN_DIST for h in heads)]
        pool = far or [c for c in reach if c != a.body[0]]
        if not pool:
            return False
        old, new = a.body[0], random.choice(pool)
        a.body = [new]
        a.still()
        self.apple_queue.clear()
        self.teleports -= 1
        self.events.append(("poof", old, 0))
        self.events.append(("poof", new, 0))
        return True

    def release(self, raw):
        if raw in self.held:
            self.held.remove(raw)

    def _apple_step(self):
        a = self.apple
        d = self.apple_queue.popleft() if self.apple_queue else (self.held[-1] if self.held else None)
        a.h_from = a.body[0]
        if d is not None:
            nx, ny = a.body[0][0] + d[0], a.body[0][1] + d[1]
            solid = set(self.walls)
            for s in self.snakes:
                if s.alive:
                    solid.update(s.body[1:])
                elif not s.gone:
                    solid.update(s.body)
            if 0 <= nx < self.cols and 0 <= ny < self.rows and (nx, ny) not in solid:
                a.body = [(nx, ny)]
                a.dir = d
        a.h_to = a.body[0]
        for i, s in enumerate(self.snakes):
            if s.alive and s.body[0] == a.body[0]:
                for t in self.snakes:
                    t.still()
                self._eaten(i)
                return
        if self.players == 1:
            s = self.snakes[0]
            s.queue.clear()
            s.dir = self._ai_dir(s, a.body[0])

    def _ai_dir(self, s, goal):
        head = s.body[0]
        blocked = set(self.walls) | set(s.body if s.grow > 0 else s.body[:-1])

        def free(c):
            return 0 <= c[0] < self.cols and 0 <= c[1] < self.rows and c not in blocked

        options = [d for d in DIRS if free((head[0] + d[0], head[1] + d[1]))]
        if not options:
            return s.dir
        prev, queue = {head: None}, deque([head])
        while queue and goal not in prev:
            c = queue.popleft()
            for d in DIRS:
                n = (c[0] + d[0], c[1] + d[1])
                if free(n) and n not in prev:
                    prev[n] = c
                    queue.append(n)
        if random.random() < APPLE_MISTAKE:
            return random.choice(options)
        if goal in prev:
            step = goal
            while prev[step] != head:
                step = prev[step]
            return (step[0] - head[0], step[1] - head[1])

        def room(c):
            seen, todo = {c}, deque([c])
            while todo and len(seen) < 60:
                x = todo.popleft()
                for d in DIRS:
                    n = (x[0] + d[0], x[1] + d[1])
                    if free(n) and n not in seen:
                        seen.add(n)
                        todo.append(n)
            return len(seen)

        return max(options, key=lambda d: room((head[0] + d[0], head[1] + d[1])))

    def _apple_after(self):
        a = self.apple
        for i, s in enumerate(self.snakes):
            if s.alive and s.body[0] == a.body[0]:
                self._eaten(i)
                return
        if self.players == 1:
            a.score = int(self.tick_no * self.interval())
        elif self.time_left() <= 0:
            self.why = "time"
            self._end_round()
            return
        if self.tick_no % APPLE_GROW_TICKS == 0:
            for s in self.snakes:
                if s.alive:
                    s.grow += 1

    def _eaten(self, by=None):
        self.apple.alive = False
        self.why = "eaten"
        self.events.append(("eaten", self.apple.body[0], 0))
        if self.players > 1 and by is not None:
            self.winner = self.slot_of_snake(by)
        self._end_round()

    def tick(self):
        self.tick_no += 1
        if self.apple is not None:
            self._apple_step()
            if self.state == "dead":
                return
            if self.tick_no % APPLE_SNAKE_PAUSE == 0:
                for s in self.snakes:
                    s.still()
                self._apple_after()
                return
        for s in self.snakes:
            if not s.alive and not s.gone and self.players > 1 and self.clock - s.died >= CORPSE_TIME:
                s.gone = True
        alive = [s for s in self.snakes if s.alive]
        for s in alive:
            if s.queue:
                s.dir = s.queue.popleft()
        wrap = self.mode == "No Borders"
        plan = {}
        for s in alive:
            nx, ny = s.body[0][0] + s.dir[0], s.body[0][1] + s.dir[1]
            if wrap:
                nx, ny = nx % self.cols, ny % self.rows
            elif not (0 <= nx < self.cols and 0 <= ny < self.rows):
                plan[s] = None
                continue
            plan[s] = (nx, ny)
        targets = Counter(c for c in plan.values() if c is not None)
        food_at = {f["cell"]: f for f in self.foods}
        eats = {s: plan[s] in food_at and targets[plan[s]] == 1 for s in alive}
        grows = {s: (eats[s] and self.mode != "Wall") or s.grow > 0 for s in alive}

        solid = set(self.walls)
        for s in alive:
            solid.update(s.body if grows[s] else s.body[:-1])
        for s in self.snakes:
            if not s.alive and not s.gone:
                solid.update(s.body)
        dying = [s for s in alive if plan[s] is None or plan[s] in solid or targets[plan[s]] > 1]

        for s in alive:
            idx = self.snakes.index(s)
            if s in dying:
                s.alive = False
                s.still()
                s.died = self.clock
                self.events.append(("die", s.body[0], idx))
                continue
            new = plan[s]
            prev = s.body
            body = [new] + prev
            tail = None if grows[s] else body.pop()
            if grows[s] and not eats[s] and s.grow > 0:
                s.grow -= 1
            s.body = body
            s.h_from, s.h_to = prev[0], new
            s.path_cells = body[1:]
            s.t_from = tail if tail is not None else body[-1]
            s.t_to = body[-1]
            if eats[s]:
                self.foods.remove(food_at[new])
                s.score += 1
                self.events.append(("eat", new, idx))
                if self.mode == "Wall" and tail is not None:
                    self.walls[tail] = self.clock
                if self.mode == "Twin":
                    self._reverse(s, idx)
        self.fill_foods()
        survivors = [s for s in self.snakes if s.alive]
        self.last_dying = [self.snakes.index(s) for s in dying]
        if dying and (self.players == 1 or len(survivors) <= (0 if self.apple is not None else 1)):
            if self.apple is not None and self.players > 1:
                self.why = "crashed"
            self._end_round()
        if self.apple is not None and self.state != "dead":
            self._apple_after()

    def _reverse(self, s, idx):
        s.body.reverse()
        b = s.body
        s.path_cells = b[:-1]
        s.h_from = s.h_to = b[0]
        s.t_from, s.t_to = b[-2], b[-1]
        s.dir = (wrap_step(b[0][0] - b[1][0], self.cols), wrap_step(b[0][1] - b[1][1], self.rows))
        s.queue.clear()
        s.twin_flip = not s.twin_flip
        self.events.append(("reverse", b[0], idx))

    def _end_round(self):
        if self.state == "dead":
            return
        self.state = "dead"
        self.dead_at = self.clock
        if self.players == 1:
            if self.apple is not None and self.apple.alive:
                self.win = True
                self.apple.score += APPLE_WIN_BONUS
            self._record_best()
        elif self.apple is not None:
            if self.apple.alive:
                self.winner = self.apple_slot
                self.why = self.why or "crashed"
        else:
            alive = [i for i, s in enumerate(self.snakes) if s.alive]
            if len(alive) == 1:
                self.winner = alive[0]
            else:
                pool = self.last_dying or list(range(len(self.snakes)))
                best = max(self.snakes[i].score for i in pool)
                top = [i for i in pool if self.snakes[i].score == best]
                self.winner = top[0] if len(top) == 1 else -1

    def drop_player(self, idx):
        if self.state == "dead":
            return
        if self.apple is not None and self.players > 1:
            kind, k = self.slots[idx]
            if kind == "apple":
                self.apple.alive = False
                self.why = "left"
                alive = [i for i, s in enumerate(self.snakes) if s.alive]
                self.winner = self.slot_of_snake(alive[0]) if alive else -1
                self._end_round()
                return
            idx = k
        s = self.snakes[idx]
        if not s.alive:
            return
        s.alive = False
        s.still()
        s.died = self.clock
        self.events.append(("die", s.body[0], idx))
        if self.apple is not None and self.players > 1:
            if not any(x.alive for x in self.snakes):
                self.why = "crashed"
                self._end_round()
        elif sum(1 for x in self.snakes if x.alive) <= 1:
            self.last_dying = [idx]
            self._end_round()

    def _record_best(self):
        if self.score > self.cfg.best.get(self.mode, 0):
            self.cfg.best[self.mode] = self.score
            self.new_best = self.score > 0
            self.cfg.save()

    def snapshot(self):
        return {"t": "state", "tick": self.tick_no, "state": self.state, "iv": self.interval(),
                "cd": self.cd, "winner": self.winner, "foods": [f["cell"] for f in self.foods],
                "walls": list(self.walls), "snakes": [s.to_dict() for s in self.snakes],
                "apple": self.apple.to_dict() if self.apple is not None else None,
                "tp": self.teleports, "why": self.why,
                "events": [list(e) for e in self.events]}

    def apply_snapshot(self, d):
        if d["state"] == "dead" and self.state != "dead":
            self.dead_at = self.clock
        self.state, self.iv, self.cd, self.winner = d["state"], d["iv"], d["cd"], d["winner"]
        self.teleports, self.why = d.get("tp", 0), d.get("why", "")
        known = {f["cell"]: f for f in self.foods}
        self.foods = [known.get(tuple(c)) or {"cell": tuple(c), "born": self.clock} for c in d["foods"]]
        self.walls = {tuple(c): self.walls.get(tuple(c), self.clock) for c in d["walls"]}
        for s, sd in zip(self.snakes, d["snakes"]):
            s.load(sd)
        if self.apple is not None and d.get("apple"):
            self.apple.load(d["apple"])
        for kind, cell, idx in d["events"]:
            self.events.append((kind, tuple(cell), idx))
        if d["tick"] != self.tick_no:
            self.tick_no = d["tick"]
            self.acc = 0.0

class SnakeView:

    def __init__(self, direction=(1, 0)):
        self.pts = []
        self.face = math.atan2(direction[1], direction[0])
        self.look = [float(direction[0]), float(direction[1])]
        self.mouth = 0.0


class Net:

    def __init__(self, sock):
        self.sock = sock
        self.buf = b""
        self.out = b""
        self.closed = False

    def send(self, msg):
        if self.closed:
            return
        self.out += (json.dumps(msg, separators=(",", ":")) + "\n").encode()
        self._flush()

    def _flush(self):
        while self.out and not self.closed:
            try:
                n = self.sock.send(self.out)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                self.closed = True
                return
            self.out = self.out[n:]

    def poll(self):
        msgs = []
        if self.closed:
            return msgs
        self._flush()
        while not self.closed:
            try:
                chunk = self.sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                self.closed = True
                break
            if not chunk:
                self.closed = True
                break
            self.buf += chunk
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            try:
                msgs.append(json.loads(line))
            except ValueError:
                pass
        return msgs

    def close(self):
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass


def local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"

SS = 4


def make_fruit(kind, px):
    S = px * SS
    surf = pygame.Surface((S, S), pygame.SRCALPHA)
    surf.fill((*FRUIT_COLORS[kind], 0))
    u = S / 100.0
    fs = 1.16

    def T(x, y):
        return int((50 + (x - 50) * fs) * u), int((52 + (y - 52) * fs) * u)

    def circle(col, x, y, r):
        pygame.draw.circle(surf, col, T(x, y), max(1, int(r * fs * u)))

    def poly(col, pts):
        pygame.draw.polygon(surf, col, [T(x, y) for x, y in pts])

    def line(col, a, b, w):
        pygame.draw.line(surf, col, T(*a), T(*b), max(1, int(w * fs * u)))

    def ellipse(col, x, y, w, h):
        x0, y0 = T(x, y)
        pygame.draw.ellipse(surf, col, (x0, y0, int(w * fs * u), int(h * fs * u)))

    leaf, leaf_dark, brown = (104, 184, 66), (66, 134, 48), (112, 72, 40)
    if kind == "Apple":
        for dx, dy, r in ((38, 58, 27), (62, 58, 27), (50, 63, 29)):
            circle((170, 36, 38), dx, dy, r + 2.5)
        for dx, dy, r in ((38, 58, 27), (62, 58, 27), (50, 63, 29)):
            circle((226, 60, 52), dx, dy, r)
        ellipse((248, 130, 112), 25, 44, 12, 19)
        line(brown, (50, 36), (53, 17), 4.5)
        poly(leaf_dark, [(53, 25), (68, 12), (82, 19), (70, 31)])
        poly(leaf, [(54, 24), (67, 14), (78, 19), (68, 28)])
    elif kind == "Cherry":
        line((88, 126, 58), (35, 68), (56, 15), 3.2)
        line((88, 126, 58), (68, 62), (56, 15), 3.2)
        poly(leaf_dark, [(56, 16), (72, 6), (88, 15), (72, 23)])
        poly(leaf, [(57, 16), (72, 9), (84, 15), (72, 21)])
        for cx_, cy_ in ((33, 71), (68, 65)):
            circle((128, 12, 36), cx_, cy_, 21)
            circle((190, 24, 50), cx_, cy_, 18.5)
            circle((244, 118, 126), cx_ - 6, cy_ - 7, 5)
    elif kind == "Orange":
        circle((208, 104, 14), 50, 57, 38)
        circle((255, 152, 32), 50, 57, 35)
        ellipse((255, 200, 118), 28, 36, 16, 22)
        circle((224, 120, 20), 50, 30, 3)
        poly(leaf_dark, [(50, 24), (66, 11), (82, 19), (66, 30)])
        poly(leaf, [(51, 23), (65, 13), (78, 19), (65, 27)])
    elif kind == "Lemon":
        circle((196, 160, 18), 10, 52, 8)
        circle((196, 160, 18), 90, 52, 8)
        ellipse((196, 160, 18), 8, 27, 84, 54)
        circle((250, 222, 64), 11, 52, 6)
        circle((250, 222, 64), 89, 52, 6)
        ellipse((250, 222, 64), 11, 30, 78, 48)
        ellipse((255, 246, 160), 26, 38, 28, 10)
        poly(leaf_dark, [(56, 32), (72, 14), (88, 22), (72, 36)])
        poly(leaf, [(58, 31), (72, 17), (84, 22), (72, 32)])
    elif kind == "Grapes":
        spots = [(32, 42), (52, 42), (72, 42), (42, 60), (62, 60), (52, 78)]
        for x, y in spots:
            circle((84, 34, 134), x, y, 16.5)
        for x, y in spots:
            circle((132, 66, 190), x, y, 14)
            circle((196, 148, 238), x - 4.5, y - 4.5, 3.6)
        line(brown, (52, 30), (55, 13), 4.5)
        poly(leaf_dark, [(56, 22), (72, 8), (88, 16), (74, 28)])
        poly(leaf, [(57, 21), (72, 11), (84, 16), (73, 25)])
    return pygame.transform.smoothscale(surf, (px, px))


def make_trophy(px):
    S = px * SS
    surf = pygame.Surface((S, S), pygame.SRCALPHA)
    gold, dark = (255, 200, 52), (214, 146, 20)
    surf.fill((*gold, 0))
    u = S / 100.0
    for cx_ in (22, 78):
        pygame.draw.circle(surf, dark, (int(cx_ * u), int(32 * u)), int(13 * u), int(5.5 * u))
    pygame.draw.polygon(surf, dark, [(int(x * u), int(y * u)) for x, y in
                                     [(25, 12), (75, 12), (70, 46), (50, 61), (30, 46)]])
    pygame.draw.polygon(surf, gold, [(int(x * u), int(y * u)) for x, y in
                                     [(29, 15), (71, 15), (66, 44), (50, 57), (34, 44)]])
    pygame.draw.rect(surf, dark, (int(43 * u), int(58 * u), int(14 * u), int(16 * u)))
    pygame.draw.rect(surf, gold, (int(28 * u), int(72 * u), int(44 * u), int(12 * u)),
                     border_radius=int(3 * u))
    return pygame.transform.smoothscale(surf, (px, px))


def make_gear(px, color):
    S = px * SS
    surf = pygame.Surface((S, S), pygame.SRCALPHA)
    surf.fill((*color, 0))
    u = S / 100.0
    c = (50 * u, 50 * u)

    def pol(r, deg):
        a = math.radians(deg)
        return int(c[0] + math.cos(a) * r * u), int(c[1] + math.sin(a) * r * u)

    pygame.draw.circle(surf, color, (int(c[0]), int(c[1])), int(33 * u))
    for k in range(8):
        a = k * 45
        pygame.draw.polygon(surf, color, [pol(30, a - 11), pol(46, a - 7.5), pol(46, a + 7.5), pol(30, a + 11)])
    pygame.draw.circle(surf, (*color, 0), (int(c[0]), int(c[1])), int(14 * u))
    return pygame.transform.smoothscale(surf, (px, px))


def make_dim(size, alpha, radius=0):
    surf = pygame.Surface(size, pygame.SRCALPHA)
    pygame.draw.rect(surf, (0, 0, 0, alpha), surf.get_rect(), border_radius=radius)
    return surf


def make_app_icon(px=64):
    S = px * SS
    u = S / 100.0
    surf = pygame.Surface((S, S), pygame.SRCALPHA)
    surf.fill((74, 117, 44, 0))

    def R(x, y, w, h):
        return pygame.Rect(int(x * u), int(y * u), int(w * u), int(h * u))

    pygame.draw.rect(surf, (74, 117, 44), R(2, 2, 96, 96), border_radius=int(24 * u))
    pygame.draw.rect(surf, (170, 215, 81), R(6, 6, 88, 88), border_radius=int(20 * u))
    pygame.draw.circle(surf, (170, 36, 38), (int(76 * u), int(24 * u)), int(11.5 * u))
    pygame.draw.circle(surf, (226, 60, 52), (int(76 * u), int(24 * u)), int(9 * u))
    pygame.draw.line(surf, (112, 72, 40), (int(76 * u), int(15 * u)), (int(78 * u), int(8 * u)), max(1, int(2.5 * u)))
    outline, body = (34, 74, 190), (66, 118, 244)
    pygame.draw.rect(surf, outline, R(10, 47, 46, 30), border_radius=int(9 * u))
    pygame.draw.rect(surf, body, R(13, 50, 40, 24), border_radius=int(7 * u))
    pygame.draw.rect(surf, outline, R(42, 38, 48, 48), border_radius=int(13 * u))
    pygame.draw.rect(surf, (74, 128, 250), R(45, 41, 42, 42), border_radius=int(10 * u))
    pygame.draw.polygon(surf, (236, 52, 66), [(int(68 * u), int(61 * u)), (int(86 * u), int(50 * u)),
                                              (int(86 * u), int(72 * u))])
    for ey in (50, 72):
        pygame.draw.circle(surf, (32, 38, 60), (int(61 * u), int(ey * u)), int(8 * u))
        pygame.draw.circle(surf, (255, 255, 255), (int(61 * u), int(ey * u)), int(6.4 * u))
        pygame.draw.circle(surf, (18, 22, 38), (int(63.5 * u), int(ey * u)), int(3.4 * u))
    return pygame.transform.smoothscale(surf, (px, px))


def cover_scale(img, size):
    iw, ih = img.get_size()
    tw, th = size
    s = max(tw / iw, th / ih)
    sw, sh = max(tw, round(iw * s)), max(th, round(ih * s))
    scaled = pygame.transform.smoothscale(img, (sw, sh))
    return scaled.subsurface(((sw - tw) // 2, (sh - th) // 2, tw, th)).copy()


def load_photo(path):
    img = pygame.image.load(str(path))
    flat = pygame.Surface(img.get_size()).convert()
    flat.fill((255, 255, 255))
    flat.blit(img.convert_alpha(), (0, 0))
    return flat


def prepare_photo(kind, img):
    iw, ih = img.get_size()
    if kind in ("head_photo", "fruit_photo"):
        side = min(512 if kind == "head_photo" else 256, iw, ih)
        return cover_scale(img, (side, side))
    if max(iw, ih) > 1920:
        s = 1920 / max(iw, ih)
        return pygame.transform.smoothscale(img, (max(1, round(iw * s)), max(1, round(ih * s))))
    return img


def build_head_photo(square, size, radius_frac):
    scaled = pygame.transform.smoothscale(square, (size, size))
    out = pygame.Surface((size, size), pygame.SRCALPHA)
    out.blit(scaled, (0, 0))
    out.fill((0, 0, 0, 255), special_flags=pygame.BLEND_RGBA_MAX)
    mask = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=int(size * radius_frac))
    out.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    return out


def build_fruit_photo(square, px):
    S = px * SS
    surf = pygame.Surface((S, S), pygame.SRCALPHA)
    surf.fill((30, 30, 50, 0))
    d = int(S * 0.9)
    pygame.draw.circle(surf, (30, 30, 50), (S // 2, S // 2), d // 2 + max(2, int(S * 0.035)))
    layer = pygame.Surface((d, d), pygame.SRCALPHA)
    layer.blit(pygame.transform.smoothscale(square, (d, d)), (0, 0))
    layer.fill((0, 0, 0, 255), special_flags=pygame.BLEND_RGBA_MAX)
    mask = pygame.Surface((d, d), pygame.SRCALPHA)
    pygame.draw.circle(mask, (255, 255, 255, 255), (d // 2, d // 2), d // 2)
    layer.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    surf.blit(layer, ((S - d) // 2, (S - d) // 2))
    return pygame.transform.smoothscale(surf, (px, px))


def build_runs(P):
    runs, axis, prev = [], None, P[0]
    cur = [prev[0], prev[1], prev[0], prev[1]]
    for p in P[1:]:
        dx, dy = p[0] - prev[0], p[1] - prev[1]
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            continue
        a = "h" if abs(dx) > abs(dy) else "v"
        if axis is not None and a != axis:
            runs.append(cur)
            cur = [prev[0], prev[1], prev[0], prev[1]]
        axis = a
        cur[0], cur[1] = min(cur[0], p[0]), min(cur[1], p[1])
        cur[2], cur[3] = max(cur[2], p[0]), max(cur[3], p[1])
        prev = p
    runs.append(cur)
    return runs


def seam_points(P, start, step):
    out, nxt, acc = [], start, 0.0
    for a, b in zip(P, P[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < 1e-6:
            continue
        while nxt <= acc + length:
            f = (nxt - acc) / length
            out.append((a[0] + dx * f, a[1] + dy * f, abs(dx) > abs(dy)))
            nxt += step
        acc += length
    return out


def snake_copy(s):
    n = Snake.__new__(Snake)
    n.__dict__.update(s.__dict__)
    n.body = list(s.body)
    n.path_cells = list(s.path_cells)
    n.queue = deque(s.queue)
    return n


def frame_copy(g):
    f = Game.__new__(Game)
    f.__dict__.update(g.__dict__)
    f.snakes = [snake_copy(s) for s in g.snakes]
    f.apple = snake_copy(g.apple) if g.apple is not None else None
    f.foods = list(g.foods)
    f.walls = dict(g.walls)
    f.events = []
    f.held = list(g.held)
    f.apple_queue = deque(g.apple_queue)
    f.slots = list(g.slots)
    f.styles = list(g.styles) if g.styles else None
    f.remote_rules = dict(g.remote_rules)
    return f


class LRU:
    def __init__(self, limit):
        self.limit = limit
        self.d = OrderedDict()

    def get(self, key, builder):
        v = self.d.get(key)
        if v is None:
            v = builder()
            self.d[key] = v
            if len(self.d) > self.limit:
                self.d.popitem(last=False)
        else:
            self.d.move_to_end(key)
        return v

    def clear(self):
        self.d.clear()


def luminance(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def on_color(c):
    return (24, 26, 32) if luminance(c) > 150 else (255, 255, 255)


class Fonts:
    WIN = {"heavy": ["seguibl.ttf", "segoeuib.ttf"], "bold": ["segoeuib.ttf"],
           "semi": ["seguisb.ttf", "segoeuib.ttf"], "regular": ["segoeui.ttf"]}
    SYS = {"heavy": ("segoeuiblack,segoeui,helveticaneue,arial,dejavusans", True),
           "bold": ("segoeui,helveticaneue,arial,dejavusans", True),
           "semi": ("segoeuisemibold,segoeui,helveticaneue,arial,dejavusans", True),
           "regular": ("segoeui,helveticaneue,arial,dejavusans", False)}

    def __init__(self):
        self.files = {}
        fonts_dir = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
        for weight, names in self.WIN.items():
            for name in names:
                p = fonts_dir / name
                if sys.platform == "win32" and p.exists():
                    self.files[weight] = str(p)
                    break
        self.cache = {}

    def get(self, size, weight="semi"):
        size = max(8, int(size))
        key = (size, weight)
        f = self.cache.get(key)
        if f is None:
            path = self.files.get(weight)
            if path:
                f = pygame.font.Font(path, size)
            else:
                names, bold = self.SYS[weight]
                f = pygame.font.SysFont(names, size, bold=bold)
            self.cache[key] = f
        return f


class Gfx:
    S = 4

    def __init__(self):
        self.corners = LRU(256)
        self.discs = LRU(512)
        self.rings = LRU(128)
        self.panels = LRU(96)
        self.shadows = LRU(48)

    def _corner_set(self, r, color):
        S = self.S
        rgb = color[:3]
        a = color[3] if len(color) > 3 else 255
        big = pygame.Surface((2 * r * S, 2 * r * S), pygame.SRCALPHA)
        big.fill((*rgb, 0))
        pygame.draw.circle(big, (*rgb, a), (r * S, r * S), r * S)
        disc = pygame.transform.smoothscale(big, (2 * r, 2 * r))
        return (disc.subsurface((0, 0, r, r)).copy(), disc.subsurface((r, 0, r, r)).copy(),
                disc.subsurface((0, r, r, r)).copy(), disc.subsurface((r, r, r, r)).copy())

    def rrect(self, surf, color, rect, radius):
        x, y, w, h = (int(round(v)) for v in rect)
        if w <= 0 or h <= 0:
            return
        r = int(min(radius, w // 2, h // 2))
        if r < 2:
            surf.fill(color, (x, y, w, h))
            return
        surf.fill(color, (x + r, y, w - 2 * r, h))
        surf.fill(color, (x, y + r, r, h - 2 * r))
        surf.fill(color, (x + w - r, y + r, r, h - 2 * r))
        tl, tr, bl, br = self.corners.get((r, tuple(color)), lambda: self._corner_set(r, tuple(color)))
        surf.blit(tl, (x, y))
        surf.blit(tr, (x + w - r, y))
        surf.blit(bl, (x, y + h - r))
        surf.blit(br, (x + w - r, y + h - r))

    def disc(self, surf, color, center, radius):
        rq = max(0.5, round(radius * 2) / 2)
        key = (rq, tuple(color))

        def build():
            S = self.S
            d = int(math.ceil(rq * 2)) + 2
            big = pygame.Surface((d * S, d * S), pygame.SRCALPHA)
            big.fill((*color[:3], 0))
            pygame.draw.circle(big, color, (d * S // 2, d * S // 2), int(rq * S))
            return pygame.transform.smoothscale(big, (d, d))

        sp = self.discs.get(key, build)
        d = sp.get_width()
        surf.blit(sp, (int(round(center[0] - d / 2)), int(round(center[1] - d / 2))))

    def ring(self, surf, color, center, radius, width):
        rq, wq = max(1, int(round(radius))), max(1, int(round(width)))
        key = (rq, wq, tuple(color))

        def build():
            S = self.S
            d = 2 * rq + 4
            big = pygame.Surface((d * S, d * S), pygame.SRCALPHA)
            big.fill((*color[:3], 0))
            pygame.draw.circle(big, color, (d * S // 2, d * S // 2), rq * S, wq * S)
            return pygame.transform.smoothscale(big, (d, d))

        sp = self.rings.get(key, build)
        d = sp.get_width()
        surf.blit(sp, (int(round(center[0] - d / 2)), int(round(center[1] - d / 2))))

    def panel(self, size, radius, fill, border=None, border_w=1):
        w, h = int(size[0]), int(size[1])
        key = (w, h, int(radius), tuple(fill), tuple(border) if border else None, border_w)

        def build():
            S = 2
            big = pygame.Surface((w * S, h * S), pygame.SRCALPHA)
            big.fill((*fill[:3], 0))
            if border:
                pygame.draw.rect(big, border, big.get_rect(), border_radius=int(radius * S))
                inner = big.get_rect().inflate(-2 * border_w * S, -2 * border_w * S)
                pygame.draw.rect(big, (*fill[:3], 0), inner, border_radius=max(0, int((radius - border_w) * S)))
                tmp = pygame.Surface(inner.size, pygame.SRCALPHA)
                tmp.fill((0, 0, 0, 0))
                pygame.draw.rect(tmp, fill, tmp.get_rect(), border_radius=max(0, int((radius - border_w) * S)))
                big.blit(tmp, inner.topleft)
            else:
                pygame.draw.rect(big, fill, big.get_rect(), border_radius=int(radius * S))
            return pygame.transform.smoothscale(big, (w, h))

        return self.panels.get(key, build)

    def mask(self, size, radius):
        return self.panel(size, radius, (255, 255, 255, 255))

    def shadow(self, size, radius, alpha=90, spread=18):
        w, h = int(size[0]), int(size[1])
        key = (w, h, int(radius), alpha, spread)

        def build():
            pad = spread * 2
            small_k = 6
            sw, sh = max(2, (w + 2 * pad) // small_k), max(2, (h + 2 * pad) // small_k)
            s = pygame.Surface((sw, sh), pygame.SRCALPHA)
            s.fill((0, 0, 0, 0))
            r = pygame.Rect(pad // small_k, pad // small_k, max(1, w // small_k), max(1, h // small_k))
            pygame.draw.rect(s, (0, 0, 0, alpha), r, border_radius=max(1, int(radius / small_k)))
            for _ in range(2):
                s = pygame.transform.smoothscale(s, (max(2, sw // 2), max(2, sh // 2)))
                s = pygame.transform.smoothscale(s, (sw, sh))
            return pygame.transform.smoothscale(s, (w + 2 * pad, h + 2 * pad)), pad

        return self.shadows.get(key, build)

    def frosted(self, screen, rect, radius):
        rect = pygame.Rect(rect)
        clip = rect.clip(screen.get_rect())
        if clip.w < 4 or clip.h < 4:
            return None, clip
        sub = screen.subsurface(clip)
        k = 14
        small = pygame.transform.smoothscale(sub, (max(2, clip.w // k), max(2, clip.h // k)))
        blurred = pygame.transform.smoothscale(small, clip.size).convert_alpha()
        m = self.mask(rect.size, radius)
        blurred.blit(m, (rect.x - clip.x, rect.y - clip.y), special_flags=pygame.BLEND_RGBA_MULT)
        if clip.size != rect.size:
            full = pygame.Surface(rect.size, pygame.SRCALPHA)
            full.fill((0, 0, 0, 0))
            full.blit(blurred, (clip.x - rect.x, clip.y - rect.y))
            return full, rect
        return blurred, rect


class SettingsPanel:
    def __init__(self, app):
        self.app = app
        self.open = False
        self.opened_at = -10.0
        self.sel = 0
        self.k = 1.0
        self.rows = []
        self.panel_rect = pygame.Rect(0, 0, 0, 0)
        self.done_rect = pygame.Rect(0, 0, 0, 0)
        self.footer_top = 0
        self.note_text, self.note_until = "", 0.0

    def set_open(self, value):
        if value and not self.open:
            self.opened_at = self.app.time
        self.open = value
        if self.open:
            self.compute(self.app.w, self.app.h)

    def toggle(self):
        self.set_open(not self.open)

    def compute(self, w, h):
        k = max(0.5, min(self.app.ui, (h - 24) / 690, (w - 24) / 580))
        self.k = k
        pw, ph = int(580 * k), int(690 * k)
        slide = int((1 - smoothstep((self.app.time - self.opened_at) / 0.22)) * 24 * k)
        pr = self.panel_rect = pygame.Rect((w - pw) // 2, (h - ph) // 2 + slide, pw, ph)
        pad, row_h = int(20 * k), int(44 * k)
        top = pr.y + int(72 * k)
        self.rows = []
        for i in range(len(OPTIONS)):
            row = pygame.Rect(pr.x + pad, top + i * row_h, pr.w - 2 * pad, row_h - int(6 * k))
            pill = pygame.Rect(0, 0, int(row.w * 0.44), row.h - int(8 * k))
            pill.midright = (row.right - int(6 * k), row.centery)
            arrow_w = int(34 * k)
            left = pygame.Rect(pill.x, pill.y, arrow_w, pill.h)
            right = pygame.Rect(pill.right - arrow_w, pill.y, arrow_w, pill.h)
            value = pygame.Rect(left.right, pill.y, right.x - left.right, pill.h)
            self.rows.append((row, left, right, value, pill))
        self.footer_top = top + len(OPTIONS) * row_h + int(6 * k)
        self.done_rect = pygame.Rect(0, 0, int(170 * k), int(44 * k))
        self.done_rect.midbottom = (pr.centerx, pr.bottom - int(18 * k))

    def change(self, i, step):
        key = OPTIONS[i][0]
        if self.app.role == "client" and key in LOCKED_FOR_CLIENT:
            self.note_text, self.note_until = "Only the host can change this setting.", self.app.time + 2.5
            return
        self.app.cfg.cycle(key, step)
        self.app.on_setting_changed(key)

    def on_key(self, key):
        n = len(OPTIONS)
        if key in (pygame.K_ESCAPE, pygame.K_RETURN, pygame.K_KP_ENTER):
            self.open = False
        elif key in (pygame.K_UP, pygame.K_w):
            self.sel = (self.sel - 1) % n
        elif key in (pygame.K_DOWN, pygame.K_s):
            self.sel = (self.sel + 1) % n
        elif key in (pygame.K_LEFT, pygame.K_a):
            self.change(self.sel, -1)
        elif key in (pygame.K_RIGHT, pygame.K_d):
            self.change(self.sel, +1)

    def on_move(self, pos):
        for i, (row, *_rest) in enumerate(self.rows):
            if row.collidepoint(pos):
                self.sel = i

    def on_click(self, pos):
        if self.done_rect.collidepoint(pos) or not self.panel_rect.collidepoint(pos):
            self.open = False
            return
        for i, (row, left, right, value, _pill) in enumerate(self.rows):
            if row.collidepoint(pos):
                self.sel = i
                if left.collidepoint(pos):
                    self.change(i, -1)
                elif right.collidepoint(pos) or value.collidepoint(pos):
                    self.change(i, +1)
                return

    def on_wheel(self, pos, dy):
        for i, (row, *_rest) in enumerate(self.rows):
            if row.collidepoint(pos) and dy:
                self.sel = i
                self.change(i, 1 if dy > 0 else -1)
                return

    def draw(self, screen, th):
        if not self.open:
            return
        app = self.app
        w, h = screen.get_size()
        self.compute(w, h)
        k, pr = self.k, self.panel_rect
        app.dim_screen(screen, 150, self.opened_at)
        rad = int(26 * k)
        app.glass(screen, pr, rad, th["panel"], 0.72)
        app.text(screen, "Settings", 30 * k, th["text"], "heavy", center=(pr.centerx, pr.y + int(38 * k)))

        mouse = pygame.mouse.get_pos()
        for i, (key, label, _values) in enumerate(OPTIONS):
            row, left, right, value, pill = self.rows[i]
            selected = i == self.sel
            if selected or row.collidepoint(mouse):
                screen.blit(app.gfx.panel(row.size, int(14 * k), (255, 255, 255, 30 if selected else 16),
                                          (*th["accent"], 170) if selected else None, max(1, int(1.5 * k))), row)
            app.text(screen, label, 18 * k, th["text"], "semi", midleft=(row.x + int(14 * k), row.centery))
            cur = app.shown_value(key)
            self.draw_preview(screen, th, key, cur, pygame.Rect(pill.x - int(44 * k), row.y, int(36 * k), row.h))
            locked = app.role == "client" and key in LOCKED_FOR_CLIENT
            screen.blit(app.gfx.panel(pill.size, pill.h // 2, (0, 0, 0, 46)), pill)
            app.text(screen, str(cur), 17 * k, th["muted"] if locked else th["accent"], "bold", center=value.center)
            for rect, sign in ((left, -1), (right, 1)):
                col = th["accent"] if rect.collidepoint(mouse) and not locked else th["muted"]
                cx_, cy_, s = rect.centerx, rect.centery, int(5 * k)
                pts = [(cx_ - sign * s * 0.7, cy_ - s * 1.25), (cx_ + sign * s * 0.7, cy_), (cx_ - sign * s * 0.7, cy_ + s * 1.25)]
                pygame.draw.lines(screen, col, False, pts, max(2, int(2.6 * k)))
                if not locked:
                    app.hits.append(rect)
            app.hits.append(row)

        y = self.footer_top
        screen.fill(mix(th["panel"], (255, 255, 255), 0.22), (pr.x + int(28 * k), y, pr.w - int(56 * k), 1))
        font = app.fonts.get(16 * k, "regular")
        sel_key = OPTIONS[self.sel][0]
        if sel_key in PHOTO_FILES:
            have = "A photo is saved." if app.photos[sel_key] is not None else "No photo yet."
            desc = f"{PHOTO_LABELS[sel_key]}: {have} Drag an image file onto the window to set it."
        else:
            mode = app.shown_value("mode")
            desc = f"{mode}: {MODE_INFO[mode]}"
        if app.time < self.note_until:
            desc = self.note_text
        y += int(12 * k)
        for ln in wrap_text(font, desc, pr.w - int(70 * k))[:2]:
            app.text(screen, ln, 16 * k, th["muted"], "regular", midtop=(pr.centerx, y))
            y += font.get_linesize()
        app.text(screen, "Up/Down select    Left/Right change    Esc close", 13 * k, th["muted"], "regular",
                 midbottom=(pr.centerx, self.done_rect.top - int(10 * k)))
        app.button(screen, th, self.done_rect, "Done", True, 18 * k)

    def draw_preview(self, screen, th, key, value, rect):
        app, k = self.app, self.k
        if key == "fruit":
            sz = int(28 * k)
            screen.blit(app.fruit_sprite(value, sz, "panel"), (rect.centerx - sz // 2, rect.centery - sz // 2))
        elif key == "snake":
            st = SNAKE_STYLES[value]
            b = int(11 * k)
            x0 = rect.centerx - int(b * 1.6)
            for j, col in enumerate((st["body"], st["body"], st["head"])):
                r = pygame.Rect(x0 + j * int(b * 1.05), rect.centery - b // 2, b, b)
                app.gfx.rrect(screen, st["outline"], r.inflate(2, 2), 4)
                app.gfx.rrect(screen, col, r, 3)
        elif key in PHOTO_FILES:
            img = app.photos[key]
            if img is not None:
                sz = int(26 * k)
                thumb = app.cached(("thumb", key), (sz, app.photo_ver), lambda: cover_scale(img, (sz, sz)))
                r = thumb.get_rect(center=rect.center)
                app.gfx.rrect(screen, th["text"], r.inflate(4, 4), 6)
                screen.blit(thumb, r)
        elif key == "theme":
            t = THEMES[value]
            b = int(10 * k)
            x0, y0 = rect.centerx - b, rect.centery - b
            app.gfx.rrect(screen, t["frame"], (x0 - 2, y0 - 2, 2 * b + 4, 2 * b + 4), 5)
            for dx in range(2):
                for dy in range(2):
                    screen.fill(t["board_a"] if (dx + dy) % 2 == 0 else t["board_b"], (x0 + dx * b, y0 + dy * b, b, b))


class App:
    def __init__(self):
        sys.setswitchinterval(0.0005)
        pygame.display.init()
        pygame.font.init()
        pygame.display.set_caption("Snake")
        pygame.display.set_icon(make_app_icon(64))
        self.windowed_size = self.initial_size()
        self.fullscreen = False
        self.screen = pygame.display.set_mode(self.windowed_size, pygame.RESIZABLE)
        self.clock = pygame.time.Clock()
        self.fps_cap = self.refresh_rate()
        self.lock = threading.RLock()
        self.cfg = Config()
        self.game = Game(self.cfg)
        self.panel = SettingsPanel(self)
        self.fonts = Fonts()
        self.gfx = Gfx()
        self.t0 = time.perf_counter()

        self.running = True
        self.paused = False
        self.engine_error = None
        self._cache = {}
        self._text_cache = LRU(700)
        self.heads = LRU(600)
        self.scaled_fruit = LRU(160)
        self.cursor_hand = False
        self.hits = []
        self.photos = {"bg_photo": None, "head_photo": None, "fruit_photo": None}
        self.photo_ver = 0
        self.pending_drop = None
        self.drop_rects = {}
        self.toast_text, self.toast_until, self.toast_at = "", 0.0, 0.0
        self.load_saved_photos()

        self.role = "solo"
        self.local_idx = 0
        self.net = None
        self.peers = []
        self.server = self.beacon = self.disc = None
        self.pending_conn = None
        self.found = {}
        self.mp_screen = None
        self.mp_opened = 0.0
        self.mp_msg = ""
        self.mp_rects = {}
        self.mp_btn_rect = pygame.Rect(0, 0, 0, 0)
        self.gear_rect = pygame.Rect(0, 0, 0, 0)
        self.join_text = ""
        self.host_port, self.my_ip = NET_PORT, "127.0.0.1"
        self.next_beacon = self.last_rx = self.last_ping = self.last_cd = 0.0
        self.sent_tick, self.sent_state = -1, None
        self.round_no = 0
        self.first_apple = 1

        self.fx_reset = True
        self.particles = []
        self.eat_fx = []
        self.rings = []
        self.shake = 0.0
        self.gear_angle = 0.0
        self.blink_left = 0.0
        self.blink_next = 3.0
        self.card_anim = {}
        self.fps_shown = 0.0
        self.show_fps = False
        self.tinted = None

        self.capture()
        self.layout(*self.screen.get_size())
        self.render_reset()
        self.apply_title_tint()

    @staticmethod
    def initial_size():
        k = PLATFORM.dpi
        w, h = int(START_SIZE[0] * k), int(START_SIZE[1] * k)
        try:
            dw, dh = pygame.display.get_desktop_sizes()[0]
            s = min(1.0, dw * 0.9 / w, dh * 0.85 / h)
            w, h = int(w * s), int(h * s)
        except Exception:
            pass
        return max(w, MIN_SIZE[0]), max(h, MIN_SIZE[1])

    @staticmethod
    def refresh_rate():
        rate = 0
        for getter in ("get_desktop_refresh_rates",):
            fn = getattr(pygame.display, getter, None)
            if fn:
                try:
                    rate = max(fn())
                except Exception:
                    rate = 0
        if not rate:
            try:
                info = pygame.display.get_current_refresh_rate()
                rate = info
            except Exception:
                rate = 0
        return int(clamp(rate or 120, 60, 240))

    def apply_title_tint(self):
        th = THEMES[self.cfg["theme"]]
        key = (self.cfg["theme"], self.fullscreen)
        if self.tinted == key:
            return
        self.tinted = key
        try:
            hwnd = pygame.display.get_wm_info().get("window")
        except Exception:
            hwnd = None
        PLATFORM.tint_title_bar(hwnd, th["window"], luminance(th["window"]) < 140)

    @property
    def time(self):
        return time.perf_counter() - self.t0

    def cached(self, slot, key, builder):
        entry = self._cache.get(slot)
        if entry is None or entry[0] != key:
            entry = (key, builder())
            self._cache[slot] = entry
        return entry[1]

    def text(self, screen, s, size, color, weight="semi", **anchor):
        size = max(8, int(size))
        key = (s, size, weight, tuple(color))
        surf = self._text_cache.get(key, lambda: self.fonts.get(size, weight).render(s, True, color))
        rect = surf.get_rect(**{k: (int(v[0]), int(v[1])) for k, v in anchor.items()})
        screen.blit(surf, rect)
        return rect

    def text_size(self, s, size, weight="semi"):
        return self.fonts.get(size, weight).size(s)

    def fruit_photo_on(self):
        return self.cfg["fruit_photo"] == "On" and self.photos["fruit_photo"] is not None

    def fruit_sprite(self, kind, px, purpose):
        px = max(4, int(px))
        if purpose != "panel" and self.fruit_photo_on():
            return self.cached(("fruit_photo", purpose), (px, self.photo_ver),
                               lambda: build_fruit_photo(self.photos["fruit_photo"], px))
        return self.cached(("fruit", kind, purpose), px, lambda: make_fruit(kind, px))

    def scaled(self, sprite, sprite_key, px):
        if px == sprite.get_width():
            return sprite
        return self.scaled_fruit.get((sprite_key, px), lambda: pygame.transform.smoothscale(sprite, (px, px)))

    def trophy_sprite(self, px):
        return self.cached("trophy", px, lambda: make_trophy(px))

    def gear_sprite(self, px, color):
        return self.cached("gear", (px, color), lambda: make_gear(px, color))

    def load_saved_photos(self):
        for kind, name in PHOTO_FILES.items():
            try:
                self.photos[kind] = prepare_photo(kind, load_photo(PHOTO_DIR / name))
            except (pygame.error, OSError, FileNotFoundError):
                self.photos[kind] = None
        self.photo_ver += 1

    def import_photo(self, path, kind):
        try:
            img = prepare_photo(kind, load_photo(path))
        except (pygame.error, OSError):
            self.show_toast("Couldn't open that file. Try a PNG or JPG.")
            return
        try:
            PHOTO_DIR.mkdir(exist_ok=True)
            pygame.image.save(img, str(PHOTO_DIR / PHOTO_FILES[kind]))
        except (pygame.error, OSError):
            pass
        self.photos[kind] = img
        self.photo_ver += 1
        self.heads.clear()
        self.cfg.v[kind] = "On"
        self.cfg.save()
        self.show_toast(f"{PHOTO_LABELS[kind]} set")

    def show_toast(self, text):
        if self.time >= self.toast_until:
            self.toast_at = self.time
        self.toast_text, self.toast_until = text, self.time + 2.8

    def on_drop_choice(self, e):
        choice = None
        if e.type == pygame.KEYDOWN:
            choice = {pygame.K_b: "bg_photo", pygame.K_h: "head_photo", pygame.K_f: "fruit_photo",
                      pygame.K_ESCAPE: "cancel"}.get(e.key)
        elif e.button == 1:
            for name, rect in self.drop_rects.items():
                if rect.collidepoint(e.pos):
                    choice = name
        if choice is None:
            return
        path, self.pending_drop = self.pending_drop, None
        if choice != "cancel":
            self.import_photo(path, choice)

    def head_photo(self):
        return self.photos["head_photo"] if self.cfg["head_photo"] == "On" else None

    def layout(self, w, h):
        self.w, self.h = w, h
        self.ui = clamp(min(w / 900, h / 720), 0.6, 2.6)
        self.header_h = int(clamp(h * 0.085, 46, 64 * max(1.0, self.ui)))
        margin = int(clamp(min(w, h) * 0.03, 8, 40))
        g = self.fg
        cols, rows = g.cols, g.rows
        self.cell = max(6, int(min((w - 2 * margin) / cols, (h - self.header_h - 2 * margin) / rows)))
        bw, bh = self.cell * cols, self.cell * rows
        self.board = pygame.Rect((w - bw) // 2, self.header_h + (h - self.header_h - margin - bh) // 2, bw, bh)

    def handle_event(self, e):
        if e.type == getattr(pygame, "DROPFILE", -1):
            self.pending_drop = e.file
            self.panel.open = False
            return
        if self.pending_drop is not None and e.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
            self.on_drop_choice(e)
            return
        if e.type == pygame.QUIT:
            self.close_net()
            self.running = False
        elif e.type == pygame.VIDEORESIZE:
            if not self.fullscreen and (e.w < MIN_SIZE[0] or e.h < MIN_SIZE[1]):
                self.screen = pygame.display.set_mode((max(e.w, MIN_SIZE[0]), max(e.h, MIN_SIZE[1])), pygame.RESIZABLE)
        elif e.type == getattr(pygame, "WINDOWFOCUSLOST", -1):
            self.game.held.clear()
            if self.role in ("solo", "local") and self.game.state == "running":
                self.paused = True
        elif e.type == pygame.KEYDOWN:
            if e.key == pygame.K_F11:
                self.toggle_fullscreen()
            elif e.key == pygame.K_F3:
                self.show_fps = not self.show_fps
            else:
                self.on_key(e.key, getattr(e, "unicode", ""))
        elif e.type == pygame.KEYUP:
            if e.key in KEYMAP:
                self.on_key_up(e.key)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and self.mp_screen:
            self.mp_click(e.pos)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and self.gear_rect.collidepoint(e.pos):
            self.panel.toggle()
        elif (e.type == pygame.MOUSEBUTTONDOWN and e.button == 1 and not self.panel.open
              and self.mp_btn_rect.collidepoint(e.pos)):
            self.open_mp_menu()
        elif self.panel.open:
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                self.panel.on_click(e.pos)
            elif e.type == pygame.MOUSEMOTION:
                self.panel.on_move(e.pos)
            elif e.type == pygame.MOUSEWHEEL:
                self.panel.on_wheel(pygame.mouse.get_pos(), e.y)

    def toggle_fullscreen(self):
        if self.fullscreen:
            self.fullscreen = False
            self.screen = pygame.display.set_mode(self.windowed_size, pygame.RESIZABLE)
        else:
            self.windowed_size = self.screen.get_size()
            self.fullscreen = True
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        self.tinted = None
        self.apply_title_tint()

    def on_key(self, key, ch=""):
        g = self.game
        if self.mp_screen:
            self.mp_key(key, ch)
            return
        if self.panel.open:
            self.panel.on_key(key)
            return
        if key == pygame.K_ESCAPE:
            self.panel.toggle()
        elif key == pygame.K_m:
            self.open_mp_menu()
        elif key == pygame.K_p:
            if self.role in ("solo", "local") and g.state == "running":
                self.paused = not self.paused
        elif key in TELEPORT_KEYS and g.apple is not None and (key == pygame.K_r or self.role == "local"):
            self.do_teleport()
        elif key in (pygame.K_r, pygame.K_BACKSPACE):
            if self.role != "host" or g.state == "dead":
                self.restart()
        elif key in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER) and g.state == "dead":
            self.restart()
        elif key in KEYMAP and not self.paused:
            self.steer_local(KEYMAP[key], key)

    def steer_local(self, raw, key=None):
        if self.role == "client":
            if self.net is not None:
                self.net.send({"t": "dir", "d": list(raw)})
        elif self.role == "local":
            self.game.steer(0 if key in ARROW_KEYS else 1, raw)
        else:
            self.game.steer(self.local_idx, raw)

    def on_key_up(self, key):
        g, d = self.game, KEYMAP[key]
        if self.role == "client":
            if g.apple is not None and g.apple_slot == self.local_idx and self.net is not None:
                self.net.send({"t": "rel", "d": list(d)})
        elif self.role == "local":
            if g.apple is not None and g.apple_slot is not None:
                if key in (ARROW_KEYS if g.apple_slot == 0 else WASD_KEYS):
                    g.release(d)
        elif g.apple is None or g.apple_slot in (None, self.local_idx):
            g.release(d)

    def do_teleport(self):
        g = self.game
        if g.apple is None or self.paused:
            return
        if self.role == "client":
            if g.apple_slot == self.local_idx and self.net is not None:
                self.net.send({"t": "tp"})
        elif self.role == "host":
            if g.apple_slot == self.local_idx:
                g.teleport()
        else:
            g.teleport()

    def restart(self):
        if self.role == "client":
            return
        if self.role in ("host", "local"):
            self.start_round()
            return
        self.game.reset(players=1)
        self.finish_restart()

    def finish_restart(self):
        self.paused = False
        self.fx_reset = True

    def on_setting_changed(self, key):
        self.cfg.save()
        if key in ("map", "mode"):
            self.restart()
        elif key == "apples":
            self.game.fill_foods()
        elif key in ("snake", "head_photo"):
            self.heads.clear()
        elif key == "theme":
            self.apply_title_tint()

    def engine_loop(self):
        last = time.perf_counter()
        while self.running:
            now = time.perf_counter()
            dt = min(now - last, 0.1)
            last = now
            with self.lock:
                try:
                    self.engine_step(dt)
                except Exception:
                    self.engine_error = traceback.format_exc()
                    self.running = False
                    return
            time.sleep(0.001)

    def engine_step(self, dt):
        self.net_update(dt)
        g = self.game
        if self.role in ("solo", "local"):
            sim = (not self.paused and not self.panel.open and self.pending_drop is None
                   and self.mp_screen is None)
        else:
            sim = True
        g.update(dt, simulate=sim)
        self.net_send_state()

    def capture(self):
        g = self.game
        self.fg = frame_copy(g)
        self.fevents = list(g.events)
        g.events.clear()
        self.frole, self.fidx, self.fpaused = self.role, self.local_idx, self.paused
        self.fbest = dict(self.cfg.best)

    def run(self):
        engine = threading.Thread(target=self.engine_loop, name="snake-engine", daemon=True)
        engine.start()
        last = time.perf_counter()
        frames, fps_t = 0, last
        try:
            while self.running:
                self.clock.tick(self.fps_cap)
                now = time.perf_counter()
                dt = min(now - last, 0.1)
                last = now
                events = pygame.event.get()
                with self.lock:
                    for e in events:
                        self.handle_event(e)
                    self.capture()
                self.render_update(dt)
                self.draw()
                frames += 1
                if now - fps_t >= 0.5:
                    self.fps_shown = frames / (now - fps_t)
                    frames, fps_t = 0, now
        finally:
            self.running = False
            engine.join(timeout=1.0)
            with self.lock:
                self.close_net()
            pygame.quit()
            PLATFORM.close()
        if self.engine_error:
            raise RuntimeError(self.engine_error)

    def render_reset(self):
        self.particles.clear()
        self.eat_fx.clear()
        self.rings.clear()
        self.shake = 0.0
        self.views = [SnakeView(s.dir) for s in self.fg.snakes]
        for v, s in zip(self.views, self.fg.snakes):
            v.pts = self.snake_points(s)
        self.fx_reset = False

    def render_update(self, dt):
        g = self.fg
        if self.fx_reset:
            self.render_reset()
        self.consume_events()
        if len(self.views) != len(g.snakes):
            self.views = [SnakeView(s.dir) for s in g.snakes]
        for view, s in zip(self.views, g.snakes):
            view.pts = self.snake_points(s)
        self.animate_head(dt)
        for p in self.particles:
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            damp = math.exp(-3.0 * dt)
            p[2] *= damp
            p[3] *= damp
            p[4] -= dt
        self.particles = [p for p in self.particles if p[4] > 0]
        self.eat_fx = [f for f in self.eat_fx if self.time - f["start"] < f["dur"]]
        self.rings = [r for r in self.rings if self.time - r[2] < 0.4]
        self.shake = max(0.0, self.shake - dt * 2.5)
        target = 75.0 if self.panel.open else 0.0
        self.gear_angle += (target - self.gear_angle) * min(1.0, dt * 12)

    def burst(self, x, y, color, n):
        for _ in range(n):
            a, sp, life = random.uniform(0, math.tau), random.uniform(1.5, 4.5), random.uniform(0.35, 0.7)
            self.particles.append([x, y, math.cos(a) * sp, math.sin(a) * sp, life, life, color,
                                   random.uniform(0.09, 0.17)])

    def consume_events(self):
        g = self.fg
        for kind, cell, idx in self.fevents:
            cx, cy = cell[0] + 0.5, cell[1] + 0.5
            if kind == "eat":
                self.burst(cx, cy, FRUIT_COLORS[self.cfg["fruit"]], 14)
                self.eat_fx.append({"cell": cell, "start": self.time,
                                    "dur": max(0.05, (1 - g.t) * g.interval())})
            elif kind == "die":
                if idx < len(g.snakes):
                    self.burst(cx, cy, SNAKE_STYLES[self.style_of(idx)]["body"], 22)
                self.shake = 1.0
            elif kind == "reverse":
                self.burst(cx, cy, (255, 255, 255), 12)
            elif kind == "eaten":
                self.burst(cx, cy, FRUIT_COLORS[self.cfg["fruit"]], 26)
                self.eat_fx.append({"cell": cell, "start": self.time,
                                    "dur": max(0.05, (1 - g.t) * g.interval())})
                self.shake = 1.0
            elif kind == "poof":
                self.burst(cx, cy, (255, 255, 255), 10)
                self.rings.append([cx, cy, self.time])
        self.fevents = []

    def delta(self, dx, dy):
        g = self.fg
        if g.mode == "No Borders":
            return wrap_step(dx, g.cols), wrap_step(dy, g.rows)
        return dx, dy

    def snake_points(self, s):
        g = self.fg
        t, W, H = g.t, g.cols, g.rows

        def slide(a, b):
            return (a[0] + wrap_step(b[0] - a[0], W) * t + 0.5,
                    a[1] + wrap_step(b[1] - a[1], H) * t + 0.5)

        def attach(prev, x, y):
            return prev[0] + wrap_step(x - prev[0], W), prev[1] + wrap_step(y - prev[1], H)

        pts = [slide(s.h_from, s.h_to)]
        for c in s.path_cells:
            pts.append(attach(pts[-1], c[0] + 0.5, c[1] + 0.5))
        tail = slide(s.t_from, s.t_to)
        pts.append(attach(pts[-1], tail[0], tail[1]))
        return pts

    def animate_head(self, dt):
        g = self.fg
        cells = g.targets() + [f["cell"] for f in self.eat_fx]
        for view, s in zip(self.views, g.snakes):
            hx, hy = view.pts[0]
            target = math.atan2(s.dir[1], s.dir[0])
            diff = (target - view.face + math.pi) % math.tau - math.pi
            view.face += diff * (1 - math.exp(-dt * 18))
            want = 0.0
            if s.alive and cells:
                best = None
                for cx, cy in cells:
                    dx, dy = self.delta(cx + 0.5 - hx, cy + 0.5 - hy)
                    d = math.hypot(dx, dy)
                    if best is None or d < best[0]:
                        best = (d, dx, dy)
                d, dx, dy = best
                front = (dx * s.dir[0] + dy * s.dir[1]) / max(d, 1e-6)
                if front > 0.55 and d < 3.6:
                    want = clamp((3.6 - d) / 2.4, 0.0, 1.0)
            rate = 24 if want > view.mouth else 14
            view.mouth += (want - view.mouth) * (1 - math.exp(-dt * rate))
            lx, ly = float(s.dir[0]), float(s.dir[1])
            if s.queue:
                lx, ly = lx * 0.6 + s.queue[0][0] * 0.9, ly * 0.6 + s.queue[0][1] * 0.9
            n = math.hypot(lx, ly) or 1.0
            k = 1 - math.exp(-dt * 14)
            view.look[0] += (lx / n - view.look[0]) * k
            view.look[1] += (ly / n - view.look[1]) * k
        self.blink_next -= dt
        if self.blink_next <= 0:
            self.blink_left, self.blink_next = 0.13, random.uniform(2.5, 5.5)
        self.blink_left = max(0.0, self.blink_left - dt)

    def glass(self, screen, rect, radius, tint, strength=0.55, shadow=True):
        rect = pygame.Rect(rect)
        if shadow:
            sh, pad = self.gfx.shadow(rect.size, radius, 110, int(14 * self.ui))
            screen.blit(sh, (rect.x - pad, rect.y - pad + int(8 * self.ui)))
        frost, at = self.gfx.frosted(screen, rect, radius)
        if frost is not None:
            screen.blit(frost, at)
        screen.blit(self.gfx.panel(rect.size, radius, (*tint, int(255 * strength))), rect)
        screen.blit(self.gfx.panel(rect.size, radius, (255, 255, 255, 0), (255, 255, 255, 46),
                                   max(1, int(1.2 * self.ui))), rect)

    def pill(self, screen, rect, tint, alpha=0.32, border=True):
        rect = pygame.Rect(rect)
        r = rect.h // 2
        screen.blit(self.gfx.panel(rect.size, r, (*tint, int(255 * alpha)),
                                   (255, 255, 255, 40) if border else None, max(1, int(self.ui))), rect)

    def button(self, screen, th, rect, label, primary, size, name=None):
        rect = pygame.Rect(rect)
        hover = rect.collidepoint(pygame.mouse.get_pos())
        rad = min(rect.h // 2, int(14 * self.ui))
        if primary:
            base = mix(th["accent"], (255, 255, 255), 0.18 if hover else 0.0)
            sh, pad = self.gfx.shadow(rect.size, rad, 70, int(8 * self.ui))
            screen.blit(sh, (rect.x - pad, rect.y - pad + int(4 * self.ui)))
            screen.blit(self.gfx.panel(rect.size, rad, (*base, 255)), rect)
            color = on_color(base)
        else:
            screen.blit(self.gfx.panel(rect.size, rad, (255, 255, 255, 48 if hover else 26),
                                       (255, 255, 255, 60), max(1, int(self.ui))), rect)
            color = th["text"]
        self.text(screen, label, size, color, "bold", center=rect.center)
        self.hits.append(rect)
        if name:
            self.mp_rects[name] = rect
        return rect

    def dim_screen(self, screen, alpha, since):
        a = int(alpha * smoothstep((self.time - since) / 0.18))
        if a <= 0:
            return
        w, h = screen.get_size()
        layer = self.cached("dim_full", (w, h), lambda: pygame.Surface((w, h)).convert())
        layer.fill((0, 0, 0))
        layer.set_alpha(a)
        screen.blit(layer, (0, 0))

    def card(self, screen, th, center, lines, slot="card"):
        ui = self.ui
        key = tuple((s, size) for s, size, _c in lines)
        anim = self.card_anim.get(slot)
        if anim is None or anim[0] != key:
            first_line_same = anim is not None and anim[0][1:] == key[1:]
            anim = (key, self.time if not first_line_same else anim[1])
            self.card_anim[slot] = anim
        p = smoothstep((self.time - anim[1]) / 0.22)
        pad_x, pad_y, gap = int(26 * ui), int(18 * ui), int(6 * ui)
        rendered = []
        for s, size, color in lines:
            weight = "heavy" if size >= 30 else ("bold" if size >= 20 else "semi")
            sz = max(8, int(size * ui))
            surf = self._text_cache.get((s, sz, weight, tuple(color)),
                                        lambda s=s, sz=sz, w=weight, c=color: self.fonts.get(sz, w).render(s, True, c))
            rendered.append(surf)
        w = max(s.get_width() for s in rendered) + pad_x * 2
        h = sum(s.get_height() for s in rendered) + gap * (len(rendered) - 1) + pad_y * 2
        rect = pygame.Rect(0, 0, w, h)
        rect.center = (int(center[0]), int(center[1] + (1 - p) * 10 * ui))
        rad = int(22 * ui)
        if p < 0.999:
            layer = pygame.Surface(rect.size, pygame.SRCALPHA)
            layer.fill((0, 0, 0, 0))
            frost, _at = self.gfx.frosted(screen, rect, rad)
            if frost is not None and frost.get_size() == rect.size:
                layer.blit(frost, (0, 0))
            layer.blit(self.gfx.panel(rect.size, rad, (*th["panel"], 150)), (0, 0))
            layer.blit(self.gfx.panel(rect.size, rad, (255, 255, 255, 0), (255, 255, 255, 46), max(1, int(1.2 * ui))), (0, 0))
            y = pad_y
            for s in rendered:
                layer.blit(s, ((w - s.get_width()) // 2, y))
                y += s.get_height() + gap
            sc = 0.94 + 0.06 * p
            sw, shh = max(1, int(w * sc)), max(1, int(h * sc))
            layer = pygame.transform.smoothscale(layer, (sw, shh))
            layer.set_alpha(int(255 * p))
            screen.blit(layer, layer.get_rect(center=rect.center))
            return
        self.glass(screen, rect, rad, th["panel"], 0.6)
        y = rect.y + pad_y
        for s in rendered:
            screen.blit(s, (rect.centerx - s.get_width() // 2, y))
            y += s.get_height() + gap

    def draw(self):
        screen = pygame.display.get_surface()
        self.screen = screen
        self.layout(*screen.get_size())
        self.hits = []
        th = THEMES[self.cfg["theme"]]
        self.draw_background(screen, th)
        self.draw_board(screen, th)
        self.draw_header(screen, th)
        if not (self.panel.open or self.mp_screen or self.pending_drop):
            self.draw_state_cards(screen, th, self.board)
        with self.lock:
            self.panel.draw(screen, th)
            self.draw_drop_card(screen, th)
            self.draw_mp_screen(screen, th)
        self.draw_toast(screen, th)
        if self.show_fps:
            self.text(screen, f"{self.fps_shown:.0f} fps", 14 * self.ui, (255, 255, 255), "bold",
                      bottomleft=(int(10 * self.ui), self.h - int(8 * self.ui)))
        self.update_cursor()
        pygame.display.flip()

    def update_cursor(self):
        pos = pygame.mouse.get_pos()
        hand = any(r.collidepoint(pos) for r in self.hits)
        if hand != self.cursor_hand:
            self.cursor_hand = hand
            try:
                pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_HAND if hand else pygame.SYSTEM_CURSOR_ARROW)
            except Exception:
                pass

    def draw_background(self, screen, th):
        size = screen.get_size()
        br = self.board
        rad = self.board_radius()

        def build():
            w, h = size
            surf = pygame.Surface(size).convert()
            grad = pygame.Surface((1, 64)).convert()
            for y in range(64):
                grad.set_at((0, y), mix(th["window"], th["window2"], y / 63))
            surf.blit(pygame.transform.smoothscale(grad, size), (0, 0))
            glow = pygame.Surface((32, 32), pygame.SRCALPHA)
            for r in range(16, 0, -1):
                a = int(26 * (1 - r / 16) ** 1.5)
                pygame.draw.circle(glow, (255, 255, 255, a), (16, 9), r)
            surf.blit(pygame.transform.smoothscale(glow, (int(w * 1.4), int(h * 1.2))), (-int(w * 0.2), -int(h * 0.25)))
            vig = pygame.Surface((32, 32), pygame.SRCALPHA)
            vig.fill((0, 0, 0, 70))
            pygame.draw.ellipse(vig, (0, 0, 0, 0), (-4, -4, 40, 40))
            for _ in range(2):
                vig = pygame.transform.smoothscale(vig, (16, 16))
                vig = pygame.transform.smoothscale(vig, (32, 32))
            surf.blit(pygame.transform.smoothscale(vig, size), (0, 0))
            sh, pad = self.gfx.shadow(br.size, rad, 120, int(self.cell * 0.6))
            surf.blit(sh, (br.x - pad, br.y - pad + int(self.cell * 0.18)))
            return surf

        key = (self.cfg["theme"], size, tuple(br), rad)
        screen.blit(self.cached("bg", key, build), (0, 0))

    def board_radius(self):
        return max(8, int(self.cell * 0.42))

    def draw_header(self, screen, th):
        g, cfg = self.fg, self.cfg
        w, hh, ui = self.w, self.header_h, self.ui
        cy = hh // 2 + int(4 * ui)
        ph = int(hh * 0.72)
        icon = int(ph * 0.66)
        pad = int(hh * 0.3)
        gap = int(ph * 0.22)
        items = []
        tsize = ph * 0.5

        if g.players == 1:
            items.append(("img", self.fruit_sprite(cfg["fruit"], icon, "icon")))
            items.append(("txt", str(g.score), tsize, th["text"], "heavy"))
            items.append(("sp", gap * 2))
            items.append(("img", self.trophy_sprite(icon)))
            best = max(self.fbest.get(g.mode, 0), g.score)
            items.append(("txt", str(best), tsize, th["text"], "heavy"))
            if g.apple is not None:
                items.append(("sp", gap * 2))
                items.append(("dots", g.teleports, th["accent"]))
        elif g.apple is not None:
            for slot in self.order():
                kind, k = g.slots[slot]
                if kind == "apple":
                    items.append(("img", self.fruit_sprite(cfg["fruit"], icon, "icon")))
                    items.append(("txt", self.label(slot), ph * 0.38, th["text"], "bold"))
                    items.append(("dots", g.teleports, th["accent"]))
                else:
                    s, st = g.snakes[k], SNAKE_STYLES[self.style_of(k)]
                    items.append(("chip", st))
                    info = f"{self.label(slot)} {len(s.body)}" if s.alive else f"{self.label(slot)} out"
                    items.append(("txt", info, ph * 0.38, th["text"] if s.alive else th["muted"], "bold"))
                items.append(("sp", gap))
            items.append(("txt", f"{int(math.ceil(g.time_left()))}s", ph * 0.44, th["accent"], "heavy"))
        else:
            for i in self.order():
                s = g.snakes[i]
                st = SNAKE_STYLES[self.style_of(i)]
                items.append(("chip", st))
                items.append(("txt", f"{self.label(i)} {s.score}", ph * (0.42 if len(g.snakes) == 2 else 0.36),
                              th["text"] if s.alive else th["muted"], "bold"))
                items.append(("sp", gap))
            items.pop()

        def width_of(it):
            kind = it[0]
            if kind == "img":
                return it[1].get_width()
            if kind == "txt":
                return self.text_size(it[1], it[2], it[4])[0]
            if kind == "sp":
                return it[1]
            if kind == "dots":
                return int(ph * 0.22) * APPLE_TELEPORTS
            if kind == "chip":
                return int(icon * 0.7)
            return 0

        inner = sum(width_of(it) for it in items) + gap * max(0, len([i for i in items if i[0] != "sp"]) - 1)
        pill = pygame.Rect(pad, cy - ph // 2, inner + 2 * int(ph * 0.42), ph)
        self.pill(screen, pill, th["panel"], 0.45)
        x = pill.x + int(ph * 0.42)
        prev_sp = True
        for it in items:
            kind = it[0]
            if kind == "sp":
                x += it[1]
                prev_sp = True
                continue
            if not prev_sp:
                x += gap
            prev_sp = False
            if kind == "img":
                screen.blit(it[1], (x, cy - it[1].get_height() // 2))
            elif kind == "txt":
                self.text(screen, it[1], it[2], it[3], it[4], midleft=(x, cy))
            elif kind == "dots":
                step = int(ph * 0.22)
                for t in range(APPLE_TELEPORTS):
                    col = it[2] if t < it[1] else (255, 255, 255)
                    self.gfx.disc(screen, (*col, 255 if t < it[1] else 60), (x + t * step + step / 2, cy), ph * 0.075)
            elif kind == "chip":
                c = int(icon * 0.7)
                r = pygame.Rect(x, cy - c // 2, c, c)
                self.gfx.rrect(screen, it[1]["outline"], r, int(c * 0.3))
                self.gfx.rrect(screen, it[1]["body"], r.inflate(-4, -4), int(c * 0.26))
            x += width_of(it)
        left_end = pill.right

        bh = ph
        gr = self.gear_rect = pygame.Rect(0, 0, bh, bh)
        gr.midright = (w - pad, cy)
        hover = gr.collidepoint(pygame.mouse.get_pos())
        self.pill(screen, gr, mix(th["panel"], (255, 255, 255), 0.2 if hover or self.panel.open else 0.0), 0.45)
        gear = self.gear_sprite(int(bh * 0.56), th["text"])
        if self.gear_angle > 0.5:
            gear = pygame.transform.rotozoom(gear, -self.gear_angle, 1.0)
        screen.blit(gear, gear.get_rect(center=gr.center))
        self.hits.append(gr)

        live = self.frole != "solo"
        label = "VS" if live else "2P"
        lw = self.text_size(label, bh * 0.36, "heavy")[0]
        mb = self.mp_btn_rect = pygame.Rect(0, 0, int(bh * 0.62) + lw + int(bh * 0.55), bh)
        mb.midright = (gr.x - int(bh * 0.22), cy)
        hover = mb.collidepoint(pygame.mouse.get_pos()) and not self.panel.open
        if live:
            screen.blit(self.gfx.panel(mb.size, mb.h // 2, (*th["accent"], 255)), mb)
            fg = on_color(th["accent"])
        else:
            self.pill(screen, mb, mix(th["panel"], (255, 255, 255), 0.2 if hover else 0.0), 0.45)
            fg = th["text"]
        pic = self.people_icon(int(bh * 0.46), fg)
        screen.blit(pic, (mb.x + int(bh * 0.3), cy - pic.get_height() // 2))
        self.text(screen, label, bh * 0.36, fg, "heavy", midleft=(mb.x + int(bh * 0.3) + pic.get_width() + int(bh * 0.12), cy))
        self.hits.append(mb)

        speed = g.rule("speed")
        info = f"{g.mode}  ·  {speed}"
        if g.mode == "Rush":
            info += f"  ·  x{(1 / g.interval()) / SPEEDS[speed]:.1f}"
        size = ph * 0.4
        tw = self.text_size(info, size, "semi")[0]
        cx = w // 2
        if cx - tw // 2 > left_end + pad and cx + tw // 2 < mb.x - pad:
            self.text(screen, info, size, th["muted"], "semi", center=(cx, cy))
        elif mb.x - pad - tw > left_end + pad:
            self.text(screen, info, size, th["muted"], "semi", midright=(mb.x - pad, cy))

    def people_icon(self, px, color):
        def build():
            S = 4
            s = pygame.Surface((px * S, px * S), pygame.SRCALPHA)
            s.fill((*color, 0))
            u = px * S / 100
            back = (*color, 150)
            pygame.draw.circle(s, back, (int(66 * u), int(32 * u)), int(15 * u))
            pygame.draw.ellipse(s, back, (int(44 * u), int(54 * u), int(48 * u), int(40 * u)))
            pygame.draw.circle(s, color, (int(38 * u), int(34 * u)), int(18 * u))
            pygame.draw.ellipse(s, color, (int(10 * u), int(58 * u), int(56 * u), int(42 * u)))
            return pygame.transform.smoothscale(s, (px, px))
        return self.cached(("people", color), px, build)

    def build_board(self, th, cols, rows, cell, wrap, photo=None):
        w, h = cols * cell, rows * cell
        rad = self.board_radius()
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        for y in range(rows):
            for x in range(cols):
                surf.fill(th["board_a"] if (x + y) % 2 == 0 else th["board_b"], (x * cell, y * cell, cell, cell))
        if photo is not None:
            surf.blit(cover_scale(photo, (w, h)), (0, 0))
            surf.fill((0, 0, 0, 255), special_flags=pygame.BLEND_RGBA_MAX)
            tint = pygame.Surface((w, h), pygame.SRCALPHA)
            tint.fill((0, 0, 0, 0))
            for y in range(rows):
                for x in range(cols):
                    if (x + y) % 2 == 0:
                        tint.fill((255, 255, 255, 24), (x * cell, y * cell, cell, cell))
            surf.blit(tint, (0, 0))
            surf.blit(make_dim((w, h), 40), (0, 0))
        shade = pygame.Surface((1, 32), pygame.SRCALPHA)
        for y in range(32):
            shade.set_at((0, y), (255, 255, 255, int(22 * (1 - y / 31) ** 2)))
        surf.blit(pygame.transform.smoothscale(shade, (w, max(2, h // 3))), (0, 0))
        bw = max(2, cell // 12)
        if wrap:
            dash, gap = max(4, cell // 3), max(3, cell // 4)
            col = mix(th["frame"], th["board_a"], 0.25)
            for x in range(rad, w - rad, dash + gap):
                for y in (bw // 2, h - bw - bw // 2):
                    surf.fill(col, (x, y, dash, bw))
            for y in range(rad, h - rad, dash + gap):
                for x in (bw // 2, w - bw - bw // 2):
                    surf.fill(col, (x, y, bw, dash))
        surf.blit(self.gfx.mask((w, h), rad), (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        if not wrap:
            surf.blit(self.gfx.panel((w, h), rad, (*th["frame"], 0), (*th["frame"], 255), bw), (0, 0))
        surf.blit(self.gfx.panel((w, h), rad, (255, 255, 255, 0), (255, 255, 255, 50), 1), (0, 0))
        return surf.convert_alpha()

    def draw_board(self, screen, th):
        g, cell, br = self.fg, self.cell, self.board
        wrap = g.mode == "No Borders"
        photo = self.photos["bg_photo"] if self.cfg["bg_photo"] == "On" else None
        surf = self.cached("board", (self.cfg["theme"], g.cols, g.rows, cell, wrap,
                                     self.photo_ver if photo is not None else 0),
                           lambda: self.build_board(th, g.cols, g.rows, cell, wrap, photo))
        ox, oy = br.x, br.y
        if self.shake > 0:
            amp = cell * 0.14 * self.shake
            ox += int(random.uniform(-amp, amp))
            oy += int(random.uniform(-amp, amp))
        screen.blit(surf, (ox, oy))

        screen.set_clip(pygame.Rect(ox, oy, br.w, br.h))
        self.draw_walls(screen, th, ox, oy)
        self.draw_foods(screen, ox, oy)
        self.draw_apple(screen, th, ox, oy)
        self.draw_rings(screen, th, ox, oy)
        self.draw_snakes(screen, ox, oy)
        for x, y, vx, vy, life, total, color, size in self.particles:
            s = size * cell * (life / total) * 1.6
            if s >= 1:
                self.gfx.rrect(screen, color, (ox + x * cell - s / 2, oy + y * cell - s / 2, s, s), int(s // 3))
        screen.set_clip(None)

    def draw_walls(self, screen, th, ox, oy):
        g, cell = self.fg, self.cell
        for (x, y), born in g.walls.items():
            size = int(cell * 0.92 * ease_out_back((g.clock - born) / 0.28))
            if size < 2:
                continue
            r = pygame.Rect(0, 0, size, size)
            r.center = (int(ox + (x + 0.5) * cell), int(oy + (y + 0.5) * cell))
            bev = max(2, size // 9)
            rad = max(2, size // 4)
            self.gfx.rrect(screen, th["wall_lo"], r, rad)
            self.gfx.rrect(screen, th["wall"], (r.x, r.y, r.w - bev, r.h - bev), rad)
            self.gfx.rrect(screen, th["wall_hi"], (r.x + bev * 1.5, r.y + bev * 1.5, max(2, r.w // 3), bev), bev // 2)

    def draw_foods(self, screen, ox, oy):
        g, cell = self.fg, self.cell
        kind = self.cfg["fruit"]
        sprite = self.fruit_sprite(kind, cell, "board")
        skey = (kind, cell, self.photo_ver if self.fruit_photo_on() else 0)
        shadow = self.cached("shadow", cell, lambda: self.build_shadow(cell))

        def blit_fruit(x, y, scale, bob):
            px, py = ox + x * cell, oy + y * cell
            if scale > 0.3:
                screen.blit(shadow, (int(px + cell * 0.2), int(py + cell * 0.76)))
            if abs(scale - 1) < 0.02:
                screen.blit(sprite, (int(px), int(py + bob)))
            elif scale > 0.02:
                sz = max(1, int(round(cell * scale / 2)) * 2)
                s = self.scaled(sprite, skey, sz)
                screen.blit(s, (int(px + (cell - sz) / 2), int(py + (cell - sz) / 2 + bob)))

        now = self.time
        for f in g.foods:
            x, y = f["cell"]
            bob = math.sin(now * 4 + x * 1.3 + y * 0.7) * cell * 0.03
            blit_fruit(x, y, ease_out_back((g.clock - f["born"]) / 0.3), bob)
        for fx in self.eat_fx:
            p = (now - fx["start"]) / fx["dur"]
            blit_fruit(fx["cell"][0], fx["cell"][1], 1.0 - smoothstep((p - 0.55) / 0.45), 0)

    def draw_rings(self, screen, th, ox, oy):
        cell = self.cell
        for x, y, born in self.rings:
            p = (self.time - born) / 0.4
            radius = cell * (0.3 + 1.0 * p)
            width = max(1, cell * 0.14 * (1 - p))
            col = mix((255, 255, 255), th["accent"], p)
            self.gfx.ring(screen, (*col, 255), (ox + x * cell, oy + y * cell), radius, width)

    def draw_apple(self, screen, th, ox, oy):
        g, cell = self.fg, self.cell
        a = g.apple
        if a is None or not a.alive:
            return
        t = g.t
        x = lerp(a.h_from[0], a.h_to[0], t) + 0.5
        y = lerp(a.h_from[1], a.h_to[1], t) + 0.5
        bob = math.sin(self.time * 7) * cell * 0.03
        cx, cy = ox + x * cell, oy + y * cell + bob
        ring = cell * (0.62 + 0.04 * math.sin(self.time * 6))
        rw = max(3, cell // 12)
        self.gfx.ring(screen, (30, 30, 50, 255), (cx, cy), ring + 1, rw + 2)
        self.gfx.ring(screen, (*th["accent"], 255), (cx, cy), ring, rw)
        sprite = self.fruit_sprite(self.cfg["fruit"], cell, "board")
        screen.blit(sprite, (int(cx - cell / 2), int(cy - cell / 2)))
        if self.fruit_photo_on():
            return
        heads = [v.pts[0] for v, s in zip(self.views, g.snakes) if s.alive and v.pts]
        if not heads and self.views and self.views[0].pts:
            heads = [self.views[0].pts[0]]
        if not heads:
            return
        hx, hy = min(heads, key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)
        dx, dy = hx - x, hy - y
        dist = math.hypot(dx, dy) or 1.0
        er = cell * (0.135 if dist < 3.5 else 0.105)
        for side in (-1, 1):
            ex, ey = cx + side * cell * 0.17, cy - cell * 0.02
            self.gfx.disc(screen, (30, 30, 50, 255), (ex, ey), er + 2)
            self.gfx.disc(screen, (255, 255, 255, 255), (ex, ey), er)
            self.gfx.disc(screen, (20, 22, 40, 255), (ex + dx / dist * er * 0.5, ey + dy / dist * er * 0.5),
                          max(1, er * 0.5))

    @staticmethod
    def build_shadow(cell):
        w, h = int(cell * 0.6), max(2, int(cell * 0.18))
        s = pygame.Surface((w * 4, h * 4), pygame.SRCALPHA)
        s.fill((0, 0, 0, 0))
        pygame.draw.ellipse(s, (0, 0, 0, 60), s.get_rect())
        return pygame.transform.smoothscale(s, (w, h))

    def draw_snakes(self, screen, ox, oy):
        g = self.fg
        for i in range(min(len(self.views), len(g.snakes))):
            if not g.snakes[i].gone and self.views[i].pts:
                self.draw_one_snake(screen, ox, oy, i)

    def draw_one_snake(self, screen, ox, oy, i):
        g, cell, st = self.fg, self.cell, SNAKE_STYLES[self.style_of(i)]
        pts = self.views[i].pts
        offsets = [(0, 0)]
        if g.mode == "No Borders":
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            kx0 = math.floor((-max(xs) - 0.5) / g.cols) + 1
            kx1 = math.ceil((g.cols - min(xs) + 0.5) / g.cols) - 1
            ky0 = math.floor((-max(ys) - 0.5) / g.rows) + 1
            ky1 = math.ceil((g.rows - min(ys) + 0.5) / g.rows) - 1
            offsets = [(kx * g.cols, ky * g.rows) for kx in range(kx0, kx1 + 1) for ky in range(ky0, ky1 + 1)]
        body_w = cell * 0.78
        half = body_w / 2
        ol = max(2, round(cell * 0.07))
        radius = int(body_w * st["radius"])
        dead = not g.snakes[i].alive
        body_col = mix(st["body"], (120, 120, 130), 0.35) if dead else st["body"]
        for sx, sy in offsets:
            P = [(ox + (x + sx) * cell, oy + (y + sy) * cell) for x, y in pts]
            rects = []
            for x0, y0, x1, y1 in build_runs(P):
                l, t = round(x0 - half), round(y0 - half)
                rects.append(pygame.Rect(l, t, round(x1 + half) - l, round(y1 + half) - t))
            for r in rects:
                self.gfx.rrect(screen, st["outline"], r.inflate(2 * ol, 2 * ol), radius + ol)
            for r in rects:
                self.gfx.rrect(screen, body_col, r, radius)
            if st["shine"] and not dead:
                for r in rects:
                    if r.w >= r.h:
                        hl = (r.x + 0.22 * body_w, r.y + 0.14 * body_w, r.w - 0.44 * body_w, 0.2 * body_w)
                    else:
                        hl = (r.x + 0.14 * body_w, r.y + 0.22 * body_w, 0.2 * body_w, r.h - 0.44 * body_w)
                    if hl[2] > 1 and hl[3] > 1:
                        self.gfx.rrect(screen, st["shine"], hl, int(min(hl[2], hl[3]) // 2))
            if st["seam"]:
                for x, y, horizontal in seam_points(P, 1.5 * cell, cell):
                    a, b = (0, half - ol), (0, -(half - ol))
                    if not horizontal:
                        a, b = (half - ol, 0), (-(half - ol), 0)
                    pygame.draw.line(screen, st["seam"], (x + a[0], y + a[1]), (x + b[0], y + b[1]), max(2, cell // 14))
            self.draw_head(screen, P[0][0], P[0][1], i)

    def draw_head(self, screen, cx, cy, i):
        g, cell = self.fg, self.cell
        name, view, snake = self.style_of(i), self.views[i], g.snakes[i]
        dead = not snake.alive
        photo = i == self.photo_snake() and self.head_photo() is not None
        face_q = int(round((view.face % math.tau) / math.tau * 72)) % 72
        mouth_q = int(round(clamp(view.mouth, 0, 1) * 12))
        look_q = (int(round(view.look[0] * 5)), int(round(view.look[1] * 5)))
        blink = self.blink_left > 0 and not dead
        key = (name, cell, face_q, mouth_q, look_q if not photo else 0, blink and not photo, dead, photo,
               self.photo_ver if photo else 0)
        sprite = self.heads.get(key, lambda: self.build_head(name, cell, face_q, mouth_q, look_q, blink, dead, photo))
        P = sprite.get_width()
        screen.blit(sprite, (int(round(cx - P / 2)), int(round(cy - P / 2))))

    def build_head(self, name, cell, face_q, mouth_q, look_q, blinking, dead, photo):
        st = SNAKE_STYLES[name]
        SSH = 3
        P = int(cell * 1.9) + 2
        big = pygame.Surface((P * SSH, P * SSH), pygame.SRCALPHA)
        big.fill((*st["outline"], 0))
        cc = (P * SSH / 2, P * SSH / 2)
        u = cell * 0.96 * SSH
        ol = max(2, round(cell * 0.07)) * SSH
        mouth = mouth_q / 12
        if photo:
            rf = max(st["radius"], 0.30)
            size = int(round(u * (1 + 0.10 * mouth)))
            rect = pygame.Rect(0, 0, size, size)
            rect.center = ip(cc)
            pygame.draw.rect(big, st["outline"], rect.inflate(2 * ol, 2 * ol), border_radius=int(size * rf) + ol)
            big.blit(build_head_photo(self.photos["head_photo"], size, rf), rect)
            if dead:
                big.blit(make_dim((size, size), 120, int(size * rf)), rect)
            return pygame.transform.smoothscale(big, (P, P))
        rad = max(2, int(u * st["radius"]))
        head = pygame.Rect(0, 0, int(u), int(u))
        head.center = ip(cc)
        pygame.draw.rect(big, st["outline"], head.inflate(2 * ol, 2 * ol), border_radius=rad + ol)
        head_col = mix(st["head"], (120, 120, 130), 0.35) if dead else st["head"]
        pygame.draw.rect(big, head_col, head, border_radius=rad)
        if st["shine"] and not dead:
            hl = pygame.Rect(head.x + int(u * 0.16), head.y + int(u * 0.1), int(u * 0.68), int(u * 0.14))
            sh = pygame.Surface(hl.size, pygame.SRCALPHA)
            sh.fill((0, 0, 0, 0))
            pygame.draw.rect(sh, (*st["shine"], 120), sh.get_rect(), border_radius=hl.h // 2)
            big.blit(sh, hl)

        face = face_q / 72 * math.tau
        fx, fy = math.cos(face), math.sin(face)
        qx, qy = -fy, fx
        corner = st["radius"]

        def at(a, b):
            return ip((cc[0] + (fx * a + qx * b) * u, cc[1] + (fy * a + qy * b) * u))

        def at_inside(a, b):
            x, y = fx * a + qx * b, fy * a + qy * b
            lim = 0.5 - 0.035
            r = max(corner * 0.9, 0.02)
            core = lim - r
            sx, sy = (1 if x >= 0 else -1), (1 if y >= 0 else -1)
            ax, ay = abs(x), abs(y)
            dx, dy = ax - core, ay - core
            if dx > 0 and dy > 0:
                d = math.hypot(dx, dy)
                if d > r:
                    ax, ay = core + dx * r / d, core + dy * r / d
            ax, ay = min(ax, lim), min(ay, lim)
            return ip((cc[0] + sx * ax * u, cc[1] + sy * ay * u))

        o = mouth
        if o > 0.04:
            def wedge(back, front_f, front_w, grow):
                pts = [(back, 0.06 * grow * o), (0.18, 0.22 * grow * o), (front_f, front_w * o)]
                return [at_inside(a, b) for a, b in pts] + [at_inside(a, -b) for a, b in reversed(pts)]
            pygame.draw.polygon(big, (140, 18, 32), wedge(-0.22, 0.50, 0.44, 1.0))
            pygame.draw.polygon(big, (236, 52, 66), wedge(-0.15, 0.45, 0.34, 0.8))
            if o > 0.5:
                pygame.draw.polygon(big, (255, 140, 150), [at_inside(0.14, 0.0), at_inside(0.40, 0.11 * o),
                                                           at_inside(0.40, -0.11 * o)])
        eye_r, pup_r = 0.16 * u, 0.088 * u
        lx, ly = look_q[0] / 5, look_q[1] / 5
        for side in (1, -1):
            ex, ey = at(-0.14, 0.285 * side)
            if blinking:
                dx, dy = qx * eye_r, qy * eye_r
                pygame.draw.line(big, (32, 38, 60), (ex - dx, ey - dy), (ex + dx, ey + dy), max(2, int(0.05 * u)))
                continue
            pygame.draw.circle(big, (32, 38, 60), (ex, ey), int(eye_r + 0.022 * u))
            pygame.draw.circle(big, (255, 255, 255), (ex, ey), int(eye_r))
            if dead:
                d = eye_r * 0.55
                w = max(2, int(0.045 * u))
                pygame.draw.line(big, (32, 38, 60), (ex - d, ey - d), (ex + d, ey + d), w)
                pygame.draw.line(big, (32, 38, 60), (ex - d, ey + d), (ex + d, ey - d), w)
            else:
                px_, py_ = ex + lx * 0.075 * u, ey + ly * 0.075 * u
                pygame.draw.circle(big, (18, 22, 38), ip((px_, py_)), int(pup_r))
                pygame.draw.circle(big, (255, 255, 255), ip((px_ - 0.03 * u, py_ - 0.03 * u)), max(1, int(0.028 * u)))
        return pygame.transform.smoothscale(big, (P, P))

    def sheet(self, screen, th, size, since):
        ui = self.ui
        k = smoothstep((self.time - since) / 0.22)
        card = pygame.Rect(0, 0, int(size[0] * ui), int(size[1] * ui))
        card.center = (self.w // 2, self.h // 2 + int((1 - k) * 24 * ui))
        self.dim_screen(screen, 150, since)
        self.glass(screen, card, int(26 * ui), th["panel"], 0.72)
        return card

    def draw_drop_card(self, screen, th):
        self.drop_rects = {}
        if self.pending_drop is None:
            return
        ui = self.ui
        if not hasattr(self, "drop_since") or self.drop_since[0] != self.pending_drop:
            self.drop_since = (self.pending_drop, self.time)
        card = self.sheet(screen, th, (600, 230), self.drop_since[1])
        name = Path(self.pending_drop).name
        if len(name) > 40:
            name = name[:37] + "..."
        self.text(screen, "Use this photo as", 26 * ui, th["text"], "heavy", center=(card.centerx, card.y + int(38 * ui)))
        self.text(screen, name, 15 * ui, th["muted"], "regular", center=(card.centerx, card.y + int(70 * ui)))
        pad = int(20 * ui)
        bw, bh = (card.w - 4 * pad) // 3, int(50 * ui)
        choices = (("bg_photo", "Background (B)"), ("head_photo", "Head (H)"), ("fruit_photo", "Fruit (F)"))
        for i, (kind, label) in enumerate(choices):
            r = pygame.Rect(card.x + pad + i * (bw + pad), card.y + int(102 * ui), bw, bh)
            self.drop_rects[kind] = r
            self.button(screen, th, r, label, True, 17 * ui)
        self.text(screen, "Esc to cancel", 14 * ui, th["muted"], "regular", center=(card.centerx, card.bottom - int(30 * ui)))

    def draw_toast(self, screen, th):
        if self.time >= self.toast_until or self.panel.open:
            return
        ui = self.ui
        t_in = smoothstep((self.time - self.toast_at) / 0.25)
        t_out = smoothstep((self.toast_until - self.time) / 0.25)
        k = min(t_in, t_out)
        size = 16 * ui
        tw, th_ = self.text_size(self.toast_text, size, "semi")
        r = pygame.Rect(0, 0, tw + int(44 * ui), th_ + int(22 * ui))
        r.midbottom = (self.w // 2, self.h - int(22 * ui) + int((1 - k) * 60 * ui))
        self.glass(screen, r, r.h // 2, th["panel"], 0.6)
        self.text(screen, self.toast_text, size, th["text"], "semi", center=r.center)

    def style_of(self, i):
        g = self.fg
        if g.styles:
            slot = g.slot_of_snake(i)
            if slot is not None and slot < len(g.styles):
                return g.styles[slot]
        return self.cfg["snake"]

    def label(self, slot):
        n = self.fg.players
        if self.frole == "local":
            return f"P{slot + 1}"
        if slot == self.fidx:
            return "You"
        return "Opponent" if n == 2 else f"P{slot + 1}"

    def order(self):
        n = self.fg.players
        if self.frole == "local":
            return list(range(n))
        return [self.fidx] + [i for i in range(n) if i != self.fidx]

    def photo_snake(self):
        g = self.fg
        if self.frole == "local":
            first = g.snake_of(0)
            return first if first is not None else g.snake_of(1)
        if g.apple is not None and g.players == 1:
            return 0
        return g.snake_of(self.fidx)

    def shown_value(self, key):
        if self.role == "client" and key in LOCKED_FOR_CLIENT:
            return self.game.remote_rules[key]
        return self.cfg[key]

    def open_mp_menu(self):
        self.panel.open = False
        self.mp_msg = ""
        self.set_mp_screen("menu")

    def set_mp_screen(self, scr):
        if scr is not None and self.mp_screen is None:
            self.mp_opened = self.time
        self.mp_screen = scr

    def begin_hosting(self):
        self.close_lobby()
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if sys.platform != "win32":
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            for port in range(NET_PORT, NET_PORT + 10):
                try:
                    srv.bind(("", port))
                    break
                except OSError:
                    continue
            else:
                srv.close()
                raise OSError("no free port")
            srv.listen(MAX_PLAYERS)
            srv.setblocking(False)
            beacon = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            beacon.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            beacon.setblocking(False)
        except OSError as exc:
            self.mp_msg = f"Couldn't start hosting: {exc}"
            return
        self.server, self.beacon, self.host_port, self.my_ip = srv, beacon, port, local_ip()
        self.next_beacon = 0.0
        self.mp_screen = "host_wait"

    def begin_join(self):
        self.close_lobby()
        try:
            d = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if sys.platform != "win32":
                d.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            d.bind(("", DISCOVERY_PORT))
            d.setblocking(False)
            self.disc = d
        except OSError:
            self.disc = None
        self.mp_screen = "join"

    def connect_to(self, text):
        text = text.strip()
        if not text:
            self.mp_msg = "Type the host's IP address first."
            return
        host, _, port = text.partition(":")
        try:
            port = int(port) if port else NET_PORT
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setblocking(False)
            sock.connect_ex((host, port))
        except (OSError, ValueError):
            self.mp_msg = "That address doesn't look right."
            return
        if self.pending_conn is not None:
            self.pending_conn["sock"].close()
        self.pending_conn = {"sock": sock, "deadline": self.time + 6.0}
        self.mp_msg = f"Connecting to {host}..."

    def adopt(self, sock):
        sock.setblocking(False)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        return Net(sock)

    def close_listeners(self):
        for name in ("server", "beacon", "disc"):
            sock = getattr(self, name)
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
                setattr(self, name, None)
        if self.pending_conn is not None:
            try:
                self.pending_conn["sock"].close()
            except OSError:
                pass
            self.pending_conn = None
        self.found = {}

    def close_lobby(self):
        self.close_listeners()
        if self.role != "host":
            for p in self.peers:
                p["net"].send({"t": "bye"})
                p["net"].close()
            self.peers = []
        self.mp_msg = ""

    def close_net(self):
        for net in [self.net] + [p["net"] for p in self.peers]:
            if net is not None:
                net.send({"t": "bye"})
                net.close()
        self.net, self.peers = None, []
        self.close_listeners()
        self.mp_msg = ""

    def leave_multiplayer(self, message=None):
        self.close_net()
        self.role, self.local_idx, self.mp_screen = "solo", 0, None
        self.round_no = 0
        self.game = Game(self.cfg)
        self.finish_restart()
        if message:
            self.show_toast(message)

    def send_lobby(self):
        ready = [p for p in self.peers if p["ready"]]
        for i, p in enumerate(ready):
            p["net"].send({"t": "lobby", "you": i + 1, "count": len(ready) + 1})

    def lobby_styles(self):
        taken = [self.cfg["snake"]]
        for p in self.peers:
            if p["ready"]:
                want = p["style"]
                if want not in SNAKE_STYLES or want in taken:
                    want = next(n for n in SNAKE_STYLES if n not in taken)
                taken.append(want)
        return taken

    def start_match(self):
        ready = [p for p in self.peers if p["ready"]]
        if not ready:
            self.mp_msg = "Waiting for at least one more player."
            return
        for p in self.peers:
            if not p["ready"]:
                p["net"].close()
        self.peers = ready
        self.close_listeners()
        for p, style in zip(self.peers, self.lobby_styles()[1:]):
            p["style"] = style
        self.role, self.local_idx, self.mp_screen = "host", 0, None
        self.panel.open = False
        self.start_round()

    def drop_peer(self, peer):
        if peer not in self.peers:
            return
        peer["net"].close()
        self.peers.remove(peer)
        if self.role == "host":
            self.game.drop_player(peer["idx"])
            if not self.peers:
                self.leave_multiplayer("Everyone else left the game")
            else:
                self.show_toast(f"Player {peer['idx'] + 1} left")
        else:
            self.send_lobby()

    def start_local(self):
        self.close_net()
        mine = self.cfg["snake"]
        self.role, self.local_idx, self.mp_screen = "local", 0, None
        self.panel.open = False
        self.game = Game(self.cfg)
        self.game.styles = [mine, next(n for n in SNAKE_STYLES if n != mine)]
        self.round_no = 0
        self.start_round()

    def start_round(self):
        g = self.game
        local = self.role == "local"
        if local:
            n = 2
        else:
            self.peers = [p for p in self.peers if not p["net"].closed]
            if not self.peers:
                self.leave_multiplayer("Everyone else left the game")
                return
            for i, p in enumerate(self.peers):
                p["idx"] = i + 1
            n = len(self.peers) + 1
            g.styles = [self.cfg["snake"]] + [p["style"] for p in self.peers]
        apple_slot = None
        if self.cfg["mode"] == "Apple":
            apple_slot = (self.first_apple + self.round_no) % n
        self.round_no += 1
        g.reset(players=n, countdown=3.0, apple_slot=apple_slot)
        if not local:
            rules = {k: self.cfg[k] for k in ("map", "mode", "apples", "speed")}
            for p in self.peers:
                p["net"].send({"t": "reset", "you": p["idx"], "players": n, "styles": g.styles, "rules": rules,
                               "apple_slot": g.apple_slot})
            self.sent_tick, self.sent_state = -1, None
        self.finish_restart()

    def on_reset(self, m):
        if self.role != "client":
            self.role = "client"
            self.game = Game(self.cfg, remote=True)
            self.mp_screen, self.panel.open = None, False
            self.close_listeners()
        self.local_idx = int(m.get("you", self.local_idx))
        g = self.game
        g.remote_rules, g.styles = dict(m["rules"]), list(m["styles"])
        g.reset(players=int(m.get("players", 2)), countdown=3.0, apple_slot=m.get("apple_slot"))
        self.finish_restart()

    def on_net_message(self, m):
        kind = m.get("t")
        if kind == "bye":
            self.leave_multiplayer("The host ended the game")
        elif kind == "full":
            self.close_net()
            self.begin_join()
            self.mp_msg = f"That game is full ({MAX_PLAYERS} players max)."
        elif kind == "lobby":
            self.mp_msg = f"Connected: {m['count']}/{MAX_PLAYERS} players. Waiting for the host to start..."
        elif kind == "reset":
            self.on_reset(m)
        elif kind == "cd" and self.role == "client":
            self.game.cd = float(m["v"])
        elif kind == "state" and self.role == "client":
            self.game.apply_snapshot(m)

    def on_peer_message(self, peer, m):
        kind = m.get("t")
        g = self.game
        if kind == "hello" and not peer["ready"]:
            peer["style"], peer["ready"] = m.get("style"), True
            self.send_lobby()
        elif self.role != "host" or peer["idx"] <= 0:
            if kind == "bye":
                self.drop_peer(peer)
        elif kind == "dir":
            d = tuple(m.get("d", ()))
            if d in DIRS:
                g.steer(peer["idx"], d)
        elif kind == "rel":
            if g.apple is not None and g.apple_slot == peer["idx"]:
                g.release(tuple(m.get("d", ())))
        elif kind == "tp":
            if g.apple is not None and g.apple_slot == peer["idx"]:
                g.teleport()
        elif kind == "bye":
            self.drop_peer(peer)

    def net_update(self, dt):
        if self.server is not None:
            if self.beacon is not None and self.time >= self.next_beacon:
                self.next_beacon = self.time + 1.0
                count = 1 + sum(1 for p in self.peers if p["ready"])
                try:
                    self.beacon.sendto(f"SNAKE{NET_VERSION}:{self.host_port}:{count}".encode(),
                                       ("255.255.255.255", DISCOVERY_PORT))
                except OSError:
                    pass
            for _ in range(4):
                try:
                    conn, _addr = self.server.accept()
                except OSError:
                    break
                net = self.adopt(conn)
                if len(self.peers) >= MAX_PLAYERS - 1:
                    net.send({"t": "full"})
                    net.close()
                else:
                    self.peers.append({"net": net, "style": None, "ready": False, "idx": 0, "rx": self.time})

        if self.disc is not None:
            for _ in range(8):
                try:
                    data, addr = self.disc.recvfrom(256)
                except OSError:
                    break
                if data.startswith(f"SNAKE{NET_VERSION}:".encode()):
                    try:
                        parts = data.split(b":")
                        count = int(parts[2]) if len(parts) > 2 else 1
                        self.found[addr[0]] = (int(parts[1]), self.time, count)
                    except ValueError:
                        pass
            self.found = {ip: v for ip, v in self.found.items() if self.time - v[1] < 4.0}

        pc = self.pending_conn
        if pc is not None:
            sock = pc["sock"]
            try:
                _r, writable, failed = select.select([], [sock], [sock], 0)
                err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            except (OSError, ValueError):
                writable, failed, err = [], [sock], 1
            if failed or (writable and err):
                sock.close()
                self.pending_conn = None
                self.mp_msg = "Couldn't connect. Same Wi-Fi? Is the host waiting?"
            elif writable:
                self.pending_conn = None
                self.close_lobby()
                self.net = self.adopt(sock)
                self.last_rx = self.last_ping = self.time
                self.net.send({"t": "hello", "v": NET_VERSION, "style": self.cfg["snake"]})
                self.mp_msg = "Connected. Waiting for the host to start..."
            elif self.time > pc["deadline"]:
                sock.close()
                self.pending_conn = None
                self.mp_msg = "Timed out. Check the address and the Wi-Fi."

        for peer in list(self.peers):
            for m in peer["net"].poll():
                peer["rx"] = self.time
                self.on_peer_message(peer, m)
                if peer not in self.peers:
                    break
            if peer in self.peers and (peer["net"].closed or self.time - peer["rx"] > 6.0):
                self.drop_peer(peer)
        if self.peers and self.time - self.last_ping > 1.0:
            self.last_ping = self.time
            for p in self.peers:
                p["net"].send({"t": "ping"})

        if self.net is not None:
            for m in self.net.poll():
                self.last_rx = self.time
                self.on_net_message(m)
                if self.net is None:
                    return
            if self.net.closed:
                self.leave_multiplayer("Disconnected from the host")
            elif self.time - self.last_rx > 6.0:
                self.leave_multiplayer("Connection lost")
            elif self.time - self.last_ping > 1.0:
                self.last_ping = self.time
                self.net.send({"t": "ping"})

    def net_send_state(self):
        if self.role != "host" or not self.peers:
            return
        g = self.game
        if g.tick_no != self.sent_tick or g.state != self.sent_state:
            snap = g.snapshot()
            for p in self.peers:
                p["net"].send(snap)
            self.sent_tick, self.sent_state = g.tick_no, g.state
        if g.state == "countdown" and self.time - self.last_cd >= 0.1:
            self.last_cd = self.time
            for p in self.peers:
                p["net"].send({"t": "cd", "v": g.cd})

    def mp_key(self, key, ch):
        scr = self.mp_screen
        if key == pygame.K_ESCAPE:
            if scr == "menu":
                self.mp_screen = None
            else:
                self.close_net()
                self.mp_screen = "menu"
        elif scr == "menu" and self.role == "solo":
            if key == pygame.K_h:
                self.begin_hosting()
            elif key == pygame.K_j:
                self.begin_join()
            elif key == pygame.K_l:
                self.start_local()
        elif scr == "host_wait":
            if key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                self.start_match()
            elif key == pygame.K_a and self.cfg["mode"] == "Apple":
                self.first_apple = (self.first_apple + 1) % (1 + sum(1 for p in self.peers if p["ready"]))
        elif scr == "join":
            if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.connect_to(self.join_text)
            elif key == pygame.K_BACKSPACE:
                self.join_text = self.join_text[:-1]
            elif ch and (ch.isalnum() or ch in ".:-") and len(self.join_text) < 40:
                self.join_text += ch

    def mp_click(self, pos):
        for name, rect in list(self.mp_rects.items()):
            if rect.collidepoint(pos):
                self.mp_action(name)
                return

    def mp_action(self, name):
        if name == "host":
            self.begin_hosting()
        elif name == "join":
            self.begin_join()
        elif name == "local":
            self.start_local()
        elif name == "apple":
            self.first_apple = (self.first_apple + 1) % (1 + sum(1 for p in self.peers if p["ready"]))
        elif name == "start":
            self.start_match()
        elif name == "close":
            self.mp_screen = None
        elif name == "leave":
            self.leave_multiplayer("Left multiplayer")
        elif name in ("cancel", "back"):
            self.close_net()
            self.mp_screen = "menu"
        elif name == "connect":
            self.connect_to(self.join_text)
        elif name.startswith("found:"):
            ip = name[6:]
            self.join_text = ip
            self.connect_to(f"{ip}:{self.found.get(ip, (NET_PORT,))[0]}")

    def draw_mp_screen(self, screen, th):
        self.mp_rects = {}
        scr = self.mp_screen
        if scr is None:
            return
        ui = self.ui
        card = self.sheet(screen, th, (560, {"menu": 440, "host_wait": 462, "join": 450}[scr]), self.mp_opened)
        cx = card.centerx

        def say(s, y, size=17, color=None, weight="regular"):
            self.text(screen, s, size * ui, color or th["muted"], weight, center=(cx, card.y + int(y * ui)))

        def button(name, label, y, width=0.72, primary=True, dx=0, height=50):
            r = pygame.Rect(0, 0, int(card.w * width), int(height * ui))
            r.centerx, r.y = cx + int(dx), card.y + int(y * ui)
            self.button(screen, th, r, label, primary, 18 * ui, name)

        apple_mode = self.cfg["mode"] == "Apple"
        if scr == "menu":
            say("Multiplayer", 42, 30, th["text"], "heavy")
            say("Wi-Fi (up to 4 players, Windows + Mac) or one PC (2 players)", 78)
            say("Apple mode: one of you is the apple, the others are the snakes" if apple_mode
                else "Tip: Game Mode Apple = real apple vs real snakes", 102, 14)
            if self.role == "solo":
                button("host", "Host a game on Wi-Fi  (H)", 134)
                button("join", "Join a game on Wi-Fi  (J)", 196, primary=False)
                button("local", "Play on this PC  (L)", 258, primary=False)
                button("close", "Close", 336, 0.4, primary=False, height=44)
            else:
                who = {"host": "the host", "client": "a guest", "local": "playing on this PC"}[self.role]
                say(f"You are {who}", 150, 20, th["text"], "semi")
                button("leave", "Leave this game", 196)
                button("close", "Close", 270, 0.4, primary=False, height=44)
            if self.mp_msg:
                say(self.mp_msg, 410, 15, th["accent"], "semi")
        elif scr == "host_wait":
            say("Hosting", 38, 30, th["text"], "heavy")
            say("Friends open Multiplayer, pick Join and type:", 76)
            ipr = pygame.Rect(0, 0, int(card.w * 0.6), int(58 * ui))
            ipr.center = (cx, card.y + int(122 * ui))
            screen.blit(self.gfx.panel(ipr.size, int(16 * ui), (0, 0, 0, 60)), ipr)
            self.text(screen, self.my_ip, 36 * ui, th["accent"], "heavy", center=ipr.center)
            say(f"port {self.host_port}  ·  or they pick this game from the list", 168, 14)
            ready = [p for p in self.peers if p["ready"]]
            lobby_styles = self.lobby_styles()
            for i in range(MAX_PLAYERS):
                y = card.y + int((204 + i * 28) * ui)
                taken = i == 0 or i - 1 < len(ready)
                name = "Player 1 (you, host)" if i == 0 else (f"Player {i + 1} joined" if taken else "waiting...")
                if taken:
                    st = SNAKE_STYLES[lobby_styles[i]]
                    c = int(16 * ui)
                    r = pygame.Rect(cx - int(118 * ui), y - c // 2, c, c)
                    self.gfx.rrect(screen, st["outline"], r, int(c * 0.3))
                    self.gfx.rrect(screen, st["body"], r.inflate(-4, -4), int(c * 0.25))
                else:
                    self.gfx.ring(screen, (*th["muted"], 160), (cx - int(110 * ui), y), 7 * ui, max(1, 1.5 * ui))
                self.text(screen, name, 17 * ui, th["text"] if taken else th["muted"], "semi" if taken else "regular",
                          midleft=(cx - int(90 * ui), y))
            n = len(ready) + 1
            if apple_mode:
                button("apple", f"First apple: Player {self.first_apple % n + 1}   (change: A)", 322, 0.8, False, height=36)
            if self.mp_msg:
                say(self.mp_msg, 366, 14, th["accent"], "semi")
            button("start", f"Start game ({n} players)" if ready else "Start (need 2+ players)", 384, 0.5, bool(ready),
                   dx=-card.w * 0.2)
            button("cancel", "Cancel", 384, 0.28, False, dx=card.w * 0.3)
        elif scr == "join":
            say("Join a game", 42, 30, th["text"], "heavy")
            say("Type the host's IP address and press Enter", 80)
            box = pygame.Rect(0, 0, int(card.w * 0.72), int(50 * ui))
            box.centerx, box.y = cx, card.y + int(106 * ui)
            screen.blit(self.gfx.panel(box.size, int(14 * ui), (0, 0, 0, 70), (*th["accent"], 220), max(2, int(2 * ui))), box)
            caret = "|" if int(self.time * 2) % 2 == 0 else " "
            self.text(screen, self.join_text + caret, 22 * ui, th["text"], "semi", center=box.center)
            y = 172
            for ip_, (port, _seen, count) in list(self.found.items())[:2]:
                button(f"found:{ip_}", f"{ip_}   ({count}/{MAX_PLAYERS} players)", y, primary=False, height=46)
                y += 56
            if not self.found:
                dots = "." * (1 + int(self.time * 2) % 3)
                say(f"Looking for games on this Wi-Fi{dots}", 196, 15)
            if self.mp_msg:
                say(self.mp_msg, 316, 15, th["accent"], "semi")
            button("connect", "Connect", 368, 0.34, True, dx=-card.w * 0.19)
            button("back", "Back", 368, 0.34, False, dx=card.w * 0.19)

    def draw_state_cards(self, screen, th, br):
        g = self.fg
        text, muted, acc = th["text"], th["muted"], th["accent"]
        role, me = self.frole, self.fidx

        def dim():
            rad = self.board_radius()
            screen.blit(self.cached("dim_board", (br.size, rad),
                                    lambda: self.gfx.panel(br.size, rad, (0, 0, 0, 110))), br.topleft)

        if g.state == "countdown":
            lines = [(str(max(1, math.ceil(g.cd))), 72, text)]
            if g.apple is not None:
                if role == "local":
                    keys = "arrow keys" if g.apple_slot == 0 else "WASD"
                    lines += [(f"P{g.apple_slot + 1} is the APPLE ({keys}, R teleports)", 18, text),
                              (f"{APPLE_TIME:.0f} seconds to survive, otherwise a snake eats you", 15, muted)]
                elif g.apple_slot == me:
                    lines += [("You are the APPLE: run! Hold a key to move, R teleports", 18, text),
                              (f"Survive {APPLE_TIME:.0f} seconds or make every snake crash", 15, muted)]
                else:
                    lines += [("You are a SNAKE: catch the apple!", 18, text),
                              (f"The first snake to eat it wins. Time limit {APPLE_TIME:.0f}s", 15, muted)]
            elif role == "local":
                lines += [("P1: arrow keys      P2: WASD", 18, text), ("Last snake alive wins", 15, muted)]
            else:
                lines += [(f"Get ready: {g.players} snakes start together", 16, muted)]
            spot = (br.centerx, br.y + br.h * 0.2) if g.apple is not None else br.center
            self.card(screen, th, spot, lines)
        elif g.state == "idle":
            bob = math.sin(self.time * 3) * 3 * self.ui
            if g.apple is not None:
                lines = [("You are the apple! Hold an arrow key or WASD to run", 21, text),
                         ("R teleports you (3x)    Backspace restarts    P pause    Esc settings", 14, muted)]
            else:
                lines = [("Press an arrow key or WASD to start", 21, text),
                         ("P pause    R restart    M multiplayer    Esc settings    F11 fullscreen", 14, muted)]
            self.card(screen, th, (br.centerx, br.bottom - br.h * 0.17 + bob), lines)
        elif self.fpaused:
            dim()
            self.card(screen, th, br.center, [("Paused", 38, text), ("Press P to resume", 17, muted)])
        elif g.state == "dead" and g.clock - g.dead_at > 0.45:
            dim()
            if g.players == 1:
                best = max(self.fbest.get(g.mode, 0), g.score)
                title = ("You trapped the snake!" if g.win else "You got eaten!") if g.apple is not None \
                    else ("You win!" if g.win else "Game over")
                lines = [(title, 40, text), (f"Score {g.score}     Best {best}", 22, acc)]
                if g.new_best:
                    lines.append(("New high score!", 18, acc))
                lines.append(("Press Space or Enter to play again", 15, muted))
            else:
                if g.winner == -1:
                    title = "Draw"
                elif role == "local":
                    title = f"{self.label(g.winner)} wins!"
                elif g.winner == me:
                    title = "You win!"
                else:
                    title = "You lose" if g.players == 2 else f"Player {g.winner + 1} wins"
                again = ("Press Space or Enter for the next round" if role in ("host", "local")
                         else "Waiting for the host to start the next round...")
                if g.apple is not None:
                    why = {"eaten": "The apple got eaten", "time": "The apple survived!",
                           "crashed": "Every snake crashed", "left": "A player left"}.get(g.why, "")
                    lines = [(title, 40, text), (why, 22, acc),
                             (f"Apple this round: {self.label(g.apple_slot)}  (roles rotate)", 15, muted), (again, 15, muted)]
                else:
                    scores = "   ".join(f"{self.label(i)} {g.snakes[i].score}" for i in self.order())
                    lines = [(title, 40, text), (scores, 22 if g.players == 2 else 19, acc), (again, 15, muted)]
            self.card(screen, th, br.center, lines)
        elif g.state == "running" and g.players > 1 and role != "local":
            mine = g.snake_of(me)
            if mine is not None and not g.snakes[mine].alive:
                self.card(screen, th, (br.centerx, br.y + 46 * self.ui),
                          [("You're out. Watching until the round ends.", 17, text)])


def main():
    try:
        App().run()
    except Exception:
        report_fatal("Snake crashed", traceback.format_exc())
        _close_crash_log()
        PLATFORM.close()
        raise SystemExit(1)
    _close_crash_log()


if __name__ == "__main__":
    main()
