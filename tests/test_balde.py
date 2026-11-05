"""Balde de fichas del lector de libros (marketdata/providers.py): el mismo promedio de pedidos por segundo, pero
varios pueden salir juntos. Antes había 200 ms obligatorios entre dos pedidos cualesquiera, y el libro de un
deslistado (el precio cae entre los 2 y 5 s del aviso) esperaba detrás del de otra posición."""
import asyncio

import aiohttp

from conftest import serve
from fake_binance import FakeBinance
from tradingagent.core.clock import ManualClock
from tradingagent.marketdata import binance_futures as bfd


async def test_cinco_libros_juntos_sin_esperar_y_el_sexto_respeta_el_promedio(monkeypatch):
    waits = []
    real_sleep = asyncio.sleep

    async def fake_sleep(s, *a, **k):
        waits.append(s)
        await real_sleep(0)

    clock = ManualClock()
    async with serve(FakeBinance(clock=clock).app()) as url, aiohttp.ClientSession() as s:
        books = bfd.books(s, clock, base=url)
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "TSLAUSDT"):
            await books.book(sym)                                   # una ráfaga de 5: ninguno espera
        assert waits == []
        await books.book("XAUUSDT")                                 # el sexto en el mismo instante: 1/5 s
        assert len(waits) == 1 and abs(waits[0] - 0.2) < 1e-9
        clock.advance(1.0)                                          # al segundo el balde se llenó otra vez
        for sym in ("BTCUSDT", "ETHUSDT"):
            await books.book(sym)
        assert len(waits) == 1
