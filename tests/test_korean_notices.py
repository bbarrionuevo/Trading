"""Avisos negativos de Upbit y Bithumb al instante (decision/korean_notices.py, estudio del 7-nov:
estudios/hipotesis_direccionales.py advertencias): corto en el perpetuo de Binance apenas llega el aviso, sin esperar a la
IA, si llegó a tiempo y el precio no cayó más que lo medido. La advertencia de Upbit sale a las 4 h y arriesga 2 %; el
fin del soporte (Upbit o Bithumb, el primero que llega) sale a los 5 min y arriesga 1 %. Los títulos son reales."""
import asyncio
import calendar
import copy
import json
from dataclasses import replace

import aiohttp
from aiohttp import web

from conftest import serve
from fake_binance import PRICES, FakeBinance
from tradingagent import limits as limits_mod
from tradingagent.core.clock import ManualClock
from tradingagent.core.events import TIER_OFFICIAL, DedupResult, IngestedNews, NewsItem, NoiseResult
from tradingagent.decision import korean_notices as kn
from tradingagent.decision import policy
from tradingagent.execution.paper import LatencyModel, PaperConfig
from tradingagent.ingestion import announcements as an
from tradingagent.ingestion.catalog import DEFAULT_SOURCES
from tradingagent.ingestion.normalize import parse_datetime
from tradingagent.ingestion.sources import PollingSource
from tradingagent.intelligence.classifier import Signal
from tradingagent.live import LiveTrader
from tradingagent.research import modulos
from tradingagent.research.futures import BinanceFutures
from tradingagent.research.labeler import minute

T0 = calendar.timegm((2026, 11, 9, 7, 0, 58))          # el aviso, a los 58 s de un minuto
WARNING = "솔라나(SOL) 거래 유의 종목 지정 안내"
END_UPBIT = "솔라나(SOL) 거래지원 종료 안내 (12/9 15:00)"
END_BITHUMB = "솔라나(SOL), 푸코인(FOO) 거래지원 종료"


def test_los_titulos_reales_se_leen_tramo_por_tramo_como_en_el_estudio():
    assert kn.kinds("이니시아(INIT) 거래 유의 종목 지정 안내") == [("advertencia", "INIT")]
    assert kn.kinds("왁스(WAXP) 유의 촉구 안내") == [("advertencia", "WAXP")]
    assert kn.kinds("멀티버스엑스(EGLD) 거래 유의 종목 지정 기간 연장 안내") == [("extendida", "EGLD")]
    assert kn.kinds("샌드박스(SAND) 거래 유의 종목 지정 해제 안내") == [("levantada", "SAND")]
    assert kn.kinds("블러(BLUR) 거래지원 종료 안내 (11/30 15:00)") == [("fin_del_soporte", "BLUR")]
    assert kn.kinds("블라스트(BLAST) 거래지원 종료 및 네트워크 전환에 따른 출금 일시 중단 안내") == [
        ("fin_del_soporte", "BLAST")]
    assert kn.kinds("만트라(MANTRA) 거래유의종목 지정 연장, 질리카(ZIL) 거래지원 종료") == [
        ("extendida", "MANTRA"), ("fin_del_soporte", "ZIL")]                      # Bithumb: dos avisos en uno
    assert kn.kinds("울트라(UOS), 메이플라워(MAY), 알타바(TAVA) 아치루트(AL) 거래유의종목 지정") == [
        ("advertencia", c) for c in ("UOS", "MAY", "TAVA", "AL")]
    assert kn.kinds("바운드리스(ZKC) 거래유의종목 지정 해제 및 레이블 에이아이(LBL), 클레이스왑(KSP) 거래지원 종료") == [
        ("levantada", "ZKC"), ("fin_del_soporte", "LBL"), ("fin_del_soporte", "KSP")]
    assert kn.kinds("휴머니티(H) 거래지원종료") == [("fin_del_soporte", "H")]
    assert kn.kinds("거래유의종목 및 거래지원 종료 일정 안내") == []                          # sin monedas
    assert kn.kinds("콘엑스(XPLA) 입출금 일시 중지 안내 (11/07 오후 12시~)") == []              # depósitos: nada
    # un cambio de fecha de un aviso anterior no es un aviso nuevo
    assert kn.kinds("썬더코어(TT) 거래지원 종료 안내 (거래지원 종료 일정 변경 안내)") == []
    assert kn.kinds("ThunderCore 네트워크 종료에 따른 썬더코어(TT) 출금 중단(종료) 및 거래지원 종료 예정일 변경 안내 "
                    "(09/01 09:00 ~)") == []
    assert kn.kinds("셀프 체인(SLF) 거래지원 종료 (출금 지원 종료 일시 변경)") == []


def test_cada_regla_con_su_fuente():
    adv, fin = kn.BY_CATEGORY["advertencia_upbit"], kn.BY_CATEGORY["fin_soporte_corto"]
    assert kn.notice("upbit.avisos", "이니시아(INIT) 거래 유의 종목 지정 안내") == [(adv, "INIT")]
    assert kn.notice("bithumb.avisos", "이니시아(INIT) 거래유의종목 지정") == []      # la de Bithumb rinde la mitad
    assert kn.notice("upbit.avisos", "블러(BLUR) 거래지원 종료 안내 (11/30 15:00)") == [(fin, "BLUR")]
    assert kn.notice("bithumb.avisos", "블러(BLUR), 코어(CORE) 거래지원 종료") == [(fin, "BLUR"), (fin, "CORE")]
    assert kn.notice("upbit.avisos", "샌드박스(SAND) 거래 유의 종목 지정 해제 안내") == []   # levantada: sin ventaja
    assert kn.notice("coindesk", "블러(BLUR) 거래지원 종료 안내") == []                       # solo los avisos oficiales
    p = policy.EXITS["advertencia_upbit"]
    assert (p.stop, p.hold_s, p.max_x, p.max_slippage_bps) == (0.10, 4 * 3600, 0.20, 50)       # 2 % al stop
    p = policy.EXITS["fin_soporte_corto"]
    assert (p.stop, p.hold_s, p.max_x, p.max_slippage_bps) == (0.10, 300, 0.10, 50)            # 1 % al stop


GW = {"status": 200, "code": "00000", "message": "success", "data": [
    {"id": 1655562, "categoryName1": "거래지원종료", "categoryName2": None, "title": "블러(BLUR), 코어(CORE) 거래지원 종료",
     "pcUrl": "https://feed.bithumb.com/notice/1655562", "publicationDateTime": "2026-11-09 16:00:00",
     "modifyDateTime": "2026-11-09 15:41:10"},
    {"id": "nope", "title": "sin número", "publicationDateTime": "2026-11-09 15:00:00"},
    {"id": 1655561, "categoryName1": "입출금", "title": "콘엑스(XPLA) 입출금 일시 중지 안내 (11/07 오후 12시~)",
     "pcUrl": "https://evil.example/x", "publicationDateTime": "2026-11-06 17:00:00"}]}


def test_el_lector_de_bithumb_toma_la_hora_de_corea_y_arma_el_enlace_con_el_numero():
    es = an.parse_bithumb(json.dumps(GW, ensure_ascii=False).encode())
    assert [e.guid for e in es] == ["bithumb:1655562", "bithumb:1655561"]              # sin número: afuera
    assert parse_datetime(es[0].published) == calendar.timegm((2026, 11, 9, 7, 0, 0))   # 16:00 en Corea
    assert es[1].link == "https://feed.bithumb.com/notice/1655561"                     # nunca el enlace que trae
    assert es[0].categories == ("거래지원종료",)
    assert an.parse_bithumb(b'{"data": null}') == []
    spec = {s.id: s for s in DEFAULT_SOURCES}["bithumb.avisos"]
    assert spec.enabled and spec.tier == TIER_OFFICIAL and spec.format == "bithumb" and spec.interval_s <= 1
    assert spec.url.startswith("https://gw.bithumb.com/")                               # la de su web: sin caché


async def test_un_aviso_nuevo_de_bithumb_llega_por_la_tuberia_con_su_hora():
    state = {"data": GW["data"][2:]}

    async def handler(request):
        return web.json_response({"status": 200, "data": state["data"]})

    app = web.Application()
    app.router.add_get("/gw", handler)
    clock = ManualClock(calendar.timegm((2026, 11, 9, 7, 0, 1)))
    async with serve(app) as url, aiohttp.ClientSession() as s:
        spec = next(x for x in DEFAULT_SOURCES if x.id == "bithumb.avisos")
        p = PollingSource(replace(spec, url=f"{url}/gw"), s, clock, None)
        items, first = await p.poll_once()
        assert first and len(items) == 1                                              # lo que ya estaba: historia
        state["data"] = GW["data"][:1] + GW["data"][2:]                               # sale el fin del soporte
        items, first = await p.poll_once()
    assert not first and [i.title for i in items] == ["블러(BLUR), 코어(CORE) 거래지원 종료"]
    assert items[0].source == "bithumb.avisos" and items[0].received_at - items[0].published_at == 1.0


class Alerts:
    def __init__(self):
        self.items = []

    def submit(self, alert):
        self.items.append(alert)


class FakeLlm:
    enabled = True

    def __init__(self, signal):
        self.signal, self.budget, self.calls = signal, None, 0

    async def classify(self, title, body, fallback):
        from tradingagent.intelligence.llm import LlmResult
        self.calls += 1
        return LlmResult(self.signal)


def notice(nid, title, source="upbit.avisos", published=T0, received=None):
    it = NewsItem(id=nid, source=source, tier=1, kind="api", title=title, body="", published_at=published,
                  received_at=published + 1 if received is None else received,
                  url=f"https://example.invalid/{nid}")
    return IngestedNews(item=it, age_s=1.0, freshness="fresh", dedup=DedupResult("new", nid),
                        noise=NoiseResult(0, False), accepted=True)


def market(after: float):
    """SOL a 150 hasta el minuto del aviso y a `after` desde el siguiente."""
    return lambda sym, m: (after if m > minute(T0) else 150.0) if sym == "SOLUSDT" else PRICES.get(sym)


async def trader(session, url, clock, home, llm=None):
    t = LiveTrader(BinanceFutures(base=url), session, clock, copy.deepcopy(limits_mod.load()), alerts=Alerts(),
                   home=home, capital=120.0, paper_config=PaperConfig(latency=LatencyModel(median_ms=0)), llm=llm)
    await t.start()
    return t


def decisions(home):
    f = home / "paper-vivo" / "binance-futuros" / "decisiones" / "2026-11.jsonl"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


async def settle(t):
    while t.pending:
        await asyncio.gather(*list(t.pending))


async def run_one(tmp_path, name, ing, *, at, after, llm=None):
    clock = ManualClock(at)
    home = tmp_path / name
    async with serve(FakeBinance(price=market(after), clock=clock).app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, home, llm)
        await t.on_news(ing)
        await settle(t)
        return t, home


async def test_advertencia_de_upbit_corto_con_stop_de_10_y_salida_a_las_4_horas(tmp_path):
    t, home = await run_one(tmp_path, "a", notice("w1", WARNING), at=T0 + 3, after=149.0)   # −0,7 %: no cayó
    pos = t.st["positions"]["SOLUSDT"]
    assert pos["side"] == "corto" and pos["category"] == "advertencia_upbit"
    entry = float(pos["avg_price"])
    assert 1.09 < float(pos["stop"]) / entry < 1.11                                      # stop fijo de 10 %
    assert abs(pos["until"] - (T0 + 3) - 4 * 3600) < 5                                   # sale a las 4 h
    assert entry * float(pos["qty"]) <= 0.20 * 120 + 0.5                                 # 2 % del saldo al stop
    rows = decisions(home)
    got = next(r for r in rows if r["decision"] == "entrar")
    assert got["signal"]["version"] == "advertencia-upbit-1"
    assert got["params"] == {"max_late_s": 30.0, "max_drop": 0.03} and got["aviso"]["demora_s"] == 1.0
    assert any("lo_opera_la_regla_de_avisos_coreanos" in r["motivos"] for r in rows if r["decision"] == "no_entrar")


async def test_advertencia_que_ya_cayo_o_llega_tarde_no_entra(tmp_path):
    t, home = await run_one(tmp_path, "cayo", notice("w2", WARNING), at=T0 + 3, after=145.0)   # −3,3 %
    assert t.st["positions"] == {}
    why = [r["motivos"] for r in decisions(home) if r["signal"].get("category") == "advertencia_upbit"]
    assert why and why[0][0].startswith("ya_cayo:-3.33")
    t, home = await run_one(tmp_path, "tarde", notice("w3", WARNING), at=T0 + 31, after=149.0)
    assert t.st["positions"] == {}
    why = [r["motivos"] for r in decisions(home) if r["signal"].get("category") == "advertencia_upbit"]
    assert why == [["advertencia_upbit_tarde:31.0s"]]


async def test_fin_del_soporte_de_bithumb_corto_con_salida_a_los_5_min(tmp_path):
    ing = notice("b1", END_BITHUMB, source="bithumb.avisos")
    t, home = await run_one(tmp_path, "b", ing, at=T0 + 2, after=147.0)                      # −2 %: entra igual
    pos = t.st["positions"]["SOLUSDT"]
    assert pos["side"] == "corto" and pos["category"] == "fin_soporte_corto"
    assert 1.09 < float(pos["stop"]) / float(pos["avg_price"]) < 1.11
    assert abs(pos["until"] - (T0 + 2) - 300) < 5                                        # sale a los 5 min
    assert float(pos["avg_price"]) * float(pos["qty"]) <= 0.10 * 120 + 0.5              # 1 % del saldo al stop
    assert len(t.st["positions"]) == 1                                                    # FOO no tiene perpetuo
    rows = decisions(home)
    assert all(r["signal"].get("category") == "fin_soporte_corto" for r in rows)          # ni lectura general ni IA


async def test_fin_del_soporte_tarde_o_ya_caido_no_entra(tmp_path):
    t, home = await run_one(tmp_path, "t", notice("e1", END_UPBIT), at=T0 + 6, after=149.0)
    assert t.st["positions"] == {}
    assert [r["motivos"] for r in decisions(home) if r["signal"].get("category") == "fin_soporte_corto"] == [
        ["fin_soporte_corto_tarde:6.0s"]]
    t, home = await run_one(tmp_path, "c", notice("e2", END_UPBIT), at=T0 + 3, after=140.0)   # −6,7 %
    assert t.st["positions"] == {}
    why = [r["motivos"] for r in decisions(home) if r["signal"].get("category") == "fin_soporte_corto"]
    assert why and why[0][0].startswith("ya_cayo:-6.67")


async def test_el_mismo_fin_del_soporte_de_bithumb_y_de_upbit_abre_una_sola_vez(tmp_path):
    """Decisión conjunta (BLUR, SOPH, ZIL... en el estudio): Bithumb publica primero o junto. El segundo aviso no
    abre otra vez ni lanza otra segunda lectura que pueda cerrar la del primero."""
    clock = ManualClock(T0 + 2)
    llm = FakeLlm(Signal("deslistado", -0.8, 0.7, ("SOL",), 120))                         # la IA confirma
    home = tmp_path / "j"
    async with serve(FakeBinance(price=market(148.0), clock=clock).app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, home, llm)
        await t.on_news(notice("j1", END_BITHUMB, source="bithumb.avisos"))
        await settle(t)
        clock.advance(2)
        await t.on_news(notice("j2", END_UPBIT, published=T0 + 1, received=T0 + 3))      # Telegram, 1 s después
        await settle(t)
        assert list(t.st["positions"]) == ["SOLUSDT"] and t.st["positions"]["SOLUSDT"]["event_id"] == "j1"
    rows = [r for r in decisions(home) if r["signal"].get("category") == "fin_soporte_corto"]
    assert [r["decision"] for r in rows] == ["entrar", "no_entrar"] and "ya_en_cartera" in rows[1]["motivos"]
    ab = (home / "paper-vivo" / "binance-futuros" / "variante-ia.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(ab) == 1 and json.loads(ab[0])["evento"] == "j1"                            # una sola segunda lectura


async def test_el_fin_del_soporte_no_frena_por_el_libro_y_la_advertencia_si(tmp_path):
    """El fin del soporte cae en segundos (como un listado salta): sin las alarmas del libro, anotadas. La advertencia
    baja de a poco: las mira."""
    async def one(name, ing):
        clock = ManualClock(T0 + 3)
        home = tmp_path / name
        fake = FakeBinance(price=market(149.0), clock=clock, spread_bps=80.0)      # spread de 80 pb (> 30 del sobre)
        async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
            t = await trader(s, url, clock, home)
            await t.on_news(ing)
            await settle(t)
            return t, home
    t, home = await one("fin", notice("r1", END_UPBIT))
    assert "SOLUSDT" in t.st["positions"]
    got = next(r for r in decisions(home) if r["decision"] == "entrar")
    assert any(a.startswith("spread:") for a in got["analisis"]["alarmas_del_libro"])
    t, home = await one("adv", notice("r2", WARNING))
    assert t.st["positions"] == {}
    why = [r["motivos"] for r in decisions(home) if r["signal"].get("category") == "advertencia_upbit"]
    assert why and any(m.startswith("spread:") for m in why[0])


async def test_si_la_ia_no_confirma_la_advertencia_se_cierra(tmp_path):
    llm = FakeLlm(Signal("otro", 0.3, 0.6, ("SOL",), 60))                                 # la IA lee otra cosa
    t, home = await run_one(tmp_path, "ia", notice("w4", WARNING), at=T0 + 3, after=149.0, llm=llm)
    assert t.st["positions"] == {} and llm.calls >= 1
    f = home / "paper-vivo" / "binance-futuros" / "operaciones" / "2026-11.jsonl"
    exits = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if '"salida"' in x]
    assert exits and exits[0]["motivo"] == "la_ia_no_confirma"


async def test_lo_demas_de_bithumb_no_se_lee_ni_gasta_ia(tmp_path):
    llm = FakeLlm(Signal("deslistado", -0.8, 0.7, ("SOL",), 120))
    t, home = await run_one(tmp_path, "n", notice("n1", "솔라나(SOL) 거래유의종목 지정", source="bithumb.avisos"),
                            at=T0 + 2, after=149.0, llm=llm)
    assert t.st["positions"] == {} and llm.calls == 0 and decisions(home) == []


def test_el_tablero_muestra_los_dos_modulos_con_las_constantes_del_codigo(tmp_path):
    mods = {m["id"]: m for m in modulos.catalog(tmp_path)}
    adv, fin, dsl = mods["advertencia_upbit"], mods["fin_soporte_corto"], mods["deslistado_corto"]
    assert ["Llega a tiempo", "hasta 30 s del mensaje"] in adv["parametros"]
    assert ["Freno de precio", "no entra si ya cayó más de 3 %"] in adv["parametros"]
    assert ["Salida", "a las 4 h"] in adv["parametros"] and ["Tamaño", "0,2 veces el saldo: 2 % al stop"] in adv["parametros"]
    assert ["Llega a tiempo", "hasta 5 s de publicado"] in fin["parametros"]
    assert ["Salida", "a los 5 min"] in fin["parametros"] and ["Tamaño", "0,1 veces el saldo: 1 % al stop"] in fin["parametros"]
    assert ["Tope de la orden", "2 % del mejor precio"] in dsl["parametros"]               # el tope ensanchado
    assert ["Tope de la orden", "0,5 % del mejor precio"] in fin["parametros"]
