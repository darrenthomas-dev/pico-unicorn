# pico-unicorn

MicroPython display for a Pimoroni **Galactic Unicorn** (53×11 LEDs, Pico W).
It shows office sensor readings received over MQTT, plays small animations on
button presses, draws rain when it's raining, and takes over the whole panel
with a red alert when an Uptime Kuma monitor goes down.

## Setup

1. Flash the Pimoroni Galactic Unicorn MicroPython firmware (it provides
   `galactic`, `picographics` and `umqtt.simple`).
2. Create `config.py` next to `main.py`:

   ```python
   WIFI_SSID = "your-wifi"
   WIFI_PASS = "your-password"

   MQTT_BROKER = "192.168.1.10"
   MQTT_PORT = 1883
   MQTT_TOPIC = "office/sensors"

   # Optional
   MQTT_ALERT_TOPIC = "unicorn/alert"   # default "unicorn/alert"
   ALERT_TIMEOUT_MIN = 30               # default 30
   ```

3. Copy `main.py` and `config.py` to the Pico. It connects to Wi-Fi and the
   broker on boot.

## Sensor messages (`MQTT_TOPIC`)

JSON, any subset of these keys:

```json
{"temperature": 21, "humidity": 55, "co2": 700, "rain": 0.6}
```

- Keys missing from a message keep their last value. At boot the values are
  22°C, 50%, 600 ppm and no rain.
- `rain` is an intensity from `0` (dry) to `1` (downpour). `true`/`false` also work.

## Display

- **Section 1 (left quarter)** shows the office status:

  | Condition | Colour |
  |---|---|
  | below 18°C | blue |
  | 18–22°C | green |
  | 22–26°C | amber |
  | 26°C and above | red |
  | CO2 above 1200 ppm | red (overrides temperature) |
  | humidity above 60% | flickers |

- **Sections 2–4** are dim background colours.
- **Rain** draws pale drops across the full width. More rain means more and
  faster drops.

## Buttons

| Button | Action |
|---|---|
| A | Cat walks across the display |
| B | Person rises, stays 10 s, then sinks |
| C | Scrolls the current temperature |
| D (tap) | Previews rain for 10 s |
| D (hold 2 s) | Silences the alerts currently showing |
| Brightness +/− | Adjusts brightness (0.1–1.0) |

## Uptime Kuma alerts (`MQTT_ALERT_TOPIC`)

In Uptime Kuma, add an **MQTT** notification pointing at the same broker with
topic `unicorn/alert`, and attach it to your monitors. **Name each monitor
after its domain**, because the plain-text formats only carry the monitor name.

While any site is down, the whole panel pulses red and the down domains scroll
in white, separated by ` | `. The normal display resumes when they clear.

- A site clears when Kuma reports it up, or `ALERT_TIMEOUT_MIN` minutes after
  the last message about it. Kuma's resend reminders reset that timer.
- Holding D silences the current alerts. A silenced site shows again if it
  recovers and then goes down again.
- Kuma doesn't send these messages as retained, so after a reboot the Pico
  won't know about an ongoing outage until Kuma sends the next message.

Accepted message formats:

- JSON with `monitor.name` / `monitor.url` and `heartbeat.status` (0 = down,
  1 = up). The domain is taken from the URL when present.
- Bracket text: `[example.com] [🔴 Down] timeout`
- Log-style text: `Monitor #6 'example.com': Failing: Timeout ...`

Emoji are removed because the display font can't draw them. Unrecognised
messages print `[ALERT] unrecognised: ...` to the console.

> **TODO: capture a real Uptime Kuma MQTT message.** The parser was written
> without a sample from an actual Kuma notification, so the formats above are
> best guesses. When possible, run
> `mosquitto_sub -h <broker> -t unicorn/alert -v`, trigger a real down and up
> (the notification's **Test** button may not match a real alert), and add the
> exact payloads here. Then adjust `parse_alert` in `main.py` if needed.
