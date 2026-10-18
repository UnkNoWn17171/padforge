"""Live touchpad measurements: report rate, wake-up delay, noise, latency.

Everything here is computed from the frames themselves, so the same numbers
appear in the GUI, ``padforge measure`` and the daemon status.
"""
from __future__ import annotations

import math
import statistics
import time
from collections import deque
from dataclasses import dataclass, field

from .mt import Frame

WAKE_FRAMES = 12           # frames per touch sequence used for the wake-up estimate
STILL_WINDOW_S = 0.30
STILL_MAX_MM = 0.6


@dataclass
class Summary:
    frames: int = 0
    sessions: int = 0
    rate_hz: float | None = None          # from device timestamps (MSC_TIMESTAMP)
    kernel_rate_hz: float | None = None   # from kernel timestamps
    interval_jitter_ms: float | None = None
    wake_delay_ms: float | None = None    # median extra delay of the first report
    wake_delay_max_ms: float | None = None
    read_latency_ms: float | None = None  # kernel timestamp -> our read()
    noise_mm: float | None = None         # position noise of a resting finger
    max_fingers: int = 0
    dropped: int = 0
    samples: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = dict(vars(self))
        d.pop("samples", None)
        return d


class TouchStats:
    def __init__(self, history: int = 600):
        self.hw_dt: deque[float] = deque(maxlen=history)       # µs
        self.k_dt: deque[float] = deque(maxlen=history)        # s
        self.latency: deque[float] = deque(maxlen=history)     # s
        self.wake: deque[float] = deque(maxlen=64)             # s
        self.noise: deque[float] = deque(maxlen=64)            # mm
        self.frames = 0
        self.sessions = 0
        self.max_fingers = 0
        self._last_hw: int | None = None
        self._last_k: float | None = None
        self._session: list[tuple[float, int | None]] | None = None
        self._session_done = False
        self._still: deque[tuple[float, float, float]] = deque()
        self._still_id: int | None = None

    def feed(self, f: Frame, read_time: float | None = None) -> None:
        self.frames += 1
        count = f.count
        self.max_fingers = max(self.max_fingers, count)
        if read_time is not None:
            lat = read_time - f.time
            if 0 <= lat < 1.0:
                self.latency.append(lat)

        active = count > 0 or bool(f.ended)
        if active:
            if self._last_hw is not None and f.hw_time is not None:
                d = f.hw_time - self._last_hw
                if 0 < d < 100_000:
                    self.hw_dt.append(d)
            if self._last_k is not None:
                dk = f.time - self._last_k
                if 0 < dk < 0.1:
                    self.k_dt.append(dk)
            self._last_hw, self._last_k = f.hw_time, f.time
        else:
            self._last_hw = self._last_k = None

        # Wake-up delay: compare device and kernel clocks over the first frames.
        if count > 0 and self._session is None:
            self._session = []
            self._session_done = False
            self.sessions += 1
        if self._session is not None:
            if not self._session_done:
                self._session.append((f.time, f.hw_time))
                if len(self._session) >= WAKE_FRAMES:
                    self._finish_session()
            if count == 0:
                if not self._session_done and len(self._session) >= 4:
                    self._finish_session()
                self._session = None

        self._track_still(f)

    def _finish_session(self) -> None:
        self._session_done = True
        s = self._session
        if not s or s[0][1] is None:
            return
        k0, h0 = s[0]
        best = None
        for k, h in s[1:]:
            if h is None:
                return
            dh = (h - h0) / 1e6
            dk = k - k0
            if dh <= 0 or dh > 0.5:
                return          # device clock reset or wrapped: no estimate
            extra = dh - dk
            best = extra if best is None else max(best, extra)
        if best is not None:
            self.wake.append(max(0.0, best))

    def _track_still(self, f: Frame) -> None:
        fingers = f.fingers
        if len(fingers) != 1:
            self._still.clear()
            self._still_id = None
            return
        t = fingers[0]
        if t.tracking_id != self._still_id:
            self._still.clear()
            self._still_id = t.tracking_id
        self._still.append((f.time, t.x, t.y))
        while self._still and f.time - self._still[0][0] > STILL_WINDOW_S:
            self._still.popleft()
        if len(self._still) >= 10 and f.time - self._still[0][0] >= STILL_WINDOW_S * 0.9:
            xs = [p[1] for p in self._still]
            ys = [p[2] for p in self._still]
            if max(xs) - min(xs) < STILL_MAX_MM and max(ys) - min(ys) < STILL_MAX_MM:
                sd = math.hypot(statistics.pstdev(xs), statistics.pstdev(ys))
                self.noise.append(sd)
                self._still.clear()

    def summary(self, dropped: int = 0) -> Summary:
        s = Summary(frames=self.frames, sessions=self.sessions, max_fingers=self.max_fingers,
                    dropped=dropped)
        if len(self.hw_dt) >= 8:
            med = statistics.median(self.hw_dt)
            s.rate_hz = 1e6 / med
            s.interval_jitter_ms = statistics.pstdev(self.hw_dt) / 1000.0
        if len(self.k_dt) >= 8:
            s.kernel_rate_hz = 1.0 / statistics.median(self.k_dt)
            if s.interval_jitter_ms is None:
                s.interval_jitter_ms = statistics.pstdev(self.k_dt) * 1000.0
        if self.wake:
            s.wake_delay_ms = statistics.median(self.wake) * 1000.0
            s.wake_delay_max_ms = max(self.wake) * 1000.0
        if self.latency:
            s.read_latency_ms = statistics.median(self.latency) * 1000.0
        if self.noise:
            s.noise_mm = statistics.median(self.noise)
        s.samples = {"intervals": len(self.hw_dt), "wake": len(self.wake),
                     "noise": len(self.noise)}
        return s


def now() -> float:
    return time.monotonic()
