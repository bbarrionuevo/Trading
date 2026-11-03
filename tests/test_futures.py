"""Futuros perpetuos de MEXC en paper contra un MEXC falso: cortos, comisiones (D-5), funding, liquidación y
el cliente privado de solo lectura (sin órdenes, A-3)."""
import calendar
import contextlib
import copy
import hashlib
import hmac
import json
from decimal import Decimal as D

import aiohttp
import pytest

from conftest import serve
from fake_mexc import ACCESS, SECRET, FakeMexc
from fake_telegram import TOKEN, FakeTelegram
from tradingagent import limits as limits_mod
from tradingagent.alerts.telegram import TelegramTransport
from tradingagent.core.clock import ManualClock
from tradingagent.execution.paper import LatencyModel, PaperConfig
from tradingagent.intelligence.classifier import classify
from tradingagent.marketdata import mexc
from tradingagent.marketdata.providers import MarketDataError
from tradingagent.research.collector import _Telegram
from tradingagent.research.futures import MexcFutures, liquidation_price
from tradingagent.research.shadow import run_paper

T0 = calendar.timegm((2026, 11, 2, 12, 0, 0))
BASES = {"BTC_USDT": 100_000.0, "ETH_USDT": 4_000.0, "SOL_USDT": 150.0}
HACK = "Binance hacked: $40M in BTC stolen"
ETH_LISTING = "Coinbase will list Ethereum (ETH) futures"
BTC_LISTING = "Kraken adds trading for Bitcoin (BTC) options"


def step_price(moves):
    def price(sym, m):
        p = BASES[sym]
        for t, factor in moves.get(sym, []):
            if m >= t * 1000:
                p *= factor
        return p
    return price


def ev(eid, title, t, pub_lag=10):
    return {"id": eid, "title": title, "source": "media", "tier": 2, "cluster": eid, "cluster_sources": 1,
            "published_at": t - pub_lag, "received_at": t - 5}


class Futures:
    def __init__(self, tmp_path, moves=None, keys=True):
        self.clock = ManualClock(T0)
        self.data = tmp_path / "datos"
        self.mk = FakeMexc(step_price(moves or {}), self.clock)
        self.tg = FakeTelegram()
        self.keys = keys
        self.limits = copy.deepcopy(limits_mod.load())
        self.cfg = PaperConfig(latency=LatencyModel(median_ms=0), max_book_age_s=5.0, stop_slippage_bps=D(20))

    async def __aenter__(self):
        self.stack = contextlib.AsyncExitStack()
        self.url = await self.stack.enter_async_context(serve(self.mk.app()))
        turl = await self.stack.enter_async_context(serve(self.tg.app()))
        self.session = await self.stack.enter_async_context(aiohttp.ClientSession())
        transport = TelegramTransport(TOKEN, "4242", base_url=turl)
        self.stack.push_async_callback(transport.close)
        self.telegram = _Telegram(transport, 0)
        ro = mexc.ReadOnlyClient(self.session, ACCESS, SECRET, base=self.url, clock=self.clock) if self.keys else None
        self.market = MexcFutures(base=self.url, readonly=ro)
        return self

    async def __aexit__(self, *exc):
        await self.stack.aclose()

    async def run(self, events=(), **kw):
        items = [(e, classify(e["title"])) for e in events]
        return await run_paper(self.data, items, clock=self.clock, session=self.session, limits=self.limits,
                               telegram=self.telegram, capital=120.0, market=self.market, paper_config=self.cfg, **kw)

    def state(self):
        return json.loads((self.data / "paper" / "mexc-futuros" / "estado.json").read_text(encoding="utf-8"))

    def rows(self, kind):
        f = self.data / "paper" / "mexc-futuros" / kind / "2026-11.jsonl"
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


async def test_corto_con_noticia_negativa_comisiones_d5_y_take_profit(tmp_path):
    t1 = T0 + 10 * 60 + 30
    async with Futures(tmp_path, {"BTC_USDT": [(T0 + 12 * 60, 0.98)]}) as f:      # −2 % a las 12:12
        f.clock.set(t1)
        s1 = await f.run([ev("h1", HACK, t1)])
        assert s1["entradas"] == 1 and s1["abiertas"] == ["BTC_USDT"]
        st = f.state()
        inst = st["instruments"]["BTC_USDT"]
        assert (inst["taker_cuenta"], inst["maker_cuenta"]) == ("0.0006", "0.0002")   # leído con la clave de lectura
        pos = st["positions"]["BTC_USDT"]
        assert pos["side"] == "corto" and D(pos["qty"]) == D("-0.0002") and D(pos["avg_price"]) == D("99990.0")
        assert D(pos["stop"]) == D("100590.0") and D(pos["tp"]) == D("98790.1")      # stop arriba, TP abajo
        assert float(pos["liq"]) == pytest.approx(99990 * 1.996)                     # a 1x, lejísimos
        entry = f.rows("operaciones")[0]
        assert D(entry["fee"]) == D("0.0002") * D("99990.0") * D("0.0006")          # D-5: la comisión mayor (cuenta)
        assert "Vendí en corto" in f.tg.messages[-1]["text"] and "futuros MEXC" in f.tg.messages[-1]["text"]

        f.clock.set(t1 + 5 * 60)
        s2 = await f.run()
        out = f.rows("operaciones")[-1]
        assert s2["take_profit"] == 1 and out["lado"] == "corto" and D(out["price"]) == D("98790.1")
        gross = D("0.0002") * (D("99990.0") - D("98790.1"))
        fees = D("0.0002") * D("99990.0") * D("0.0006") + D("0.0002") * D("98790.1") * D("0.0002")
        assert D(out["pnl_neto"]) == pytest.approx(gross - fees, abs=D("1e-8")) and D(out["pnl_neto"]) > 0
        assert s2.get("funding", 0) == 0                                              # cerró antes del cobro de las 16


async def test_funding_se_cobra_mientras_la_posicion_esta_abierta(tmp_path):
    t1 = T0 + 3 * 3600 + 58 * 60 + 30                                               # 15:58:30, cobro a las 16:00
    async with Futures(tmp_path) as f:
        f.clock.set(t1)
        s1 = await f.run([ev("e1", ETH_LISTING, t1), ev("b1", BTC_LISTING, t1)])
        eth = next(d for d in f.rows("decisiones") if d["symbol"] == "ETH_USDT")
        assert eth["motivos"][0].startswith("nominal_menor_al_minimo")              # 1 contrato = 0,01 ETH ≈ 40 USDT
        assert s1["abiertas"] == ["BTC_USDT"]
        pos = f.state()["positions"]["BTC_USDT"]
        assert pos["side"] == "largo" and pos["funding_next"] == (T0 + 4 * 3600) * 1000
        f.clock.set(t1 + 5 * 60)
        s2 = await f.run()
        assert s2["funding"] == 1 and s2["abiertas"] == ["BTC_USDT"]
        row = next(r for r in f.rows("operaciones") if r["tipo"] == "funding")
        qty = abs(D(pos["qty"]))
        assert D(row["pago"]) == pytest.approx(qty * D("100000.0") * D("0.0001"), abs=D("1e-8"))   # el largo paga
        assert f.state()["positions"]["BTC_USDT"]["funding_next"] == (T0 + 12 * 3600) * 1000
        f.clock.set(t1 + 62 * 60)                                                     # expira: el neto incluye el funding
        await f.run()
        out = f.rows("operaciones")[-1]
        assert out["motivo"] == "expiracion"
        assert D(out["pnl_neto"]) < -D(row["pago"])                                   # comisiones + funding


async def test_un_salto_que_pasa_la_liquidacion_se_registra_como_tal(tmp_path):
    assert liquidation_price(100.0, 1, 1.0, 0.004) == pytest.approx(0.4)
    assert liquidation_price(100.0, -1, 1.0, 0.004) == pytest.approx(199.6)
    assert liquidation_price(100.0, 1, 10.0, 0.004) == pytest.approx(90.4)
    t1 = T0 + 10 * 60 + 30
    async with Futures(tmp_path, {"BTC_USDT": [(T0 + 13 * 60, 0.90)]}) as f:          # −10 % de golpe a las 12:13
        f.mk.overrides[("BTC_USDT", (T0 + 12 * 60) * 1000)] = (100_000.0, 100_050.0, 99_950.0, 100_000.0)
        f.clock.set(t1)
        await f.run([ev("b1", BTC_LISTING, t1)])
        st = f.state()
        st["positions"]["BTC_USDT"]["liq"] = "93000"                                 # como si estuviera apalancado
        (f.data / "paper" / "mexc-futuros" / "estado.json").write_text(json.dumps(st), encoding="utf-8")
        f.clock.set(t1 + 5 * 60)
        s2 = await f.run()
        out = f.rows("operaciones")[-1]
        assert s2["liquidacion"] == 1 and D(out["price"]) == D("93000.0")
        assert any("se liquidó" in m["text"] for m in f.tg.messages)


async def test_cliente_de_solo_lectura_firma_y_no_sale_de_su_lista(tmp_path):
    t = "1793642808000"
    want = hmac.new(SECRET.encode(), f"{ACCESS}{t}symbol=BTC_USDT".encode(), hashlib.sha256).hexdigest()
    assert mexc.signature(ACCESS, SECRET, t, {"symbol": "BTC_USDT"}) == want
    clock = ManualClock(T0)
    mk = FakeMexc(step_price({}), clock)
    async with serve(mk.app()) as url, aiohttp.ClientSession() as session:
        ro = mexc.ReadOnlyClient(session, ACCESS, SECRET, base=url, clock=clock)
        assert await ro.account_ok() and (await ro.fee_rates("BTC_USDT"))["taker"] == 0.0006
        assert all(s["ok"] and s["method"] == "GET" for s in mk.signed)
        for path in ("/api/v1/private/order/submit", "/api/v1/private/position/change_leverage",
                     "/api/v1/private/account/assets/../../order/submit"):
            with pytest.raises(PermissionError):
                await ro.get(path)
        bad = mexc.ReadOnlyClient(session, ACCESS, "OTRO" + SECRET, base=url, clock=clock)
        with pytest.raises(MarketDataError) as e:
            await bad.account_ok()
        assert SECRET not in str(e.value) and ACCESS not in str(e.value)
    with pytest.raises(ValueError):
        mexc.ReadOnlyClient(None, "", "")
    book = mexc.parse_depth("BTC_USDT", {"bids": [[99990.0, 50, 2]], "asks": [[100010.0, 30, 1]], "timestamp": 1},
                            D("0.0001"), 1.0)
    assert book.bids[0][1] == D("0.0050") and book.asks[0][1] == D("0.0030")      # contratos × tamaño


async def test_sin_claves_usa_lo_publico_y_las_comisiones_configuradas(tmp_path):
    t1 = T0 + 10 * 60 + 30
    async with Futures(tmp_path, keys=False) as f:
        f.clock.set(t1)
        await f.run([ev("h1", HACK, t1)])
        entry = f.rows("operaciones")[0]
        assert D(entry["fee"]) == D(entry["notional"]) * D("0.0005")                 # 0,05 % configurado > 0,02 %
        assert f.mk.signed == []
