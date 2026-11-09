# pico-unicorn
## Uptime Kuma alerts

Built into `main.py`: when a monitor goes down or recovers, the alert takes over the whole display, and the normal office display comes back once it's done. If `KUMA_URL` isn't set in `config.py`, the alerts are switched off. `uptime_kuma.py` is a standalone version that shows only the alerts.

Shows your [Uptime Kuma](https://github.com/louislam/uptime-kuma) monitors on the Galactic Unicorn:

- **Monitor goes down** → red background, scrolls `example.com DOWN` on repeat while it stays down (cycles through every monitor that's down).
- **Monitor comes back up** → green background, scrolls `example.com UP` a couple of times, then returns to idle.
- **All good** → the normal display in `main.py` (a blank screen with a faint heartbeat pixel in `uptime_kuma.py`).

The domain is taken from the monitor's URL (or hostname for ping/port monitors), falling back to the monitor's name.

### Setup

1. In Uptime Kuma go to **Settings → API Keys** and create a key.
2. Add these to `config.py` on the Pico:

```python
KUMA_URL = "http://192.168.1.10:3001"   # your Uptime Kuma address
KUMA_API_KEY = "uk1_xxxxxxxxxxxx"
KUMA_POLL_SECONDS = 30                  # optional, default 30
KUMA_UP_REPEATS = 2                     # optional, times to scroll "UP"
```

3. Run `main.py` as usual (or run `uptime_kuma.py` for the alerts on their own).

It reads Uptime Kuma's `/metrics` endpoint one line at a time, so it stays light on RAM even with lots of monitors. The scrolling pauses briefly (about a second) each time it polls.
