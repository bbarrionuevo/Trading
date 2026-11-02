"""Relojes inyectables. Las edades (staleness), los TTL y las latencias se miden con un reloj, nunca con
time.time() suelto: así los tests y la reproducción de días pasados son deterministas."""
import time
from typing import Protocol


class Clock(Protocol):
    def time(self) -> float:
        """Época UTC en segundos (reloj de pared)."""

    def monotonic(self) -> float:
        """Segundos monótonos, para duraciones."""


class SystemClock:
    def time(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()


class ManualClock:
    """Reloj que solo avanza cuando se le pide (tests y reproducción)."""

    def __init__(self, start: float = 1_767_225_600.0):        # 2026-01-01T00:00:00Z
        self._t, self._m = float(start), 0.0

    def time(self) -> float:
        return self._t

    def monotonic(self) -> float:
        return self._m

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("el reloj no retrocede")
        self._t += seconds
        self._m += seconds

    def set(self, epoch: float) -> None:
        if epoch < self._t:
            raise ValueError("el reloj no retrocede")
        self._m += epoch - self._t
        self._t = epoch
