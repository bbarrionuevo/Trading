"""Kraken, solo lectura. Firma API-Sign: HMAC-SHA512(path + SHA256(nonce+postdata))."""
import base64
import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

from .base import ReadOnlyClient


class KrakenReadOnly(ReadOnlyClient):
    PUBLIC_PREFIX = "/0/public/"
    ALLOWED_PRIVATE = frozenset({"/0/private/Balance", "/0/private/TradeBalance"})

    def __init__(self, key=None, secret=None, **kw):
        super().__init__("https://api.kraken.com", **kw)
        self.key = key or os.environ.get("KRAKEN_API_KEY", "")
        self.secret = secret or os.environ.get("KRAKEN_SECRET_KEY", "")

    def ticker(self, pair: str):
        path = "/0/public/Ticker"
        self._guard(path)
        r = self.s.get(self.base_url + path, params={"pair": pair}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _private(self, path):
        self._guard(path)
        data = {"nonce": str(int(time.time() * 1000))}
        post = urlencode(data)
        digest = hashlib.sha256((data["nonce"] + post).encode()).digest()
        sig = hmac.new(base64.b64decode(self.secret), path.encode() + digest, hashlib.sha512)
        headers = {"API-Key": self.key, "API-Sign": base64.b64encode(sig.digest()).decode()}
        r = self.s.post(self.base_url + path, data=data, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        j = r.json()
        if j.get("error"):
            raise RuntimeError(j["error"])
        return j["result"]

    def balances(self):
        return self._private("/0/private/Balance")
