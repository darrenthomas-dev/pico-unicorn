import time
import json
import random
import network
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
ALERT_TOPIC = getattr(config, "MQTT_ALERT_TOPIC", "unicorn/alert")
ALERT_TIMEOUT_MS = getattr(config, "ALERT_TIMEOUT_MIN", 30) * 60 * 1000

CLIENT_ID = "unicorn_" + str(time.ticks_ms())

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
    "temperature": 22,
    "humidity": 50,
    "co2": 600,
    "rain": 0
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
# UPTIME KUMA ALERTS
# =========================
# site -> {"last": ticks_ms of last message, "silenced": bool}
alerts = {}
alert_scroll_x = WIDTH
alert_was_showing = False


def ascii_only(text):

    return "".join(c for c in text if ord(c) < 128).strip()


def domain_from_url(url):

    if not url or "://" not in url:
        return None

    host = url.split("://", 1)[1]
    host = host.split("/", 1)[0].split(":", 1)[0]

    return host or None


def parse_alert(msg):

    # Returns (site, is_down), or None if the message isn't understood
    text = msg.decode() if isinstance(msg, bytes) else msg

    # JSON: {"monitor": {"name", "url"}, "heartbeat": {"status"}, "msg"}
    try:
        data = json.loads(text)
    except ValueError:
        data = None

    if isinstance(data, dict):
        monitor = data.get("monitor") or {}
        heartbeat = data.get("heartbeat") or {}

        site = domain_from_url(monitor.get("url")) or monitor.get("name")
        status = heartbeat.get("status")

        if site and status in (0, 1):
            return ascii_only(site), status == 0

        text = data.get("msg") or ""

    # Text: "[name] [Down] reason" or "Monitor #6 'name': Failing ..."
    site = None
    status_text = text

    parts = text.split("[")
    brackets = [p.split("]", 1)[0] for p in parts[1:] if "]" in p]

    if text.count("'") >= 2:
        site = text.split("'")[1]

    elif brackets:
        site = brackets[0]
        if len(brackets) > 1:
            status_text = brackets[1]

    if not site:
        return None

    lower = status_text.lower()

    if "down" in lower or "fail" in lower:
        return ascii_only(site), True

    if "up" in lower or "success" in lower:
        return ascii_only(site), False

    return None


def handle_alert(msg):

    result = parse_alert(msg)

    if result is None:
        print("[ALERT] unrecognised:", msg)
        return

    site, is_down = result
    now = time.ticks_ms()

    if is_down:
        # repeat "down" messages keep an existing silence
        entry = alerts.get(site, {"silenced": False})
        entry["last"] = now
        alerts[site] = entry
        print("[ALERT] DOWN", site)

    elif site in alerts:
        del alerts[site]
        print("[ALERT] UP", site)


def expire_alerts(now):

    for site in list(alerts):
        if time.ticks_diff(now, alerts[site]["last"]) > ALERT_TIMEOUT_MS:
            del alerts[site]
            print("[ALERT] timed out", site)


def silence_alerts():

    for entry in alerts.values():
        entry["silenced"] = True

    print("[ALERT] silenced")


def visible_alerts():

    return [site for site in alerts if not alerts[site]["silenced"]]


def draw_alert(now, sites):

    global alert_scroll_x

    # slow red pulse, 120..220
    level = 120 + abs(((now // 20) % 100) - 50) * 2

    graphics.set_pen(graphics.create_pen(level, 0, 0))
    graphics.clear()

    text = "  |  ".join(sites)
    text_width = graphics.measure_text(text, scale=1)

    graphics.set_pen(graphics.create_pen(255, 255, 255))
    graphics.text(text, alert_scroll_x, 2, scale=1)

    alert_scroll_x -= 1

    if alert_scroll_x < -text_width:
        alert_scroll_x = WIDTH


# =========================
# MQTT CALLBACK
# =========================
def mqtt_callback(topic, msg):

    if isinstance(topic, bytes):
        topic = topic.decode()

    if topic == ALERT_TOPIC:
        try:
            handle_alert(msg)
        except Exception as e:
            print("[ALERT ERROR]", e)
        return

    try:
        data = json.loads(msg)
        # Merge so keys missing from this message keep their last value
        for key in office:
            if key in data:
                office[key] = data[key]
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
        client.subscribe(ALERT_TOPIC)

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
# RAIN
# =========================
RAIN_MAX_DROPS = 20

# each drop: [x, y, speed, active]
rain_drops = [[0, 0, 1, False] for _ in range(RAIN_MAX_DROPS)]
last_rain_move = 0
rain_preview_until = 0


def rain_intensity(now):

    try:
        r = float(office.get("rain", 0))
    except Exception:
        r = 0.0

    # Button D preview
    if time.ticks_diff(rain_preview_until, now) > 0:
        r = max(r, 0.6)

    return max(0.0, min(r, 1.0))


def update_rain(now):

    global last_rain_move

    if time.ticks_diff(now, last_rain_move) < 80:
        return

    last_rain_move = now

    intensity = rain_intensity(now)
    target = int(intensity * RAIN_MAX_DROPS)
    active = sum(1 for d in rain_drops if d[3])

    for d in rain_drops:

        if d[3]:
            d[1] += d[2]

            if d[1] >= HEIGHT:
                d[3] = False
                active -= 1

        # only spawn while raining, so drops on screen finish when it stops
        elif active < target and random.randint(0, 3) == 0:
            d[0] = random.randint(0, WIDTH - 1)
            d[1] = -random.randint(0, 3)
            d[2] = 2 if intensity > 0.6 and random.randint(0, 1) else 1
            d[3] = True
            active += 1


def draw_rain():

    tail = graphics.create_pen(60, 70, 110)
    head = graphics.create_pen(180, 200, 255)

    for d in rain_drops:

        if d[3]:
            graphics.set_pen(tail)
            safe_pixel(d[0], d[1] - 1)

            graphics.set_pen(head)
            safe_pixel(d[0], d[1])


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
d_press_start = None
d_long_fired = False

while True:

    try:
        if client:
            client.check_msg()
    except Exception as e:
        print("[MQTT ERROR]", e)
        reconnect_mqtt()

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
    # BUTTON D: tap = rain preview, hold 2s = silence alerts
    # -------------------------
    if galactic.is_pressed(GalacticUnicorn.SWITCH_D):

        if d_press_start is None:
            d_press_start = now
            d_long_fired = False

        elif not d_long_fired and time.ticks_diff(now, d_press_start) >= 2000:
            silence_alerts()
            d_long_fired = True

    elif d_press_start is not None:

        if not d_long_fired:
            rain_preview_until = time.ticks_add(now, 10000)

        d_press_start = None

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
    # RAIN UPDATE
    # -------------------------
    update_rain(now)

    # -------------------------
    # ALERTS
    # -------------------------
    expire_alerts(now)
    down_sites = visible_alerts()

    if down_sites and not alert_was_showing:
        alert_scroll_x = WIDTH

    alert_was_showing = bool(down_sites)

    # -------------------------
    # DRAW
    # -------------------------
    if down_sites:
        draw_alert(now, down_sites)
    else:
        draw_background()
        draw_section_1(frame)
        draw_rain()

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
