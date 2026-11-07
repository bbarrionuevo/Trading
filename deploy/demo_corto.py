"""Ida y vuelta de un CORTO en la DEMO de Binance con el venue del dinero real (plata ficticia, claves *_TEST del .env de
la PC; nunca la cuenta real). Como entraría un deslistado: venta límite IOC con el tope de 2 %, stop de emergencia de
cierre total (compra al +30 %: 1,5 × el stop de 20 %), espera 6,5 s (D-6: nada se cancela antes de 5 s) y sale
comprando con "solo reducir" (tope de 0,5 %). Revisa en la demo la posición y el stop en cada paso. No imprime claves
ni saldos.

Uso, en la PC y desde el repo: `python deploy/demo_corto.py [SIMBOLO]` (XRPUSDT por defecto; 20 USDT ficticios). Arma
un simulacro de la DEMO en una carpeta temporal, así que no toca `TRADING_HOME`. Medido el 7-nov: todo OK (venta con
límite −2,00 % bajo el mejor comprador, stop de compra al +30 %, salida y nada abierto al final).
"""
import asyncio
import os
import sys
import tempfile
import time
from decimal import Decimal as D
from pathlib import Path

import aiohttp

from tradingagent import limits as limits_mod
from tradingagent.core.bus import EventBus
from tradingagent.core.clock import SystemClock
from tradingagent.execution.live import arming
from tradingagent.execution.live.binance_futures import DEMO, BinanceClient
from tradingagent.execution.live.binance_venue import BinanceVenue
from tradingagent.execution.orders import OrderIntent, OrderType, Side
from tradingagent.execution.portfolio import Portfolio
from tradingagent.execution.service import ExecutionService
from tradingagent.marketdata import binance_futures as bfd
from tradingagent.research.futures import BinanceFutures

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XRPUSDT"
ENV = Path(os.environ.get("LOCALAPPDATA", "")) / "TRADING" / ".env"
WANT = ("BINANCE_API_KEY_TEST", "BINANCE_API_SECRET_TEST")      # solo la demo: nunca las claves reales


def demo_keys() -> tuple[str | None, str | None]:
    vals = {}
    for line in ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []:
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            if k.strip() in WANT:
                vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals.get(WANT[0]), vals.get(WANT[1])


async def amount(client) -> D:
    return sum((D(str(p.get("positionAmt", 0) or 0)) for p in await client.positions(SYMBOL)
                if p.get("symbol") == SYMBOL), D(0))


async def main() -> int:
    key, secret = demo_keys()
    if not (key and secret):
        print("faltan BINANCE_API_KEY_TEST y BINANCE_API_SECRET_TEST en el .env de la PC")
        return 1
    home = Path(tempfile.mkdtemp(prefix="demo-corto-"))
    arming.arm(home, 1, "", "", simulacro=True, capital=100.0, venue="binance-futuros", mode="demo",
               strategies=("deslistado_corto",))
    lim = limits_mod.load()
    clock = SystemClock()
    ok = True

    def check(what: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and good
        print(f"{'✅' if good else '❌'} {what}" + (f" · {detail}" if detail else ""))

    async with aiohttp.ClientSession() as s:
        client = BinanceClient(s, key, secret, base=DEMO)
        await client.sync_time()
        check("la demo en modo unidireccional", not await client.dual_side())
        if await amount(client) != 0:
            print(f"la demo ya tiene una posición en {SYMBOL}: no sigo (cerrala o elegí otro símbolo)")
            return 1
        reg = await BinanceFutures(base=DEMO).instruments(s, [SYMBOL], {}, clock.time())
        inst = reg.get(SYMBOL)
        books = bfd.books(s, clock, DEMO)
        pf = Portfolio(D("100"))
        venue = BinanceVenue(client, s, reg, books, pf, clock, home=home, api_key=key, limits=lim, leverage=1,
                             state_path=home / "stops.json", simulated=True)
        service = ExecutionService(venue, EventBus(), clock, limits=lim)
        book = await books.book(SYMBOL)
        ref = book.best_bid
        emergency = inst.round_price(ref * (1 + D("1.5") * D("0.20")), up=True)
        t0 = time.perf_counter()
        order, block = await service.execute(OrderIntent(
            client_id=f"pc-corto-{int(time.time())}", symbol=SYMBOL, side=Side.SELL, type=OrderType.MARKET,
            notional=D("20"), max_slippage_bps=D("200"), ref_price=ref,
            tags={"role": "entry", "category": "deslistado_corto", "emergency_stop": str(emergency)}))
        ms = (time.perf_counter() - t0) * 1000
        check("venta en corto llenada (IOC)", order is not None and bool(order.filled_qty),
              f"{block or (order.status if order else '')} · {ms:.0f} ms desde la PC")
        if order is None or not order.filled_qty:
            return 1
        cap = order.limit_price / ref - 1
        check("tope de la orden de 2 % debajo del mejor comprador", D("-0.0201") < cap < D("-0.0199"),
              f"límite {order.limit_price} contra {ref} ({cap * 100:+.2f} %)")
        check("llenó a un precio razonable (cerca del mejor comprador)", order.avg_price >= ref * D("0.995"),
              f"{order.avg_price}")
        held = await amount(client)
        check("la demo muestra la posición corta", held == -order.filled_qty, f"{held}")
        stops = [x for x in await client.open_stops(SYMBOL)]
        st = stops[0] if stops else {}
        check("stop de emergencia de cierre total, del lado de la compra",
              len(stops) == 1 and st.get("side") == "BUY" and str(st.get("closePosition")).lower() == "true",
              f"{st.get('orderType', st.get('type'))} {st.get('side')} a {st.get('triggerPrice')} "
              f"({(D(str(st.get('triggerPrice', 0))) / order.avg_price - 1) * 100:+.1f} %)" if st else "no está")
        await asyncio.sleep(6.5)                                  # D-6: nada se cancela a menos de 5 s de puesto
        ask = (await books.book(SYMBOL)).best_ask
        out, block = await service.execute(OrderIntent(
            client_id=f"pc-salida-{int(time.time())}", symbol=SYMBOL, side=Side.BUY, type=OrderType.MARKET,
            qty=order.filled_qty, max_slippage_bps=D("50"), reduce_only=True, ref_price=ask,
            tags={"role": "exit", "category": "deslistado_corto"}))
        check("salida comprando con solo reducir", out is not None and out.filled_qty == order.filled_qty,
              block or (str(out.status) if out else ""))
        check("la demo quedó sin posición", await amount(client) == 0)
        for _ in range(30):                                       # el stop que sobra: lo vence Binance o se cancela
            if not await client.open_stops(SYMBOL):
                break
            await asyncio.sleep(0.5)
        check("el stop de emergencia ya no está", not await client.open_stops(SYMBOL))
        if out is not None and out.filled_qty:
            r = (order.avg_price - out.avg_price) / order.avg_price
            print(f"ida y vuelta: vendí a {order.avg_price}, compré a {out.avg_price} ({r * 100:+.3f} % antes de "
                  f"comisiones; plata ficticia)")
    print("OK" if ok else "HAY QUE REVISAR")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
