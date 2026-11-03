"""Prueba sin riesgo del modo real, en la PC del dueño: `python -m tradingagent mexc-real-check`.

Antes de arriesgar un peso, confirma que MEXC entiende las órdenes exactamente como el programa las pide:

1. Lee la cuenta de futuros (el saldo no se muestra ni se publica).
2. Pone una orden "solo maker" de 1 contrato un 3 % lejos del precio: abrir largo por debajo del mejor
   comprador y abrir corto por encima del mejor vendedor. Una orden "solo maker" no puede ejecutarse contra
   el libro (si cruzara, el exchange la rechaza), y un 3 % no se mueve en el segundo que dura la prueba.
3. Lee la orden en el exchange y verifica lado, tamaño, precio, apalancamiento, margen aislado y que no se
   ejecutó nada. La cancela y verifica que quedó cancelada.
4. Verifica que no apareció ninguna posición. Si algo salió mal, cancela todo lo del contrato y, si por
   algún motivo se abrió algo, lo cierra.

Si cualquier paso no coincide, el resultado es "no OK" y el modo real no se puede armar.
"""
import time
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from ...core.redact import redact
from ...marketdata import mexc
from .mexc_futures import (
    CANCELED,
    CLOSE_LONG,
    CLOSE_SHORT,
    IOC,
    ISOLATED,
    OPEN,
    PENDING,
    POST_ONLY,
    TradingClient,
    TradingError,
    side_code,
)

DISTANCE = Decimal("0.03")
TEST_LEVERAGE = 3


def _round(price: Decimal, tick: Decimal, up: bool) -> Decimal:
    return (price / tick).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR) * tick


def _held(positions: list[dict]) -> float:
    return sum(float(p.get("holdVol", 0) or 0) for p in positions)


async def real_check(client: TradingClient, session, symbol: str = "BTC_USDT", *, base: str = mexc.BASE,
                     pause_s: float = 0.5) -> dict:
    import asyncio
    steps: list[dict] = []

    def step(name: str, ok: bool, detail: str = "") -> bool:
        steps.append({"paso": name, "ok": bool(ok), "detalle": detail})
        return ok

    try:
        await client.usdt()
        step("leer la cuenta de futuros", True)
        info = await mexc.detail(session, symbol, base=base)
        depth = await mexc._get(session, f"{base}/api/v1/contract/depth/{symbol}", {"limit": "5"}, f"depth {symbol}")
        book = mexc.parse_depth(symbol, depth, Decimal(str(info["contract_size"])), time.time())
        tick = Decimal(str(info["tick"]))
        before = _held(await client.positions(symbol))
        for long_ in (True, False):
            ref = book.best_bid if long_ else book.best_ask
            price = _round(ref * (1 - DISTANCE) if long_ else ref * (1 + DISTANCE), tick, up=not long_)
            side = side_code(True, long_)
            oid = await client.submit(symbol=symbol, side=side, vol=1, price=price, leverage=TEST_LEVERAGE,
                                      order_type=POST_ONLY, external_oid=f"chk{int(time.time() * 1000)}{side}")
            await asyncio.sleep(pause_s)
            o = await client.order(oid)
            problems = []
            if int(o.get("side", 0)) != side:
                problems.append(f"lado {o.get('side')} en vez de {side}")
            if float(o.get("vol", 0)) != 1:
                problems.append(f"tamaño {o.get('vol')} en vez de 1")
            if abs(Decimal(str(o.get("price", 0))) - price) > tick:
                problems.append(f"precio {o.get('price')} en vez de {price}")
            if int(o.get("leverage", 0)) != TEST_LEVERAGE:
                problems.append(f"apalancamiento {o.get('leverage')} en vez de {TEST_LEVERAGE}")
            if int(o.get("openType", 0)) != ISOLATED:
                problems.append(f"margen {o.get('openType')} en vez de aislado")
            if float(o.get("dealVol", 0) or 0) != 0:
                problems.append("se ejecutó (no debía)")
            if int(o.get("state", 0)) not in (PENDING, OPEN):
                problems.append(f"estado {o.get('state')} en vez de abierta")
            await client.cancel([oid])
            await asyncio.sleep(pause_s)
            if int((await client.order(oid)).get("state", 0)) != CANCELED:
                problems.append("no quedó cancelada")
            step(f"orden de prueba ({'abrir largo' if long_ else 'abrir corto'})", not problems, "; ".join(problems))
        after = await client.positions(symbol)
        step("no se abrió ninguna posición", _held(after) == before,
             "" if _held(after) == before else "apareció una posición")
    except (TradingError, mexc.MarketDataError, PermissionError, ValueError, KeyError, TypeError) as e:
        step("error", False, redact(str(e))[:300])
    ok = len(steps) >= 4 and all(s["ok"] for s in steps)
    if not ok:
        await _clean_up(client, session, symbol, base)
    return {"ok": ok, "simbolo": symbol, "pasos": steps}


async def _clean_up(client: TradingClient, session, symbol: str, base: str) -> None:
    """Si la prueba falló: nada colgado y, si se abrió algo (no debería), se cierra con tope de precio."""
    try:
        await client.cancel_all(symbol)
        info = await mexc.detail(session, symbol, base=base)
        tick = Decimal(str(info["tick"]))
        last = Decimal(str((await mexc.ticker(session, symbol, base=base))["last"]))
        for p in await client.positions(symbol):
            vol = int(float(p.get("holdVol", 0) or 0))
            if vol <= 0:
                continue
            long_ = int(p.get("positionType", 1)) == 1
            price = _round(last * (Decimal("0.99") if long_ else Decimal("1.01")), tick, up=not long_)
            await client.submit(symbol=symbol, side=CLOSE_LONG if long_ else CLOSE_SHORT, vol=vol, price=price,
                                leverage=int(p.get("leverage", 1) or 1), order_type=IOC,
                                external_oid=f"chkcierre{int(time.time() * 1000)}", position_id=p.get("positionId"))
    except (TradingError, mexc.MarketDataError, PermissionError, ValueError, KeyError, TypeError):
        pass
