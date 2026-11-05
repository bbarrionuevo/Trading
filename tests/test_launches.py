"""Perpetuos nuevos de cripto en Binance (decision/launches.py) contra un Binance falso: a los 15 minutos del inicio,
si el precio quedó en o debajo del primero, corto con stop fijo de 30 % y salida a las 72 h, arriesgando 2 % del
saldo. Sin IA: la dirección la da el estudio del 4-nov (docs/ESTUDIO-LANZAMIENTOS.md)."""
import asyncio
import calendar
import copy
import json

import aiohttp
import pytest

import fake_binance
from conftest import serve
from fake_binance import PRICES, FakeBinance
from tradingagent import limits as limits_mod
from tradingagent.core.clock import ManualClock
from tradingagent.decision import launches
from tradingagent.execution.paper import LatencyModel, PaperConfig
from tradingagent.live import LiveTrader
from tradingagent.research.futures import BinanceFutures
from tradingagent.research.labeler import minute

T0 = calendar.timegm((2026, 11, 5, 8, 15, 0))


class Alerts:
    def __init__(self):
        self.items = []

    def submit(self, alert):
        self.items.append(alert)


def market(after: float):
    """NEW a 1,00 en su primer minuto y a `after` desde el siguiente."""
    return lambda sym, m: (after if m > minute(T0) else 1.0) if sym == "NEWUSDT" else PRICES.get(sym)


async def trader(session, url, clock, home):
    t = LiveTrader(BinanceFutures(base=url), session, clock, copy.deepcopy(limits_mod.load()), alerts=Alerts(),
                   home=home, capital=120.0, paper_config=PaperConfig(latency=LatencyModel(median_ms=0)))
    await t.start()
    return t


def decisions(home):
    f = home / "paper-vivo" / "binance-futuros" / "decisiones" / "2026-11.jsonl"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def new_contract(monkeypatch):
    monkeypatch.setitem(fake_binance.SPECS, "NEWUSDT", ("0.0001", "1", "1", "5", "PERPETUAL", "COIN", "NEW"))


def test_solo_perpetuos_de_cripto_recien_empezados():
    cat = {"NEWUSDT": {"tipo": "PERPETUAL", "subyacente": "COIN", "trading": True, "alta": T0},
           "OLDUSDT": {"tipo": "PERPETUAL", "subyacente": "COIN", "trading": True, "alta": T0 - 3600},
           "TSLAUSDT": {"tipo": "TRADIFI_PERPETUAL", "subyacente": "EQUITY", "trading": True, "alta": T0},
           "WAITUSDT": {"tipo": "PERPETUAL", "subyacente": "COIN", "trading": False, "alta": T0},
           "牛来USDT": {"tipo": "PERPETUAL", "subyacente": "COIN", "trading": True, "alta": T0}}
    assert launches.new_contracts(cat, T0 + 60) == [("NEWUSDT", T0)]
    assert launches.new_contracts(cat, T0 + 60, seen={"NEWUSDT": T0}) == []
    assert launches.new_contracts(cat, T0 + 1300) == []                      # pasaron más de 20 minutos


async def test_a_los_15_minutos_si_ya_bajo_va_corto_con_stop_de_30(tmp_path, new_contract):
    clock = ManualClock(T0 + 905)
    fake = FakeBinance(price=market(0.95), clock=clock)
    fake.onboard["NEWUSDT"] = T0 * 1000
    home = tmp_path / "home"
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, home)
        await t.check_launches()
        await asyncio.gather(*t.pending)
        pos = t.st["positions"]["NEWUSDT"]
        assert pos["side"] == "corto" and pos["category"] == "lanzamiento_corto"
        entry = float(pos["avg_price"])
        assert 1.29 < float(pos["stop"]) / entry < 1.31                     # stop fijo de 30 %
        assert abs(pos["until"] - clock.time() - 72 * 3600) < 5              # sale a las 72 h
        notional = entry * float(pos["qty"])
        assert notional <= 0.065 * 120 + 0.5                                 # 2 % del saldo al stop
        await t.check_launches()                                             # ya visto: no se repite
        assert not t.pending
    row = next(r for r in decisions(home) if r["decision"] == "entrar")
    assert row["signal"]["version"] == "lanzamiento-1"


async def test_si_subio_no_entra(tmp_path, new_contract):
    clock = ManualClock(T0 + 905)
    fake = FakeBinance(price=market(1.05), clock=clock)
    fake.onboard["NEWUSDT"] = T0 * 1000
    home = tmp_path / "home"
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, home)
        await t.check_launches()
        await asyncio.gather(*t.pending)
        assert t.st["positions"] == {}
    why = [r["motivos"] for r in decisions(home) if r["signal"].get("category") == "lanzamiento_corto"]
    assert why and why[0][0].startswith("no_bajo:+5.0")
