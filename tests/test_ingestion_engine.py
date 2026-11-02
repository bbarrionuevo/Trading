import asyncio

from conftest import JournalSpy
from tradingagent.core.bus import EventBus
from tradingagent.core.events import TIER_MEDIA, NewsItem, stable_id
from tradingagent.ingestion.dedup import DedupIndex
from tradingagent.ingestion.engine import TOPIC_ACCEPTED, TOPIC_CORROBORATION, TOPIC_REJECTED, IngestionEngine
from tradingagent.ingestion.noise import NoiseFilter
from tradingagent.ingestion.replay import replay


def engine(clock, journal=None):
    bus = EventBus()
    subs = {t: bus.subscribe(t, "test", maxsize=100) for t in (TOPIC_ACCEPTED, TOPIC_REJECTED, TOPIC_CORROBORATION)}
    return IngestionEngine(bus, clock, DedupIndex(clock), NoiseFilter(), journal=journal), subs


def news(clock, title, source="coindesk", age=10.0, url=""):
    return NewsItem(id=stable_id(source, url or title), source=source, tier=TIER_MEDIA, kind="rss", title=title,
                    url=url, published_at=clock.time() - age, received_at=clock.time())


async def test_fresca_se_acepta_vieja_ruido_y_duplicado_se_rechazan_con_motivo(clock):
    j = JournalSpy()
    eng, subs = engine(clock, j)
    ok = await eng.handle(news(clock, "Binance halts withdrawals after hot wallet exploit"), max_age_s=300)
    assert ok.accepted and ok.freshness == "fresh" and ok.age_s == 10.0
    stale = await eng.handle(news(clock, "Fed signals pause in rate hikes this year", age=1200), max_age_s=300)
    assert not stale.accepted and "stale" in stale.reasons
    noise = await eng.handle(news(clock, "Top 5 coins to buy before the halving"))
    assert not noise.accepted and "ruido" in noise.reasons
    dup = await eng.handle(news(clock, "Binance halts withdrawals after hot-wallet exploit", source="theblock"))
    assert not dup.accepted and dup.reasons == ("duplicado_exact",)
    assert subs[TOPIC_ACCEPTED].queue.qsize() == 1 and subs[TOPIC_REJECTED].queue.qsize() == 3
    corr = subs[TOPIC_CORROBORATION].get_nowait()
    assert corr.cluster_id == ok.dedup.cluster_id and corr.distinct_sources == 2
    assert [k for k, _ in j.records] == ["news"] * 4 and j.records[0][1]["accepted"] is True


async def test_sin_hora_cuenta_como_vista_ahora_salvo_en_la_historia(clock):
    eng, subs = engine(clock)
    it = NewsItem(id="x1", source="fed.press", tier=1, kind="rss", title="FOMC statement released today",
                  received_at=clock.time())
    ev = await eng.handle(it)
    assert ev.accepted and ev.freshness == "unknown" and ev.age_s is None
    old = NewsItem(id="x2", source="fed.press", tier=1, kind="rss", title="Older press release from archive",
                   received_at=clock.time())
    ev2 = await eng.handle(old, backlog=True)
    assert not ev2.accepted and ev2.reasons == ("backlog",)


async def test_hora_futura_se_marca_y_no_se_descarta(clock):
    eng, _ = engine(clock)
    it = news(clock, "Exchange announces delisting of three tokens", age=-3600)
    ev = await eng.handle(it)
    assert ev.freshness == "future" and ev.accepted


async def test_reproduccion_avanza_el_reloj_y_mide_edad_de_llegada(clock):
    eng, subs = engine(clock)
    t0 = clock.time()
    rows = [{"title": "ETF approval sparks rally in Ether futures", "published_at": t0 + 60, "delay_s": 4},
            {"title": "Old story that arrives way too late here", "published_at": t0 + 70, "delay_s": 900}]
    evs = await replay(rows, eng, clock)
    assert evs[0].accepted and evs[0].age_s == 4.0
    assert not evs[1].accepted and "stale" in evs[1].reasons
    assert clock.time() == t0 + 970


async def test_la_evaluacion_no_bloquea_el_bucle(clock):
    """Mil noticias seguidas: cada evaluación es sub-milisegundo y otra tarea sigue corriendo."""
    eng, _ = engine(clock)
    ticks = 0

    async def other():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    t = asyncio.create_task(other())
    for i in range(1000):
        await eng.handle(news(clock, f"Distinct market headline {i} with unique words w{i}x{i * 7}"))
        if i % 50 == 0:
            await asyncio.sleep(0)
    t.cancel()
    assert eng.stats()["eval_p99_ms"] < 5 and ticks >= 15
