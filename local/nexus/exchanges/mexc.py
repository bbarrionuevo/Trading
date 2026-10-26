"""MEXC spot, SOLO LECTURA (HMAC SHA256). No existe ningún método que envíe órdenes ni retiros."""
import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

from .base import ReadOnlyClient


class MexcReadOnly(ReadOnlyClient):
    PUBLIC_PREFIX = "/api/v3/ticker"
    # capital/config/getall: SOLO LECTURA de redes, depósitos/retiros habilitados y comisiones por moneda.
    ALLOWED_PRIVATE = frozenset({"/api/v3/account", "/api/v3/tradeFee", "/api/v3/trades",
                                 "/api/v3/depth", "/api/v3/exchangeInfo", "/api/v3/capital/config/getall"})

    def __init__(self, key=None, secret=None, **kw):
        super().__init__("https://api.mexc.com", **kw)
        self.key = key or os.environ.get("MEXC_API_KEY", "")
        self.secret = secret or os.environ.get("MEXC_SECRET_KEY", "")

    @property
    def has_keys(self):
        return bool(self.key and self.secret)

    def _get(self, path, params=None, signed=False):
        self._guard(path)
        params, headers = dict(params or {}), {}
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 5000
            params["signature"] = hmac.new(self.secret.encode(), urlencode(params).encode(),
                                           hashlib.sha256).hexdigest()
            headers["X-MEXC-APIKEY"] = self.key
        r = self.s.get(self.base_url + path, params=params, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # públicos
    def book_ticker(self, symbol):
        return self._get("/api/v3/ticker/bookTicker", {"symbol": symbol})

    def trades(self, symbol, limit=100):
        return self._get("/api/v3/trades", {"symbol": symbol, "limit": limit})

    # privados (lectura)
    def account(self):
        return self._get("/api/v3/account", signed=True)

    def trade_fee(self, symbol):
        return self._get("/api/v3/tradeFee", {"symbol": symbol}, signed=True)

    def capital_config(self):
        """Redes por moneda: depósito/retiro habilitados y comisión de retiro (solo lectura)."""
        return self._get("/api/v3/capital/config/getall", signed=True)


def parse_fee(raw):
    """Devuelve (maker_pct, taker_pct) desde la respuesta de tradeFee, tolerando ambos formatos."""
    d = raw.get("data", raw) if isinstance(raw, dict) else {}
    try:
        return float(d["makerCommission"]) * 100, float(d["takerCommission"]) * 100
    except (KeyError, TypeError, ValueError):
        return None
