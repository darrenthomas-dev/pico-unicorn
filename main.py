import time
import json
import gc
import network
import ubinascii
import urequests
import config

from umqtt.simple import MQTTClient

from galactic import GalacticUnicorn
from picographics import PicoGraphics
from picographics import DISPLAY_GALACTIC_UNICORN as DISPLAY


WIFI_SSID = config.WIFI_SSID
WIFI_PASS = config.WIFI_PASS

MQTT_BROKER = config.MQTT_BROKER
MQTT_PORT = config.MQTT_PORT
TOPIC = config.MQTT_TOPIC

CLIENT_ID = "unicorn_" + str(time.ticks_ms())

# Uptime Kuma alerts are switched off if KUMA_URL isn't in config.py
KUMA_URL = getattr(config, "KUMA_URL", "").rstrip("/")
KUMA_API_KEY = getattr(config, "KUMA_API_KEY", "")
KUMA_POLL_SECONDS = getattr(config, "KUMA_POLL_SECONDS", 30)
KUMA_UP_REPEATS = getattr(config, "KUMA_UP_REPEATS", 2)

# =========================
# DISPLAY
# =========================
galactic = GalacticUnicorn()
graphics = PicoGraphics(DISPLAY)

WIDTH = GalacticUnicorn.WIDTH
HEIGHT = GalacticUnicorn.HEIGHT

brightness = 0.35
galactic.set_brightness(brightness)

section_width = WIDTH // 4

# =========================
# SENSOR STATE
# =========================
office = {
    "temperature": 0,
    "humidity": 0,
    "co2": 0
}

# =========================
# TEMP SCROLL STATE (NEW)
# =========================
temp_scroll_active = False
temp_scroll_x = WIDTH
last_c_press = 0

# =========================
# WIFI
# =========================
def connect_wifi():

    print("[WIFI] connecting...")

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASS)

    timeout = 20

    while not wlan.isconnected() and timeout > 0:
        time.sleep(1)
        timeout -= 1
        print("[WIFI] waiting...")

    if wlan.isconnected():
        print("[WIFI] connected")
        print(wlan.ifconfig())
        return True

    print("[WIFI] FAILED")
    return False


# =========================
# MQTT CALLBACK
# =========================
def mqtt_callback(topic, msg):

    global office

    try:
        office = json.loads(msg)
        print("[MQTT]", office)

    except Exception as e:
        print("[MQTT JSON ERROR]", e)


client = None


def connect_mqtt():

    global client

    try:

        print("[MQTT] connecting...")

        client = MQTTClient(
            CLIENT_ID,
            MQTT_BROKER,
            port=MQTT_PORT,
            keepalive=60
        )

        client.set_callback(mqtt_callback)

        client.connect()
        client.subscribe(TOPIC)

        print("[MQTT] connected")

        return True

    except Exception as e:
        print("[MQTT CONNECT FAILED]", e)
        return False


def reconnect_mqtt():

    print("[MQTT] reconnecting...")
    time.sleep(5)
    connect_mqtt()


# =========================
# UPTIME KUMA
# =========================
# Polls Uptime Kuma's /metrics endpoint. While a monitor is down the whole
# display turns red and scrolls "<domain> DOWN"; when it recovers it turns
# green and scrolls "<domain> UP" a few times, then the normal display returns.

KUMA_STATUS_DOWN = 0
KUMA_STATUS_UP = 1

kuma_auth = "Basic " + ubinascii.b2a_base64(
    (":" + KUMA_API_KEY).encode()
).decode().strip()

kuma_known = {}          # name -> last status
kuma_down = []           # names currently down
kuma_up_queue = []       # names that just recovered, waiting to be shown

alert_mode = "NONE"      # NONE | DOWN | UP
alert_text = ""
alert_x = WIDTH
alert_width = 0
alert_up_passes = 0
alert_down_index = 0
last_kuma_poll = time.ticks_add(time.ticks_ms(), -KUMA_POLL_SECONDS * 1000)


def kuma_label(line, name):

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


def kuma_display_name(line):

    # Prefer the domain from the URL, then the hostname, then the monitor name
    url = kuma_label(line, "monitor_url")
    if url and "://" in url:
        host = url.split("://", 1)[1].split("/", 1)[0]
        host = host.split("@")[-1].split(":", 1)[0]
        if host:
            return host

    return (kuma_label(line, "monitor_hostname")
            or kuma_label(line, "monitor_name")
            or "unknown")


def fetch_kuma_statuses():

    # Returns {name: status} or None on failure.
    # Reads the response a line at a time to save RAM.
    gc.collect()
    statuses = {}
    r = None

    try:
        r = urequests.get(
            KUMA_URL + "/metrics",
            headers={"Authorization": kuma_auth}
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

            statuses[kuma_display_name(line)] = status

        return statuses

    except Exception as e:
        print("[KUMA ERROR]", e)
        return None

    finally:
        if r:
            r.close()
        gc.collect()


def update_kuma_states(statuses):

    global kuma_known

    first_poll = not kuma_known

    for name, status in statuses.items():

        if status == KUMA_STATUS_DOWN:
            if name not in kuma_down:
                kuma_down.append(name)
                print("[KUMA] DOWN", name)
            if name in kuma_up_queue:
                kuma_up_queue.remove(name)

        elif status == KUMA_STATUS_UP:
            if name in kuma_down:
                kuma_down.remove(name)
            # Don't announce everything as "UP" on boot
            if kuma_known.get(name) == KUMA_STATUS_DOWN and not first_poll:
                print("[KUMA] UP", name)
                if name not in kuma_up_queue:
                    kuma_up_queue.append(name)

        # pending / maintenance: leave the current state alone

    # Deleted / paused monitors shouldn't stay stuck as down
    for name in list(kuma_down):
        if name not in statuses:
            kuma_down.remove(name)

    kuma_known = statuses


def start_alert_scroll(text):

    global alert_text, alert_x, alert_width

    alert_text = text
    alert_x = WIDTH
    alert_width = graphics.measure_text(text, scale=1)


def next_alert():

    # Called when an alert scroll finishes (or when nothing is showing)
    global alert_mode, alert_up_passes, alert_down_index

    if alert_mode == "UP" and alert_up_passes > 0:
        alert_up_passes -= 1
        start_alert_scroll(alert_text)
        return

    # Recoveries first, so the green shows as soon as something is back
    if kuma_up_queue:
        alert_mode = "UP"
        alert_up_passes = KUMA_UP_REPEATS - 1
        start_alert_scroll(kuma_up_queue.pop(0) + " UP")
        return

    if kuma_down:
        alert_mode = "DOWN"
        alert_down_index %= len(kuma_down)
        start_alert_scroll(kuma_down[alert_down_index] + " DOWN")
        alert_down_index += 1
        return

    alert_mode = "NONE"


def draw_alert():

    if alert_mode == "DOWN":
        graphics.set_pen(graphics.create_pen(160, 0, 0))
    else:
        graphics.set_pen(graphics.create_pen(0, 140, 0))
    graphics.clear()

    graphics.set_pen(graphics.create_pen(255, 255, 255))
    graphics.text(alert_text, alert_x, 2, -1, 1)


# =========================
# BACKGROUND
# =========================
def draw_background():

    graphics.set_pen(graphics.create_pen(0, 0, 0))
    graphics.clear()

    graphics.set_pen(graphics.create_pen(30, 0, 0))
    graphics.rectangle(0, 0, section_width, HEIGHT)

    graphics.set_pen(graphics.create_pen(0, 30, 0))
    graphics.rectangle(section_width, 0, section_width, HEIGHT)

    graphics.set_pen(graphics.create_pen(0, 0, 30))
    graphics.rectangle(section_width * 2, 0, section_width, HEIGHT)

    graphics.set_pen(graphics.create_pen(30, 0, 30))
    graphics.rectangle(section_width * 3, 0, WIDTH - section_width * 3, HEIGHT)


# =========================
# SECTION 1
# =========================
def draw_section_1(frame):

    t = office.get("temperature", 22)
    h = office.get("humidity", 50)
    c = office.get("co2", 600)

    if t < 18:
        colour = (0, 0, 255)
    elif t < 22:
        colour = (0, 255, 0)
    elif t < 26:
        colour = (255, 180, 0)
    else:
        colour = (255, 0, 0)

    if c > 1200:
        colour = (255, 0, 0)

    pulse = 1.0
    if h > 60:
        pulse = 0.7 if frame == 0 else 0.3

    r = int(colour[0] * pulse)
    g = int(colour[1] * pulse)
    b = int(colour[2] * pulse)

    graphics.set_pen(graphics.create_pen(r, g, b))
    graphics.rectangle(0, 0, section_width, HEIGHT)


# =========================
# TEMP SCROLL RENDER (NEW)
# =========================
def draw_temp_scroll(x):

    temp = office.get("temperature", 0)
    text = "TEMP: {}C".format(temp)

    graphics.set_pen(graphics.create_pen(255, 255, 255))
    graphics.text(text, x, 2, scale=1)


# =========================
# CAT
# =========================
cat_x = -8
cat_active = False
cat_frame = 0
last_cat_move = 0


def safe_pixel(x, y):

    if 0 <= x < WIDTH and 0 <= y < HEIGHT:
        graphics.pixel(x, y)


def draw_cat(x, frame):

    graphics.set_pen(graphics.create_pen(220, 220, 220))

    for px in range(1, 6):
        for py in range(4, 7):
            safe_pixel(x + px, py)

    for px in range(5, 8):
        for py in range(3, 6):
            safe_pixel(x + px, py)

    safe_pixel(x + 5, 2)
    safe_pixel(x + 7, 2)

    safe_pixel(x, 3)
    safe_pixel(x, 4)

    if frame == 0:
        safe_pixel(x + 2, 7)
        safe_pixel(x + 5, 6)
    else:
        safe_pixel(x + 2, 6)
        safe_pixel(x + 5, 7)


# =========================
# PERSON
# =========================
person_state = "IDLE"
person_y = HEIGHT
person_start_time = 0


def draw_person(x, y):

    graphics.set_pen(graphics.create_pen(255, 255, 255))

    for px in range(1, 4):
        for py in range(0, 3):
            safe_pixel(x + px, y + py)

    for px in range(1, 4):
        for py in range(3, 7):
            safe_pixel(x + px, y + py)

    for py in range(3, 6):
        safe_pixel(x, y + py)
        safe_pixel(x + 4, y + py)

    safe_pixel(x, y + 3)
    safe_pixel(x + 4, y + 3)

    if (time.ticks_ms() // 200) % 2 == 0:

        safe_pixel(x + 1, y + 7)
        safe_pixel(x + 2, y + 7)
        safe_pixel(x + 3, y + 7)
        safe_pixel(x + 1, y + 8)

    else:

        safe_pixel(x + 1, y + 7)
        safe_pixel(x + 2, y + 7)
        safe_pixel(x + 3, y + 7)
        safe_pixel(x + 3, y + 8)


# =========================
# STARTUP
# =========================
print("=== BOOT ===")

if connect_wifi():
    connect_mqtt()


# =========================
# MAIN LOOP
# =========================
frame = 0
counter = 0

while True:

    try:
        if client:
            client.check_msg()
    except Exception as e:
        print("[MQTT ERROR]", e)
        reconnect_mqtt()

    now = time.ticks_ms()

    # -------------------------
    # UPTIME KUMA POLL
    # -------------------------
    if KUMA_URL and time.ticks_diff(now, last_kuma_poll) >= KUMA_POLL_SECONDS * 1000:

        last_kuma_poll = now

        statuses = fetch_kuma_statuses()
        if statuses is not None:
            update_kuma_states(statuses)

        if alert_mode == "NONE":
            next_alert()

        now = time.ticks_ms()

    # -------------------------
    # CAT TRIGGER (A)
    # -------------------------
    if galactic.is_pressed(GalacticUnicorn.SWITCH_A):
        if not cat_active:
            cat_active = True
            cat_x = -8

    # -------------------------
    # PERSON TRIGGER (B)
    # -------------------------
    if galactic.is_pressed(GalacticUnicorn.SWITCH_B):
        if person_state == "IDLE":
            person_state = "UP"
            person_y = HEIGHT

    # -------------------------
    # TEMP SCROLL TRIGGER (C) NEW
    # -------------------------
    if galactic.is_pressed(GalacticUnicorn.SWITCH_C):
        if time.ticks_diff(now, last_c_press) > 300:
            temp_scroll_active = True
            temp_scroll_x = WIDTH
            last_c_press = now

    # -------------------------
    # PERSON STATE MACHINE
    # -------------------------
    if person_state == "UP":

        person_y -= 1

        if person_y <= 3:
            person_y = 3
            person_state = "HOLD"
            person_start_time = now

    elif person_state == "HOLD":

        if time.ticks_diff(now, person_start_time) > 10000:
            person_state = "DOWN"

    elif person_state == "DOWN":

        person_y += 1

        if person_y > HEIGHT:
            person_state = "IDLE"

    # -------------------------
    # TEMP SCROLL UPDATE
    # -------------------------
    if temp_scroll_active:

        temp_scroll_x -= 1

        if temp_scroll_x < -80:
            temp_scroll_active = False

    # -------------------------
    # DRAW
    # -------------------------
    draw_background()
    draw_section_1(frame)

    if person_state != "IDLE":
        draw_person(2, person_y)

    if cat_active:

        draw_cat(cat_x, cat_frame)

        if time.ticks_diff(now, last_cat_move) > 120:
            last_cat_move = now
            cat_x += 1
            cat_frame = 1 - cat_frame

        if cat_x > WIDTH:
            cat_active = False

    # TEMP SCROLL DRAW (NEW)
    if temp_scroll_active:
        draw_temp_scroll(temp_scroll_x)

    # UPTIME KUMA ALERT (drawn over everything else)
    if alert_mode != "NONE":

        draw_alert()

        alert_x -= 1
        if alert_x < -alert_width:
            next_alert()

    # BRIGHTNESS
    if galactic.is_pressed(GalacticUnicorn.SWITCH_BRIGHTNESS_UP):
        brightness += 0.02

    if galactic.is_pressed(GalacticUnicorn.SWITCH_BRIGHTNESS_DOWN):
        brightness -= 0.02

    brightness = max(min(brightness, 1.0), 0.1)
    galactic.set_brightness(brightness)

    galactic.update(graphics)

    counter += 1
    if counter % 200 == 0:
        print("[RUNNING]", office)

    frame = 1 - frame

    time.sleep(0.05)
