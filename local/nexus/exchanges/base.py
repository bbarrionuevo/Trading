"""Cliente HTTP de SOLO LECTURA: solo permite GET sobre endpoints en lista blanca."""
import requests


class ReadOnlyViolation(Exception):
    pass


class ReadOnlyClient:
    ALLOWED_PRIVATE: frozenset = frozenset()

    def __init__(self, base_url: str, session=None, timeout=10):
        self.base_url = base_url.rstrip("/")
        self.s = session or requests.Session()
        self.timeout = timeout

    def _guard(self, path: str):
        if path not in self.ALLOWED_PRIVATE and not path.startswith(self.PUBLIC_PREFIX):
            raise ReadOnlyViolation(f"Endpoint no permitido (solo lectura): {path}")

    PUBLIC_PREFIX = "/__public__"
