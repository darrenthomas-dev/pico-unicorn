import time
import json
import gc
import network
import ntptime
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

# Weather clock idle screen. Weather pages are skipped if no location is set.
WEATHER_LAT = getattr(config, "WEATHER_LAT", None)
WEATHER_LON = getattr(config, "WEATHER_LON", None)
WEATHER_UNITS = getattr(config, "WEATHER_UNITS", "celsius")   # or "fahrenheit"
WEATHER_POLL_MINUTES = getattr(config, "WEATHER_POLL_MINUTES", 15)
UTC_OFFSET_HOURS = getattr(config, "UTC_OFFSET_HOURS", 0)     # used until weather loads
CLOCK_24H = getattr(config, "CLOCK_24H", True)
PAGE_SECONDS = getattr(config, "PAGE_SECONDS", 6)

# Night dimming (local time, 24h). Set both hours the same to turn it off.
NIGHT_START_HOUR = getattr(config, "NIGHT_START_HOUR", 22)
NIGHT_END_HOUR = getattr(config, "NIGHT_END_HOUR", 7)
NIGHT_BRIGHTNESS = getattr(config, "NIGHT_BRIGHTNESS", 0.05)

# =========================
# DISPLAY
# =========================
galactic = GalacticUnicorn()
graphics = PicoGraphics(DISPLAY)

WIDTH = GalacticUnicorn.WIDTH
HEIGHT = GalacticUnicorn.HEIGHT

brightness = 0.35
night_brightness = NIGHT_BRIGHTNESS
galactic.set_brightness(brightness)

# =========================
# SENSOR STATE
# =========================
office = {
    "temperature": 0,
    "humidity": 0,
    "co2": 0
}
office_updated = False

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

    global office, office_updated

    try:
        office = json.loads(msg)
        office_updated = True
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
# IDLE SCREEN: WEATHER CLOCK
# =========================
# Rotates through simplified pages that fit the 53x11 display:
#   CLOCK     12:47  FRI / 13-08
#   WEATHER   icon, big temperature, today's high / low
#   FORECAST  next 3 days: icon, day, high
#   OFFICE    MQTT readings with the comfort colour bar (if data received)

# Tiny pixel font. Every glyph is 5 rows; widths vary.
FONT = {
    "0": ("###", "#.#", "#.#", "#.#", "###"),
    "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("###", "..#", "###", "#..", "###"),
    "3": ("###", "..#", ".##", "..#", "###"),
    "4": ("#.#", "#.#", "###", "..#", "..#"),
    "5": ("###", "#..", "###", "..#", "###"),
    "6": ("###", "#..", "###", "#.#", "###"),
    "7": ("###", "..#", "..#", ".#.", ".#."),
    "8": ("###", "#.#", "###", "#.#", "###"),
    "9": ("###", "#.#", "###", "..#", "###"),
    "A": (".#.", "#.#", "###", "#.#", "#.#"),
    "C": (".##", "#..", "#..", "#..", ".##"),
    "E": ("###", "#..", "##.", "#..", "###"),
    "F": ("###", "#..", "##.", "#..", "#.."),
    "H": ("#.#", "#.#", "###", "#.#", "#.#"),
    "I": ("###", ".#.", ".#.", ".#.", "###"),
    "M": ("#.#", "###", "###", "#.#", "#.#"),
    "N": ("##.", "#.#", "#.#", "#.#", "#.#"),
    "O": (".#.", "#.#", "#.#", "#.#", ".#."),
    "R": ("##.", "#.#", "##.", "#.#", "#.#"),
    "S": (".##", "#..", ".#.", "..#", "##."),
    "T": ("###", ".#.", ".#.", ".#.", ".#."),
    "U": ("#.#", "#.#", "#.#", "#.#", "###"),
    "W": ("#.#", "#.#", "###", "###", "#.#"),
    ":": (".", "#", ".", "#", "."),
    "-": ("...", "...", "###", "...", "..."),
    "%": ("#.#", "..#", ".#.", "#..", "#.#"),
    "°": ("##", "##", "..", "..", ".."),
    " ": ("..", "..", "..", "..", ".."),
}

DAY_NAMES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


def text_width(text, scale=1):

    w = 0
    for ch in text:
        w += len(FONT.get(ch, FONT[" "])[0]) * scale + 1
    return max(w - 1, 0)


def draw_small(text, x, y, pen, scale=1):

    graphics.set_pen(pen)

    for ch in text:
        glyph = FONT.get(ch, FONT[" "])
        for row, bits in enumerate(glyph):
            for col, bit in enumerate(bits):
                if bit == "#":
                    graphics.rectangle(x + col * scale, y + row * scale, scale, scale)
        x += len(glyph[0]) * scale + 1

    return x


# Pixel-art icons. Each letter is a colour from ICON_COLOURS, "." is empty.
ICONS = {
    "sun": (
        ".....Y.....",
        ".Y.......Y.",
        "....YYY....",
        "...YYYYY...",
        "..YYYYYYY..",
        "Y.YYYYYYO.Y",
        "..YYYYYOO..",
        "...YYOOO...",
        "....OOO....",
        ".Y.......Y.",
        ".....Y.....",
    ),
    "moon": (
        "...........",
        "...MMMM....",
        "..MMM...W..",
        ".MMM.......",
        ".MMM.......",
        ".MMM...W...",
        ".MMM.......",
        "..MMM....W.",
        "...MMMM....",
        "...........",
        "...........",
    ),
    "partly": (
        "...Y.......",
        ".Y...Y.....",
        "...YYY.....",
        "Y.YYYYY....",
        "..YYYWWW...",
        ".Y.YWWWWWW.",
        "...WWWWWWWW",
        "..WWWWWWWWW",
        "...GGGGGGG.",
        "...........",
        "...........",
    ),
    "cloud": (
        "...........",
        "...........",
        "...........",
        "....WWW....",
        "...WWWWWW..",
        ".WWWWWWWWW.",
        "WWWWWWWWWWW",
        "WWWWWWWWWWW",
        ".GGGGGGGGG.",
        "...........",
        "...........",
    ),
    "rain": (
        "...........",
        "....WWW....",
        "...WWWWWW..",
        ".WWWWWWWWW.",
        "WWWWWWWWWWW",
        ".GGGGGGGGG.",
        "...........",
        "..B...B....",
        ".B...B...B.",
        "....B...B..",
        "...B...B...",
    ),
    "snow": (
        "...........",
        "....WWW....",
        "...WWWWWW..",
        ".WWWWWWWWW.",
        "WWWWWWWWWWW",
        ".GGGGGGGGG.",
        "...........",
        "..C....C...",
        "....C....C.",
        ".C.........",
        "......C....",
    ),
    "storm": (
        "...........",
        "....GGG....",
        "...GGGGGG..",
        ".GGGGGGGGG.",
        "GGGGGGGGGGG",
        ".DDDDDDDDD.",
        ".....YY....",
        ".B..YY...B.",
        "...YYYY....",
        "B....YY..B.",
        "....Y......",
    ),
    "fog": (
        "...........",
        "...........",
        ".GGGGGGGG..",
        "...........",
        "..WWWWWWWWW",
        "...........",
        "GGGGGGGG...",
        "...........",
        "..WWWWWWWW.",
        "...........",
        "...........",
    ),
}

SMALL_ICONS = {
    "sun":    ("..Y..", ".YYY.", "YYYYO", ".YOO.", "..O.."),
    "partly": ("..Y..", "YYY..", "YYWW.", ".WWWW", "WWWWW"),
    "cloud":  (".....", "..WW.", ".WWWW", "WWWWW", ".GGGG"),
    "rain":   (".WWW.", "WWWWW", "GGGGG", ".B.B.", "B.B.."),
    "snow":   (".WWW.", "WWWWW", "GGGGG", ".C.C.", "C...C"),
    "storm":  (".GGG.", "GGGGG", ".YY..", "..YY.", ".Y..."),
    "fog":    ("GGGG.", ".....", ".GGGG", ".....", "GGGG."),
}

ICON_COLOURS = {
    "Y": graphics.create_pen(255, 200, 0),
    "O": graphics.create_pen(255, 120, 0),
    "M": graphics.create_pen(230, 220, 150),
    "W": graphics.create_pen(200, 200, 215),
    "G": graphics.create_pen(110, 110, 125),
    "D": graphics.create_pen(60, 60, 70),
    "B": graphics.create_pen(60, 140, 255),
    "C": graphics.create_pen(220, 240, 255),
}

PEN_BLACK = graphics.create_pen(0, 0, 0)
PEN_WHITE = graphics.create_pen(255, 255, 255)
PEN_GREY = graphics.create_pen(120, 120, 120)
PEN_DAY = graphics.create_pen(200, 80, 220)
PEN_HIGH = graphics.create_pen(255, 170, 60)
PEN_LOW = graphics.create_pen(60, 150, 255)


def draw_icon(icon, x, y):

    for row, line in enumerate(icon):
        for col, ch in enumerate(line):
            if ch != ".":
                graphics.set_pen(ICON_COLOURS[ch])
                safe_pixel(x + col, y + row)


def icon_name(code, is_day=1):

    # WMO weather codes, as used by Open-Meteo
    if code == 0:
        return "sun" if is_day else "moon"
    if code in (1, 2):
        return "partly" if is_day else "cloud"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if code >= 95:
        return "storm"
    return "cloud"


# -------------------------
# CLOCK (NTP)
# -------------------------
clock_synced = False
utc_offset = int(UTC_OFFSET_HOURS * 3600)   # replaced by Open-Meteo's offset
last_ntp_sync = 0


def sync_clock():

    global clock_synced, last_ntp_sync

    last_ntp_sync = time.ticks_ms()

    try:
        ntptime.settime()
        clock_synced = True
        print("[NTP] synced")
    except Exception as e:
        print("[NTP ERROR]", e)


def local_time():

    return time.localtime(time.time() + utc_offset)


def is_night():

    # Needs the real time, so stays in day mode until NTP has synced
    if not clock_synced or NIGHT_START_HOUR == NIGHT_END_HOUR:
        return False

    hour = local_time()[3]

    if NIGHT_START_HOUR > NIGHT_END_HOUR:
        # crosses midnight, e.g. 22 -> 7
        return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR

    return NIGHT_START_HOUR <= hour < NIGHT_END_HOUR


# -------------------------
# WEATHER (Open-Meteo, no API key needed)
# -------------------------
weather = None
last_weather_poll = 0


def fetch_weather():

    global weather, utc_offset, last_weather_poll

    last_weather_poll = time.ticks_ms()

    if WEATHER_LAT is None or WEATHER_LON is None:
        return

    gc.collect()
    r = None

    try:
        r = urequests.get(
            "https://api.open-meteo.com/v1/forecast"
            "?latitude={}&longitude={}"
            "&current=temperature_2m,weather_code,is_day"
            "&daily=weather_code,temperature_2m_max,temperature_2m_min"
            "&timezone=auto&forecast_days=4&temperature_unit={}".format(
                WEATHER_LAT, WEATHER_LON, WEATHER_UNITS
            )
        )

        if r.status_code != 200:
            print("[WEATHER] HTTP", r.status_code)
            return

        data = r.json()
        current = data["current"]
        daily = data["daily"]

        weather = {
            "temp": current["temperature_2m"],
            "code": current["weather_code"],
            "is_day": current.get("is_day", 1),
            "codes": daily["weather_code"],
            "highs": daily["temperature_2m_max"],
            "lows": daily["temperature_2m_min"],
        }

        # Handles daylight saving for the configured location
        utc_offset = data.get("utc_offset_seconds", utc_offset)

        print("[WEATHER]", weather["temp"], weather["code"])

    except Exception as e:
        print("[WEATHER ERROR]", e)

    finally:
        if r:
            r.close()
        gc.collect()


def deg(value):

    return "{}°".format(int(round(value)))


# -------------------------
# PAGES
# -------------------------
def draw_clock_page():

    if not clock_synced:
        draw_small("--:--", 1, 1, PEN_GREY, 2)
        return

    t = local_time()
    hour = t[3]
    if not CLOCK_24H:
        hour = hour % 12 or 12

    # Big time, colon blinks every second
    x = draw_small("{:02d}".format(hour), 1, 1, PEN_WHITE, 2)
    if t[5] % 2 == 0:
        draw_small(":", x, 1, PEN_WHITE, 2)
    draw_small("{:02d}".format(t[4]), x + 3, 1, PEN_WHITE, 2)

    # Day on top, date underneath, right aligned
    day = DAY_NAMES[t[6]]
    date = "{:02d}-{:02d}".format(t[2], t[1])
    draw_small(day, WIDTH - 1 - text_width(day), 0, PEN_DAY)
    draw_small(date, WIDTH - 1 - text_width(date), 6, PEN_GREY)


def draw_weather_page():

    now_text = str(int(round(weather["temp"])))
    high = deg(weather["highs"][0])
    low = deg(weather["lows"][0])

    # big number with a small degree sign
    now_w = text_width(now_text, 2) + 1 + text_width("°")
    hilo_w = max(text_width(high), text_width(low))

    total = 11 + 3 + now_w + 3 + hilo_w
    show_hilo = total <= WIDTH
    if not show_hilo:
        total = 11 + 3 + now_w

    x = (WIDTH - total) // 2

    draw_icon(ICONS[icon_name(weather["code"], weather["is_day"])], x, 0)
    x += 14

    end = draw_small(now_text, x, 1, PEN_WHITE, 2)
    draw_small("°", end, 1, PEN_WHITE)
    x += now_w + 3

    if show_hilo:
        draw_small(high, x, 0, PEN_HIGH)
        draw_small(low, x, 6, PEN_LOW)


def draw_forecast_page():

    today = local_time()[6]

    for i in range(3):

        day = i + 1
        if day >= len(weather["codes"]):
            break

        x = i * 18
        draw_icon(SMALL_ICONS[icon_name(weather["codes"][day])], x + 1, 3)
        draw_small(DAY_NAMES[(today + day) % 7], x + 7, 0, PEN_GREY)
        draw_small(deg(weather["highs"][day]), x + 7, 6, PEN_HIGH)


def comfort_colour():

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

    # Slow pulse when it's humid
    pulse = 1.0
    if h > 60:
        pulse = 0.8 if (time.ticks_ms() // 500) % 2 == 0 else 0.3

    return graphics.create_pen(
        int(colour[0] * pulse),
        int(colour[1] * pulse),
        int(colour[2] * pulse)
    )


def draw_office_page():

    graphics.set_pen(comfort_colour())
    graphics.rectangle(0, 0, 2, HEIGHT)

    t = office.get("temperature", 0)
    h = office.get("humidity", 0)
    c = office.get("co2", 0)

    draw_small(deg(t) + " " + "{}%".format(int(round(h))), 4, 0, PEN_WHITE)

    if c > 1200:
        co2_pen = graphics.create_pen(255, 0, 0)
    elif c > 1000:
        co2_pen = PEN_HIGH
    else:
        co2_pen = PEN_WHITE

    x = draw_small("CO2", 4, 6, PEN_GREY)
    draw_small(str(int(c)), x + 2, 6, co2_pen)


page_index = 0
page_started = 0


def available_pages():

    pages = [draw_clock_page]
    if weather:
        pages.append(draw_weather_page)
        pages.append(draw_forecast_page)
    if office_updated:
        pages.append(draw_office_page)
    return pages


def draw_idle():

    global page_index, page_started

    pages = available_pages()

    if time.ticks_diff(time.ticks_ms(), page_started) >= PAGE_SECONDS * 1000:
        page_started = time.ticks_ms()
        page_index += 1

    page_index %= len(pages)

    graphics.set_pen(PEN_BLACK)
    graphics.clear()

    pages[page_index]()


# =========================
# TEMP SCROLL RENDER (NEW)
# =========================
def draw_temp_scroll(x):

    temp = office.get("temperature", 0)
    text = "TEMP: {}C".format(temp)

    graphics.set_pen(graphics.create_pen(0, 0, 0))
    graphics.clear()

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
    sync_clock()
    fetch_weather()


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
    # CLOCK + WEATHER REFRESH
    # -------------------------
    if time.ticks_diff(now, last_ntp_sync) >= (6 * 3600 * 1000 if clock_synced else 60 * 1000):
        sync_clock()
        now = time.ticks_ms()

    if time.ticks_diff(now, last_weather_poll) >= (WEATHER_POLL_MINUTES * 60 * 1000 if weather else 60 * 1000):
        fetch_weather()
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
    draw_idle()

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
    # Day and night have their own levels; the buttons adjust whichever
    # one is active right now.
    step = 0
    if galactic.is_pressed(GalacticUnicorn.SWITCH_BRIGHTNESS_UP):
        step = 0.02 if not is_night() else 0.01

    if galactic.is_pressed(GalacticUnicorn.SWITCH_BRIGHTNESS_DOWN):
        step = -0.02 if not is_night() else -0.01

    if is_night():
        night_brightness = max(min(night_brightness + step, 1.0), 0.01)
        galactic.set_brightness(night_brightness)
    else:
        brightness = max(min(brightness + step, 1.0), 0.1)
        galactic.set_brightness(brightness)

    galactic.update(graphics)

    counter += 1
    if counter % 200 == 0:
        print("[RUNNING]", office)

    frame = 1 - frame

    time.sleep(0.05)
