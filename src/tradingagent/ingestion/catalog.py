"""Catálogo de fuentes por defecto. Cada una se puede apagar o ajustar en TRADING_HOME/config.yaml.

Nivel 1 (oficial): comunicados de la Fed, la SEC y presentaciones 8-K de EDGAR (la SEC exige una
cabecera User-Agent con nombre y contacto: se toma de SEC_USER_AGENT y sin ella la fuente queda
apagada). Nivel 2: medios cripto establecidos. Las URLs se verifican en la PC con
`python -m tradingagent sources --check` (desde la nube de desarrollo esos hosts están bloqueados).

Latencia: un RSS sondeado cada 30-60 s llega minutos tarde frente a un feed pago de baja latencia.
Para eso existe el receptor de webhooks (ingestion/webhook.py): cualquier feed rápido se enchufa ahí.
"""
import os
from dataclasses import replace

from ..core.events import TIER_MEDIA, TIER_OFFICIAL
from .sources import SourceSpec

EDGAR_8K = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include"
            "&start=0&count=40&output=atom")

DEFAULT_SOURCES = (
    SourceSpec("fed.press", "https://www.federalreserve.gov/feeds/press_all.xml", tier=TIER_OFFICIAL,
               interval_s=60, max_age_s=900, category="macro", assets=("BTC", "ETH")),
    SourceSpec("fed.monetary", "https://www.federalreserve.gov/feeds/press_monetary.xml", tier=TIER_OFFICIAL,
               interval_s=30, max_age_s=900, category="macro", assets=("BTC", "ETH")),
    SourceSpec("sec.press", "https://www.sec.gov/news/pressreleases.rss", tier=TIER_OFFICIAL,
               interval_s=60, max_age_s=900, category="regulatorio", requires_env=("SEC_USER_AGENT",)),
    SourceSpec("sec.edgar.8k", EDGAR_8K, tier=TIER_OFFICIAL, interval_s=60, max_age_s=900, category="filings",
               requires_env=("SEC_USER_AGENT",)),
    SourceSpec("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", tier=TIER_MEDIA,
               interval_s=45, max_age_s=300, category="cripto"),
    SourceSpec("cointelegraph", "https://cointelegraph.com/rss", tier=TIER_MEDIA,
               interval_s=45, max_age_s=300, category="cripto"),
    SourceSpec("decrypt", "https://decrypt.co/feed", tier=TIER_MEDIA, interval_s=60, max_age_s=300,
               category="cripto"),
    SourceSpec("theblock", "https://www.theblock.co/rss.xml", tier=TIER_MEDIA, interval_s=60, max_age_s=300,
               category="cripto"),
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
