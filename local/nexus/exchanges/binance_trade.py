"""Binance spot CON ÓRDENES, solo para CUBRIR la cobertura MEXC→Binance: comprar/vender en spot,
consultar y cancelar órdenes propias.

No existe ningún método de retiro, transferencia, margen ni futuros: la lista blanca de abajo es lo
único que puede tocar y un test verifica que ningún otro módulo contenga endpoints de órdenes.
La clave debe crearse SIN permiso de retiro y limitada a la IP de la PC (R-01). Este cliente solo lo
usa `agent/hedge_live.py` cuando hay una autorización local vigente del dueño para la cobertura (R-02).
"""
import hashlib
import hmac
import time
from urllib.parse import urlencode

from .base import ReadOnlyViolation
from .binance import BinanceReadOnly
from .mexc_trade import OrderRejected                  # mismo criterio: rechazo = resultado conocido


class BinanceTrader(BinanceReadOnly):
    ORDER_PATHS = frozenset({("POST", "/api/v3/order"), ("DELETE", "/api/v3/order"), ("GET", "/api/v3/order"),
                             ("GET", "/api/v3/openOrders")})

    def _send(self, method, path, params):
        if (method, path) not in self.ORDER_PATHS:
            raise ReadOnlyViolation(f"Endpoint no permitido: {method} {path}")
        params = {k: v for k, v in params.items() if v is not None}
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        params["signature"] = hmac.new(self.secret.encode(), urlencode(params).encode(), hashlib.sha256).hexdigest()
        r = self.s.request(method, self.base_url + path, params=params, headers={"X-MBX-APIKEY": self.key},
                           timeout=self.timeout)
        if 400 <= r.status_code < 500:
            try:
                body = r.json()
            except ValueError:
                body = {}
            raise OrderRejected(r.status_code, body.get("code"), body.get("msg") or r.text[:200])
        r.raise_for_status()                                  # 5xx: resultado desconocido
        return r.json()

    def new_order(self, symbol, side, type_, quantity, price=None, cid=None):
        """MARKET para cubrir al instante; FULL devuelve lo ejecutado y a qué precio."""
        tif = "GTC" if type_ == "LIMIT" else None
        return self._send("POST", "/api/v3/order", {"symbol": symbol, "side": side, "type": type_,
                                                    "quantity": quantity, "price": price, "timeInForce": tif,
                                                    "newClientOrderId": cid, "newOrderRespType": "FULL"})

    def get_order(self, symbol, cid):
        return self._send("GET", "/api/v3/order", {"symbol": symbol, "origClientOrderId": cid})

    def cancel_order(self, symbol, cid):
        return self._send("DELETE", "/api/v3/order", {"symbol": symbol, "origClientOrderId": cid})

    def open_orders(self, symbol):
        return self._send("GET", "/api/v3/openOrders", {"symbol": symbol})

    def account(self):
        return self._get("/api/v3/account", signed=True)

    def exchange_info(self, symbol):
        return self._get("/api/v3/exchangeInfo", {"symbol": symbol})
