"""Cliente de TRADING de Binance Futures USDⓈ-M (dinero real), que reemplaza al de MEXC (el dueño, 4-nov-2026: MEXC
restringió la cuenta). La única pieza del programa que puede mandar órdenes a Binance.

- **Lista cerrada** de operaciones (REGLAS A-3): leer saldo, posiciones, órdenes, ejecuciones, modo de la cuenta y
  estado de las reglas cuantitativas; abrir y cerrar con órdenes LIMIT IOC o "solo maker" (GTX); validar sin
  ejecutar (`/fapi/v1/order/test`); cancelar; fijar margen aislado y apalancamiento.
- **El stop de emergencia va por el servicio de órdenes Algo** (`/fapi/v1/algoOrder`): desde el 2-dic-2025 Binance
  rechaza las órdenes condicionales en la ruta común (código -4120, visto en la PC el 4-nov). Es un STOP_MARKET que
  cierra toda la posición, disparado por el precio de marca. Cualquier otra ruta lanza `PermissionError` antes de salir a la red. No existe código de retiros
  ni de transferencias, y la clave de la PC no debería tener esos permisos.
- **Para no ser baneados** (reglas cuantitativas de Binance y límites por IP, investigados el 4-nov):
  - sin reintentos automáticos de órdenes; ante HTTP 429 o 418 no se manda nada más hasta que pase la pausa
    (`Retry-After`, o 2 minutos);
  - un error 503 "Unknown error" NO es una falla: la orden pudo entrar. Se consulta por el id propio
    (`newClientOrderId`, una intención = una orden: D-1) antes de cualquier otra cosa;
  - órdenes con tope de precio (nunca a mercado sin tope: D-2), pocas y de tamaño real.
- Firma HMAC-SHA256 sobre la query, clave en `X-MBX-APIKEY`, `timestamp` corregido con la hora del servidor y
  `recvWindow` de 5 s. Los errores nunca muestran las claves (`redact`).
"""
import hashlib
import hmac
import json
import time
from decimal import Decimal
from urllib.parse import urlencode

import aiohttp

from ...core.redact import redact

BASE = "https://fapi.binance.com"
GET_PATHS = ("/fapi/v2/balance", "/fapi/v2/positionRisk", "/fapi/v1/positionSide/dual", "/fapi/v1/multiAssetsMargin",
             "/fapi/v1/order", "/fapi/v1/openOrders", "/fapi/v1/userTrades", "/fapi/v1/apiTradingStatus",
             "/fapi/v1/commissionRate", "/fapi/v1/algoOrder", "/fapi/v1/openAlgoOrders")
POST_PATHS = ("/fapi/v1/order", "/fapi/v1/order/test", "/fapi/v1/leverage", "/fapi/v1/marginType",
              "/fapi/v1/algoOrder")
DELETE_PATHS = ("/fapi/v1/order", "/fapi/v1/allOpenOrders", "/fapi/v1/algoOrder", "/fapi/v1/algoOpenOrders")
PUBLIC_PATHS = ("/fapi/v1/time", "/fapi/v1/exchangeInfo", "/fapi/v1/depth", "/fapi/v1/ticker/bookTicker")
LIMIT, STOP_MARKET = "LIMIT", "STOP_MARKET"
IOC, GTX = "IOC", "GTX"                           # GTX = "solo maker": si cruzara el libro, Binance la rechaza
BUY, SELL = "BUY", "SELL"
DONE = ("FILLED", "CANCELED", "EXPIRED", "REJECTED", "EXPIRED_IN_MATCH")
BAN_PAUSE_S = 120.0


class BinanceError(RuntimeError):
    def __init__(self, msg, *, code=None, unknown=False):
        super().__init__(msg)
        self.code, self.unknown = code, unknown    # unknown: 503 sin saber si entró (consultar antes de seguir)


def allowed(method: str, path: str) -> bool:
    return path in {"GET": GET_PATHS, "POST": POST_PATHS, "DELETE": DELETE_PATHS}.get(method, ())


def sign(secret: str, query: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def fmt(x) -> str:
    """Número para Binance: decimal sin exponente ni ceros de más."""
    d = Decimal(str(x)).normalize()
    return format(d, "f")


class BinanceClient:
    def __init__(self, session: aiohttp.ClientSession, api_key: str, secret: str, *, base: str = BASE, clock=None,
                 timeout_s: float = 8.0, recv_window: int = 5000):
        if not api_key or not secret:
            raise ValueError("faltan BINANCE_API_KEY y BINANCE_API_SECRET")
        self.session, self._key, self._secret, self.base = session, api_key, secret, base.rstrip("/")
        self.clock, self.timeout_s, self.recv_window = clock, timeout_s, recv_window
        self.offset_ms = 0                                   # hora del servidor menos la local
        self.paused_until = 0.0                              # después de un 429/418: no se manda nada
        self.used_weight = 0

    def _now(self) -> float:
        return self.clock.time() if self.clock else time.time()

    async def sync_time(self) -> int:
        data = await self._send("GET", "/fapi/v1/time", {}, signed=False)
        self.offset_ms = int(data["serverTime"]) - int(self._now() * 1000)
        return self.offset_ms

    async def public(self, path: str, params: dict | None = None):
        if path not in PUBLIC_PATHS:
            raise PermissionError(f"lectura pública no permitida: {path}")
        return await self._send("GET", path, dict(params or {}), signed=False)

    async def request(self, method: str, path: str, params: dict | None = None):
        if not allowed(method, path):
            raise PermissionError(f"operación no permitida para el cliente de trading: {method} {path}")
        return await self._send(method, path, dict(params or {}), signed=True)

    async def _send(self, method: str, path: str, params: dict, *, signed: bool):
        if self._now() < self.paused_until:
            raise BinanceError(f"{method} {path}: en pausa por límite de Binance hasta "
                               f"{time.strftime('%H:%M:%S', time.localtime(self.paused_until))}")
        headers = {"X-MBX-APIKEY": self._key} if signed else {}
        if signed:
            params.update(timestamp=int(self._now() * 1000) + self.offset_ms, recvWindow=self.recv_window)
            query = urlencode(params)
            query += f"&signature={sign(self._secret, query)}"
        else:
            query = urlencode(params)
        url = f"{self.base}{path}" + (f"?{query}" if query else "")
        try:
            async with self.session.request(method, url, headers=headers,
                                            timeout=aiohttp.ClientTimeout(total=self.timeout_s)) as r:
                status, text = r.status, await r.text()
                self.used_weight = int(r.headers.get("X-MBX-USED-WEIGHT-1M", 0) or 0)
                retry = r.headers.get("Retry-After")
        except (aiohttp.ClientError, TimeoutError) as e:
            raise BinanceError(f"{method} {path}: {type(e).__name__}", unknown=method != "GET") from None
        if status in (418, 429):                             # nunca insistir: así se ganan los baneos por IP
            self.paused_until = self._now() + (float(retry) if retry and retry.isdigit() else BAN_PAUSE_S)
            raise BinanceError(f"{method} {path}: HTTP {status} (límite de Binance): pausa", code=status)
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            raise BinanceError(f"{method} {path}: respuesta no JSON (HTTP {status})",
                               unknown=status == 503 and method != "GET") from None
        if status != 200:
            code = data.get("code") if isinstance(data, dict) else None
            msg = data.get("msg", "") if isinstance(data, dict) else ""
            raise BinanceError(redact(f"{method} {path}: HTTP {status} · código {code} · {msg}"[:300],
                                      secrets=(self._key, self._secret)),
                               code=code, unknown=status == 503 and method != "GET")
        return data

    # -- lectura ----------------------------------------------------------------------------------------
    async def usdt(self) -> dict:
        """Saldo en USDT de la cuenta de futuros: {"equity", "available"} (nunca se publica)."""
        for a in await self.request("GET", "/fapi/v2/balance") or []:
            if a.get("asset") == "USDT":
                equity = float(a.get("balance", 0) or 0) + float(a.get("crossUnPnl", 0) or 0)
                return {"equity": equity, "available": float(a.get("availableBalance", 0) or 0)}
        return {"equity": 0.0, "available": 0.0}

    async def positions(self, symbol: str | None = None) -> list[dict]:
        data = await self.request("GET", "/fapi/v2/positionRisk", {"symbol": symbol} if symbol else None) or []
        return [p for p in data if float(p.get("positionAmt", 0) or 0) != 0]

    async def dual_side(self) -> bool:
        return bool((await self.request("GET", "/fapi/v1/positionSide/dual") or {}).get("dualSidePosition"))

    async def multi_assets(self) -> bool:
        return bool((await self.request("GET", "/fapi/v1/multiAssetsMargin") or {}).get("multiAssetsMargin"))

    async def trading_status(self) -> dict:
        """Reglas cuantitativas: {"indicators": {símbolo: [...]}} (vacío = ningún indicador cerca del bloqueo)."""
        return await self.request("GET", "/fapi/v1/apiTradingStatus") or {}

    async def order(self, symbol: str, client_id: str) -> dict:
        return await self.request("GET", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_id}) or {}

    async def open_orders(self, symbol: str) -> list[dict]:
        return await self.request("GET", "/fapi/v1/openOrders", {"symbol": symbol}) or []

    async def fills(self, symbol: str, order_id) -> list[dict]:
        return await self.request("GET", "/fapi/v1/userTrades", {"symbol": symbol, "orderId": order_id}) or []

    # -- órdenes ------------------------------------------------------------------------------------------
    def _order_params(self, *, symbol, side, qty, price, tif, client_id, reduce_only=False) -> dict:
        if side not in (BUY, SELL) or tif not in (IOC, GTX) or price is None or not Decimal(str(qty)) > 0:
            raise ValueError("solo órdenes límite IOC o solo maker, con precio y cantidad")   # nunca a mercado (D-2)
        p = {"symbol": symbol, "side": side, "type": LIMIT, "timeInForce": tif, "price": fmt(price), "quantity": fmt(qty),
             "newClientOrderId": client_id[:36], "newOrderRespType": "RESULT"}
        if reduce_only:
            p["reduceOnly"] = "true"
        return p

    async def submit(self, **kw) -> dict:
        return await self.request("POST", "/fapi/v1/order", self._order_params(**kw))

    async def test_order(self, **kw) -> dict:
        """Valida la orden en Binance sin mandarla al mercado (no ejecuta ni queda abierta)."""
        return await self.request("POST", "/fapi/v1/order/test", self._order_params(**kw))

    # -- stop de emergencia (servicio de órdenes Algo) ---------------------------------------------------
    async def emergency_stop(self, *, symbol: str, side: str, trigger_price, client_id: str,
                             qty=None) -> dict:
        """STOP_MARKET disparado por el precio de marca. Sin `qty`: cierra TODA la posición (lo normal). Con `qty`
        (solo la prueba sin riesgo, lejísimos del precio): una orden condicional común, para validar la ruta."""
        if side not in (BUY, SELL) or trigger_price is None:
            raise ValueError("el stop necesita lado y precio de disparo")
        p = {"algoType": "CONDITIONAL", "symbol": symbol, "side": side, "type": STOP_MARKET,
             "triggerPrice": fmt(trigger_price), "workingType": "MARK_PRICE", "priceProtect": "true",
             "clientAlgoId": client_id[:36], "newOrderRespType": "RESULT"}
        if qty is None:
            p["closePosition"] = "true"
        else:
            p["quantity"] = fmt(qty)
        return await self.request("POST", "/fapi/v1/algoOrder", p)

    async def stop(self, client_id: str) -> dict:
        return await self.request("GET", "/fapi/v1/algoOrder", {"clientAlgoId": client_id}) or {}

    async def open_stops(self, symbol: str) -> list[dict]:
        data = await self.request("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol})
        return data if isinstance(data, list) else (data or {}).get("orders", [])

    async def cancel_stop(self, client_id: str) -> dict:
        return await self.request("DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": client_id})

    async def cancel_all_stops(self, symbol: str) -> dict:
        return await self.request("DELETE", "/fapi/v1/algoOpenOrders", {"symbol": symbol})

    async def cancel(self, symbol: str, client_id: str) -> dict:
        return await self.request("DELETE", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_id})

    async def cancel_all(self, symbol: str) -> dict:
        return await self.request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})

    async def set_isolated(self, symbol: str) -> None:
        try:
            await self.request("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": "ISOLATED"})
        except BinanceError as e:
            if e.code != -4046:                        # -4046: ya estaba en aislado
                raise

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        await self.request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(leverage)})
