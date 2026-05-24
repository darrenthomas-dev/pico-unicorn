import time
import json
import network

from umqtt.simple import MQTTClient

from galactic import GalacticUnicorn
from picographics import PicoGraphics
from picographics import DISPLAY_GALACTIC_UNICORN as DISPLAY

# =========================
# WIFI
# =========================
WIFI_SSID = ""
WIFI_PASS = ""

# =========================
# MQTT
# =========================
MQTT_BROKER = ""
MQTT_PORT = 
TOPIC = b"unicorn/office"

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
    "temperature": 0,
    "humidity": 0,
    "co2": 0
}

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

# =========================
# MQTT
# =========================
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
# CAT (ORIGINAL)
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

    # Body
    for px in range(1, 6):
        for py in range(4, 7):
            safe_pixel(x + px, py)

    # Head
    for px in range(5, 8):
        for py in range(3, 6):
            safe_pixel(x + px, py)

    # Ears
    safe_pixel(x + 5, 2)
    safe_pixel(x + 7, 2)

    # Tail
    safe_pixel(x, 3)
    safe_pixel(x, 4)

    # Walking legs
    if frame == 0:
        safe_pixel(x + 2, 7)
        safe_pixel(x + 5, 6)
    else:
        safe_pixel(x + 2, 6)
        safe_pixel(x + 5, 7)

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

    # MQTT
    try:
        if client:
            client.check_msg()
    except Exception as e:
        print("[MQTT ERROR]", e)
        reconnect_mqtt()

    # START CAT
    if galactic.is_pressed(GalacticUnicorn.SWITCH_A):

        if not cat_active:
            cat_active = True
            cat_x = -8

    # DRAW
    draw_background()
    draw_section_1(frame)

    # CAT
    if cat_active:

        draw_cat(cat_x, cat_frame)

        now = time.ticks_ms()

        if time.ticks_diff(now, last_cat_move) > 120:

            last_cat_move = now
            cat_x += 1
            cat_frame = 1 - cat_frame

        if cat_x > WIDTH:
            cat_active = False

    # BRIGHTNESS
    if galactic.is_pressed(GalacticUnicorn.SWITCH_BRIGHTNESS_UP):
        brightness += 0.02

    if galactic.is_pressed(GalacticUnicorn.SWITCH_BRIGHTNESS_DOWN):
        brightness -= 0.02

    brightness = max(min(brightness, 1.0), 0.1)
    galactic.set_brightness(brightness)

    # UPDATE DISPLAY
    galactic.update(graphics)

    # DEBUG
    counter += 1
    if counter % 200 == 0:
        print("[RUNNING]", office)

    frame = 1 - frame

    time.sleep(0.05)
