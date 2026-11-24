"""Camera face recognition: enrolment and identification at the door.

Pipeline, all OpenCV (python3-opencv) on the CPU:

* **YuNet** finds faces and five landmarks (eyes, nose, mouth corners).
* **SFace** turns the aligned face into 128 numbers (an embedding).  Two
  pictures of the same person point the same way (cosine similarity near 1);
  different people score low.

Only those embeddings are stored, with the user record in data/users.json --
never a photo.

One worker thread owns the camera for the app's whole life.  The screen on
display tells it what to do: :meth:`FaceService.watch` (Home: identify whoever
stands at the door) or :meth:`FaceService.enrol` (collect pictures of one
face); otherwise it idles with the camera open.  Results come back through
callbacks on the worker thread, which the Tk layer wraps in ``App.post``,
exactly like the fingerprint worker.

Unlock policy while watching:

* only the largest face counts, and only once it is ``min_face_size`` wide,
  so people walking past further away are ignored;
* it must match the same enrolled user on ``confirm_frames`` frames in a row;
* after a match nothing more happens until the door has relocked and
  ``cooldown_seconds`` have passed, and the same person opens it again only
  after stepping out of view: lingering at the door does not keep it open.
"""

from __future__ import annotations

import base64
import threading
import time
from dataclasses import dataclass, field

from .config import Config
from .fingerprint import OpResult

# Official OpenCV model zoo files: config option -> (zoo path, size, SHA-256).
# tools/fetch_face_models.py downloads and verifies them.
MODELS = {
    "detector_model": (
        "face_detection_yunet/face_detection_yunet_2023mar.onnx", 232589,
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "recognizer_model": (
        "face_recognition_sface/face_recognition_sface_2021dec.onnx", 38696353,
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}
# Saved with every enrolled face: embeddings from another model mean nothing.
MODEL_ID = "sface-2021dec"

# A black frame is no picture: darker than BLANK_MEAN and flatter than
# BLANK_STD (0-255 grey).  snapshot() tries up to SNAPSHOT_TRIES frames before
# it gives up, and says so in the event log at most every BLANK_LOG_EVERY s.
BLANK_MEAN = 12.0
BLANK_STD = 4.0
SNAPSHOT_TRIES = 8
BLANK_LOG_EVERY = 600.0

MODE_IDLE = "idle"
MODE_WATCH = "watch"
MODE_ENROL = "enrol"

# What the camera sees, reported to the screen on every change.
FACE_NONE = "none"          # nobody in front of the camera
FACE_FAR = "far"            # a face, too small: too far from the door
FACE_MANY = "many"          # enrolment: more than one person close by
FACE_CHECKING = "checking"  # a face is being compared / captured
FACE_MATCH = "match"        # an enrolled face; the door was asked to open
FACE_UNKNOWN = "unknown"    # a face that matches nobody
FACE_PAUSED = "paused"      # just matched, waiting out the cooldown
FACE_SEEN = "seen"          # matched earlier and still in view: must step away
CAMERA_OFFLINE = "offline"

# Preview box colour per state (BGR), matching app/ui/theme.py.
BOX_COLOURS = {
    FACE_FAR: (34, 153, 210),
    FACE_MANY: (34, 153, 210),
    FACE_CHECKING: (247, 129, 47),
    FACE_MATCH: (80, 185, 63),
    FACE_PAUSED: (80, 185, 63),
    FACE_SEEN: (165, 152, 139),
    FACE_UNKNOWN: (73, 81, 248),
}

DETECT_WIDTH = 320      # faces are searched in a downscaled copy of the frame
ACTIVE_FPS = 8          # face checks per second while someone is in view
IDLE_FPS = 3            # ... and while nobody is, which is most of the day
AWAKE_SECONDS = 3.0     # stay at ACTIVE_FPS this long after the last face
PREVIEW_FPS = 10        # frames rendered for the screen; the rest are skipped
UNKNOWN_FRAMES = 6      # failed comparisons before a face counts as unknown
PRESENCE_GAP = 1.5      # seconds without a face before it counts as gone
DENY_LOG_GAP = 10.0     # at most one "unknown face" log line this often
ENROL_SCORE = 0.85      # enrolment wants clearer pictures than the door
ENROL_GAP = 0.4         # seconds between enrolment pictures
ENROL_TIMEOUT = 40.0


class FaceUnavailable(Exception):
    """OpenCV, a model or the configuration is missing."""


def encode_embedding(vec) -> str:
    import numpy as np

    return base64.b64encode(np.asarray(vec, dtype="<f4").tobytes()).decode("ascii")


def decode_embedding(text: str):
    import numpy as np

    return np.frombuffer(base64.b64decode(text), dtype="<f4")


def _unit(vec):
    import numpy as np

    norm = float(np.linalg.norm(vec))
    return vec / norm if norm else vec


def check_models(config: Config) -> None:
    """Raise :class:`FaceUnavailable` unless both model files look right."""
    for option, (_, size, _) in MODELS.items():
        path = config.path_for("face", option)
        if not path.is_file():
            raise FaceUnavailable(f"Model file missing: {path}\n\n"
                                  "Download it with:\n"
                                  "    python3 tools/fetch_face_models.py")
        if path.stat().st_size != size:
            raise FaceUnavailable(f"Model file is incomplete or wrong: {path}\n\n"
                                  "Fetch it again with:\n"
                                  "    python3 tools/fetch_face_models.py")


# ----------------------------------------------------------------------- engine
class FaceEngine:
    """YuNet + SFace.  Not thread-safe: only the worker thread uses it."""

    def __init__(self, config: Config):
        import cv2

        check_models(config)
        self._cv2 = cv2
        self._detector = cv2.FaceDetectorYN.create(
            str(config.path_for("face", "detector_model")), "", (DETECT_WIDTH, 240),
            config.getfloat("face", "detect_score", 0.8), 0.3, 50)
        self._recognizer = cv2.FaceRecognizerSF.create(
            str(config.path_for("face", "recognizer_model")), "")
        self._input_size: tuple[int, int] | None = None

    def detect(self, frame) -> list:
        """Faces as YuNet rows (box, landmarks, score) in frame pixels,
        largest first."""
        cv2 = self._cv2
        h, w = frame.shape[:2]
        scale = min(1.0, DETECT_WIDTH / w)
        small = cv2.resize(frame, (round(w * scale), round(h * scale)),
                           interpolation=cv2.INTER_AREA) if scale < 1 else frame
        size = (small.shape[1], small.shape[0])
        if size != self._input_size:
            self._detector.setInputSize(size)
            self._input_size = size
        _, faces = self._detector.detect(small)
        if faces is None:
            return []
        faces = faces.copy()
        faces[:, :14] /= scale
        return sorted(faces, key=lambda f: f[2] * f[3], reverse=True)

    def embed(self, frame, face):
        """Unit-length 128-d embedding of one detected face."""
        aligned = self._recognizer.alignCrop(frame, face)
        return _unit(self._recognizer.feature(aligned).flatten().astype("float32"))


class Gallery:
    """Every enrolled embedding as one matrix, rebuilt when users.json changes."""

    def __init__(self):
        self.revision = -1
        self._owners: list[str] = []
        self._matrix = None

    def refresh(self, users) -> None:
        if users.revision == self.revision:
            return
        import numpy as np

        owners, rows = [], []
        for user, face in users.all_faces():
            if face.model != MODEL_ID:
                continue
            for sample in face.samples:
                vec = decode_embedding(sample)
                if vec.shape == (128,):
                    owners.append(user.id)
                    rows.append(vec)
        self._owners = owners
        self._matrix = np.vstack(rows) if rows else None
        self.revision = users.revision

    def best(self, embedding, exclude: str | None = None) -> tuple[str | None, float]:
        """Closest enrolled user and the cosine similarity to them."""
        if self._matrix is None:
            return None, 0.0
        scores = self._matrix @ embedding
        if exclude is not None:
            for i, owner in enumerate(self._owners):
                if owner == exclude:
                    scores[i] = -1.0
        i = int(scores.argmax())
        if scores[i] < 0:
            return None, 0.0
        return self._owners[i], float(scores[i])


# ---------------------------------------------------------------------- service
@dataclass
class _Job:
    """What the worker is doing for the screen on display."""

    mode: str = MODE_IDLE
    token: int = 0
    preview: tuple[int, int] | None = None
    on_state: object = None
    on_match: object = None
    on_progress: object = None
    on_done: object = None
    state: str = ""
    faces: list = field(default_factory=list)   # last detection, for the preview
    last_face: float = 0.0
    next_check: float = 0.0
    next_preview: float = 0.0
    # watch
    streak_user: str | None = None
    streak: int = 0
    misses: int = 0
    last_seen: float = 0.0
    reported: bool = False
    quiet_until: float = 0.0
    let_in: str | None = None   # matched on this visit; cleared once they leave
    # enrol
    user_id: str | None = None
    samples: list = field(default_factory=list)
    last_sample: float = 0.0
    deadline: float = 0.0
    finished: bool = False


def is_blank(frame) -> bool:
    """True when ``frame`` (BGR or grey) is one flat, dark colour: no picture.

    The camera sometimes hands over such frames (every pixel about 2, no noise
    at all).  A real scene, even a dim one, has texture, so it passes.
    """
    import cv2

    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    mean, std = cv2.meanStdDev(grey)
    return float(mean[0][0]) < BLANK_MEAN and float(std[0][0]) < BLANK_STD


class _Snapshot:
    """One pending :meth:`FaceService.snapshot` call."""

    def __init__(self):
        self.ready = threading.Event()
        self.frame = None


def _call(callback, *args) -> None:
    if callback is None:
        return
    try:
        callback(*args)
    except Exception:  # a broken screen callback must not stop the camera
        pass


class FaceService:
    def __init__(self, config: Config, users, events=None):
        self.config = config
        self.users = users
        self.events = events
        self._lock = threading.Lock()
        self._job = _Job()
        self._token = 0
        self._preview: tuple[int, bytes] | None = None
        self._preview_seq = 0
        self._online: bool | None = None
        self._listeners: list = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_deny_log = 0.0
        self._last_error = ""
        self._gallery = Gallery()
        self._snaps: list[_Snapshot] = []   # threads waiting for a still picture
        # The last snapshot() gave up because every frame was black.
        self.last_blank = False
        self._blank_logged = float("-inf")
        self._reason = self._check()

    def _check(self) -> str:
        """Why the feature cannot run, or "" when it can."""
        if not self.config.getbool("face", "enabled", False):
            return ("Face recognition is disabled.\n\n"
                    "Connect a USB camera, run python3 tools/fetch_face_models.py, "
                    "then set [face] enabled = true in config.ini.")
        try:
            import cv2  # noqa: F401
            import numpy  # noqa: F401
        except ImportError as exc:
            return (f"OpenCV is not installed ({exc}).\n\n"
                    "    sudo apt install python3-opencv")
        try:
            check_models(self.config)
        except FaceUnavailable as exc:
            return str(exc)
        return ""

    # -------------------------------------------------------------- properties
    @property
    def available(self) -> bool:
        return not self._reason

    @property
    def camera_online(self) -> bool:
        return bool(self._online)

    def status_text(self) -> str:
        if self._reason:
            return self._reason
        camera = self.config.get("face", "camera")
        n = len(self.users.all_faces())
        state = "online" if self._online else "offline"
        return f"Camera {camera} {state}  ·  {n} face{'s' if n != 1 else ''} enrolled."

    def add_listener(self, callback) -> None:
        """``callback(online: bool)`` whenever the camera comes or goes."""
        with self._lock:
            self._listeners.append(callback)

    # ------------------------------------------------------------------- jobs
    def watch(self, on_state=None, on_match=None, preview=None) -> int:
        """Identify faces until :meth:`release`.  ``on_match(user_id, score)``
        fires once per confirmed match; ``on_state(state)`` on every change."""
        return self._start(_Job(mode=MODE_WATCH, preview=preview, on_state=on_state,
                                on_match=on_match))

    def enrol(self, user_id: str | None, on_state=None, on_progress=None, on_done=None,
              preview=None) -> int:
        """Collect ``face.samples`` pictures of one face for ``user_id`` (None for
        someone not in users.json yet).  It is refused if it already matches
        another user.

        ``on_progress(n, total)`` after each picture; ``on_done(OpResult)`` once,
        with ``data["samples"]`` (encoded embeddings) on success.
        """
        job = _Job(mode=MODE_ENROL, preview=preview, on_state=on_state,
                   on_progress=on_progress, on_done=on_done, user_id=user_id,
                   deadline=time.monotonic() + ENROL_TIMEOUT)
        return self._start(job)

    def _start(self, job: _Job) -> int:
        with self._lock:
            self._token += 1
            job.token = self._token
            self._job = job
            self._preview = None
        if not self._online:
            job.state = CAMERA_OFFLINE
            _call(job.on_state, CAMERA_OFFLINE)
        return job.token

    def release(self, token: int) -> None:
        """Back to idle, unless another screen has taken over meanwhile."""
        with self._lock:
            if self._job.token == token:
                self._job = _Job()
                self._preview = None

    def preview_frame(self) -> tuple[int, bytes] | None:
        """Latest ``(sequence, PPM bytes)`` for the screen's preview."""
        with self._lock:
            return self._preview

    # --------------------------------------------------------------- pictures
    def snapshot(self, timeout: float = 2.0):
        """A fresh camera frame (BGR array, as the camera sees it: not
        mirrored, no box), or None when there is no working camera.

        Safe from any thread.  The worker owns the camera, so this asks it for
        its next frame and waits; whatever the screen is doing goes on.

        A flat black frame is no picture (see :func:`is_blank`): the next
        frames are tried, and when they are all black the answer is None with
        ``last_blank`` set, so the caller can say why there is no picture.
        """
        self.last_blank = False
        if not self.available or self._thread is None or not self._online:
            return None
        deadline = time.monotonic() + timeout
        blank = False
        for _ in range(SNAPSHOT_TRIES):
            frame = self._next_frame(deadline - time.monotonic())
            if frame is None:
                break
            if not is_blank(frame):
                return frame
            blank = True
        if blank:
            self.last_blank = True
            now = time.monotonic()
            if self.events and now - self._blank_logged >= BLANK_LOG_EVERY:
                self._blank_logged = now
                self.events.warning("face.blank",
                                    "the camera gave only black frames: no picture taken")
        return None

    def _next_frame(self, timeout: float):
        """The worker's next frame, or None when none comes within ``timeout``."""
        if timeout <= 0:
            return None
        request = _Snapshot()
        with self._lock:
            self._snaps.append(request)
        if not request.ready.wait(timeout):
            with self._lock:
                if request in self._snaps:
                    self._snaps.remove(request)
        return request.frame

    def snapshot_jpeg(self, timeout: float = 2.0, quality: int | None = None) -> bytes | None:
        """:meth:`snapshot` as JPEG bytes."""
        frame = self.snapshot(timeout)
        if frame is None:
            return None
        import cv2

        if quality is None:
            quality = self.config.getint("photos", "jpeg_quality", 80)
        ok, data = cv2.imencode(".jpg", frame,
                                [cv2.IMWRITE_JPEG_QUALITY, max(30, min(int(quality), 95))])
        return data.tobytes() if ok else None

    def _serve_snapshots(self, frame) -> None:
        """Worker thread: hand ``frame`` to everyone waiting for a picture."""
        with self._lock:
            waiting, self._snaps = self._snaps, []
        if not waiting:
            return
        still = self._upright(frame).copy()
        for request in waiting:
            request.frame = still
            request.ready.set()

    def _fail_snapshots(self) -> None:
        """Worker thread: the camera is gone; nobody should wait out a timeout."""
        with self._lock:
            waiting, self._snaps = self._snaps, []
        for request in waiting:
            request.ready.set()

    def _upright(self, frame):
        """``frame`` turned by ``[face] rotate``."""
        rotate = self.config.getint("face", "rotate", 0) % 360
        if not rotate:
            return frame
        import cv2

        codes = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
                 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
        return cv2.rotate(frame, codes[rotate]) if rotate in codes else frame

    # ----------------------------------------------------------------- worker
    def start(self) -> None:
        if self.available and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="face", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        import cv2

        cv2.setNumThreads(2)  # leave the other cores to the UI and the API
        try:
            engine = FaceEngine(self.config)
        except Exception as exc:
            self._reason = f"Could not load the face models: {exc}"
            if self.events:
                self.events.error("face.backend", self._reason)
            self._set_online(False)
            return
        if self.events:
            self.events.info("face.backend",
                             f"camera {self.config.get('face', 'camera')}, "
                             f"{len(self.users.all_faces())} face(s) enrolled")
        cap, misses = None, 0
        while not self._stop.is_set():
            with self._lock:
                job = self._job
            if cap is None:
                cap = self._open_camera()
                if cap is None:
                    self._set_online(False)
                    self._fail_snapshots()
                    self._report(job, CAMERA_OFFLINE)
                    self._stop.wait(3.0)
                    continue
                misses = 0
            now = time.monotonic()
            check = job.mode != MODE_IDLE and now >= job.next_check
            draw = job.preview is not None and now >= job.next_preview
            if check or draw or self._snaps:
                ok, frame = cap.read()
            else:
                ok, frame = cap.grab(), None  # keep the camera fresh, skip decoding
            if not ok:
                misses += 1
                if misses >= 15:
                    cap.release()
                    cap = None
                else:
                    self._stop.wait(0.05)
                continue
            misses = 0
            self._set_online(True)
            if frame is None:
                continue
            try:
                self._serve_snapshots(frame)
                if check or draw:
                    self._process(engine, job, frame, check, draw)
            except Exception as exc:
                self._error(exc)
        if cap is not None:
            cap.release()
        self._fail_snapshots()

    def _open_camera(self):
        import cv2

        cfg = self.config
        device = cfg.get("face", "camera")
        cap = cv2.VideoCapture(int(device) if device.isdigit() else device, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.getint("face", "width", 640))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.getint("face", "height", 480))
        cap.set(cv2.CAP_PROP_FPS, cfg.getint("face", "fps", 15))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _set_online(self, online: bool) -> None:
        with self._lock:
            if online == self._online:
                return
            first = self._online is None
            self._online = online
            listeners = list(self._listeners)
        if self.events and not (first and online):
            device = self.config.get("face", "camera")
            if online:
                self.events.info("face.camera", f"camera {device} online")
            else:
                self.events.warning("face.camera", f"camera {device} offline")
        for cb in listeners:
            _call(cb, online)

    def _error(self, exc: Exception) -> None:
        text = f"{type(exc).__name__}: {exc}"
        if text != self._last_error and self.events:
            self.events.error("face.error", text)
        self._last_error = text

    def _report(self, job: _Job, state: str) -> None:
        if state != job.state:
            job.state = state
            _call(job.on_state, state)

    # ------------------------------------------------------------- processing
    def _process(self, engine: FaceEngine, job: _Job, frame, check: bool,
                 draw: bool) -> None:
        """``check``: look for faces and act on them; ``draw``: refresh the
        preview (with the box from the latest check)."""
        frame = self._upright(frame)
        now = time.monotonic()
        if check:
            faces = job.faces = engine.detect(frame)
            if faces:
                job.last_face = now
            awake = job.mode == MODE_ENROL or now - job.last_face < AWAKE_SECONDS
            job.next_check = now + 1.0 / (ACTIVE_FPS if awake else IDLE_FPS)
            if job.mode == MODE_WATCH:
                state = self._watch_step(engine, job, frame, faces)
            else:
                state = self._enrol_step(engine, job, frame, faces)
            self._report(job, state)
        if draw:
            job.next_preview = now + 1.0 / PREVIEW_FPS
            self._render(frame, job.faces[0] if job.faces else None, job.state, job)

    def _watch_step(self, engine, job: _Job, frame, faces) -> str:
        cfg = self.config
        now = time.monotonic()
        face = faces[0] if faces else None
        close = face is not None and face[2] >= cfg.getint("face", "min_face_size", 90)
        if close:
            job.last_seen = now
        elif now - job.last_seen > PRESENCE_GAP:
            job.reported = False
            job.misses = 0
            job.let_in = None
        if now < job.quiet_until:
            return FACE_PAUSED
        if not close:
            job.streak = 0
            return FACE_NONE if face is None else FACE_FAR

        self._gallery.refresh(self.users)
        user_id, score = self._gallery.best(engine.embed(frame, face))
        threshold = cfg.getfloat("face", "match_threshold", 0.45)
        if user_id is not None and score >= threshold:
            job.misses = 0
            if user_id == job.let_in:
                job.streak = 0
                return FACE_SEEN
            job.streak = job.streak + 1 if user_id == job.streak_user else 1
            job.streak_user = user_id
            if job.streak < max(1, cfg.getint("face", "confirm_frames", 3)):
                return FACE_CHECKING
            job.streak = 0
            job.let_in = user_id
            job.quiet_until = (now + cfg.unlock_seconds
                               + cfg.getfloat("face", "cooldown_seconds", 5.0))
            _call(job.on_match, user_id, score)
            return FACE_MATCH

        job.streak = 0
        job.misses += 1
        if job.misses < UNKNOWN_FRAMES:
            return FACE_CHECKING
        if not job.reported:
            job.reported = True
            if self.events and now - self._last_deny_log >= DENY_LOG_GAP:
                self._last_deny_log = now
                closest = self.users.by_id(user_id) if user_id else None
                self.events.warning(
                    "face.deny", "unknown face" + (
                        f" (closest {closest.name} {score:.2f})" if closest else ""))
        return FACE_UNKNOWN

    def _enrol_step(self, engine, job: _Job, frame, faces) -> str:
        cfg = self.config
        if job.finished:
            return FACE_PAUSED
        now = time.monotonic()
        if now > job.deadline:
            self._finish(job, OpResult(
                False, "No clear face was seen. Stand about half a metre from the "
                       "camera, facing it, in good light."))
            return FACE_NONE
        min_size = cfg.getint("face", "min_face_size", 90)
        close = [f for f in faces if f[2] >= min_size]
        if not faces:
            return FACE_NONE
        if len(close) > 1:
            return FACE_MANY
        if not close:
            return FACE_FAR
        face = close[0]
        if face[14] < ENROL_SCORE or now - job.last_sample < ENROL_GAP:
            return FACE_CHECKING

        import numpy as np

        embedding = engine.embed(frame, face)
        threshold = cfg.getfloat("face", "match_threshold", 0.45)
        if job.samples and float(_unit(np.mean(job.samples, axis=0)) @ embedding) < threshold:
            return FACE_CHECKING  # not the face captured so far: someone else stepped in
        job.samples.append(embedding)
        job.last_sample = now
        total = max(1, cfg.getint("face", "samples", 5))
        _call(job.on_progress, len(job.samples), total)
        if len(job.samples) < total:
            return FACE_CHECKING

        self._gallery.refresh(self.users)
        owner, score = self._gallery.best(_unit(np.mean(job.samples, axis=0)),
                                          exclude=job.user_id)
        if owner is not None and score >= threshold:
            other = self.users.by_id(owner)
            self._finish(job, OpResult(
                False, f"This face is already enrolled for "
                       f"{other.name if other else 'another user'}."))
            return FACE_UNKNOWN
        self._finish(job, OpResult(True, "Face captured.", {
            "samples": [encode_embedding(s) for s in job.samples]}))
        return FACE_MATCH

    def _finish(self, job: _Job, result: OpResult) -> None:
        job.finished = True
        _call(job.on_done, result)

    def _render(self, frame, face, state: str, job: _Job) -> None:
        import cv2

        width, height = job.preview
        img = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        colour = BOX_COLOURS.get(state)
        if face is not None and colour:
            sx, sy = width / frame.shape[1], height / frame.shape[0]
            x, y, w, h = (float(v) for v in face[:4])
            cv2.rectangle(img, (int(x * sx), int(y * sy)),
                          (int((x + w) * sx), int((y + h) * sy)), colour, 2)
        if self.config.getbool("face", "mirror", True):
            img = cv2.flip(img, 1)
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        data = b"P6\n%d %d\n255\n" % (width, height) + rgb.tobytes()
        with self._lock:
            if self._job is not job:
                return  # the screen moved on while this frame was processed
            self._preview_seq += 1
            self._preview = (self._preview_seq, data)

    # --------------------------------------------------------------- shutdown
    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
