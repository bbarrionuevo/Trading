"""Parser de RSS 2.0, RSS 1.0 (RDF) y Atom, seguro frente a XML hostil.

Los feeds son datos no confiables: se parsean con defusedxml (sin entidades ni DTD externas: bloquea
"billion laughs" y XXE) y con tope de tamaño y de ítems. Se toleran los defectos más comunes de los
feeds reales: espacios de nombres mezclados, CDATA, `&` sin escapar y fechas en varios formatos.
"""
import re
from dataclasses import dataclass, field

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

MAX_FEED_BYTES = 5_000_000
_BARE_AMP = re.compile(rb"&(?!#\d+;|#x[0-9a-fA-F]+;|[A-Za-z][A-Za-z0-9]{1,31};)")


class FeedError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FeedEntry:
    title: str
    link: str = ""
    guid: str = ""
    summary: str = ""
    published: str = ""                 # texto crudo: lo convierte normalize.parse_datetime
    categories: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _text(el) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def _children(el, name):
    return [c for c in el if _local(c.tag) == name]


def _first(el, *names):
    for n in names:
        for c in el:
            if _local(c.tag) == n:
                return c
    return None


def _atom_link(entry) -> str:
    """El enlace "alternate" (o sin rel); si no hay, el primero con href."""
    links = _children(entry, "link")
    for ln in links:
        if (ln.get("rel") or "alternate") == "alternate" and ln.get("href"):
            return ln.get("href").strip()
    for ln in links:
        if ln.get("href"):
            return ln.get("href").strip()
    return _text(links[0]) if links else ""


def _entry(el, atom: bool) -> FeedEntry | None:
    title = _text(_first(el, "title"))
    if atom:
        link = _atom_link(el)
        guid = _text(_first(el, "id"))
        summary = _text(_first(el, "summary", "content"))
        published = _text(_first(el, "published", "updated", "issued", "modified"))
        cats = tuple(c.get("term") or _text(c) for c in _children(el, "category"))
    else:
        link_el = _first(el, "link")
        link = _text(link_el) or (link_el.get("href", "") if link_el is not None else "")
        guid_el = _first(el, "guid")
        guid = _text(guid_el) or (el.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about") or "")
        summary = _text(_first(el, "encoded", "description", "summary"))
        published = _text(_first(el, "pubDate", "date", "published", "updated"))
        cats = tuple(_text(c) for c in _children(el, "category") + _children(el, "subject"))
    if not title and not summary:
        return None
    extra = {}
    for name in ("form-type", "filing-type", "cik", "company-name", "accession-number"):   # EDGAR
        found = el.find(f".//{{*}}{name}")
        if found is not None and _text(found):
            extra[name] = _text(found)
    return FeedEntry(title=title, link=link, guid=guid, summary=summary, published=published,
                     categories=tuple(c for c in cats if c), extra=extra)


def _parse(data: bytes):
    try:
        return SafeET.fromstring(data, forbid_dtd=False, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as e:
        raise FeedError(f"XML rechazado por seguridad: {type(e).__name__}") from e


def parse_feed(data: bytes, max_items: int = 200) -> list[FeedEntry]:
    if not data:
        raise FeedError("respuesta vacía")
    if len(data) > MAX_FEED_BYTES:
        raise FeedError(f"feed de {len(data)} bytes supera el máximo")
    try:
        root = _parse(data)
    except SafeET.ParseError:
        try:                                         # defecto común: '&' sin escapar en títulos
            root = _parse(_BARE_AMP.sub(b"&amp;", data))
        except SafeET.ParseError as e:
            raise FeedError(f"XML inválido: {e}") from e
    kind = _local(root.tag)
    if kind == "feed":                                 # Atom
        nodes, atom = _children(root, "entry"), True
    elif kind in ("rss", "RDF"):
        channel = _first(root, "channel")
        nodes = _children(channel, "item") if channel is not None else []
        nodes += _children(root, "item")               # RSS 1.0: los ítems cuelgan de la raíz
        atom = False
    else:
        raise FeedError(f"formato desconocido: <{kind}>")
    out = []
    for el in nodes[:max_items]:
        e = _entry(el, atom)
        if e is not None:
            out.append(e)
    return out
