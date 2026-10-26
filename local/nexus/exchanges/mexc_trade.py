"""MEXC spot CON ÓRDENES (Fase 6). Solo comprar/vender en spot, consultar y cancelar órdenes propias.

No existe ningún método de retiro, transferencia, margen ni futuros: la lista blanca de abajo es lo
único que puede tocar y un test verifica que ningún otro módulo contenga endpoints de órdenes.
La clave debe crearse SIN permiso de retiro, con la IP de la PC y, mejor, en una subcuenta que tenga
solo el capital autorizado (R-01). Este cliente solo lo usa `agent/live.py` cuando hay una autorización
local vigente del dueño (R-02).
"""
import hashlib
import hmac
import time
from urllib.parse import urlencode

from .base import ReadOnlyViolation
from .mexc import MexcReadOnly


class OrderRejected(Exception):
    """El exchange respondió y rechazó el pedido: el resultado es CONOCIDO (no se creó / no existe).
    Cualquier otra excepción (red, tiempo, 5xx) deja el resultado DESCONOCIDO y hay que conciliar."""

    def __init__(self, status, code, msg):
        super().__init__(f"HTTP {status} código {code}: {msg}")
        self.status, self.code, self.msg = status, code, msg

    @property
    def not_found(self):
        m = (self.msg or "").lower()
        return self.code in (-2011, -2013) or "not exist" in m or "unknown order" in m


class MexcTrader(MexcReadOnly):
    ORDER_PATHS = frozenset({("POST", "/api/v3/order"), ("DELETE", "/api/v3/order"), ("GET", "/api/v3/order"),
                             ("GET", "/api/v3/openOrders"), ("GET", "/api/v3/myTrades")})

    def _send(self, method, path, params):
        if (method, path) not in self.ORDER_PATHS:
            raise ReadOnlyViolation(f"Endpoint no permitido: {method} {path}")
        params = {k: v for k, v in params.items() if v is not None}
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        params["signature"] = hmac.new(self.secret.encode(), urlencode(params).encode(), hashlib.sha256).hexdigest()
        r = self.s.request(method, self.base_url + path, params=params, headers={"X-MEXC-APIKEY": self.key},
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
        return self._send("POST", "/api/v3/order", {"symbol": symbol, "side": side, "type": type_,
                                                    "quantity": quantity, "price": price, "newClientOrderId": cid})

    def get_order(self, symbol, cid):
        return self._send("GET", "/api/v3/order", {"symbol": symbol, "origClientOrderId": cid})

    def cancel_order(self, symbol, cid):
        return self._send("DELETE", "/api/v3/order", {"symbol": symbol, "origClientOrderId": cid})

    def open_orders(self, symbol):
        return self._send("GET", "/api/v3/openOrders", {"symbol": symbol})

    def exchange_info(self, symbol):
        return self._get("/api/v3/exchangeInfo", {"symbol": symbol})
