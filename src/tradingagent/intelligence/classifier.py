"""Clasificador determinista de noticias (etapa 2, capa rápida): categoría, polaridad, confianza, activos
y horizonte, en microsegundos y sin red.

- Reglas auditables: cada señal dice qué reglas la dispararon (`rules`).
- La confianza es una prior razonada; se calibra con los resultados reales que junta el recolector
  (REGLAS C-1 y C-5).
- La certeza (0..1) es la parte de la confianza que habla de si el hecho es firme: rumor, desmentido,
  señales encontradas, solo en el cuerpo o "ya se movió" la bajan. Escala la polaridad P del vector de
  evento (intelligence/vector.py); el nivel de la fuente y la corroboración van a la importancia I.
- El texto es un dato a clasificar, nunca una instrucción (C-2): ninguna frase de la noticia cambia cómo
  se decide.
- Los activos salen del texto (grounding): si no hay ninguno y la noticia es de mercado (macro,
  regulación, ETF), se usa BTC como referencia, con menos confianza.
"""
import re
from dataclasses import asdict, dataclass

from ..research import assets as asset_mod

VERSION = "reglas-2"


@dataclass(frozen=True)
class Signal:
    category: str
    polarity: float              # -1 (muy negativa) .. +1 (muy positiva) para los activos de la señal
    confidence: float            # 0..1 (prior; la calibración la reemplaza por una probabilidad medida)
    assets: tuple[str, ...]
    horizon_min: int
    rules: tuple[str, ...] = ()
    version: str = VERSION
    certainty: float = 1.0       # 0..1: firmeza del hecho (rumores, desmentidos, señales encontradas...)
    # Lo que lee la IA en el texto (0-10; None = sin IA): cuánto es ESE activo el protagonista, la gravedad del
    # hecho y el tamaño esperado del movimiento. El vector toma el menor entre esto y el a priori (vector.py).
    relevance: float | None = None
    importance: float | None = None
    impact: float | None = None

    @property
    def conviction(self) -> float:
        return abs(self.polarity) * self.confidence

    def as_dict(self) -> dict:
        d = asdict(self)
        d["assets"], d["rules"] = list(self.assets), list(self.rules)
        d["polarity"], d["confidence"] = round(self.polarity, 3), round(self.confidence, 3)
        d["certainty"] = round(self.certainty, 3)
        return d


@dataclass(frozen=True)
class Rule:
    name: str
    category: str
    polarity: float
    confidence: float
    horizon_min: int
    pattern: re.Pattern


def _r(p: str) -> re.Pattern:
    return re.compile(p, re.IGNORECASE)


RULES = (
    # -- negativas -------------------------------------------------------------------------------
    Rule("hackeo", "hack", -0.8, 0.6, 120, _r(
        r"\b(hack(s|ed|er|ers)?|exploit(s|ed)?|drain(s|ed)?|stolen|breach(es|ed)?|compromised|rug[- ]?pull(s|ed)?)\b")),
    Rule("deslistado", "deslistado", -0.7, 0.6, 120, _r(
        r"\b(delist(s|ed|ing)?|will remove .{0,30}trading|suspend(s|ed)? (trading|deposits|withdrawals))\b")),
    Rule("quiebra", "quiebra", -0.8, 0.6, 240, _r(
        r"\b(bankrupt(cy)?|insolven(t|cy)|chapter 11|(halt(s|ed)?|freez(e|es)|froze) withdrawals|collaps(e|es|ed))\b")),
    Rule("demanda", "regulatorio", -0.5, 0.5, 120, _r(
        r"\b(sues|sued|lawsuit|charges|charged|indict(ed|ment)|fined|penalt(y|ies)|crackdown|subpoena(s|ed)?|"
        r"wells notice|enforcement action|bans?|banned)\b")),
    Rule("etf_negativo", "etf", -0.5, 0.5, 120, _r(
        r"\bETFs?\b.{0,60}\b(reject(s|ed)?|den(y|ies|ied)|delay(s|ed)?|postpone(s|d)?|outflows?)\b|"
        r"\b(reject(s|ed)?|den(y|ies|ied)|delay(s|ed)?)\b.{0,40}\bETFs?\b")),
    Rule("macro_duro", "macro", -0.4, 0.45, 120, _r(
        r"\b(rate hikes?|raises? (interest )?rates|hikes? rates|hawkish|hotter[- ]than[- ]expected|"
        r"inflation (rises|rose|accelerat\w*|jumps?|jumped))\b")),
    Rule("desbloqueo", "desbloqueo", -0.3, 0.4, 240, _r(r"\btoken unlocks?\b|\bunlock(s|ing)? .{0,30}tokens?\b")),
    Rule("caida_red", "caida_red", -0.4, 0.45, 120, _r(
        r"\b(outage|network halt(s|ed)?|chain halt(s|ed)?|halts? block production|downtime)\b")),
    # -- positivas -------------------------------------------------------------------------------
    Rule("listado", "listado", 0.6, 0.55, 60, _r(
        r"\b(will list|lists|to list|listing of|adds? (trading|support) for|launch(es|ed)? (spot )?trading (for|of))\b")),
    Rule("etf_positivo", "etf", 0.6, 0.55, 120, _r(
        r"\bETFs?\b.{0,60}\b(approv(es|ed|al)|greenlight(s|ed)?|launch(es|ed)?|record inflows|inflows)\b|"
        r"\b(approv(es|ed)|greenlight(s|ed)?)\b.{0,40}\bETFs?\b")),
    Rule("regulatorio_favorable", "regulatorio", 0.4, 0.45, 120, _r(
        r"\b(dismiss(es|ed)|drops? (the )?(case|lawsuit|charges)|wins? (the )?(case|lawsuit)|"
        r"ends? (the |its )?(case|lawsuit|investigation|probe)|legal clarity|settle(s|d|ment))\b")),
    Rule("macro_blando", "macro", 0.4, 0.45, 120, _r(
        r"\b(rate cuts?|cuts? (interest )?rates|dovish|cooler[- ]than[- ]expected|"
        r"inflation (eases|eased|cools|cooled|slows|slowed))\b")),
    Rule("tesoreria", "tesoreria", 0.4, 0.45, 120, _r(
        r"\b(buys|bought|acquires|acquired|adds|added|purchases|purchased)\b.{0,40}\b(bitcoin|btc|ether|eth|solana)\b|"
        r"\b(strategic|treasury) (bitcoin|crypto) reserve\b")),
    Rule("adopcion", "adopcion", 0.25, 0.35, 60, _r(
        r"\b(partners? with|partnership|integrat(es|ion|ed)|adopts?|accepts? (bitcoin|crypto) payments?)\b")),
    Rule("quema", "quema", 0.25, 0.35, 120, _r(r"\b(token burn|burns? .{0,20}tokens?|buybacks?)\b")),
    Rule("mejora_red", "mejora_red", 0.25, 0.35, 120, _r(
        r"\b(mainnet|upgrade|hard fork)\b.{0,30}\b(goes live|went live|launch(es|ed)?|activat(es|ed))\b")),
)
# Un movimiento de precio ya ocurrido es reacción, no causa: no se opera (ya está en el precio).
PRICE_MOVE = _r(r"\b(price|rall(y|ies|ied)|surg(e|es|ed)|jump(s|ed)?|soar(s|ed)?|plung(e|es|ed)|tumbl(e|es|ed)|"
                r"slump(s|ed)?|drop(s|ped)?|fall(s|en)?|fell|climb(s|ed)?|gain(s|ed)?|slid(e|es)?)\b.{0,40}\d+(\.\d+)?\s?%")
UNCERTAIN = re.compile(r"\b(?i:reportedly|rumou?rs?|rumou?red|sources say|considering|weighs|mulls|plans? to|"
                       r"expected to|proposal|proposes|potentially|might|could)\b|\bmay\b")
DENIAL = _r(r"\b(denies|denied|false|fake|not true|no plans|rejects? (reports|rumou?rs)|debunk(s|ed)?)\b")
EXCHANGES = _r(r"\b(binance|coinbase|kraken|upbit|okx|bybit|robinhood|bithumb)\b")
MARKET_WIDE = {"macro", "regulatorio", "etf", "tesoreria", "quiebra"}
NOT_TRADABLE = {"precio", "otro"}


def classify(title: str, body: str = "", *, tier: int = 2, cluster_sources: int = 1,
             hint_assets: tuple[str, ...] = ()) -> Signal:
    title = title or ""
    text = f"{title}. {(body or '')[:600]}"
    found = asset_mod.detect(title) or asset_mod.detect(text) or [a for a in hint_assets if a in asset_mod.ASSETS]
    hits = [r for r in RULES if r.pattern.search(text)]
    in_title = {r.name for r in RULES if r.pattern.search(title)}
    if not hits:
        return Signal("precio" if PRICE_MOVE.search(title) else "otro", 0.0, 0.0, tuple(found), 0)
    primary = max(hits, key=lambda r: (r.name in in_title, abs(r.polarity) * r.confidence))
    polarity, confidence = primary.polarity, primary.confidence
    certainty = 1.0
    pos = sum(r.polarity for r in hits if r.polarity > 0)
    neg = sum(r.polarity for r in hits if r.polarity < 0)
    if pos and neg:                                      # señales encontradas: se compensan y bajan la confianza
        polarity = max(-1.0, min(1.0, pos + neg)) or primary.polarity * 0.25
        certainty *= 0.6
    if primary.name not in in_title:
        certainty *= 0.7                                  # solo en el cuerpo: menos peso que en el titular
    if UNCERTAIN.search(title):
        certainty *= 0.6
    if DENIAL.search(title):                             # "desmiente el hackeo": efecto chico y al revés
        polarity, certainty = polarity * -0.5, certainty * 0.6
    if PRICE_MOVE.search(title):
        certainty *= 0.5                                  # ya se movió: lo que queda es menos
    confidence *= certainty
    if tier == 1:
        confidence *= 1.25
    elif tier >= 3:
        confidence *= 0.7
    if cluster_sources >= 2:
        confidence *= 1 + 0.1 * min(cluster_sources - 1, 3)
    if primary.category in ("listado", "deslistado") and EXCHANGES.search(title):
        confidence *= 1.2
    assets = tuple(found)
    if not assets:
        assets = (asset_mod.MARKET_PROXY,)
        confidence *= 0.8 if primary.category in MARKET_WIDE else 0.3
    return Signal(primary.category, round(polarity, 3), round(min(1.0, confidence), 3), assets,
                  primary.horizon_min, tuple(r.name for r in hits), certainty=round(certainty, 3))
