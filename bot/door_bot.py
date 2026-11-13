"""The bot's side of the smart lock: copy this file next to your WhatsApp bot.

Nothing here calls an AI model, so the door keeps working with no tokens:

* every incoming message goes to the door first (``POST /command``); the door
  understands "افتح الباب", "open door", "close the door", "status", "رمز مؤقت",
  "صورة", "السجل", and the doorbell answers "نعم" / "لا";
* alerts (fire, smoke, wrong code with the picture, doorbell) are collected
  from ``GET /alerts`` and sent as they are.

Only a message the door did not understand goes on to your model, if you pass
one; with no model, or when it fails (no credit), the door's own hint is sent.

You supply two functions for your WhatsApp provider:

    send_text(to, text)
    send_image(to, jpeg_bytes, caption)

Wiring it in:

    from door_bot import DoorClient, handle_message, start_alert_loop

    door = DoorClient("http://172.17.0.1:8001", DOOR_KEY)
    start_alert_loop(door, [OWNER_NUMBER], send_text, send_image)

    # in your webhook, for every incoming text (button replies too: pass the
    # button's title or id as the text):
    handle_message(door, text, sender, send_text, send_image, llm=ask_model)

``ask_model(text, sender) -> str`` is your existing AI call; leave it out to
run with no AI at all.
"""

from __future__ import annotations

import threading
import time

import requests

OFFLINE = "⚠️ الباب غير متصل الآن، ما قدرت أوصل له. جرّب بعد قليل."


class DoorClient:
    def __init__(self, base_url: str, key: str, timeout: float = 10.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["X-Door-Key"] = key

    def command(self, text: str, sender: str = "") -> dict:
        r = self.session.post(f"{self.base}/command", json={"text": text, "from": sender},
                              timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def alerts(self) -> list[dict]:
        r = self.session.get(f"{self.base}/alerts", timeout=self.timeout)
        r.raise_for_status()
        return r.json()["alerts"]

    def ack(self, alert_id: int) -> None:
        self.session.post(f"{self.base}/alerts/ack", json={"id": alert_id},
                          timeout=self.timeout).raise_for_status()

    def photo(self, path: str) -> bytes | None:
        """``path`` as given by an alert or a reply: ``/photos/<name>.jpg``."""
        r = self.session.get(self.base + path, timeout=self.timeout)
        return r.content if r.status_code == 200 else None

    def status(self) -> dict:
        r = self.session.get(f"{self.base}/status", timeout=self.timeout)
        r.raise_for_status()
        return r.json()


def _deliver(door: DoorClient, to: str, text: str, photo: str | None, send_text, send_image):
    jpeg = None
    if photo and send_image is not None:
        try:
            jpeg = door.photo(photo)
        except requests.RequestException:
            jpeg = None
    if jpeg:
        send_image(to, jpeg, text)
    else:
        send_text(to, text)


def handle_message(door: DoorClient, text: str, sender: str, send_text, send_image=None,
                   llm=None) -> bool:
    """Answer one incoming WhatsApp message.  True when no AI was needed."""
    try:
        result = door.command(text, sender)
    except (requests.RequestException, ValueError):
        send_text(sender, OFFLINE)
        return True
    if result.get("handled"):
        _deliver(door, sender, result["reply"], result.get("photo"), send_text, send_image)
        return True
    if llm is not None:
        try:
            answer = llm(text, sender)
            if answer:
                send_text(sender, answer)
                return False
        except Exception:
            pass                      # no credit, model down: fall back to the hint
    send_text(sender, result.get("reply") or OFFLINE)
    return False


def start_alert_loop(door: DoorClient, owners: list[str], send_text, send_image=None,
                     interval: float = 3.0) -> threading.Thread:
    """Forward the door's alerts to every owner, forever, in a background
    thread.  An alert is acknowledged only after it was sent, so nothing is
    lost while WhatsApp or the tunnel is down."""

    def run():
        while True:
            try:
                for alert in door.alerts():
                    for owner in owners:
                        _deliver(door, owner, alert["message"], alert.get("photo"),
                                 send_text, send_image)
                    door.ack(alert["id"])
            except Exception as exc:
                print("door alerts:", exc)
            time.sleep(interval)

    thread = threading.Thread(target=run, name="door-alerts", daemon=True)
    thread.start()
    return thread
