"""Minimal driver for UART fingerprint modules speaking the 0xEF01 protocol.

Modules silkscreened **R22** are normally capacitive sensors from the
ZhianTec / Grow / Synochip family (same packet protocol as R30x, R50x, ZW101).
That is an *assumption until proven*: run ``tools/probe_fingerprint.py`` first.
It only sends a password-verify and a read-parameters command -- both read-only
-- and reports what the module answers.

Deliberately dependency-free beyond ``pyserial`` (already installed), so nothing
has to be installed before the hardware is confirmed.

Protocol recap
--------------
Every frame is::

    0xEF 0x01 | address(4) | PID(1) | length(2) | payload(n) | checksum(2)

``length`` counts the payload plus the two checksum bytes.  ``checksum`` is the
16-bit sum of PID, both length bytes and every payload byte.  The module answers
a command (PID 0x01) with an acknowledge (PID 0x07) whose first payload byte is
a confirmation code -- 0x00 means success.
"""

from __future__ import annotations

import time

HEADER = b"\xef\x01"

PID_COMMAND = 0x01
PID_DATA = 0x02
PID_ACK = 0x07
PID_END_DATA = 0x08

# Instruction codes.
CMD_GEN_IMG = 0x01
CMD_IMG2TZ = 0x02
CMD_MATCH = 0x03
CMD_SEARCH = 0x04
CMD_REG_MODEL = 0x05
CMD_STORE = 0x06
CMD_LOAD_CHAR = 0x07
CMD_DELETE_CHAR = 0x0C
CMD_EMPTY = 0x0D
CMD_SET_SYS_PARA = 0x0E
CMD_READ_SYS_PARA = 0x0F
CMD_VERIFY_PWD = 0x13
CMD_GET_IMAGE_EX = 0x28
CMD_CANCEL = 0x30
CMD_TEMPLATE_NUM = 0x1D
CMD_READ_INDEX_TABLE = 0x1F
CMD_AURA_LED = 0x35
CMD_HANDSHAKE = 0x40
CMD_CHECK_SENSOR = 0x36
CMD_SOFT_RESET = 0x3D

CONFIRMATION = {
    0x00: "ok",
    0x01: "packet receive error",
    0x02: "no finger on sensor",
    0x03: "failed to capture image",
    0x06: "image too disordered",
    0x07: "image too small / too few features",
    0x08: "fingerprint does not match",
    0x09: "no matching fingerprint found",
    0x0A: "failed to combine the two scans",
    0x0B: "page id out of range",
    0x0C: "error reading template from library",
    0x0D: "error uploading feature",
    0x0E: "module cannot receive more packets",
    0x0F: "error uploading image",
    0x10: "failed to delete template",
    0x11: "failed to clear library",
    0x13: "wrong password",
    0x15: "no valid primary image in buffer",
    0x18: "flash read/write error",
    0x1A: "invalid register number",
    0x1B: "incorrect register configuration",
    0x1C: "wrong notepad page number",
    0x1D: "port operation failed",
    0x1F: "fingerprint library full",
    0x20: "wrong address",
    0x21: "must verify password first",
    0x24: "sensor is not a supported model",
}


class SensorError(RuntimeError):
    """Any protocol-level failure, carrying the module's confirmation code."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code

    @classmethod
    def from_code(cls, code: int, context: str = "") -> "SensorError":
        text = CONFIRMATION.get(code, f"unknown confirmation code 0x{code:02X}")
        prefix = f"{context}: " if context else ""
        return cls(f"{prefix}{text} (0x{code:02X})", code)


class SensorTimeout(SensorError):
    """No complete frame arrived inside the serial timeout."""


class R22Sensor:
    """Synchronous client for one module on one serial port.

    Not thread-safe: the caller must serialise access (the fingerprint service
    does this with a single worker thread).
    """

    def __init__(
        self,
        port: str = "/dev/serial0",
        baudrate: int = 57600,
        address: int = 0xFFFFFFFF,
        password: int = 0x00000000,
        timeout: float = 2.0,
    ):
        self.port = port
        self.baudrate = int(baudrate)
        self.address = int(address) & 0xFFFFFFFF
        self.password = int(password) & 0xFFFFFFFF
        self.timeout = float(timeout)
        self._serial = None
        self.parameters: dict[str, int] = {}

    # ------------------------------------------------------------- connection
    def open(self) -> None:
        import serial  # pyserial

        if self._serial is not None and self._serial.is_open:
            return
        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
            write_timeout=self.timeout,
        )
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def __enter__(self) -> "R22Sensor":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ----------------------------------------------------------------- frames
    def _build(self, pid: int, payload: bytes) -> bytes:
        length = len(payload) + 2
        body = bytes([pid]) + length.to_bytes(2, "big") + payload
        checksum = sum(body) & 0xFFFF
        return HEADER + self.address.to_bytes(4, "big") + body + checksum.to_bytes(2, "big")

    def _read_exact(self, count: int, deadline: float) -> bytes:
        buf = b""
        while len(buf) < count:
            if time.monotonic() > deadline:
                raise SensorTimeout(
                    f"timed out after {len(buf)}/{count} bytes -- check TX/RX "
                    f"wiring and baud rate ({self.baudrate})"
                )
            chunk = self._serial.read(count - len(buf))
            if not chunk:
                continue
            buf += chunk
        return buf

    def _read_frame(self, timeout: float | None = None) -> tuple[int, bytes]:
        """Resynchronise on the header, then return ``(pid, payload)``."""
        if not self.is_open:
            raise SensorError("serial port is not open")
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)

        # Hunt for the 0xEF 0x01 header; tolerate line noise before it.
        window = b""
        while True:
            if time.monotonic() > deadline:
                raise SensorTimeout(
                    "no response frame -- module silent (power? TX/RX swapped? "
                    f"baud {self.baudrate}?)"
                )
            byte = self._serial.read(1)
            if not byte:
                continue
            window = (window + byte)[-2:]
            if window == HEADER:
                break

        head = self._read_exact(7, deadline)  # address(4) + pid(1) + length(2)
        pid = head[4]
        length = int.from_bytes(head[5:7], "big")
        if not 2 <= length <= 512:
            raise SensorError(f"implausible frame length {length}")
        rest = self._read_exact(length, deadline)
        payload, checksum = rest[:-2], int.from_bytes(rest[-2:], "big")

        expected = (sum(head[4:7]) + sum(payload)) & 0xFFFF
        if checksum != expected:
            raise SensorError(
                f"checksum mismatch (got 0x{checksum:04X}, expected 0x{expected:04X})"
            )
        return pid, payload

    def _command(
        self, code: int, payload: bytes = b"", timeout: float | None = None
    ) -> bytes:
        """Send one command, return the acknowledge payload *after* the code byte."""
        if not self.is_open:
            raise SensorError("serial port is not open")
        frame = self._build(PID_COMMAND, bytes([code]) + payload)
        self._serial.reset_input_buffer()
        self._serial.write(frame)
        self._serial.flush()

        pid, data = self._read_frame(timeout)
        if pid != PID_ACK:
            raise SensorError(f"expected an acknowledge frame, got PID 0x{pid:02X}")
        if not data:
            raise SensorError("empty acknowledge frame")
        return data

    def _checked(
        self, code: int, payload: bytes = b"", context: str = "",
        timeout: float | None = None,
    ) -> bytes:
        data = self._command(code, payload, timeout)
        if data[0] != 0x00:
            raise SensorError.from_code(data[0], context)
        return data[1:]

    # ------------------------------------------------------------- read-only
    def verify_password(self) -> None:
        """VfyPwd -- the standard handshake; read-only, safe to probe with."""
        self._checked(
            CMD_VERIFY_PWD,
            self.password.to_bytes(4, "big"),
            "password verify",
        )

    def read_parameters(self) -> dict[str, int]:
        """ReadSysPara -- 16 bytes of module configuration; read-only."""
        data = self._checked(CMD_READ_SYS_PARA, b"", "read system parameters")
        if len(data) < 16:
            raise SensorError(f"short parameter block ({len(data)} bytes)")
        self.parameters = {
            "status_register": int.from_bytes(data[0:2], "big"),
            "system_id": int.from_bytes(data[2:4], "big"),
            "capacity": int.from_bytes(data[4:6], "big"),
            "security_level": int.from_bytes(data[6:8], "big"),
            "device_address": int.from_bytes(data[8:12], "big"),
            "packet_size_code": int.from_bytes(data[12:14], "big"),
            "baud_setting": int.from_bytes(data[14:16], "big"),
        }
        self.parameters["packet_bytes"] = 32 << self.parameters["packet_size_code"]
        self.parameters["baudrate"] = self.parameters["baud_setting"] * 9600
        return self.parameters

    def template_count(self) -> int:
        data = self._checked(CMD_TEMPLATE_NUM, b"", "template count")
        return int.from_bytes(data[:2], "big")

    def index_table(self, capacity: int = 127) -> set[int]:
        """Slots that actually hold a template, read from the module's bitmap.

        Each index page covers 256 slots as a 32-byte little-endian-ish bitmap:
        byte *i*, bit *b* -> slot ``page * 256 + i * 8 + b``.
        """
        slots: set[int] = set()
        pages = max(1, (capacity + 255) // 256)
        for page in range(pages):
            data = self._checked(
                CMD_READ_INDEX_TABLE, bytes([page]), f"read index page {page}"
            )
            for byte_index, value in enumerate(data[:32]):
                for bit in range(8):
                    if value & (1 << bit):
                        slots.add(page * 256 + byte_index * 8 + bit)
        return {s for s in slots if s <= capacity}

    # -------------------------------------------------------------- capturing
    def try_capture(self) -> bool:
        """One GenImg: True when a finger image was taken, False when no finger."""
        code = self._command(CMD_GEN_IMG)[0]
        if code == 0x00:
            return True
        if code != 0x02:  # 0x02 == "no finger yet", anything else is real
            raise SensorError.from_code(code, "capture")
        return False

    def capture(self, timeout: float = 10.0, poll: float = 0.1) -> None:
        """GenImg until a finger is present, or raise on timeout."""
        deadline = time.monotonic() + timeout
        while True:
            if self.try_capture():
                return
            if time.monotonic() > deadline:
                raise SensorTimeout("no finger detected", 0x02)
            time.sleep(poll)

    def wait_for_finger_removed(self, timeout: float = 10.0, poll: float = 0.15) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self._command(CMD_GEN_IMG)
            if data[0] == 0x02:  # no finger
                return
            time.sleep(poll)

    def image_to_buffer(self, buffer_id: int = 1) -> None:
        self._checked(CMD_IMG2TZ, bytes([buffer_id]), "extract features")

    def build_model(self) -> None:
        self._checked(CMD_REG_MODEL, b"", "combine scans")

    def store(self, slot: int, buffer_id: int = 1) -> None:
        payload = bytes([buffer_id]) + int(slot).to_bytes(2, "big")
        self._checked(CMD_STORE, payload, f"store template in slot {slot}",
                      timeout=max(self.timeout, 3.0))

    def delete(self, slot: int, count: int = 1) -> None:
        payload = int(slot).to_bytes(2, "big") + int(count).to_bytes(2, "big")
        self._checked(CMD_DELETE_CHAR, payload, f"delete slot {slot}",
                      timeout=max(self.timeout, 3.0))

    def clear_library(self) -> None:
        self._checked(CMD_EMPTY, b"", "clear library",
                      timeout=max(self.timeout, 5.0))

    def search(
        self, buffer_id: int = 1, start: int = 0, count: int = 127
    ) -> tuple[int, int]:
        """Return ``(slot, confidence)``; raises with code 0x09 when no match."""
        payload = (
            bytes([buffer_id])
            + int(start).to_bytes(2, "big")
            + int(count).to_bytes(2, "big")
        )
        data = self._checked(CMD_SEARCH, payload, "search",
                             timeout=max(self.timeout, 3.0))
        if len(data) < 4:
            raise SensorError("short search result")
        return int.from_bytes(data[0:2], "big"), int.from_bytes(data[2:4], "big")

    def cancel(self) -> None:
        """Best-effort abort of a long-running sensor operation."""
        try:
            self._command(CMD_CANCEL)
        except SensorError:
            pass

    def set_aura(self, control: int = 2, speed: int = 0x50, colour: int = 1,
                 times: int = 1) -> None:
        """Optional LED control -- harmless to fail on modules without it."""
        try:
            self._checked(
                CMD_AURA_LED, bytes([control, speed, colour, times]), "aura led"
            )
        except SensorError:
            pass
