"""Operador paper por corridas (research/shadow.py) contra un Binance falso: entradas con el núcleo de decisión
(vector y alfa, meta-modelo, ¼-Kelly, libro real), triple barrera entre corridas (stop, take-profit y
expiración), salidas por noticia en contra, frenos (A-4, el diario cierra todo), KILL (E-1) y registros."""
import calendar
import contextlib
import copy
import json
from decimal import Decimal as D

import aiohttp
import pytest

from conftest import serve
from fake_exchange import FakeBinance
from fake_telegram import TOKEN, FakeTelegram
from tradingagent import limits as limits_mod
from tradingagent.alerts.telegram import TelegramTransport
from tradingagent.core.clock import ManualClock
from tradingagent.execution.paper import LatencyModel, PaperConfig
from tradingagent.intelligence.classifier import classify
from tradingagent.research.collector import _Telegram
from tradingagent.research.shadow import run_paper

T0 = calendar.timegm((2026, 11, 2, 12, 0, 0))              # lunes 2-nov-2026 12:00 UTC
BASES = {"BTCUSDT": 100_000.0, "ETHUSDT": 4_000.0, "SOLUSDT": 150.0}
LISTING = "Binance will list Solana (SOL) on its new spot trading pairs"


def step_price(moves: dict[str, list[tuple[float, float]]]):
    """Precio de apertura de cada minuto: base, con saltos (hora en segundos, factor) acumulativos por par."""
    def price(sym: str, m: int) -> float:
        p = BASES[sym]
        for t, factor in moves.get(sym, []):
            if m >= t * 1000:
                p *= factor
        return p
    return price


def ev(eid, title, t, *, source="media", tier=2, pub_lag=10, cluster=None, cluster_sources=1):
    return {"id": eid, "title": title, "source": source, "tier": tier, "cluster": cluster or eid,
            "cluster_sources": cluster_sources, "published_at": t - pub_lag, "received_at": t - 5}


class Paper:
    """Una carpeta de datos, un reloj y un Binance falso: `await p.run(items)` es una corrida del cron."""

    def __init__(self, tmp_path, moves=None, limits=None):
        self.clock = ManualClock(T0)
        self.data = tmp_path / "datos"
        self.mk = FakeBinance(step_price(moves or {}), self.clock)
        self.tg = FakeTelegram()
        self.limits = limits or copy.deepcopy(limits_mod.load())
        self.cfg = PaperConfig(latency=LatencyModel(median_ms=0), max_book_age_s=5.0, stop_slippage_bps=D(20))

    async def __aenter__(self):
        self.stack = contextlib.AsyncExitStack()
        url = await self.stack.enter_async_context(serve(self.mk.app()))
        turl = await self.stack.enter_async_context(serve(self.tg.app()))
        self.session = await self.stack.enter_async_context(aiohttp.ClientSession())
        transport = TelegramTransport(TOKEN, "4242", base_url=turl)
        self.stack.push_async_callback(transport.close)
        self.telegram = _Telegram(transport, 0)
        self.urls, self.candle_urls = FakeBinance.urls(url), FakeBinance.candle_urls(url)
        return self

    async def __aexit__(self, *exc):
        await self.stack.aclose()

    async def run(self, events=(), **kw):
        items = [(e, classify(e["title"], tier=e["tier"], cluster_sources=e["cluster_sources"])) for e in events]
        return await run_paper(self.data, items, clock=self.clock, session=self.session, limits=self.limits,
                               telegram=self.telegram, capital=120.0, urls=self.urls, candle_urls=self.candle_urls,
                               paper_config=self.cfg, **kw)

    def state(self):
        return json.loads((self.data / "paper" / "binance-spot" / "estado.json").read_text(encoding="utf-8"))

    def rows(self, kind):
        f = self.data / "paper" / "binance-spot" / kind / "2026-11.jsonl"
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []

    def texts(self):
        return [m["text"] for m in self.tg.messages]


async def test_entrada_con_el_nucleo_y_take_profit_entre_corridas(tmp_path):
    t1 = T0 + 10 * 60 + 30
    async with Paper(tmp_path, {"SOLUSDT": [(T0 + 12 * 60, 1.02)]}) as p:          # +2 % a las 12:12
        p.clock.set(t1)
        s1 = await p.run([ev("e1", LISTING, t1)])
        assert s1["entradas"] == 1 and s1["abiertas"] == ["SOLUSDT"]
        entry = p.rows("operaciones")[0]
        plan = entry["plan"]
        # α_E = 0,6·1·0,7·0,8 = 0,336, 10 s después 0,317 ⇒ p = 0,668; precio quieto ⇒ SL 0,6 % (piso), TP 1,2 %
        assert plan["p"] == pytest.approx(0.6683, abs=1e-3) and plan["alfa"] == pytest.approx(0.3171, abs=1e-3)
        assert (plan["sl"], plan["tp"], plan["tramos"]) == (0.006, 0.012, 1)
        # ¼-Kelly (9,7 % a arriesgar) y el 1 % de riesgo dan más: manda la exposición neta, 30 % de 120 = 36 USDT
        assert plan["kelly"] == pytest.approx(0.0966, abs=1e-3) and plan["notional"] == "36.0"
        assert plan["riesgo_usd"] < 120 * 0.01
        st = p.state()
        pos = st["positions"]["SOLUSDT"]
        assert D(pos["qty"]) == D("0.239") and D(pos["avg_price"]) == D("150.02") and pos["side"] == "largo"
        assert D(pos["stop"]) == D("149.11") and D(pos["tp"]) == D("151.83")         # al tick, del lado prudente
        assert pos["until"] == round(t1) + 3600 and pos["category"] == "listado"
        assert D(st["cash"]) == D("120") - D("0.239") * D("150.02") * D("1.001")
        assert entry["tipo"] == "entrada" and entry["modo"] == "paper" and entry["order_id"].startswith("P")
        decision = p.rows("decisiones")[0]
        assert decision["decision"] == "entrar" and decision["analisis"]["vector"]["C"] == 10
        last = p.texts()[-1]
        assert "Compré SOL" in last and "Si baja a 149,11" in last and "si sube a 151,83" in last
        assert "Chance estimada de ganar: 67 %" in last and "un listado nuevo en un exchange" in last

        p.clock.set(t1 + 5 * 60)                                                    # siguiente corrida del cron
        s2 = await p.run()
        assert s2["take_profit"] == 1 and s2["abiertas"] == [] and s2["operaciones"] == 1
        out = p.rows("operaciones")[-1]
        assert (out["motivo"], D(out["price"]), out["deslizamiento_bps"]) == ("take_profit", D("151.83"), 0.0)
        gross = D("0.239") * (D("151.83") - D("150.02"))
        fees = D("0.239") * (D("151.83") + D("150.02")) * D("0.001")
        assert D(out["pnl_neto"]) == pytest.approx(gross - fees, abs=D("0.000001"))
        assert D(out["retorno_neto"]) == pytest.approx((gross - fees) / (D("0.239") * D("150.02")), abs=D("0.000001"))
        assert D(p.state()["cash"]) == pytest.approx(D("120") + gross - fees, abs=D("0.000001"))
        assert "Gané +" in p.texts()[-1] and "cobré la ganancia" in p.texts()[-1]


async def test_stop_con_salto_noticia_en_contra_y_expiracion(tmp_path):
    t1 = T0 + 10 * 60 + 30
    lim = copy.deepcopy(limits_mod.load())
    lim["per_trade"]["max_position_pct"] = 15                                     # más estricto: entran las dos
    async with Paper(tmp_path, {"SOLUSDT": [(T0 + 13 * 60, 0.97)]}, limits=lim) as p:   # -3 % a las 12:13...
        p.mk.overrides[("SOLUSDT", (T0 + 12 * 60) * 1000)] = (150.0, 150.1, 149.9, 150.0)   # ...de un salto
        p.clock.set(t1)
        await p.run([ev("e1", LISTING, t1), ev("e2", "Coinbase will list Ethereum (ETH) futures", t1)])
        assert sorted(p.state()["positions"]) == ["ETHUSDT", "SOLUSDT"]

        p.clock.set(t1 + 5 * 60)
        s2 = await p.run([ev("n1", "Ethereum foundation wallet hacked, ETH drained", t1 + 5 * 60)])
        rows = {r["symbol"]: r for r in p.rows("operaciones") if r["tipo"] == "salida"}
        assert rows["SOLUSDT"]["motivo"] == "stop_loss"                          # abrió debajo del stop: desde ahí
        assert D(rows["SOLUSDT"]["price"]) == D("145.20")                        # 145,5 − 20 bps, al tick de abajo
        assert rows["SOLUSDT"]["deslizamiento_bps"] > 200                        # contra el stop teórico
        assert rows["ETHUSDT"]["motivo"] == "noticia_en_contra" and s2["noticia_en_contra"] == 1
        assert s2["abiertas"] == [] and "límite de pérdida" in "\n".join(p.texts())
        short = [d for d in p.rows("decisiones") if d["event_id"] == "n1"][0]
        assert "corto_no_disponible" in short["motivos"]                         # spot: sin cortos

        t3 = t1 + 10 * 60
        p.clock.set(t3)
        s3 = await p.run([ev("e3", "Kraken adds trading for Bitcoin (BTC) options", t3)])
        assert s3["entradas"] == 1 and s3["abiertas"] == ["BTCUSDT"]
        p.clock.set(t3 + 61 * 60)                                               # barrera vertical: 60 min
        s4 = await p.run()
        out = p.rows("operaciones")[-1]
        assert s4["expiracion"] == 1 and s4["abiertas"] == [] and D(out["price"]) == D("99800.00")
        assert "tiempo máximo" in p.texts()[-2] and "3 pérdidas seguidas" in p.texts()[-1]   # y la pausa
        stats = p.state()["stats"]
        assert stats["operaciones"] == 3 and stats["perdidas"] == 3
        assert D(stats["pnl_neto"]) == sum(D(r["pnl_neto"]) for r in p.rows("operaciones") if r["tipo"] == "salida")


async def test_freno_diario_cierra_todo_y_no_abre_hasta_el_otro_dia(tmp_path):
    lim = copy.deepcopy(limits_mod.load())
    lim["loss"]["daily_pct"] = 0.05                                             # más estricto, para probarlo
    lim["per_trade"]["max_position_pct"] = 15                                   # más estricto: entran las dos
    t1 = T0 + 10 * 60 + 30
    async with Paper(tmp_path, {"SOLUSDT": [(T0 + 13 * 60, 0.97)]}, limits=lim) as p:
        p.clock.set(t1)
        await p.run([ev("e1", LISTING, t1), ev("e2", "Coinbase will list Ethereum (ETH) futures", t1)])
        p.clock.set(t1 + 5 * 60)
        s2 = await p.run([ev("e3", "Kraken adds trading for Bitcoin (BTC) options", t1 + 5 * 60)])
        assert s2["stop_loss"] == 1 and s2["freno"] == 1 and s2["abiertas"] == []  # ETH sale por el freno
        assert s2.get("entradas", 0) == 0 and any("pérdida del día" in f for f in s2["frenos"])
        assert any("freno de seguridad" in x for x in p.texts()) and any("Freno del día" in x for x in p.texts())
        p.clock.set(T0 + 86400 + 60)                                            # día nuevo
        s3 = await p.run([ev("e4", "Kraken adds trading for Bitcoin (BTC) options", T0 + 86400 + 60)])
        assert s3["entradas"] == 1


async def test_enfriamiento_y_kill(tmp_path):
    lim = copy.deepcopy(limits_mod.load())
    lim["loss"]["max_consecutive_losses"] = 1                                   # más estricto: siempre se puede
    t1 = T0 + 10 * 60 + 30
    async with Paper(tmp_path, {"SOLUSDT": [(T0 + 13 * 60, 0.97)]}, limits=lim) as p:
        p.clock.set(t1)
        await p.run([ev("e1", LISTING, t1)])
        p.clock.set(t1 + 5 * 60)
        s2 = await p.run([ev("e2", "Coinbase will list Ethereum (ETH) futures", t1 + 5 * 60)])
        assert s2["stop_loss"] == 1 and s2.get("entradas", 0) == 0               # el stop dispara el enfriamiento
        assert any("enfriamiento" in f for f in s2["frenos"])
        assert sum("Pausa corta" in x for x in p.texts()) == 1
        why = p.rows("decisiones")[-1]
        assert why["decision"] == "no_entrar" and "enfriamiento" in why["motivos"][0]
        p.clock.set(t1 + 10 * 60)
        await p.run([ev("e3", "Kraken adds trading for Bitcoin (BTC) options", t1 + 10 * 60)])
        assert sum("Pausa corta" in x for x in p.texts()) == 1                   # un solo aviso por freno

        p.clock.set(t1 + 80 * 60)                                               # pasó el enfriamiento
        (p.data / "KILL").write_text("")
        s4 = await p.run([ev("e4", "Kraken adds trading for Bitcoin (BTC) options", t1 + 80 * 60)])
        assert s4.get("entradas", 0) == 0 and p.rows("decisiones")[-1]["motivos"] == ["interruptor_KILL"]
        (p.data / "KILL").unlink()
        s5 = await p.run([ev("e5", "Kraken adds trading for Bitcoin (BTC) options", t1 + 80 * 60)])
        assert s5["entradas"] == 1


async def test_drawdown_halt_hasta_el_rearme(tmp_path):
    lim = copy.deepcopy(limits_mod.load())
    lim["loss"]["max_drawdown_pct"] = 0.05                                      # más estricto, para probarlo
    t1 = T0 + 10 * 60 + 30
    async with Paper(tmp_path, {"SOLUSDT": [(T0 + 13 * 60, 0.97)]}, limits=lim) as p:
        p.clock.set(t1)
        await p.run([ev("e1", LISTING, t1)])
        p.clock.set(t1 + 5 * 60)
        s2 = await p.run()
        assert s2["stop_loss"] == 1 and any(f.startswith("HALT") for f in s2["frenos"])
        p.clock.set(t1 + 3 * 86400)                                             # días después sigue en halt
        s3 = await p.run([ev("e2", "Kraken adds trading for Bitcoin (BTC) options", t1 + 3 * 86400)])
        assert s3.get("entradas", 0) == 0 and p.rows("decisiones")[-1]["motivos"][0].startswith("HALT")
        assert p.state()["risk"]["halted"]
        s4 = await p.run([ev("e3", "Kraken adds trading for Bitcoin (BTC) options", t1 + 3 * 86400)], rearm=True)
        assert s4["entradas"] == 1 and p.state()["risk"]["halted"] == ""


async def test_noticias_que_no_se_operan_quedan_registradas_con_su_motivo(tmp_path):
    t1 = T0 + 10 * 60 + 30
    async with Paper(tmp_path) as p:
        p.clock.set(t1)
        p.mk.down.add("info")                                                   # sin filtros del par
        s = await p.run([
            ev("v", LISTING, t1, pub_lag=900),                                    # vieja (B-2)
            ev("o", LISTING, t1, pub_lag=400),                                    # alfa obsoleta (> 360 s)
            ev("r", "Kraken reportedly plans to list Solana", t1),                # rumor: el meta-modelo no da
            ev("u", LISTING, t1, tier=3),                                         # nivel 3 sin corroborar (B-1)
            ev("f", "Coinbase will list Ethereum (ETH) futures", t1),             # sin filtros: no se opera
        ])
        motives = {d["event_id"]: d["motivos"] for d in p.rows("decisiones")}
        assert motives["v"][0].startswith("noticia_vieja") and motives["o"][0] == "alfa_obsoleta:400s"
        assert motives["r"][0] == "meta_p_baja:0.54"
        assert "nivel3_sin_corroboracion" in motives["u"] and motives["f"] == ["par_sin_filtros"]
        assert s.get("entradas", 0) == 0 and s["descartada"] == 5 and p.state()["positions"] == {}

        p.mk.down.discard("info")
        p.mk.spread_bps = 45                                                    # el spread se come la ventaja
        s = await p.run([ev("w", LISTING, t1)])
        assert s.get("entradas", 0) == 0 and p.rows("decisiones")[-1]["motivos"][0].startswith("sin_ventaja_neta")
        p.mk.spread_bps = 15                                                    # rinde, pero el sobre se achicó a 10
        p.limits["market"]["max_spread_bps"] = 10
        s = await p.run([ev("w1", LISTING, t1)])
        assert s.get("entradas", 0) == 0 and p.rows("decisiones")[-1]["motivos"][0] == "spread:16.0bps"
        p.limits["market"]["max_spread_bps"] = 30
        p.mk.spread_bps = 2
        p.mk.last["SOLUSDT"] = 152.0                                            # último precio a 1,3 % del medio
        s = await p.run([ev("d", LISTING, t1)])
        assert p.rows("decisiones")[-1]["motivos"] == ["divergencia:1.33%"]
        p.mk.last.clear()
        s = await p.run([ev("w2", LISTING, t1, cluster="w")])                    # otra copia de la misma historia
        assert s["entradas"] == 1
        p.clock.set(t1 + 300)
        s = await p.run([ev("w3", LISTING, t1 + 300, cluster="w")])
        assert s.get("entradas", 0) == 0 and p.rows("decisiones")[-1]["motivos"] == ["historia_ya_operada"]


# -- conectado al recolector (collect --paper) --------------------------------------------------------
async def test_recolector_clasifica_y_opera_en_paper_con_resumen_diario(tmp_path):
    from fake_feeds import Feeds, specs
    from tradingagent import config
    from tradingagent.research.collector import collect, read_shards

    cfg = copy.deepcopy(config.load(None))
    clock = ManualClock(T0)
    feeds, tg = Feeds(), FakeTelegram()
    mk = FakeBinance(step_price({}), clock)
    data = tmp_path / "datos"
    feeds.add("media", "old", "Older market story about crypto", T0 - 3600)
    async with contextlib.AsyncExitStack() as stack:
        furl = await stack.enter_async_context(serve(feeds.app()))
        murl = await stack.enter_async_context(serve(mk.app()))
        turl = await stack.enter_async_context(serve(tg.app()))
        t = TelegramTransport(TOKEN, "4242", base_url=turl)
        stack.push_async_callback(t.close)

        async def run(**paper):
            return await collect(data, cfg, {}, clock=clock, sources=specs(furl), telegram=t, send_gap_s=0,
                                 candle_urls=FakeBinance.candle_urls(murl),
                                 paper={"capital": 120.0, "mercados": ["binance-spot"], **paper},
                                 paper_urls=FakeBinance.urls(murl))

        s1 = await run()                                                       # historia: no se opera
        assert s1["paper"]["binance-spot"]["equity"] == 120.0 and s1["paper"]["binance-spot"]["operaciones"] == 0
        assert not any("práctica" in m["text"] for m in tg.messages)            # el respaldo no avisa suelto

        t2 = T0 + 10 * 60
        feeds.add("media", "sol", LISTING, t2 - 20, body="Trading opens today.")
        feeds.add("copia", "sol2", LISTING, t2 - 10)                             # copia: corrobora, no se opera
        feeds.add("media", "pod", "Weekly crypto podcast: what to watch", t2 - 60)
        clock.set(t2)
        s2 = await run()
        assert s2["nuevas"] == 2 and s2["copias"] == 1 and s2["categorias"] == {"listado": 1, "otro": 1}
        evs = {e["url"].rsplit("/", 1)[1]: e for e in read_shards(data, "eventos", T0, clock.time())}
        assert evs["sol"]["nlp"]["category"] == "listado" and evs["sol"]["nlp"]["assets"] == ["SOL"]
        assert evs["sol"]["nlp"]["confidence"] > classify(LISTING).confidence    # corroborada en la misma corrida
        assert evs["sol2"]["nlp"]["category"] == "listado"                        # las copias también se clasifican
        assert s2["paper"]["binance-spot"]["entradas"] == 1 and s2["paper"]["binance-spot"]["abiertas"] == ["SOLUSDT"]
        assert not any("Compré" in m["text"] for m in tg.messages)              # sus operaciones tampoco

        clock.set(T0 + 13 * 3600)                                               # 3-nov 01:00: resumen de ayer
        s3 = await run()
        assert s3["paper"]["binance-spot"]["expiracion"] == 1                   # vencieron los 60 min
        digest = next(m["text"] for m in tg.messages if "Resumen de noticias del 02/11" in m["text"])
        assert "Práctica de respaldo en Binance" in digest and "Abiertas: SOL." in digest   # la cuenta al cierre
        assert "(empezó con 120,00 USDT)" in digest


async def test_si_el_paper_falla_los_datos_del_recolector_quedan(tmp_path, monkeypatch):
    from fake_feeds import Feeds, specs
    from tradingagent import config
    from tradingagent.research import shadow
    from tradingagent.research.collector import collect

    async def broken(*a, **kw):
        raise RuntimeError("falla de prueba")

    monkeypatch.setattr(shadow, "run_paper", broken)
    clock = ManualClock(T0)
    feeds = Feeds()
    async with serve(feeds.app()) as furl, serve(FakeBinance(step_price({}), clock).app()) as murl:
        s = await collect(tmp_path / "d", copy.deepcopy(config.load(None)), {}, clock=clock, sources=specs(furl),
                          candle_urls=FakeBinance.candle_urls(murl), send_gap_s=0,
                          paper={"capital": 120.0, "mercados": ["binance-spot"]})
    assert s["paper"] == {"binance-spot": {"error": "RuntimeError: falla de prueba"}}
    assert (tmp_path / "d" / "estado.json").exists()


async def test_dos_cuentas_paper_spot_y_futuros_en_la_misma_corrida(tmp_path):
    from fake_feeds import Feeds, specs
    from fake_mexc import FakeMexc
    from tradingagent import config
    from tradingagent.research.collector import collect

    clock = ManualClock(T0)
    feeds = Feeds()
    mx_prices = {"BTC_USDT": 100_000.0, "ETH_USDT": 4_000.0, "SOL_USDT": 150.0}
    feeds.add("media", "old", "Older story", T0 - 3600)
    async with serve(feeds.app()) as furl, serve(FakeBinance(step_price({}), clock).app()) as burl, \
            serve(FakeMexc(lambda sym, m: mx_prices[sym], clock).app()) as xurl:
        async def run():
            return await collect(tmp_path / "d", copy.deepcopy(config.load(None)), {}, clock=clock,
                                 sources=specs(furl), candle_urls=FakeBinance.candle_urls(burl), send_gap_s=0,
                                 paper_urls=FakeBinance.urls(burl),
                                 paper={"capital": 120.0, "mexc_base": xurl,
                                        "mercados": ["binance-spot", "mexc-futuros", "otro"]})
        await run()
        feeds.add("media", "h", "Binance hacked: $40M in BTC stolen", T0 + 590)
        clock.set(T0 + 600)
        s = await run()
    spot, fut = s["paper"]["binance-spot"], s["paper"]["mexc-futuros"]
    assert spot.get("entradas", 0) == 0 and spot["descartada"] == 1               # spot no puede ponerse corto
    assert fut["entradas"] == 1 and fut["abiertas"] == ["BTC_USDT"]                 # futuros sí
    assert (tmp_path / "d" / "paper" / "mexc-futuros" / "estado.json").exists()


async def test_categoria_apagada_por_el_aprendizaje_no_entra_pero_salir_siempre_se_puede(tmp_path):
    t1 = T0 + 10 * 60 + 30
    async with Paper(tmp_path) as p:
        p.clock.set(t1)
        s = await p.run([ev("e1", LISTING, t1)], disabled=("listado:largo",))
        assert s.get("entradas", 0) == 0
        assert p.rows("decisiones")[-1]["motivos"] == ["categoria_apagada_por_evidencia:listado"]
        s = await p.run([ev("e2", "Coinbase will list Ethereum (ETH) futures", t1)], disabled=("listado:corto",))
        assert s["entradas"] == 1                                             # el otro lado no cuenta
        p.clock.set(t1 + 300)
        s = await p.run([ev("n1", "Ethereum foundation wallet hacked, ETH drained", t1 + 300)],
                        disabled=("hack:corto", "hack:largo"))
        assert s["noticia_en_contra"] == 1 and s["abiertas"] == []
