from tradingagent.core.events import TIER_MEDIA, TIER_OFFICIAL, TIER_UNVERIFIED, NewsItem, stable_id
from tradingagent.ingestion.dedup import DedupIndex
from tradingagent.ingestion.noise import NoiseFilter


def item(title, source="a", url="", body="", tier=TIER_MEDIA):
    return NewsItem(id=stable_id(source, url or title), source=source, tier=tier, kind="rss", title=title,
                    url=url, body=body)


def test_duplicado_exacto_por_url_y_por_titulo(clock):
    d = DedupIndex(clock)
    r1 = d.check(item("Binance halts withdrawals after exploit", "a", "https://x.com/1?utm_source=t"))
    assert r1.kind == "new"
    r2 = d.check(item("Different title", "b", "https://www.x.com/1"))
    assert r2.kind == "exact" and r2.cluster_id == r1.cluster_id and r2.distinct_sources == 2
    r3 = d.check(item("Binance Halts Withdrawals After Exploit - The Block", "c"))
    assert r3.kind == "exact" and r3.distinct_sources == 3


def test_casi_duplicado_agrupa_y_cuenta_fuentes_distintas(clock):
    d = DedupIndex(clock)
    a = d.check(item("SEC sues Binance and CEO Changpeng Zhao over securities violations", "coindesk"))
    b = d.check(item("SEC sues Binance, CEO Changpeng Zhao over alleged securities violations", "theblock"))
    assert b.kind == "near" and b.cluster_id == a.cluster_id and b.similarity >= 0.6
    assert b.distinct_sources == 2 and b.first_source == "coindesk"


def test_historias_distintas_no_se_fusionan(clock):
    d = DedupIndex(clock)
    d.check(item("SEC sues Binance over securities violations", "a"))
    for t in ("SEC sues Coinbase over securities violations",       # otra empresa: otro evento
              "Fed raises rates by 25 basis points",
              "Binance lists new token pairs for trading"):
        assert d.check(item(t, "b")).kind == "new", t


def test_ventana_ttl_y_capacidad(clock):
    d = DedupIndex(clock, ttl_s=60, max_items=3)
    d.check(item("Ethereum upgrade goes live on mainnet today", "a"))
    clock.advance(61)
    assert d.check(item("Ethereum upgrade goes live on mainnet today", "b")).kind == "new"   # venció
    for i in range(5):
        d.check(item(f"Unrelated headline number {i} about markets moving", "a", f"https://x/{i}"))
    assert len(d.entries) <= 3 and d.stats()["evicted"] >= 3
    assert all(len(ids) > 0 for ids in d.buckets.values())                    # sin baldes huérfanos


def test_mismo_item_dos_veces_no_suma_fuente(clock):
    d = DedupIndex(clock)
    it = item("Bitcoin ETF sees record inflows this week", "a", "https://x/e")
    d.check(it)
    r = d.check(it)
    assert r.kind == "exact" and r.distinct_sources == 1 and r.cluster_size == 1


def test_ruido_con_motivos_y_noticias_reales_pasan():
    f = NoiseFilter()
    malas = ["XRP price prediction: could XRP hit $10 by December?",
             "Top 5 altcoins to buy before the next bull run",
             "This 100x gem presale is selling out fast!!",
             "Weekly recap: the biggest crypto stories of the week",
             "Podcast: what's next for DeFi"]
    for t in malas:
        r = f.score(item(t))
        assert r.is_noise and r.reasons, t
    buenas = ["Binance halts withdrawals after $40 million hot wallet exploit",
              "Federal Reserve cuts rates by 50 basis points, citing labor market",
              "SEC approves spot Ether ETF options trading on Nasdaq"]
    for t in buenas:
        assert not f.score(item(t)).is_noise, t


def test_oficial_nunca_es_ruido_y_no_verificado_penaliza():
    f = NoiseFilter()
    assert not f.score(item("Top 5 things", tier=TIER_OFFICIAL)).is_noise
    m = f.score(item("Shocking: here's why bitcoin dumped", tier=TIER_MEDIA))
    u = f.score(item("Shocking: here's why bitcoin dumped", tier=TIER_UNVERIFIED))
    assert u.score > m.score
    assert f.score(item("", tier=TIER_OFFICIAL)).reasons == ("titulo_vacio",)


def test_dos_avisos_con_la_misma_plantilla_y_otra_moneda_no_se_fusionan(clock):
    """6-nov: dos listados de Upbit con la misma plantilla (0,62 de parecido) se fusionaban y el segundo se perdía.
    Con otros tickers, u otro enlace del mismo aviso oficial, son dos noticias; la misma historia en otro medio, una."""
    d = DedupIndex(clock, ttl_s=6 * 3600, threshold=0.6)

    def upbit(nid, coin, name):
        return NewsItem(id=nid, source="upbit.avisos", tier=1, kind="telegram",
                        title=f"{name}({coin}) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)",
                        url=f"https://upbit.com/service_center/notice?id={nid}", published_at=clock.time(),
                        received_at=clock.time())

    assert d.check(upbit("a1", "SOL", "솔라나")).kind == "new"
    assert d.check(upbit("a2", "ETH", "이더리움")).kind == "new"                  # otro listado: no se pierde
    same = upbit("a1", "SOL", "솔라나")
    assert d.check(NewsItem(id="tg-a1", source="upbit.avisos", tier=1, kind="relay", title=same.title, url=same.url,
                            published_at=clock.time(), received_at=clock.time())).kind == "exact"   # Telegram y sitio
    b1 = NewsItem(id="b1", source="binance.ws", tier=1, kind="ws", title="Binance Will Delist ALPACA, PDA on 2026-11-20",
                  url="", published_at=clock.time(), received_at=clock.time())
    b2 = NewsItem(id="b2", source="binance.listados", tier=1, kind="api",
                  title="Binance Will Delist ALPACA, PDA on 2026-11-20 (actualizado)", url="",
                  published_at=clock.time(), received_at=clock.time())
    b3 = NewsItem(id="b3", source="binance.ws", tier=1, kind="ws", title="Binance Will Delist KDA, LOOM on 2026-11-20",
                  url="", published_at=clock.time(), received_at=clock.time())
    assert d.check(b1).kind == "new" and d.check(b2).kind == "near"              # el mismo aviso por otro canal
    assert d.check(b3).kind == "new"                                            # otro deslistado, otras monedas

