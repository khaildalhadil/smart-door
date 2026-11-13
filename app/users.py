"""User directory, the fingerprint-template registry and enrolled faces.

``data/users.json`` holds the people allowed through the door and, for each of
them, the sensor template slots enrolled to their fingers and the face
embeddings captured by the camera (app/face.py; never a photo).  Login PINs
are stored as PBKDF2 digests, same scheme as the keypad passcode.  A plain user
may have no PIN at all: then they cannot sign in, only open the door with a
finger or their face.

The registry is the Pi's view of the sensor's internal store.  The sensor is the
authority on which slots actually hold a template, so
:meth:`UserStore.reconcile` exists to fold a real device index back in.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .config import Config, new_salt
from .security import hash_passcode

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Fingerprint:
    slot: int                  # template index inside the sensor
    label: str = ""            # e.g. "right index"
    created: str = field(default_factory=_now)
    # Set when the registry says a template exists but the sensor disagrees.
    orphaned: bool = False


@dataclass
class Face:
    """One face enrolment: a few embeddings of the same person."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    label: str = ""
    model: str = ""            # recogniser that produced the embeddings
    samples: list[str] = field(default_factory=list)  # base64 float32 vectors
    created: str = field(default_factory=_now)


@dataclass
class User:
    name: str
    role: str = ROLE_USER
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    pin_salt: str = ""
    pin_hash: str = ""
    enabled: bool = True
    created: str = field(default_factory=_now)
    fingerprints: list[Fingerprint] = field(default_factory=list)
    faces: list[Face] = field(default_factory=list)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def has_pin(self) -> bool:
        return bool(self.pin_hash and self.pin_salt)


class UserStore:
    """Thread-safe JSON-backed user directory."""

    def __init__(self, config: Config, events=None):
        self.config = config
        self.events = events
        self._lock = threading.RLock()
        self._path: Path = config.path_for("logging", "file").parent / "users.json"
        self._users: list[User] = []
        #: Bumped on every load/save so readers can cache derived data.
        self.revision = 0
        self.load()

    # ------------------------------------------------------------- persistence
    def load(self) -> None:
        with self._lock:
            self._users = []
            self.revision += 1
            try:
                with open(self._path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                return
            for raw in data.get("users", []):
                try:
                    prints = [
                        Fingerprint(
                            slot=int(fp["slot"]),
                            label=str(fp.get("label", "")),
                            created=str(fp.get("created", _now())),
                            orphaned=bool(fp.get("orphaned", False)),
                        )
                        for fp in raw.get("fingerprints", [])
                    ]
                    faces = [
                        Face(
                            id=str(f["id"]),
                            label=str(f.get("label", "")),
                            model=str(f.get("model", "")),
                            samples=[str(x) for x in f.get("samples", [])],
                            created=str(f.get("created", _now())),
                        )
                        for f in raw.get("faces", [])
                    ]
                    self._users.append(
                        User(
                            id=str(raw.get("id") or uuid.uuid4().hex[:12]),
                            name=str(raw["name"]),
                            role=raw.get("role", ROLE_USER) if raw.get("role") in ROLES else ROLE_USER,
                            pin_salt=str(raw.get("pin_salt", "")),
                            pin_hash=str(raw.get("pin_hash", "")),
                            enabled=bool(raw.get("enabled", True)),
                            created=str(raw.get("created", _now())),
                            fingerprints=prints,
                            faces=faces,
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    continue

    def save(self) -> None:
        with self._lock:
            self.revision += 1
            payload = {
                "version": 1,
                "updated": _now(),
                "users": [asdict(u) for u in self._users],
            }
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass

    # ------------------------------------------------------------------ reads
    def all_users(self) -> list[User]:
        with self._lock:
            return list(self._users)

    def enabled_users(self) -> list[User]:
        with self._lock:
            return [u for u in self._users if u.enabled]

    def by_id(self, user_id: str) -> User | None:
        with self._lock:
            return next((u for u in self._users if u.id == user_id), None)

    def by_name(self, name: str) -> User | None:
        key = name.strip().casefold()
        with self._lock:
            return next((u for u in self._users if u.name.casefold() == key), None)

    def has_admin(self) -> bool:
        with self._lock:
            return any(u.is_admin and u.enabled and u.has_pin for u in self._users)

    @property
    def is_empty(self) -> bool:
        with self._lock:
            return not self._users

    # ----------------------------------------------------------------- writes
    def add_user(self, name: str, pin: str | None, role: str = ROLE_USER) -> User:
        """``pin=None`` creates a user who cannot sign in (finger / face only)."""
        name = name.strip()
        if not name:
            raise ValueError("Name must not be empty.")
        if self.by_name(name):
            raise ValueError(f"A user named '{name}' already exists.")
        if role not in ROLES:
            raise ValueError(f"Unknown role '{role}'.")
        if pin is None and role == ROLE_ADMIN:
            raise ValueError("An administrator needs a PIN.")
        user = User(name=name, role=role)
        if pin is not None:
            self._set_pin(user, pin)
        with self._lock:
            self._users.append(user)
            self.save()
        if self.events:
            self.events.info("user.add", f"{name} ({role})" + ("" if pin else ", no PIN"))
        return user

    def delete_user(self, user_id: str) -> User | None:
        with self._lock:
            user = self.by_id(user_id)
            if user is None:
                return None
            if user.is_admin and sum(
                1 for u in self._users if u.is_admin and u.enabled and u.has_pin
            ) <= 1:
                raise ValueError("Cannot delete the last administrator.")
            self._users = [u for u in self._users if u.id != user_id]
            self.save()
        if self.events:
            self.events.warning("user.delete", f"{user.name} ({user.role})")
        return user

    def set_enabled(self, user_id: str, enabled: bool) -> None:
        with self._lock:
            user = self.by_id(user_id)
            if user is None:
                return
            if user.is_admin and not enabled and sum(
                1 for u in self._users if u.is_admin and u.enabled and u.has_pin
            ) <= 1:
                raise ValueError("Cannot disable the last administrator.")
            user.enabled = enabled
            self.save()
        if self.events:
            self.events.info(
                "user.enabled", f"{user.name} -> {'enabled' if enabled else 'disabled'}"
            )

    def validate_pin(self, pin: str) -> str | None:
        lo = self.config.getint("security", "passcode_min_length", 4)
        hi = self.config.getint("security", "passcode_max_length", 12)
        if not pin.isdigit():
            return "PIN must contain digits only."
        if not lo <= len(pin) <= hi:
            return f"PIN must be {lo}-{hi} digits."
        if len(set(pin)) == 1:
            return "PIN must not be a single repeated digit."
        return None

    def _set_pin(self, user: User, pin: str) -> None:
        err = self.validate_pin(pin)
        if err:
            raise ValueError(err)
        iterations = self.config.getint("security", "pbkdf2_iterations", 200000)
        user.pin_salt = new_salt()
        user.pin_hash = hash_passcode(pin, user.pin_salt, iterations)

    def set_pin(self, user_id: str, pin: str) -> None:
        with self._lock:
            user = self.by_id(user_id)
            if user is None:
                raise ValueError("No such user.")
            self._set_pin(user, pin)
            self.save()
        if self.events:
            self.events.info("user.pin", f"PIN changed for {user.name}")

    def check_pin(self, user_id: str, pin: str) -> bool:
        import hmac

        with self._lock:
            user = self.by_id(user_id)
            if user is None or not user.enabled or not user.has_pin:
                return False
            iterations = self.config.getint("security", "pbkdf2_iterations", 200000)
            candidate = hash_passcode(pin, user.pin_salt, iterations)
            return hmac.compare_digest(candidate, user.pin_hash)

    # ----------------------------------------------------------- fingerprints
    def used_slots(self) -> set[int]:
        with self._lock:
            return {fp.slot for u in self._users for fp in u.fingerprints}

    def next_free_slot(self) -> int:
        capacity = self.config.getint("fingerprint", "capacity", 127)
        used = self.used_slots()
        for slot in range(1, capacity + 1):
            if slot not in used:
                return slot
        raise ValueError("Fingerprint sensor template store is full.")

    def all_fingerprints(self) -> list[tuple[User, Fingerprint]]:
        with self._lock:
            pairs = [(u, fp) for u in self._users for fp in u.fingerprints]
        return sorted(pairs, key=lambda pair: pair[1].slot)

    def find_slot(self, slot: int) -> tuple[User, Fingerprint] | None:
        with self._lock:
            for user in self._users:
                for fp in user.fingerprints:
                    if fp.slot == slot:
                        return user, fp
        return None

    def add_fingerprint(self, user_id: str, slot: int, label: str = "") -> Fingerprint:
        with self._lock:
            user = self.by_id(user_id)
            if user is None:
                raise ValueError("No such user.")
            if self.find_slot(slot):
                raise ValueError(f"Slot {slot} is already registered.")
            fp = Fingerprint(slot=slot, label=label.strip())
            user.fingerprints.append(fp)
            self.save()
        if self.events:
            self.events.info(
                "fingerprint.add", f"slot {slot} -> {user.name} '{fp.label}'"
            )
        return fp

    def delete_fingerprint(self, slot: int) -> tuple[User, Fingerprint] | None:
        with self._lock:
            found = self.find_slot(slot)
            if found is None:
                return None
            user, fp = found
            user.fingerprints = [f for f in user.fingerprints if f.slot != slot]
            self.save()
        if self.events:
            self.events.warning("fingerprint.delete", f"slot {slot} ({user.name})")
        return found

    def reconcile(self, device_slots: set[int]) -> dict[str, list[int]]:
        """Compare the registry against the sensor's real template index.

        Returns ``{"orphaned": [...], "unregistered": [...]}``:

        * *orphaned*  -- we have a record, the sensor has no template.
        * *unregistered* -- the sensor holds a template we know nothing about.

        Nothing is deleted automatically; the admin screen shows the drift and
        lets a human decide.
        """
        with self._lock:
            known = self.used_slots()
            orphaned = sorted(known - device_slots)
            unregistered = sorted(device_slots - known)
            for user in self._users:
                for fp in user.fingerprints:
                    fp.orphaned = fp.slot in orphaned
            self.save()
        if self.events and (orphaned or unregistered):
            self.events.warning(
                "fingerprint.sync",
                f"orphaned={orphaned} unregistered={unregistered}",
            )
        return {"orphaned": orphaned, "unregistered": unregistered}

    # ------------------------------------------------------------------ faces
    def all_faces(self) -> list[tuple[User, Face]]:
        with self._lock:
            return [(u, f) for u in self._users for f in u.faces]

    def find_face(self, face_id: str) -> tuple[User, Face] | None:
        with self._lock:
            return next(((u, f) for u in self._users for f in u.faces
                         if f.id == face_id), None)

    def add_face(self, user_id: str, samples: list[str], model: str,
                 label: str = "") -> Face:
        with self._lock:
            user = self.by_id(user_id)
            if user is None:
                raise ValueError("No such user.")
            if not samples:
                raise ValueError("No face samples.")
            face = Face(label=label.strip(), model=model, samples=list(samples))
            user.faces.append(face)
            self.save()
        if self.events:
            self.events.info("face.add", f"{user.name} ({len(samples)} samples)")
        return face

    def delete_face(self, face_id: str) -> tuple[User, Face] | None:
        with self._lock:
            found = self.find_face(face_id)
            if found is None:
                return None
            user, face = found
            user.faces = [f for f in user.faces if f.id != face_id]
            self.save()
        if self.events:
            self.events.warning("face.delete", f"{user.name} (face {face_id})")
        return found
