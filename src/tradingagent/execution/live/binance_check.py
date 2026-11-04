"""Prueba sin riesgo del modo real en Binance Futures, en la PC del dueño: `python -m tradingagent binance-real-check`.

Antes de arriesgar un peso, confirma que la cuenta está como hace falta y que Binance entiende las órdenes como el
programa las pide, sin abrir ninguna posición:

1. Lee la cuenta (el saldo no se muestra ni se publica) y el estado de las reglas cuantitativas (ningún bloqueo).
2. Modo unidireccional y "un solo activo" (con el multi-activo Binance obliga a margen cruzado; acá va aislado).
3. Pone el contrato de prueba en margen aislado y 1x.
4. Valida con la ruta oficial de prueba de órdenes (no llega al mercado) la entrada IOC con tope de
   precio y el stop de emergencia STOP_MARKET de cierre.
5. Pone UNA orden "solo maker" (GTX) un 3 % debajo del mejor comprador: no puede ejecutarse (si cruzara, Binance la
   rechaza). Verifica lado, tamaño, precio, tipo y que no se ejecutó nada, **espera 6 s** (las reglas cuantitativas
   cuentan como "cancelación inválida" la que se cancela antes de 5 s) y la cancela.
6. Verifica que no apareció ninguna posición. Si algo sale mal, cancela las órdenes de ese contrato. Nunca cierra
   posiciones por su cuenta: si apareciera una (no debería), lo dice para que el dueño la cierre.

Si cualquier paso no coincide, el resultado es "no OK" y el modo real no se puede armar.
"""
import asyncio
import time
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from ...core.redact import redact
from .binance_futures import BUY, GTX, IOC, SELL, STOP_MARKET, BinanceClient, BinanceError

SYMBOL = "XRPUSDT"                    # mínimo de 5 USDT por orden (en BTC es de 100)
DISTANCE = Decimal("0.03")
CAP = Decimal("0.003")                # tope de precio de la entrada validada
STOP = Decimal("0.05")
WAIT_BEFORE_CANCEL_S = 6.0


def _round(x: Decimal, unit: Decimal, up: bool) -> Decimal:
    return (x / unit).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR) * unit


def _locked(status: dict) -> list[str]:
    out = []
    for sym, rows in ((status or {}).get("indicators") or {}).items():
        for r in rows if isinstance(rows, list) else [rows]:
            if r.get("isLocked"):
                out.append(f"{sym}:{r.get('indicator')}")
    return out


async def real_check(client: BinanceClient, symbol: str = SYMBOL, *, pause_s: float = WAIT_BEFORE_CANCEL_S) -> dict:
    steps: list[dict] = []

    def step(name: str, ok: bool, detail: str = "") -> bool:
        steps.append({"paso": name, "ok": bool(ok), "detalle": detail})
        return ok

    placed = False
    try:
        await client.sync_time()
        money = await client.usdt()
        step("leer la cuenta de futuros", True)
        locked = _locked(await client.trading_status())
        if not step("reglas cuantitativas sin bloqueos", not locked, ", ".join(locked)):
            return {"ok": False, "simbolo": symbol, "pasos": steps}
        if not step("modo de posición unidireccional", not await client.dual_side(),
                    "en Binance: Futuros → Preferencias → Modo de posición → Unidireccional (One-way)"):
            return {"ok": False, "simbolo": symbol, "pasos": steps}
        if not step("modo de un solo activo", not await client.multi_assets(),
                    "en Binance: Futuros → Preferencias → Modo de activos → Single-Asset Mode (el multi-activo obliga "
                    "a margen cruzado)"):
            return {"ok": False, "simbolo": symbol, "pasos": steps}
        info = await client.public("/fapi/v1/exchangeInfo")
        spec = next(x for x in info["symbols"] if x["symbol"] == symbol)
        f = {x["filterType"]: x for x in spec["filters"]}
        tick, unit = Decimal(f["PRICE_FILTER"]["tickSize"]), Decimal(f["LOT_SIZE"]["stepSize"])
        min_qty, min_value = Decimal(f["LOT_SIZE"]["minQty"]), Decimal(f["MIN_NOTIONAL"]["notional"])
        book = await client.public("/fapi/v1/ticker/bookTicker", {"symbol": symbol})
        bid, ask = Decimal(book["bidPrice"]), Decimal(book["askPrice"])
        before = await client.positions(symbol)
        await client.open_orders(symbol)
        step("leer posiciones y órdenes abiertas", True)
        await client.set_isolated(symbol)
        await client.set_leverage(symbol, 1)
        step("margen aislado y 1x en el contrato de prueba", True)
        far = _round(bid * (1 - DISTANCE), tick, up=False)
        qty = _round(max(min_qty, min_value * Decimal("1.05") / far), unit, up=True)
        ms = int(time.time() * 1000)
        await client.test_order(symbol=symbol, side=BUY, qty=qty, price=_round(ask * (1 + CAP), tick, up=True), tif=IOC,
                                client_id=f"chk{ms}t")
        await client.test_order(symbol=symbol, side=SELL, qty=qty, order_type=STOP_MARKET,
                                stop_price=_round(bid * (1 - STOP), tick, up=False), client_id=f"chk{ms}s")
        step("Binance valida la entrada IOC y el stop de emergencia (sin ejecutar)", True)
        need = qty * far * Decimal("1.1")
        if Decimal(str(money["available"])) < need:
            step("orden de prueba", False, f"falta saldo en futuros (hace falta ≥ {need:.2f} USDT): se hace cuando "
                                           "cargues la plata")
            return {"ok": False, "solo_lectura": True, "simbolo": symbol, "pasos": steps}
        cid = f"chk{ms}m"
        placed = True
        await client.submit(symbol=symbol, side=BUY, qty=qty, price=far, tif=GTX, client_id=cid)
        o = await client.order(symbol, cid)
        problems = []
        if o.get("side") != BUY:
            problems.append(f"lado {o.get('side')} en vez de {BUY}")
        if o.get("type") != "LIMIT" or o.get("timeInForce") != GTX:
            problems.append(f"tipo {o.get('type')}/{o.get('timeInForce')} en vez de LIMIT/{GTX}")
        if Decimal(str(o.get("origQty", 0))) != qty:
            problems.append(f"tamaño {o.get('origQty')} en vez de {qty}")
        if abs(Decimal(str(o.get("price", 0))) - far) > tick:
            problems.append(f"precio {o.get('price')} en vez de {far}")
        if Decimal(str(o.get("executedQty", 0) or 0)) != 0:
            problems.append("se ejecutó (no debía)")
        if o.get("status") != "NEW":
            problems.append(f"estado {o.get('status')} en vez de abierta")
        await asyncio.sleep(pause_s)                  # nunca cancelar antes de 5 s (reglas cuantitativas)
        await client.cancel(symbol, cid)
        if (await client.order(symbol, cid)).get("status") != "CANCELED":
            problems.append("no quedó cancelada")
        step("orden de prueba solo maker", not problems, "; ".join(problems))
        after = await client.positions(symbol)
        step("no se abrió ninguna posición", len(after) == len(before),
             "" if len(after) == len(before) else "apareció una posición: cerrala desde Binance")
    except (BinanceError, PermissionError, ValueError, KeyError, TypeError, StopIteration) as e:
        step("error", False, redact(str(e))[:300])
    ok = len(steps) >= 9 and all(s["ok"] for s in steps)
    if not ok and placed:
        try:
            await client.cancel_all(symbol)           # nada colgado (nunca cierra posiciones solo)
        except (BinanceError, PermissionError):
            pass
    return {"ok": ok, "simbolo": symbol, "pasos": steps}
