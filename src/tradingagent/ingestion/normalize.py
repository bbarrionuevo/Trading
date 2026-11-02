"""Normalización de noticias: texto limpio, URL canónica, fechas en UTC y claves para deduplicar."""
import html
import re
import unicodedata
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {"fbclid", "gclid", "dclid", "mc_cid", "mc_eid", "ref", "ref_src", "cmpid", "igshid", "spm",
                   "src", "source", "ito", "taid", "yclid", "_ga", "guccounter"}
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s$%.]+", re.UNICODE)
# Sufijos de marca que los medios agregan al título ("... - CoinDesk", "... | Reuters").
_BRAND_SUFFIX = re.compile(r"\s+[-|–—]\s+[\w .&']{2,40}$")
STOPWORDS = frozenset("""a an and are as at be been by for from has have in into is it its of on or that the
their this to was were will with after amid over says said new not but than up down out off per via""".split())


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "iframe", "svg"}
    BLOCK = {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def clean_text(s: str | None, max_len: int = 4000) -> str:
    """HTML → texto plano, entidades resueltas, Unicode NFKC y espacios colapsados."""
    if not s:
        return ""
    if "<" in s and ">" in s:
        p = _TextExtractor()
        try:
            p.feed(s)
            p.close()
            s = "".join(p.parts)
        except Exception:                                          # noqa: BLE001 (HTML roto: texto crudo)
            s = re.sub(r"<[^>]+>", " ", s)
    s = unicodedata.normalize("NFKC", html.unescape(s))
    return _WS.sub(" ", s).strip()[:max_len]


def canonical_url(url: str | None) -> str:
    """URL comparable: esquema y host en minúscula, sin www, fragmento ni parámetros de seguimiento."""
    if not url:
        return ""
    url = url.strip()
    try:
        p = urlsplit(url)
    except ValueError:
        return url
    if not p.scheme or not p.netloc:
        return url
    host = p.hostname or ""
    host = host[4:] if host.startswith("www.") else host
    port = p.port
    if port and not ((p.scheme == "http" and port == 80) or (p.scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS]
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme.lower(), host.lower(), path, urlencode(sorted(query)), ""))


def parse_datetime(s: str | None) -> float | None:
    """RFC 822 (RSS), ISO 8601/RFC 3339 (Atom) y variantes comunes → época UTC. Sin zona: UTC."""
    if not s:
        return None
    s = s.strip()
    dt = None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        iso = s.replace("Z", "+00:00").replace("z", "+00:00")
        if " " in iso and "T" not in iso:
            iso = iso.replace(" ", "T", 1)
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    try:
        return dt.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def title_key(title: str) -> str:
    """Clave exacta de título: sin marca del medio, sin puntuación, minúsculas y espacios simples."""
    t = clean_text(title).casefold()
    t = _BRAND_SUFFIX.sub("", t)
    t = _PUNCT.sub(" ", t)
    return _WS.sub(" ", t).strip()


def tokens(text: str) -> list[str]:
    """Palabras significativas para comparar historias (sin stopwords; números conservados)."""
    t = _PUNCT.sub(" ", clean_text(text).casefold())
    out = []
    for w in t.split():
        w = w.strip(".")
        if not w or w in STOPWORDS:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]                                            # plural simple: "sues" ~ "sue"
        out.append(w)
    return out
