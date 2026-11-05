"""Catálogo de fuentes por defecto. Cada una se puede apagar o ajustar en TRADING_HOME/config.yaml.

Nivel 1 (oficial): comunicados de la Fed, la SEC y presentaciones 8-K de EDGAR (la SEC exige una
cabecera User-Agent con nombre y contacto: se toma de SEC_USER_AGENT y sin ella la fuente queda
apagada). Nivel 2: medios cripto establecidos y de bolsa (CNBC, Seeking Alpha, Investing). Las URLs se verifican en la PC con
`python -m tradingagent sources --check` (desde la nube de desarrollo esos hosts están bloqueados).

Latencia: un RSS sondeado cada 30-60 s llega minutos tarde frente a un feed pago de baja latencia.
Para eso existe el receptor de webhooks (ingestion/webhook.py): cualquier feed rápido se enchufa ahí.
"""
import itertools
import os
from dataclasses import replace

from ..core.events import TIER_MEDIA, TIER_OFFICIAL
from .announcements import BINANCE_LISTINGS, BITGET_LISTINGS, OKX_LISTINGS, UPBIT_NOTICES
from .sources import SourceSpec

# 100 por página (en el pico de los resultados se presentan decenas de 8-K por minuto) y cada 20 s: el 8-K de
# resultados (ítem 2.02) dispara la entrada con impulso a los 2 minutos (decision/earnings.py).
EDGAR_8K = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include"
            "&start=0&count=100&output=atom")
# PR Newswire, canal de resultados (medido desde la PC el 4-nov: responde a lectores; su CDN guarda copia 60 s).
# GlobeNewswire, Business Wire (su página) y Accesswire bloquean a los programas: no se usan.
PRN_EARNINGS = "https://www.prnewswire.com/rss/financial-services-latest-news/earnings-list.rss"


def binance_variants(sizes=(10, 15, 20)) -> tuple[str, ...]:
    """La misma lista de anuncios de Binance pedida de 72 formas (orden de los parámetros y tamaño de página). El
    CDN (CloudFront) guarda cada forma ~120 s: con una sola URL, un listado llegaba en promedio ~60 s tarde
    (medido en la PC el 4-nov: la "edad" de la copia iba de 10 a 115 s). Rotando 72 formas cada 3 s, cada una
    vuelve a pedirse a los ~216 s, ya vencida: la respuesta sale del servidor de Binance, fresca. CloudFront solo
    acepta tamaños 5, 10, 15, 20 y 50 (otro valor o un parámetro extra da HTTP 400)."""
    base, params = BINANCE_LISTINGS.split("?")[0], ["type=1", "catalogId=48", "pageNo=1"]
    return tuple(f"{base}?{'&'.join(order)}" for size in sizes
                 for order in itertools.permutations([*params, f"pageSize={size}"]))

CNBC = "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id={id}"

DEFAULT_SOURCES = (
    SourceSpec("fed.press", "https://www.federalreserve.gov/feeds/press_all.xml", tier=TIER_OFFICIAL,
               interval_s=60, max_age_s=600, category="macro", assets=("BTC", "ETH")),
    SourceSpec("fed.monetary", "https://www.federalreserve.gov/feeds/press_monetary.xml", tier=TIER_OFFICIAL,
               interval_s=30, max_age_s=600, category="macro", assets=("BTC", "ETH")),
    SourceSpec("sec.press", "https://www.sec.gov/news/pressreleases.rss", tier=TIER_OFFICIAL,
               interval_s=60, max_age_s=600, category="regulatorio", requires_env=("SEC_USER_AGENT",)),
    # La SEC a veces tarda (medido el 5-nov: 0,4 a 8 s con la misma página, y 64 respuestas de más de 10 s en 14 h).
    # Una demora no es una caída: se espera hasta 25 s y se reintenta como mucho al minuto (con la espera que se
    # duplica, 4 fallas seguidas dejaban ~10 min sin la SEC, y los resultados se operan a los 2 min del 8-K).
    SourceSpec("sec.edgar.8k", EDGAR_8K, tier=TIER_OFFICIAL, interval_s=20, max_age_s=600, category="filings",
               requires_env=("SEC_USER_AGENT",), timeout_s=25, max_backoff_s=60, open_after=20, cooldown_s=120),
    SourceSpec("prn.resultados", PRN_EARNINGS, tier=TIER_OFFICIAL, interval_s=30, max_age_s=600, category="bolsa"),
    SourceSpec("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", tier=TIER_MEDIA,
               interval_s=45, max_age_s=300, category="cripto"),
    SourceSpec("cointelegraph", "https://cointelegraph.com/rss", tier=TIER_MEDIA,
               interval_s=45, max_age_s=300, category="cripto"),
    SourceSpec("decrypt", "https://decrypt.co/feed", tier=TIER_MEDIA, interval_s=60, max_age_s=300,
               category="cripto"),
    SourceSpec("theblock", "https://www.theblock.co/rss.xml", tier=TIER_MEDIA, interval_s=60, max_age_s=300,
               category="cripto"),
    # Bolsa, materias primas y macro (para operar acciones, índices, oro y petróleo en MEXC futuros). Verificadas
    # el 3-nov-2026: publican al minuto y con hora; MarketWatch quedó congelado en 2025 (no va).
    SourceSpec("cnbc.top", CNBC.format(id=100003114), tier=TIER_MEDIA, interval_s=45, max_age_s=300,
               category="bolsa"),
    SourceSpec("cnbc.earnings", CNBC.format(id=15839135), tier=TIER_MEDIA, interval_s=45, max_age_s=300,
               category="bolsa"),
    SourceSpec("seekingalpha", "https://seekingalpha.com/market_currents.xml", tier=TIER_MEDIA, interval_s=45,
               max_age_s=300, category="bolsa"),
    SourceSpec("investing", "https://www.investing.com/rss/news.rss", tier=TIER_MEDIA, interval_s=60,
               max_age_s=300, category="bolsa"),
    # Anuncio OFICIAL de listados de Binance (API pública en JSON): la noticia que más mueve un precio. Estudio del
    # 3-nov (docs/ESTUDIO-LISTADOS.md): entrando al minuto gana, a los 5 minutos ya no. Cada 3 s y rotando 72
    # formas de la URL para saltar la copia de ~120 s del CDN (binance_variants).
    SourceSpec("binance.listados", BINANCE_LISTINGS, tier=TIER_OFFICIAL, interval_s=3, max_age_s=600,
               category="listado", format="binance", url_variants=binance_variants()),
    # Avisos de mercado de Upbit (en coreano). Estudio del 4-nov (180 días, en MEXC): listado nuevo +13,5 % a 5 min
    # (21 de 21 suben), alta en wones +20,9 % (14 de 14), casi todo dentro del primer minuto. Cada 8 s: Upbit
    # (Cloudflare) limita a ~10 pedidos por minuto (PC, 4-nov: cada 2 s, HTTP 429 en uno de cada tres; cada 5 s, uno
    # por minuto; cada 10 s, ninguno). En Binance entrar a los 10-20 s del aviso rinde igual o mejor que a los 5 s (el
    # primer pico se devuelve). Desde la PC solo anda con el almacén de certificados de Windows (red del 3-nov).
    SourceSpec("upbit.avisos", UPBIT_NOTICES, tier=TIER_OFFICIAL, interval_s=8, max_age_s=600, category="listado",
               format="upbit", os_trust=True),
    # OKX y Bitget quedan apagadas: en el mismo estudio, los listados de OKX no movieron la moneda en MEXC (mediana
    # ≈ 0 % a 5, 15 y 60 min) y los de Bitget son casi todos de acciones. Se prenden en config.yaml si cambia.
    SourceSpec("okx.listados", OKX_LISTINGS, tier=TIER_OFFICIAL, interval_s=20, max_age_s=600, category="listado",
               format="okx", enabled=False),
    SourceSpec("bitget.listados", BITGET_LISTINGS, tier=TIER_OFFICIAL, interval_s=30, max_age_s=600,
               category="listado", format="bitget", enabled=False),
)


def resolve(spec: SourceSpec, env=None) -> tuple[SourceSpec, str | None]:
    """(spec con cabeceras completas, motivo por el que queda apagada o None)."""
    env = os.environ if env is None else env
    if not spec.enabled:
        return spec, "apagada en la configuración"
    missing = [k for k in spec.requires_env if not env.get(k)]
    if missing:
        return spec, f"falta {', '.join(missing)} en TRADING_HOME/.env"
    if "SEC_USER_AGENT" in spec.requires_env:
        spec = replace(spec, headers={**spec.headers, "User-Agent": env["SEC_USER_AGENT"]})
    return spec, None
