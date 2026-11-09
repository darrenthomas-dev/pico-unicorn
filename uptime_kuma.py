# =========================
# UPTIME KUMA MONITOR
# =========================
# Polls Uptime Kuma's Prometheus /metrics endpoint and shows status on the
# Galactic Unicorn:
#
#   * a monitor goes DOWN  -> red background, scrolls "<domain> DOWN"
#                             (repeats for as long as it stays down)
#   * a monitor comes UP   -> green background, scrolls "<domain> UP"
#                             a few times, then goes back to idle
#
# Required in config.py:
#   WIFI_SSID, WIFI_PASS
#   KUMA_URL      = "http://192.168.1.10:3001"   (no trailing slash)
#   KUMA_API_KEY  = "uk1_xxxxxxxx"                (Settings -> API Keys)
# Optional:
#   KUMA_POLL_SECONDS = 30
#   KUMA_UP_REPEATS   = 2

import time
import gc
import network
import ubinascii
import urequests
import config

from galactic import GalacticUnicorn
from picographics import PicoGraphics
from picographics import DISPLAY_GALACTIC_UNICORN as DISPLAY


KUMA_URL = config.KUMA_URL.rstrip("/")
KUMA_API_KEY = config.KUMA_API_KEY
POLL_SECONDS = getattr(config, "KUMA_POLL_SECONDS", 30)
UP_REPEATS = getattr(config, "KUMA_UP_REPEATS", 2)

# Uptime Kuma status codes
STATUS_DOWN = 0
STATUS_UP = 1
STATUS_PENDING = 2
STATUS_MAINTENANCE = 3

# =========================
# DISPLAY
# =========================
galactic = GalacticUnicorn()
graphics = PicoGraphics(DISPLAY)
graphics.set_font("bitmap8")

WIDTH = GalacticUnicorn.WIDTH
HEIGHT = GalacticUnicorn.HEIGHT

brightness = 0.35
galactic.set_brightness(brightness)

BLACK = graphics.create_pen(0, 0, 0)
WHITE = graphics.create_pen(255, 255, 255)
RED = graphics.create_pen(160, 0, 0)
GREEN = graphics.create_pen(0, 140, 0)
DIM = graphics.create_pen(0, 20, 0)


# =========================
# WIFI
# =========================
def connect_wifi():

    print("[WIFI] connecting...")

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        return True

    wlan.connect(config.WIFI_SSID, config.WIFI_PASS)

    timeout = 20
    while not wlan.isconnected() and timeout > 0:
        time.sleep(1)
        timeout -= 1
        print("[WIFI] waiting...")

    if wlan.isconnected():
        print("[WIFI] connected", wlan.ifconfig())
        return True

    print("[WIFI] FAILED")
    return False


# =========================
# UPTIME KUMA
# =========================
AUTH_HEADER = "Basic " + ubinascii.b2a_base64(
    (":" + KUMA_API_KEY).encode()
).decode().strip()


def get_label(line, name):
    # Pull label="value" out of a Prometheus metric line
    key = name + '="'
    start = line.find(key)
    if start < 0:
        return None
    start += len(key)
    end = line.find('"', start)
    if end < 0:
        return None
    value = line[start:end]
    if value in ("", "null"):
        return None
    return value


def host_from_url(url):
    # "https://www.example.com:443/path" -> "www.example.com"
    if "://" in url:
        url = url.split("://", 1)[1]
    url = url.split("/", 1)[0]
    url = url.split("@")[-1]
    return url.split(":", 1)[0]


def display_name(line):
    url = get_label(line, "monitor_url")
    if url and "://" in url:
        host = host_from_url(url)
        if host:
            return host

    host = get_label(line, "monitor_hostname")
    if host:
        return host

    return get_label(line, "monitor_name") or "unknown"


def fetch_statuses():
    # Returns {name: status} or None on failure.
    # The metrics response is read one line at a time to save RAM.
    gc.collect()
    statuses = {}
    r = None

    try:
        r = urequests.get(
            KUMA_URL + "/metrics",
            headers={"Authorization": AUTH_HEADER}
        )

        if r.status_code != 200:
            print("[KUMA] HTTP", r.status_code)
            return None

        while True:
            raw = r.raw.readline()
            if not raw:
                break
            if not raw.startswith(b"monitor_status{"):
                continue

            line = raw.decode().strip()
            try:
                status = int(float(line.rsplit(" ", 1)[1]))
            except Exception:
                continue

            statuses[display_name(line)] = status

        return statuses

    except Exception as e:
        print("[KUMA ERROR]", e)
        return None

    finally:
        if r:
            r.close()
        gc.collect()


# =========================
# STATE
# =========================
known = {}          # name -> last status
down = []           # names currently down (in order they went down)
up_queue = []       # names that just recovered, waiting to be announced

mode = "IDLE"       # IDLE | DOWN | UP
scroll_text = ""
scroll_x = WIDTH
scroll_width = 0
up_passes_left = 0
down_index = 0


def update_states(statuses):
    global known

    first_poll = not known

    for name, status in statuses.items():
        previous = known.get(name)

        if status == STATUS_DOWN:
            if name not in down:
                down.append(name)
                print("[KUMA] DOWN", name)
            if name in up_queue:
                up_queue.remove(name)

        elif status == STATUS_UP:
            if name in down:
                down.remove(name)
            if previous == STATUS_DOWN and not first_poll:
                print("[KUMA] UP", name)
                if name not in up_queue:
                    up_queue.append(name)

        # pending / maintenance: leave the current state alone

    # Monitors that were deleted / paused shouldn't stay stuck as down
    for name in list(down):
        if name not in statuses:
            down.remove(name)

    known = statuses


def start_scroll(text):
    global scroll_text, scroll_x, scroll_width
    scroll_text = text
    scroll_x = WIDTH
    scroll_width = graphics.measure_text(text, scale=1)


def choose_next():
    # Called whenever a scroll finishes (or when idle) to decide what to show
    global mode, up_passes_left, down_index

    # Recoveries get announced first so you see the green as soon as it's back
    if mode == "UP" and up_passes_left > 0:
        up_passes_left -= 1
        start_scroll(scroll_text)
        return

    if up_queue:
        name = up_queue.pop(0)
        mode = "UP"
        up_passes_left = UP_REPEATS - 1
        start_scroll(name + " UP")
        return

    if down:
        mode = "DOWN"
        down_index %= len(down)
        start_scroll(down[down_index] + " DOWN")
        down_index += 1
        return

    mode = "IDLE"


# =========================
# DRAW
# =========================
def draw():

    if mode == "DOWN":
        graphics.set_pen(RED)
    elif mode == "UP":
        graphics.set_pen(GREEN)
    else:
        graphics.set_pen(BLACK)
    graphics.clear()

    if mode == "IDLE":
        # tiny heartbeat dot so you know it's alive and all is well
        if (time.ticks_ms() // 1000) % 2 == 0:
            graphics.set_pen(DIM)
            graphics.pixel(WIDTH - 1, HEIGHT - 1)
        return

    graphics.set_pen(WHITE)
    graphics.text(scroll_text, scroll_x, 2, -1, 1)


# =========================
# STARTUP
# =========================
print("=== UPTIME KUMA ===")
connect_wifi()

last_poll = time.ticks_add(time.ticks_ms(), -POLL_SECONDS * 1000)


# =========================
# MAIN LOOP
# =========================
while True:

    now = time.ticks_ms()

    # -------------------------
    # POLL
    # -------------------------
    if time.ticks_diff(now, last_poll) >= POLL_SECONDS * 1000:
        last_poll = now

        if connect_wifi():
            statuses = fetch_statuses()
            if statuses is not None:
                update_states(statuses)
                print("[KUMA]", len(statuses), "monitors,", len(down), "down")

        # If idle, start showing anything new straight away
        if mode == "IDLE":
            choose_next()

    # -------------------------
    # SCROLL
    # -------------------------
    if mode != "IDLE":
        scroll_x -= 1
        if scroll_x < -scroll_width:
            choose_next()

    draw()

    # -------------------------
    # BRIGHTNESS
    # -------------------------
    if galactic.is_pressed(GalacticUnicorn.SWITCH_BRIGHTNESS_UP):
        brightness += 0.02
    if galactic.is_pressed(GalacticUnicorn.SWITCH_BRIGHTNESS_DOWN):
        brightness -= 0.02
    brightness = max(min(brightness, 1.0), 0.1)
    galactic.set_brightness(brightness)

    galactic.update(graphics)

    time.sleep(0.03)
