"""Binance Spot, solo lectura (HMAC SHA256). Nunca envía órdenes."""
import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

from .base import ReadOnlyClient


class BinanceReadOnly(ReadOnlyClient):
    PUBLIC_PREFIX = "/api/v3/ticker"
    # capital/config/getall: SOLO LECTURA de redes, depósitos/retiros habilitados y comisiones por moneda.
    # account/apiRestrictions: SOLO LECTURA de los permisos de la propia clave (para verificar que no
    # tenga retiros en vez de confiar en lo que se marcó a mano).
    ALLOWED_PRIVATE = frozenset({"/api/v3/account", "/api/v3/depth", "/api/v3/exchangeInfo",
                                 "/sapi/v1/capital/config/getall", "/sapi/v1/account/apiRestrictions"})

    def __init__(self, key=None, secret=None, **kw):
        super().__init__("https://api.binance.com", **kw)
        self.key = key or os.environ.get("BINANCE_API_KEY", "")
        self.secret = secret or os.environ.get("BINANCE_SECRET_KEY", "")

    def _get(self, path, params=None, signed=False):
        self._guard(path)
        params = dict(params or {})
        headers = {}
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 5000
            q = urlencode(params)
            params["signature"] = hmac.new(self.secret.encode(), q.encode(), hashlib.sha256).hexdigest()
            headers["X-MBX-APIKEY"] = self.key
        r = self.s.get(self.base_url + path, params=params, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def price(self, symbol: str):
        return self._get("/api/v3/ticker/price", {"symbol": symbol})

    def book_ticker(self, symbol: str):
        return self._get("/api/v3/ticker/bookTicker", {"symbol": symbol})

    def balances(self):
        acc = self._get("/api/v3/account", signed=True)
        return {b["asset"]: b["free"] for b in acc["balances"] if float(b["free"]) or float(b["locked"])}

    def api_restrictions(self):
        """Permisos reales de la clave: enableWithdrawals, enableSpotAndMarginTrading, ipRestrict,
        enableInternalTransfer, permitsUniversalTransfer, enableFutures, enableMargin..."""
        return self._get("/sapi/v1/account/apiRestrictions", signed=True)

    def capital_config(self):
        """Redes por moneda: depósito/retiro habilitados y comisión de retiro (solo lectura)."""
        return self._get("/sapi/v1/capital/config/getall", signed=True)
