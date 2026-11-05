"""¿Hay canales OFICIALES de Telegram que traigan las noticias antes que nuestras fuentes? Para cada canal, la vista
pública (t.me/s/<canal>: los últimos ~20 mensajes con su hora exacta) contra la hora de publicación de la fuente
original (la lista de anuncios de Binance, el RSS de cada medio). Demora = hora del mensaje − hora de la fuente
(negativa: Telegram llega antes). Una lectura por canal y por fuente; Binance, una sola vez (el límite es por IP).

Uso: python estudios/telegram_canales.py
"""
import email.utils
import html
import json
import re
import statistics
import sys
import time
import urllib.request
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
UA = {"User-Agent": "TradingAgent/0.1 (estudio de demoras)"}
BINANCE_CMS = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20"
FEEDS = {"coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/", "cointelegraph": "https://cointelegraph.com/rss",
         "theblock": "https://www.theblock.co/rss.xml", "decrypt": "https://decrypt.co/feed"}
# canal de Telegram → fuente con la que se compara
CHANNELS = {"binance_announcements": "binance", "coindesk": "coindesk", "cointelegraph": "cointelegraph",
            "theblockcrypto": "theblock", "decryptmedia": "decrypt"}
POST = re.compile(r'data-post="(?P<post>[^"]+)"(?P<rest>.*?)(?=data-post="|\Z)', re.S)
TEXT = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(?P<t>.*?)</div>', re.S)
WHEN = re.compile(r'<time datetime="(?P<d>[^"]+)"')
TAGS = re.compile(r"<[^>]+>")
WORD = re.compile(r"[a-z0-9]{3,}")


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read()


def words(s: str) -> set[str]:
    return set(WORD.findall(s.lower()))


def channel(name: str) -> list[tuple[float, str]]:
    page = get(f"https://t.me/s/{name}").decode("utf-8", errors="replace")
    out = []
    for m in POST.finditer(page):
        t, d = TEXT.search(m.group("rest")), WHEN.search(m.group("rest"))
        if t and d:
            text = html.unescape(TAGS.sub(" ", t.group("t").replace("<br/>", "\n")))
            out.append((datetime.fromisoformat(d.group("d")).timestamp(), " ".join(text.split())))
    return out


def binance() -> list[tuple[float, str]]:
    data = json.loads(get(BINANCE_CMS))
    return [(a["releaseDate"] / 1000, a["title"]) for c in (data.get("data") or {}).get("catalogs") or []
            for a in c.get("articles") or []]


def feed(url: str) -> list[tuple[float, str]]:
    raw = get(url).decode("utf-8", errors="replace")
    out = []
    for item in re.findall(r"<item>(.*?)</item>", raw, re.S):
        title = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item, re.S)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", item)
        if title and pub:
            out.append((email.utils.parsedate_to_datetime(pub.group(1).strip()).timestamp(),
                        html.unescape(title.group(1)).strip()))
    return out


def match(posts, source, min_overlap: float = 0.6):
    """Cada mensaje con la noticia de la fuente que más palabras comparte (≥ 60 % de las del título)."""
    out = []
    for t_tg, text in posts:
        tw = words(text)
        best, score = None, 0.0
        for t_src, title in source:
            w = words(title)
            if w and (s := len(w & tw) / len(w)) > score:
                best, score = (t_src, title), s
        if best and score >= min_overlap:
            out.append((t_tg - best[0], best[1]))
    return out


sources = {"binance": binance()}
for name, url in FEEDS.items():
    try:
        sources[name] = feed(url)
    except OSError as e:
        print(f"{name}: sin RSS ({e})")
    time.sleep(1)
for ch, src in CHANNELS.items():
    try:
        posts = channel(ch)
    except OSError as e:
        print(f"@{ch}: no se pudo leer ({e})")
        continue
    time.sleep(2)
    if not posts:
        print(f"@{ch}: sin mensajes públicos (no existe o no es un canal)")
        continue
    m = match(posts, sources.get(src) or [])
    span = (time.time() - posts[0][0]) / 3600
    if not m:
        print(f"@{ch}: {len(posts)} mensajes (últimas {span:.0f} h), ninguno coincide con {src}")
        continue
    d = [x for x, _ in m]
    print(f"@{ch} vs {src}: {len(m)} de {len(posts)} mensajes coinciden · demora mediana {statistics.median(d):+.0f} s · "
          f"mín {min(d):+.0f} s · máx {max(d):+.0f} s")
    for x, title in sorted(m)[:3]:
        print(f"    {x:+6.0f} s  {title[:90]}")
