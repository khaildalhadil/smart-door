"""PN532 NFC / RFID card reader on I2C, and the service that watches it.

The PN532 talks in frames.  On I2C (address 0x24; the board's mode switch on
I2C) every read starts with a status byte, 0x01 once a reply is ready:

    host  -> PN532   00 00 FF LEN LCS D4 CMD data... DCS 00
    PN532 -> host    01 | 00 00 FF 00 FF 00                    (ACK)
    PN532 -> host    01 | 00 00 FF LEN LCS D5 CMD+1 data... DCS 00

Only the card's UID is used (InListPassiveTarget, ISO 14443-A at 106 kbps:
MIFARE Classic / Ultralight, NTAG stickers and most access cards and tags).
A UID is not a secret -- anyone can read it, and some cards can be given any
UID -- so a card is a convenience, like a key, not proof of who holds it.
users.json keeps only a keyed hash of each UID (``[card] uid_key``).

:class:`CardService` polls the reader on its own thread and hands each card
put on it, once, to whoever is watching (the door screen, or the screen that
adds a card).  The card must be taken away before it counts again.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config, new_salt
from .security import AttemptResult, LoginLimiter

I2C_SLAVE = 0x0703
TFI_HOST, TFI_PN532 = 0xD4, 0xD5
ACK = bytes([0x00, 0x00, 0xFF, 0x00, 0xFF, 0x00])

CMD_GET_FIRMWARE = 0x02
CMD_SAM_CONFIGURATION = 0x14
CMD_RF_CONFIGURATION = 0x32
CMD_IN_LIST_PASSIVE_TARGET = 0x4A

MODE_OFF, MODE_HARDWARE, MODE_SIMULATED = "off", "hardware", "simulated"

REMOVED_AFTER = 0.6     # s without the card before the same card counts again
RETRY_SECONDS = 5.0     # wait between attempts to reach an offline reader
ERRORS_OFFLINE = 3      # reads in a row that fail before the reader is "offline"


class ReaderError(Exception):
    """The reader did not answer, or answered something that makes no sense."""


class ReaderTimeout(ReaderError):
    pass


# ------------------------------------------------------------------- frames
def build_frame(cmd: int, params: bytes = b"") -> bytes:
    data = bytes([TFI_HOST, cmd]) + bytes(params)
    n = len(data)
    return (bytes([0x00, 0x00, 0xFF, n, (-n) & 0xFF]) + data
            + bytes([(-sum(data)) & 0xFF, 0x00]))


def parse_frame(raw: bytes, cmd: int) -> bytes:
    """The data of the PN532's reply to ``cmd``; ``raw`` follows the status byte."""
    start = raw.find(b"\x00\xff")
    if start < 0 or start + 4 > len(raw):
        raise ReaderError("no frame in the reply")
    i = start + 2
    length, lcs = raw[i], raw[i + 1]
    if length == 0 and lcs == 0xFF:
        raise ReaderError("an ACK came where a reply was expected")
    if (length + lcs) & 0xFF:
        raise ReaderError("bad length checksum")
    body = raw[i + 2:i + 2 + length]
    if len(body) < length or i + 2 + length >= len(raw):
        raise ReaderError("reply cut short")
    if (sum(body) + raw[i + 2 + length]) & 0xFF:
        raise ReaderError("bad data checksum")
    if body[0] == 0x7F:
        raise ReaderError("the reader rejected the command (error frame)")
    if length < 2 or body[0] != TFI_PN532 or body[1] != cmd + 1:
        raise ReaderError(f"unexpected reply {bytes(body[:2]).hex()}")
    return bytes(body[2:])


def parse_target(data: bytes) -> bytes | None:
    """The UID from an InListPassiveTarget reply, None when no card answered."""
    if not data or data[0] == 0:
        return None
    # NbTg, Tg, SENS_RES (2), SEL_RES, NFCIDLength, NFCID1...
    if len(data) < 6:
        raise ReaderError("target reply cut short")
    n = data[5]
    uid = data[6:6 + n]
    if n not in (4, 7, 10) or len(uid) != n:
        raise ReaderError(f"odd card number length {n}")
    return bytes(uid)


# ------------------------------------------------------------------ drivers
class I2CTransport:
    def __init__(self, bus: int, address: int):
        self.fd = os.open(f"/dev/i2c-{bus}", os.O_RDWR)
        try:
            fcntl.ioctl(self.fd, I2C_SLAVE, address)
        except OSError:
            os.close(self.fd)
            raise

    def write(self, data: bytes) -> None:
        os.write(self.fd, data)

    def read(self, n: int) -> bytes:
        return os.read(self.fd, n)

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


class PN532:
    """The few PN532 commands a door needs, over any read/write transport."""

    def __init__(self, transport, timeout: float = 1.0):
        self.t = transport
        self.timeout = timeout

    def close(self) -> None:
        self.t.close()

    def _wait_ready(self, timeout: float) -> None:
        end = time.monotonic() + timeout
        while True:
            try:
                status = self.t.read(1)
                if status and status[0] & 0x01:
                    return
            except OSError:
                pass   # it does not answer its address while busy or waking up
            if time.monotonic() > end:
                raise ReaderTimeout("no answer from the card reader")
            time.sleep(0.005)

    def command(self, cmd: int, params: bytes = b"", timeout: float | None = None,
                reply_len: int = 40) -> bytes:
        timeout = self.timeout if timeout is None else timeout
        frame = build_frame(cmd, params)
        for attempt in range(3):
            try:
                self.t.write(frame)
                break
            except OSError as exc:
                # Asleep after power-up: the first message only wakes it.
                if attempt == 2:
                    raise ReaderError(f"nothing answers on the I2C bus ({exc})") from exc
                time.sleep(0.05)
        self._wait_ready(timeout)
        ack = self.t.read(len(ACK) + 1)[1:]
        if ack != ACK:
            raise ReaderError(f"no ACK (got {ack.hex()})")
        self._wait_ready(timeout)
        return parse_frame(self.t.read(reply_len + 1)[1:], cmd)

    def firmware(self) -> tuple[int, int, int, int]:
        """(IC, version, revision, support); IC is 0x32 on a PN532."""
        data = self.command(CMD_GET_FIRMWARE)
        if len(data) < 4:
            raise ReaderError("firmware reply cut short")
        return data[0], data[1], data[2], data[3]

    def configure(self) -> None:
        # Normal mode (no secure module), use the IRQ line.
        self.command(CMD_SAM_CONFIGURATION, bytes([0x01, 0x14, 0x01]))
        # MxRtyATR, MxRtyPSL, MxRtyPassiveActivation: look once, then give up,
        # so a poll with no card returns at once instead of waiting forever.
        self.command(CMD_RF_CONFIGURATION, bytes([0x05, 0xFF, 0x01, 0x01]))

    def read_uid(self) -> bytes | None:
        return parse_target(self.command(CMD_IN_LIST_PASSIVE_TARGET, bytes([0x01, 0x00])))


def open_hardware(config: Config) -> PN532:
    bus = config.getint("card", "bus", 1)
    address = config.getint("card", "address", 0x24)
    reader = PN532(I2CTransport(bus, address))
    try:
        ic = reader.firmware()[0]
        if ic != 0x32:
            raise ReaderError(f"not a PN532 (chip 0x{ic:02x})")
        reader.configure()
    except Exception:
        reader.close()
        raise
    return reader


class SimulatedReader:
    """No hardware: the card on the "reader" is the hex UID in a text file
    (``echo 04A1B2C3 > data/sim_card.txt``; empty the file to take it away)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def read_uid(self) -> bytes | None:
        try:
            text = self.path.read_text().strip()
        except OSError:
            return None
        try:
            return bytes.fromhex(text) if text else None
        except ValueError:
            return None

    def close(self) -> None:
        pass


# ------------------------------------------------------------------ service
@dataclass
class CardCheck:
    """What the door makes of one card."""

    ok: bool
    user: object = None
    tag: str = ""
    reason: str = ""          # "ok", "unknown", "disabled", "locked_out"
    attempt: AttemptResult | None = None
    wait: float = 0.0         # seconds left of a lockout


def _call(callback, *args) -> None:
    try:
        callback(*args)
    except Exception:   # a screen bug must not kill the reader thread
        pass


class CardService:
    def __init__(self, config: Config, users=None, events=None, reader_factory=None,
                 simulate: bool = False):
        self.config = config
        self.users = users
        self.events = events
        self._lock = threading.Lock()
        self._watchers: list[tuple[int, object]] = []
        self._next_token = 1
        self._listeners: list = []
        self._online: bool | None = None
        self._resend = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.limiter = LoginLimiter(config, events, state_file="card_state.json",
                                    event="card.lockout", what="unknown cards")
        data_dir = config.path_for("logging", "file").parent
        if reader_factory is not None:
            self.mode = MODE_HARDWARE
            self._factory = reader_factory
        elif simulate or config.getbool("card", "simulate", False):
            self.mode = MODE_SIMULATED
            self._factory = lambda: SimulatedReader(data_dir / "sim_card.txt")
        elif config.getbool("card", "enabled", False):
            self.mode = MODE_HARDWARE
            self._factory = lambda: open_hardware(config)
        else:
            self.mode = MODE_OFF
            self._factory = None

    # ------------------------------------------------------------- state
    @property
    def available(self) -> bool:
        return self.mode != MODE_OFF

    @property
    def online(self) -> bool | None:
        return self._online

    def status_text(self) -> str:
        if self.mode == MODE_OFF:
            return ("The card reader is off.\nWire the PN532 (I2C), then run "
                    "tools/probe_card.py to switch it on.")
        if self.mode == MODE_SIMULATED:
            return "Simulated card reader (data/sim_card.txt)."
        if self._online is False:
            return "The card reader is not answering.\nCheck its four wires and the I2C switch."
        return "Card reader ready. Hold a card on it."

    def add_listener(self, callback) -> None:
        """``callback(online)`` from the reader thread when it comes or goes."""
        with self._lock:
            self._listeners.append(callback)

    def _set_online(self, online: bool, detail: str = "") -> None:
        if online == self._online:
            return
        before, self._online = self._online, online
        if self.events:
            if online:
                if before is False:   # back after a fault; a normal start is not news
                    self.events.info("card.online", "card reader answering again")
            elif before is None:
                self.events.warning("card.offline", f"card reader not found: {detail}")
            else:
                self.events.warning("card.offline", f"card reader stopped answering: {detail}")
        with self._lock:
            listeners = list(self._listeners)
        for cb in listeners:
            _call(cb, online)

    # ------------------------------------------------------------ watchers
    def watch(self, on_card, include_present: bool = False) -> int:
        """``on_card(uid)`` on the reader thread for each card put on the
        reader; the newest watcher gets it.  A card already lying on the reader
        counts only with ``include_present`` (adding a card); the door never
        opens for a card that was left there.  Returns a token for release()."""
        with self._lock:
            token = self._next_token
            self._next_token += 1
            self._watchers.append((token, on_card))
            if include_present:
                self._resend = True
        return token

    def release(self, token: int) -> None:
        with self._lock:
            self._watchers = [w for w in self._watchers if w[0] != token]

    def _dispatch(self, uid: bytes) -> None:
        with self._lock:
            target = self._watchers[-1][1] if self._watchers else None
        if target is not None:
            _call(target, uid)

    # --------------------------------------------------------- card numbers
    def _key(self) -> bytes:
        key = self.config.get("card", "uid_key")
        if not key:
            key = new_salt()
            self.config.set_and_save("card", "uid_key", key)
        return bytes.fromhex(key)

    def card_id(self, uid: bytes) -> str:
        """What users.json keeps for a card: a keyed hash of its UID."""
        return hmac.new(self._key(), uid, hashlib.sha256).hexdigest()[:32]

    @staticmethod
    def tag(uid: bytes) -> str:
        """The last 4 hex digits, to tell cards apart on the screen."""
        return uid.hex().upper()[-4:]

    # ----------------------------------------------------------- the door
    def check(self, uid: bytes) -> CardCheck:
        """Decide whether this card opens the door, and log a refusal."""
        tag = self.tag(uid)
        wait = self.limiter.lockout_remaining()
        if wait > 0:
            return CardCheck(False, tag=tag, reason="locked_out", wait=wait)
        found = self.users.find_card(self.card_id(uid)) if self.users else None
        if found is not None:
            user, _card = found
            if not user.enabled:
                if self.events:
                    self.events.warning("card.deny", f"{user.name} is disabled (card …{tag})")
                return CardCheck(False, user=user, tag=tag, reason="disabled")
            self.limiter.record(True)
            return CardCheck(True, user=user, tag=tag, reason="ok")
        attempt = self.limiter.record(False, "card")
        if self.events and not attempt.locked_out:
            n = attempt.remaining_attempts
            self.events.warning("card.deny", f"unknown card …{tag} ({n} attempt(s) left)")
        return CardCheck(False, tag=tag, reason="locked_out" if attempt.locked_out else "unknown",
                         attempt=attempt, wait=attempt.lockout_remaining)

    # ---------------------------------------------------------- the thread
    def start(self) -> None:
        if self.mode == MODE_OFF or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="card-reader", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        reader = None
        last: bytes | None = None
        gone_since: float | None = None
        errors = 0
        poll = max(0.05, self.config.getfloat("card", "poll_seconds", 0.25))
        while not self._stop.is_set():
            if reader is None:
                try:
                    reader = self._factory()
                except Exception as exc:  # noqa: BLE001 - any failure = offline
                    self._set_online(False, str(exc))
                    self._stop.wait(RETRY_SECONDS)
                    continue
                errors = 0
                self._set_online(True)
            try:
                uid = reader.read_uid()
            except Exception as exc:  # noqa: BLE001
                errors += 1
                if errors >= ERRORS_OFFLINE:
                    try:
                        reader.close()
                    except Exception:  # noqa: BLE001
                        pass
                    reader = None
                    self._set_online(False, str(exc))
                    self._stop.wait(RETRY_SECONDS)
                else:
                    self._stop.wait(poll)
                continue
            errors = 0
            now = time.monotonic()
            if uid is None:
                if last is not None:
                    gone_since = gone_since or now
                    if now - gone_since >= REMOVED_AFTER:
                        last, gone_since = None, None
            else:
                gone_since = None
                with self._lock:
                    resend, self._resend = self._resend, False
                if uid != last or resend:
                    last = uid
                    self._dispatch(uid)
            if uid is None:
                with self._lock:
                    self._resend = False
            self._stop.wait(poll)
        if reader is not None:
            try:
                reader.close()
            except Exception:  # noqa: BLE001
                pass
