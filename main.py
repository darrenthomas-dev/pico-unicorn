import time
import json
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

    # Legs
    if frame == 0:
        safe_pixel(x + 2, 7)
        safe_pixel(x + 5, 6)
    else:
        safe_pixel(x + 2, 6)
        safe_pixel(x + 5, 7)


# =========================
# PERSON (NEW)
# =========================
person_state = "IDLE"
person_y = HEIGHT
person_start_time = 0


def draw_person(x, y):

    graphics.set_pen(graphics.create_pen(255, 255, 255))

    # =========================
    # HEAD (solid block)
    # =========================
    for px in range(1, 4):
        for py in range(0, 3):
            safe_pixel(x + px, y + py)

    # =========================
    # BODY (solid torso)
    # =========================
    for px in range(1, 4):
        for py in range(3, 7):
            safe_pixel(x + px, y + py)

    # =========================
    # ARMS (thicker silhouette arms)
    # =========================
    for py in range(3, 6):
        safe_pixel(x, y + py)
        safe_pixel(x + 4, y + py)

    # shoulders fill
    safe_pixel(x, y + 3)
    safe_pixel(x + 4, y + 3)

    # =========================
    # LEGS (walking stance silhouette)
    # =========================
    if (time.ticks_ms() // 200) % 2 == 0:

        # left forward, right back
        safe_pixel(x + 1, y + 7)
        safe_pixel(x + 2, y + 7)
        safe_pixel(x + 3, y + 7)

        safe_pixel(x + 1, y + 8)

    else:

        # right forward, left back
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

    # MQTT
    try:
        if client:
            client.check_msg()
    except Exception as e:
        print("[MQTT ERROR]", e)
        reconnect_mqtt()

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
    now = time.ticks_ms()

    if galactic.is_pressed(GalacticUnicorn.SWITCH_B):
        if person_state == "IDLE":
            person_state = "UP"
            person_y = HEIGHT

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
    # DRAW
    # -------------------------
    draw_background()
    draw_section_1(frame)

    # PERSON
    if person_state != "IDLE":
        draw_person(2, person_y)

    # CAT
    if cat_active:

        draw_cat(cat_x, cat_frame)

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

    galactic.update(graphics)

    # DEBUG
    counter += 1
    if counter % 200 == 0:
        print("[RUNNING]", office)

    frame = 1 - frame

    time.sleep(0.05)

