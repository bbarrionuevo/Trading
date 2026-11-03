"""Cliente de TRADING de futuros de MEXC (dinero real). La única pieza del programa que puede mandar órdenes.

- **Lista cerrada** de operaciones: abrir y cerrar posiciones, cancelar, consultar órdenes, ejecuciones y
  posiciones, leer el saldo y fijar el apalancamiento. Cualquier otra ruta lanza `PermissionError` antes de
  salir a la red. No existe código de retiros ni de transferencias (test `test_a3`).
- Margen **aislado** siempre (una posición no arrastra a las demás).
- La firma es la misma del cliente de solo lectura, que ya funcionó con la cuenta del dueño:
  HMAC-SHA256(secreto, clave + hora + parámetros). En GET los parámetros son la query ordenada; en POST,
  el cuerpo JSON tal cual se manda.
- Los errores nunca muestran las claves (`redact`).

Códigos de la API v1 de contratos: lado 1 = abrir largo, 2 = cerrar corto, 3 = abrir corto, 4 = cerrar
largo; tipo 1 = límite, 2 = solo maker, 3 = IOC, 4 = FOK, 5 = mercado; estado 1 = sin informar,
2 = abierta, 3 = completa, 4 = cancelada, 5 = inválida. `check.py` verifica en vivo, sin riesgo, que el
exchange los interprete así antes de armar el modo real.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import quote

import aiohttp

from ...core.redact import redact
from ...marketdata.mexc import BASE

GET_PATHS = ("/api/v1/private/account/assets", "/api/v1/private/position/open_positions",
             "/api/v1/private/position/position_mode", "/api/v1/private/order/get/{}",
             "/api/v1/private/order/deal_details/{}", "/api/v1/private/order/list/open_orders/{}")
POST_PATHS = ("/api/v1/private/order/submit", "/api/v1/private/order/cancel", "/api/v1/private/order/cancel_all",
              "/api/v1/private/position/change_leverage")
OPEN_LONG, CLOSE_SHORT, OPEN_SHORT, CLOSE_LONG = 1, 2, 3, 4
LIMIT, POST_ONLY, IOC, FOK, MARKET = 1, 2, 3, 4, 5
ISOLATED = 1
PENDING, OPEN, FILLED, CANCELED, INVALID = 1, 2, 3, 4, 5
HEDGE, ONE_WAY = 1, 2                              # modo de posición de la cuenta (el conector usa cobertura)
DONE_STATES = (FILLED, CANCELED, INVALID)


class TradingError(RuntimeError):
    pass


def side_code(opening: bool, long: bool) -> int:
    if opening:
        return OPEN_LONG if long else OPEN_SHORT
    return CLOSE_LONG if long else CLOSE_SHORT


def query_string(params: dict) -> str:
    return "&".join(f"{k}={quote(str(v), safe='')}" for k, v in sorted(params.items()))


def sign(access_key: str, secret: str, req_time_ms: str, payload: str) -> str:
    return hmac.new(secret.encode(), f"{access_key}{req_time_ms}{payload}".encode(), hashlib.sha256).hexdigest()


def allowed(method: str, path: str) -> bool:
    for template in GET_PATHS if method == "GET" else POST_PATHS if method == "POST" else ():
        if "{}" not in template:
            if path == template:
                return True
            continue
        prefix = template.split("{}")[0]
        rest = path[len(prefix):]
        if path.startswith(prefix) and rest and rest.replace("_", "").replace("-", "").isalnum():
            return True
    return False


def _num(x) -> float:
    return float(str(x))


class TradingClient:
    def __init__(self, session: aiohttp.ClientSession, access_key: str, secret: str, *, base: str = BASE,
                 clock=None, timeout_s: float = 8.0):
        if not access_key or not secret:
            raise ValueError("faltan las claves de MEXC")
        self.session, self._key, self._secret, self.base = session, access_key, secret, base.rstrip("/")
        self.clock, self.timeout_s = clock, timeout_s

    def _now_ms(self) -> str:
        return str(int((self.clock.time() if self.clock else time.time()) * 1000))

    async def request(self, method: str, path: str, *, params: dict | None = None, body=None):
        if not allowed(method, path):
            raise PermissionError(f"operación no permitida para el cliente de trading: {method} {path}")
        t = self._now_ms()
        if method == "GET":
            payload = query_string(params or {})
            url, data = f"{self.base}{path}" + (f"?{payload}" if payload else ""), None
        else:
            payload = json.dumps(body, separators=(",", ":"))
            url, data = f"{self.base}{path}", payload
        headers = {"ApiKey": self._key, "Request-Time": t, "Signature": sign(self._key, self._secret, t, payload),
                   "Content-Type": "application/json"}
        try:
            async with self.session.request(method, url, data=data, headers=headers,
                                            timeout=aiohttp.ClientTimeout(total=self.timeout_s)) as r:
                status, text = r.status, await r.text()
        except (aiohttp.ClientError, TimeoutError) as e:
            raise TradingError(f"{method} {path}: {type(e).__name__}") from None
        try:
            env = json.loads(text)
        except ValueError:
            raise TradingError(f"{method} {path}: respuesta no JSON (HTTP {status})") from None
        if status != 200 or not isinstance(env, dict) or not env.get("success", False):
            code = env.get("code") if isinstance(env, dict) else None
            msg = env.get("message", "") if isinstance(env, dict) else ""
            raise TradingError(redact(f"{method} {path}: HTTP {status} · código {code} · {msg}"[:300],
                                      secrets=(self._key, self._secret)))
        return env.get("data")

    # -- lectura ----------------------------------------------------------------------------------------
    async def usdt(self) -> dict:
        """Saldo en USDT de la cuenta de futuros: {"equity", "available"} (nunca se publica)."""
        for a in await self.request("GET", "/api/v1/private/account/assets") or []:
            if a.get("currency") == "USDT":
                return {"equity": _num(a.get("equity", 0)), "available": _num(a.get("availableBalance", 0))}
        return {"equity": 0.0, "available": 0.0}

    async def positions(self, symbol: str | None = None) -> list[dict]:
        return await self.request("GET", "/api/v1/private/position/open_positions",
                                  params={"symbol": symbol} if symbol else None) or []

    async def position_mode(self) -> int:
        """1 = cobertura (largo y corto por separado: lo que usa este conector), 2 = unidireccional."""
        return int(await self.request("GET", "/api/v1/private/position/position_mode") or 0)

    async def order(self, order_id: str) -> dict:
        return await self.request("GET", f"/api/v1/private/order/get/{order_id}") or {}

    async def deals(self, order_id: str) -> list[dict]:
        return await self.request("GET", f"/api/v1/private/order/deal_details/{order_id}") or []

    async def open_orders(self, symbol: str) -> list[dict]:
        data = await self.request("GET", f"/api/v1/private/order/list/open_orders/{symbol}",
                                  params={"page_num": 1, "page_size": 50})
        return data if isinstance(data, list) else (data or {}).get("resultList", [])

    # -- órdenes ----------------------------------------------------------------------------------------
    async def submit(self, *, symbol: str, side: int, vol: int, price, leverage: int, order_type: int,
                     external_oid: str, stop_loss=None, position_id=None) -> str:
        if side not in (OPEN_LONG, CLOSE_SHORT, OPEN_SHORT, CLOSE_LONG) or order_type not in (LIMIT, POST_ONLY, IOC):
            raise ValueError(f"orden no permitida: lado {side}, tipo {order_type}")   # sin órdenes a mercado sin tope
        if int(vol) < 1 or int(leverage) < 1:
            raise ValueError("volumen y apalancamiento tienen que ser ≥ 1")
        body = {"symbol": symbol, "price": _num(price), "vol": int(vol), "leverage": int(leverage), "side": side,
                "type": order_type, "openType": ISOLATED, "externalOid": external_oid}
        if stop_loss is not None:
            body["stopLossPrice"] = _num(stop_loss)
        if position_id is not None:
            body["positionId"] = position_id
        data = await self.request("POST", "/api/v1/private/order/submit", body=body)
        oid = data.get("orderId") if isinstance(data, dict) else data
        if oid in (None, ""):
            raise TradingError("el exchange no devolvió el número de orden")
        return str(oid)

    async def cancel(self, order_ids: list[str]) -> list:
        ids = [int(x) if str(x).isdigit() else str(x) for x in order_ids]       # la API espera números (long)
        return await self.request("POST", "/api/v1/private/order/cancel", body=ids) or []

    async def cancel_all(self, symbol: str) -> None:
        await self.request("POST", "/api/v1/private/order/cancel_all", body={"symbol": symbol})

    async def set_leverage(self, symbol: str, leverage: int, long: bool) -> None:
        await self.request("POST", "/api/v1/private/position/change_leverage",
                           body={"symbol": symbol, "leverage": int(leverage), "openType": ISOLATED,
                                 "positionType": 1 if long else 2})
