"""Peso de la API de Binance compartido por todo el agente (marketdata/peso.py): se lee de cada respuesta; pasado el
80 % del límite por minuto, lo que no es urgente espera al minuto siguiente; lo urgente nunca espera."""
import time

import aiohttp

from conftest import serve
from fake_binance import FakeBinance
from tradingagent.marketdata import binance_futures as bfd
from tradingagent.marketdata import peso
from tradingagent.marketdata.peso import WeightMonitor

NOW = 1_793_900_010.0                                          # 10 s dentro de un minuto


def test_lee_el_peso_y_frena_pasado_el_80_por_ciento():
    m = WeightMonitor()
    m.observe({"X-MBX-USED-WEIGHT-1M": "1500"}, now=NOW)
    assert not m.busy(NOW)
    m.observe({"X-MBX-USED-WEIGHT-1M": "2000"}, now=NOW + 5)   # 83 %
    assert m.busy(NOW + 6) and not m.busy(NOW + 60)           # al minuto siguiente se libera
    m.observe({"X-MBX-USED-WEIGHT-1M": "900"}, now=NOW + 7)     # en el mismo minuto vale el mayor visto
    assert m.busy(NOW + 8) and m.snapshot()["maximo_visto"] == 2000
    m.observe({}, now=NOW + 9)                                  # sin la cabecera (otro exchange): nada
    assert m.used == 2000


async def test_lo_que_no_es_urgente_espera_y_las_respuestas_se_leen(monkeypatch):
    waits = []
    monitor = WeightMonitor()

    async def fake_sleep(s):
        waits.append(s)
        monitor.used = 0                                        # pasó el minuto

    monkeypatch.setattr(peso.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(bfd, "MONITOR", monitor)
    monitor.observe({"X-MBX-USED-WEIGHT-1M": "2300"}, now=time.time())
    async with serve(FakeBinance().app()) as url, aiohttp.ClientSession() as s:
        cat = await bfd.catalog(s, base=url)                    # el catálogo no es urgente: esperó
        assert cat and len(waits) == 1 and 0 < waits[0] <= 60.5
