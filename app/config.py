"""Configuration loading / saving.

Every tunable value (GPIO pin, relay polarity, unlock duration, lockout policy,
fingerprint serial settings, passcode hash) lives in ``config.ini`` -- never in
the UI code.  The file is created with sane defaults on first run and chmod'ed
to 0600 because it holds the passcode hash.
"""

from __future__ import annotations

import configparser
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.ini"
DATA_DIR = BASE_DIR / "data"

# Shipped defaults.  Anything the user edits in config.ini wins over these.
DEFAULTS: dict[str, dict[str, str]] = {
    "lock": {
        # BCM pin number wired to the relay module's IN pin.
        "relay_pin": "17",
        # Level that switches the relay ON (COM->NO closed, door open):
        # true -> HIGH ("high level trigger" boards), false -> LOW ("low level
        # trigger" boards).  Between unlocks the pin is released either way, so
        # a wrong value only means the door does not open: flip it and restart.
        "relay_active_high": "true",
        # Seconds the solenoid stays powered after a valid code.
        "unlock_seconds": "5",
        # Hard ceiling: the solenoid is not rated for continuous duty.
        "max_unlock_seconds": "10",
        # Re-assert the safe (locked) level this often, in seconds, so a lost
        # timer or a crashed thread cannot leave the door open. 0 disables.
        "watchdog_interval": "1.0",
    },
    "security": {
        # PBKDF2-HMAC-SHA256 of the keypad passcode.  Set via the UI or
        # tools/set_passcode.py -- never stored in plain text.
        "passcode_hash": "",
        "passcode_salt": "",
        "pbkdf2_iterations": "200000",
        "passcode_min_length": "4",
        "passcode_max_length": "12",
        # Failed attempts allowed before the keypad locks itself out.
        "max_attempts": "3",
        "lockout_seconds": "30",
        # Each further burst of failures multiplies the lockout, capped here.
        "lockout_backoff": "2.0",
        "lockout_max_seconds": "600",
        # Admin session auto-logout after this long idle.
        "session_timeout_seconds": "120",
    },
    "fingerprint": {
        # Leave false until the R22 pinout + protocol are confirmed with
        # tools/probe_fingerprint.py.  Nothing touches the serial port while
        # this is false.
        "enabled": "false",
        # Safety interlock: the driver refuses to open the port until a
        # successful probe has been recorded here.
        "probe_confirmed": "false",
        # Fake sensor for bench-testing the enrol/list/delete screens with no
        # hardware attached.  Takes priority over "enabled".
        "simulate": "false",
        "port": "/dev/serial0",
        "baudrate": "57600",
        "address": "0xFFFFFFFF",
        "password": "0x00000000",
        "timeout": "2.0",
        # Capacity of the sensor's internal template store.
        "capacity": "127",
        # Confidence floor for accepting a search match.
        "min_confidence": "50",
        # Home screen watches the sensor: touching it is enough, no button.
        # false brings back the "Fingerprint" button.
        "auto_scan": "true",
        # Seconds between "is a finger on the sensor?" checks while watching.
        "poll_seconds": "0.3",
    },
    "card": {
        # PN532 NFC / RFID card reader on I2C (app/nfc.py): mode switch on I2C,
        # VCC on 3.3V (never 5V), SDA -> pin 3, SCL -> pin 5.  Nothing touches
        # the bus while this is false; tools/probe_card.py checks the reader
        # and sets it to true.
        "enabled": "false",
        # Fake reader for testing the screens: the card "on the reader" is the
        # hex number in data/sim_card.txt.  Takes priority over "enabled".
        "simulate": "false",
        "bus": "1",
        "address": "0x24",
        # Seconds between "is a card on the reader?" checks.
        "poll_seconds": "0.25",
        # Key for the card hashes in data/users.json; made on first use.
        "uid_key": "",
    },
    "face": {
        # Camera face recognition (app/face.py).  Needs python3-opencv and the
        # two models from tools/fetch_face_models.py.
        "enabled": "false",
        # V4L2 device.  The by-id path survives other video devices appearing.
        "camera": "/dev/video0",
        "width": "640",
        "height": "480",
        "fps": "15",
        # Turn the image if the camera is mounted sideways / upside down.
        "rotate": "0",
        # Show the preview like a mirror (recognition always uses the real image).
        "mirror": "true",
        "detector_model": "models/face_detection_yunet_2023mar.onnx",
        "recognizer_model": "models/face_recognition_sface_2021dec.onnx",
        # Cosine similarity a face must reach against an enrolled face.  OpenCV
        # suggests 0.363 for SFace; higher means fewer false accepts.
        "match_threshold": "0.45",
        # Consecutive matching frames before the door opens.
        "confirm_frames": "3",
        # Minimum face width in camera pixels: only someone standing close to
        # the door is considered, not people walking past.
        "min_face_size": "90",
        # YuNet detection confidence floor.
        "detect_score": "0.8",
        # Pictures taken per face enrolment.
        "samples": "5",
        # Pause after a face unlock before the next one, once the door relocks.
        # The same person must also step out of view before opening it again.
        "cooldown_seconds": "5",
    },
    "fire": {
        # IR flame module, digital output DO (app/hazard.py).  Power the module
        # from 3.3V, never 5V, so DO never goes above 3.3V.
        "enabled": "false",
        # BCM number: GPIO5 = physical pin 29 (GND right next to it on pin 30).
        "pin": "5",
        # true: DO goes LOW on flame (most LM393 flame modules; the module's
        # DO LED lights).  Wrong value -> the sensor reads "fire" all the time;
        # the app notices, sends a "suspect" alert and does NOT open the door.
        "active_low": "true",
        # Internal pull: auto (up for active_low, down otherwise), up, down, off.
        "pull": "auto",
        # Flame seen at least half the time for this long -> alarm.
        "confirm_seconds": "1.0",
        # No flame for this long -> alarm over ("cleared" alert).
        "clear_seconds": "15",
        "warmup_seconds": "0",
    },
    "smoke": {
        # MQ-2 style smoke/gas module, digital output DO.  It needs 5V for its
        # heater, so DO swings to 5V: go through a divider (see WIRING.md).
        "enabled": "false",
        # GPIO6 = physical pin 31.
        "pin": "6",
        "active_low": "true",
        "pull": "off",
        "confirm_seconds": "3.0",
        "clear_seconds": "30",
        # The heater reads high for a while after power-on: ignore it meanwhile.
        "warmup_seconds": "60",
    },
    "alarm": {
        # Open the door when fire / smoke is detected.
        "open_door": "true",
        # While the alarm lasts, open again once the door has been shut this
        # long (lets the solenoid cool).  0 = open only once.
        "reopen_after_seconds": "20",
        # Stop re-opening after this many times (a sensor in sunlight must not
        # keep the door open for hours).  The alerts still go out.
        "max_reopens": "15",
    },
    "buzzer": {
        # Beeps while a fire / smoke alarm lasts (app/buzzer.py).
        "enabled": "false",
        # GPIO13 = physical pin 33 (GND on pin 39).
        "pin": "13",
        # true: the buzzer sounds on HIGH.  "Low level trigger" modules: false.
        # Wrong value -> it sounds from the moment the app starts: flip it.
        "active_high": "true",
        # 0 = active buzzer (sounds by itself on DC).  A passive buzzer only
        # clicks on DC: give it a tone in Hz instead, e.g. 2500.
        "pwm_hz": "0",
        # Beep pattern while the alarm lasts.
        "on_seconds": "0.5",
        "off_seconds": "0.5",
        # Stop beeping after this long even if the alarm goes on (a sensor in
        # sunlight must not beep for hours).  0 = until the alarm clears.
        "max_seconds": "300",
    },
    "photos": {
        # Camera picture with every entry, refusal, doorbell ring and alarm
        # (app/recorder.py), kept in data/photos/.  Needs [face] enabled.
        "enabled": "true",
        "jpeg_quality": "80",
        # Local pictures older than this are deleted; so are the oldest ones
        # past max_files.  0 = no limit.
        "keep_days": "30",
        "max_files": "3000",
    },
    "history": {
        # Rows of data/history.db older than this are deleted.  0 = keep all.
        "keep_days": "365",
    },
    "notify": {
        # Which events become a WhatsApp alert (queued for the bot, see
        # app/alerts.py).  Fire / smoke alerts are always sent.
        "wrong_code": "true",           # wrong door code, with the picture
        "unknown_fingerprint": "true",  # a finger that is not enrolled
        "unknown_card": "true",         # a card that is not registered
        "wrong_pin": "true",            # wrong sign-in PIN on the panel
        # Anyone the camera does not know (at most once per person at the
        # door, and once every 10 s).
        "unknown_face": "true",
        "temp_code_used": "true",       # the guest used the one-time code
        # Every entry: keypad, face, finger, card, menu, WhatsApp, visitor.
        "entries": "true",
        # Changes on the panel: door code, users, faces, fingers, cards,
        # lockouts cleared, app closed, relay test.
        "admin_changes": "true",
        "panel_login": "true",          # someone signed in to the panel
        "faults": "true",               # camera, fingerprint sensor, card reader, relay
        "alarm_photo": "true",          # a picture right after a fire / smoke alert
        "startup": "true",              # "the door system has (re)started"
        # Seconds between two alerts of the same kind; 0 = every event is sent.
        # (Wrong codes, PINs and cards still stop at the lockout; lockouts and
        # alarms always go.)
        "min_gap_seconds": "0",
    },
    "doorbell": {
        # "Ring bell" button on the door screen: picture + WhatsApp question.
        "enabled": "true",
        # How long the owner's "yes" / "no" is accepted after a ring.
        "answer_seconds": "120",
        # A second ring is refused until this long after the last one.
        "min_gap_seconds": "20",
    },
    "bot": {
        # WhatsApp numbers allowed to send commands (POST /command), comma
        # separated, digits only, e.g. 96891234567.  Empty = whoever the bot
        # forwards (the bot then decides who may talk to it).
        "owners": "",
        # "temp code" with no duration / no digits given.
        "temp_code_minutes": "10",
        "temp_code_digits": "6",
        # No GET /alerts from the bot for this long -> the panel shows BOT OFF
        # and the doorbell tells the visitor the owner may be offline.
        "offline_after_seconds": "45",
        "show_offline_badge": "true",
    },
    "cloud": {
        # Copy history rows and pictures to Supabase for the web dashboard
        # (app/cloud.py).  Runs only once SUPABASE_URL and SUPABASE_SERVICE_KEY
        # are set in .env; until then everything just stays on the Pi.
        "enabled": "true",
        "table": "door_events",
        "status_table": "door_status",
        "bucket": "door-photos",
        "sync_seconds": "5",
        # Device heartbeat row for the dashboard ("online, last seen ...").
        "status_seconds": "60",
        # Pictures older than this are deleted from the bucket.  0 = never.
        "photo_keep_days": "90",
    },
    "ui": {
        "fullscreen": "true",
        "width": "800",
        "height": "480",
        "hide_cursor": "true",
        # Allow Escape to leave fullscreen (handy on a desktop, off on the panel).
        "allow_escape": "false",
        # Show the software unlock button when no GPIO backend is present.
        "simulation_button": "true",
        "message_seconds": "3",
    },
    "api": {
        # HTTP API for the WhatsApp bot (app/api.py), reached through Tailscale
        # Funnel.  It only ever binds to 127.0.0.1; its key is DOOR_KEY in .env.
        "enabled": "true",
        "port": "8000",
        # Most one-time guest codes that may be active at once.
        "max_temp_codes": "20",
    },
    "logging": {
        "file": "data/events.log",
        "max_bytes": "1048576",
        "backup_count": "3",
        "level": "INFO",
    },
}


class Config:
    """Thin typed wrapper around :class:`configparser.ConfigParser`."""

    def __init__(self, path: Path = CONFIG_PATH):
        self.path = Path(path)
        self._parser = configparser.ConfigParser()
        self.load()

    # ---------------------------------------------------------------- loading
    def load(self) -> None:
        self._parser = configparser.ConfigParser()
        # Seed defaults so a partially-edited file still has every key.
        self._parser.read_dict(DEFAULTS)
        if self.path.exists():
            self._parser.read(self.path, encoding="utf-8")
        else:
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".ini.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(
                "# Smart Lock configuration.\n"
                "# Contains the passcode hash -- keep mode 0600.\n"
                "# Restart the app after editing.\n\n"
            )
            self._parser.write(fh)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    # ---------------------------------------------------------------- getters
    def get(self, section: str, option: str, fallback: str | None = None) -> str:
        try:
            return self._parser.get(section, option)
        except (configparser.NoSectionError, configparser.NoOptionError):
            if fallback is None:
                return DEFAULTS.get(section, {}).get(option, "")
            return fallback

    def getint(self, section: str, option: str, fallback: int = 0) -> int:
        try:
            return int(str(self.get(section, option)).strip(), 0)
        except (TypeError, ValueError):
            return fallback

    def getfloat(self, section: str, option: str, fallback: float = 0.0) -> float:
        try:
            return float(str(self.get(section, option)).strip())
        except (TypeError, ValueError):
            return fallback

    def getbool(self, section: str, option: str, fallback: bool = False) -> bool:
        raw = str(self.get(section, option)).strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
        return fallback

    # ---------------------------------------------------------------- setters
    def set(self, section: str, option: str, value) -> None:
        if not self._parser.has_section(section):
            self._parser.add_section(section)
        self._parser.set(section, option, str(value))

    def set_and_save(self, section: str, option: str, value) -> None:
        self.set(section, option, value)
        self.save()

    # ------------------------------------------------------------- resolution
    def path_for(self, section: str, option: str) -> Path:
        """Resolve a configured (possibly relative) path against the project root."""
        raw = self.get(section, option)
        p = Path(raw).expanduser()
        return p if p.is_absolute() else BASE_DIR / p

    @property
    def unlock_seconds(self) -> float:
        """Unlock duration, clamped to the configured hardware ceiling."""
        wanted = self.getfloat("lock", "unlock_seconds", 5.0)
        ceiling = self.getfloat("lock", "max_unlock_seconds", 10.0)
        return max(0.2, min(wanted, ceiling))


def new_salt() -> str:
    return secrets.token_hex(16)


ENV_PATH = BASE_DIR / ".env"


def env_value(name: str, path: Path = ENV_PATH) -> str:
    """A secret from the environment, else from the ``.env`` file (mode 0600,
    never committed): ``NAME=value`` lines, ``#`` comments."""
    value = os.environ.get(name, "")
    if value:
        return value.strip()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        key, sep, raw = line.partition("=")
        if sep and key.strip() == name:
            value = raw.strip().strip("'\"")
    return value
