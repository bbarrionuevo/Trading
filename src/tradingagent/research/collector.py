"""Recolector de datos de entrenamiento: una pasada corta (GitHub Actions cada pocos minutos, o en la PC).

  python -m tradingagent collect --data DIR [--no-telegram] [--paper [--capital 120] [--rearm]]

1. Lee todas las fuentes a la vez, con el ETag y lo ya visto de la corrida anterior (estado.json).
2. Evalúa con el mismo motor de ingesta, en orden de publicación. La deduplicación se reconstruye con
   los eventos de las últimas horas. Lo de la primera lectura de cada fuente es historia y no se guarda.
3. Guarda en eventos/AAAA-MM.jsonl cada noticia nueva de hasta 3 horas, con los activos detectados y la
   clasificación determinista (`nlp`: categoría, polaridad, confianza, activos, horizonte y reglas).
   También guarda las copias de otras fuentes (corroboración) y lo que el filtro marca como ruido,
   cada una marcada, para entrenar la confianza y medir el filtro. La ventana es más amplia que la de
   operar (REGLAS B-2 rige para operar, no para juntar datos), y `age_s` guarda la edad de cada una.
4. A los 62 minutos de la llegada etiqueta cada noticia original con la reacción del precio
   (reacciones/AAAA-MM.jsonl, ver labeler.py).
5. Telegram, si hay TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID:
   - un aviso al activarse;
   - las noticias oficiales de la Fed y la SEC;
   - un resumen por día;
   - un aviso, una sola vez, si todas las fuentes o todas las velas fallan, y otro al volver.
   Nunca imprime ni guarda secretos.
6. Si hay clave de Anthropic, las noticias originales que la capa rápida no resuelve sola se consultan a
   Claude Haiku 5.5 (intelligence/llm.py): la respuesta validada queda en `nlp_llm` y es la que usa el
   paper; el gasto del día va al estado (`llm`) y nunca pasa del sobre (REGLAS C-4).
7. Con `--paper`, el operador paper (shadow.py) opera las noticias originales de esta corrida contra
   los libros reales, con llenados simulados, y el resumen diario suma una línea de paper.
Todo se escribe al final de la corrida: si algo falla a mitad de camino, no queda nada a medias. Si el
paper falla, los datos del recolector ya quedaron guardados.
"""
import asyncio
import calendar
import json
import logging
import time
from collections import Counter, defaultdict
from pathlib import Path

import aiohttp

from .. import __version__, paths
from .. import limits as limits_mod
from ..alerts.model import Alert, Priority, esc
from ..core.bus import EventBus
from ..core.clock import SystemClock
from ..core.events import TIER_OFFICIAL, NewsItem
from ..core.redact import redact
from ..ingestion.dedup import DedupIndex
from ..ingestion.engine import IngestionEngine
from ..ingestion.noise import NoiseConfig, NoiseFilter
from ..ingestion.sources import PollingSource
from ..intelligence import llm as llm_mod
from ..intelligence.classifier import classify
from . import assets, learn, shadow
from .labeler import READY_AFTER_S, CandleStore, window

log = logging.getLogger("tradingagent.collector")
SCHEMA = 1
STATE, EVENTS, REACTIONS, README = "estado.json", "eventos", "reacciones", "LEEME.md"
COLLECT_MAX_AGE_S = 3 * 3600
PENDING_MAX_AGE_S = 48 * 3600
MAX_LABELS_PER_RUN = 300
MAX_OFFICIAL_ALERTS = 3
ALERT_CATEGORIES = ("macro", "regulatorio")      # los 8-K de EDGAR son cientos por día: no se avisan
LOUD_SOURCES = ("fed.monetary",)                # política monetaria: con sonido; el resto, silencioso
SNIPPET_CHARS = 280

LEEME = """# Datos de entrenamiento de TRADING (rama `data`)

Los escribe el recolector (`.github/workflows/recoleccion.yml`) cada 15 minutos. Solo datos públicos.

- `eventos/AAAA-MM.jsonl`: una noticia por línea: titular, fragmento, fuente y nivel, horas de
  publicación y llegada, activos detectados, si es la original o una copia (`dedup`, `cluster`), si
  el filtro la marcó como ruido y la clasificación determinista (`nlp`).
- `reacciones/AAAA-MM.jsonl`: la reacción del precio a cada noticia original, por par (velas públicas
  de 1 minuto): retornos a 1, 5, 15 y 60 minutos desde la publicación, extremos de la hora y lo que
  quedaba por capturar desde la llegada (`r_recv_*`).
- `estado.json`: lo ya visto por fuente, lo pendiente de etiquetar, la salud de las fuentes y los
  conteos por día.
- `paper/`: el operador paper (dinero simulado contra libros reales): `estado.json`,
  `operaciones/AAAA-MM.jsonl` (cada entrada y salida) y `decisiones/AAAA-MM.jsonl` (cada noticia
  evaluada, con el motivo de entrar o no). Un archivo `KILL` en la raíz frena las entradas.

No se edita a mano: el recolector agrega líneas y reescribe el estado en cada corrida.
"""


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _month(ts: float) -> str:
    return time.strftime("%Y-%m", time.gmtime(ts))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                log.warning("%s: línea ilegible ignorada", path.name)
    return out


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


def _append_monthly(data: Path, kind: str, rows: list[dict], ts_key: str) -> None:
    by_month = defaultdict(list)
    for r in rows:
        by_month[_month(r[ts_key])].append(r)
    for m, rs in sorted(by_month.items()):
        _append_jsonl(data / kind / f"{m}.jsonl", rs)


def read_shards(data: Path, kind: str, since: float, until: float) -> list[dict]:
    """Registros de los archivos mensuales que cubren [since, until]."""
    months, t = [], since
    while _month(t) <= _month(until):
        months.append(_month(t))
        t += 28 * 86400
    months = sorted(set(months + [_month(until)]))
    return [r for m in months for r in _read_jsonl(data / kind / f"{m}.jsonl")]


class _Telegram:
    """Envíos del recolector: nunca rompen la corrida y respetan ~1 mensaje por segundo por chat."""

    def __init__(self, transport, gap_s: float):
        self.t, self.gap_s, self.sent, self._last = transport, gap_s, 0, None

    async def send(self, text: str, *, silent: bool = True) -> bool:
        if self.t is None:
            return False
        if self._last is not None and self.gap_s:
            await asyncio.sleep(max(0.0, self.gap_s - (time.monotonic() - self._last)))
        try:
            r = await self.t.send(Alert(Priority.SYSTEM, "recolector", text[:4000], silent=silent))
        except Exception as e:                                        # noqa: BLE001 (Telegram nunca rompe la corrida)
            log.warning("Telegram falló: %s", type(e).__name__)
            return False
        finally:
            self._last = time.monotonic()
        if not r.ok:
            log.warning("Telegram no aceptó el mensaje: %s", r.error)
        self.sent += r.ok
        return r.ok

    async def send_alert(self, alert: Alert) -> bool:
        """Una alerta ya armada (HTML escapado) del operador paper, con el mismo cuidado que `send`."""
        return await self.send(alert.text, silent=alert.silent)


def digest(day: str, events: list[dict], reactions: list[dict], health: dict, paper: str = "") -> str:
    """Resumen de un día UTC: noticias por fuente, corroboradas y los mayores movimientos a 15 minutos."""
    evs = [e for e in events if _day(e["received_at"]) == day]
    orig = [e for e in evs if e.get("dedup") == "new" and not e.get("is_noise")]
    by_src = Counter(e["source"] for e in orig)
    ids = {e["id"]: e for e in orig}
    copies = Counter(e["cluster"] for e in evs if e.get("dedup") != "new")
    labeled = {r["event_id"] for r in reactions if r.get("event_id") in ids and "error" not in r}
    moves = sorted((r for r in reactions if r.get("event_id") in ids and r.get("r_15m") is not None),
                   key=lambda r: -abs(r["r_15m"]))[:3]
    lines = [f"📊 <b>Recolector · {esc(day)}</b> · {len(orig)} noticias · "
             f"{sum(1 for e in orig if e['tier'] == TIER_OFFICIAL)} oficiales · {len(labeled)} etiquetadas · "
             f"{len(copies)} corroboradas"]
    if by_src:
        lines.append(" · ".join(f"{esc(s)} {n}" for s, n in by_src.most_common(8)))
    for r in moves:
        lines.append(f"{esc(r['symbol'])} {r['r_15m'] * 100:+.2f}% en 15 min · {esc(ids[r['event_id']]['title'][:90])}")
    bad = sorted(s for s, h in health.items() if not h.get("ok"))
    if bad:
        lines.append("Fuentes con error en la última corrida: " + ", ".join(esc(s) for s in bad))
    if not orig:
        lines.append("Sin noticias nuevas ese día (o las fuentes no respondieron: ver estado.json).")
    if paper:
        lines.append(paper)
    return "\n".join(lines)


MARKETS = ("binance-spot", "mexc-futuros")


def markets(paper: dict, session, paper_urls: dict | None, candle_urls: dict | None) -> list:
    """Los mercados paper pedidos: Binance spot (datos públicos) y MEXC futuros (públicos + lectura de la cuenta
    si hay claves de solo lectura)."""
    from ..marketdata import mexc
    from .futures import MexcFutures
    out = []
    for name in paper.get("mercados", MARKETS):
        if name == "binance-spot":
            out.append(shadow.BinanceSpot(paper_urls, candle_urls))
        elif name == "mexc-futuros":
            keys = paper.get("mexc_keys") or (None, None)
            ro = mexc.ReadOnlyClient(session, *keys, base=paper.get("mexc_base", mexc.BASE)) if all(keys) else None
            out.append(MexcFutures(base=paper.get("mexc_base", mexc.BASE), readonly=ro))
        else:
            log.warning("mercado paper desconocido: %s", name)
    return out


def _load(data: Path, now: float, ttl_s: float) -> tuple[dict, list[dict]]:
    """(estado de la corrida anterior, eventos dentro de la ventana de deduplicación)."""
    data.mkdir(parents=True, exist_ok=True)
    state = paths.read_json(data / STATE, {}) or {}
    recent = [e for e in read_shards(data, EVENTS, now - ttl_s, now) if now - e["received_at"] <= ttl_s]
    return state, recent


def _digest_inputs(data: Path, day: str, now: float) -> tuple[list[dict], list[dict]]:
    since = calendar.timegm(time.strptime(day, "%Y-%m-%d"))
    evs = [e for e in read_shards(data, EVENTS, since, now) if _day(e["received_at"]) == day]
    return evs, read_shards(data, REACTIONS, since, now)


def _save(data: Path, state: dict, new_events: list[dict], reactions: list[dict]) -> None:
    _append_monthly(data, EVENTS, new_events, "received_at")
    _append_monthly(data, REACTIONS, reactions, "t_recv")
    readme = data / README
    if not readme.exists() or readme.read_text(encoding="utf-8") != LEEME:
        readme.write_text(LEEME, encoding="utf-8", newline="\n")
    paths.write_json_atomic(data / STATE, state)


async def _poll(spec, session, clock, src_state: dict):
    p = PollingSource(spec, session, clock, None)
    p.import_state(src_state.get(spec.id, {}))
    try:
        items, first = await p.poll_once()
    except Exception as e:                                            # noqa: BLE001 (una fuente no frena a las demás)
        return p, [], False, redact(e)[:200] or type(e).__name__
    return p, items, first, None


async def collect(data_dir, cfg: dict, env, *, clock=None, sources=None, telegram=None,
                  candle_urls: dict | None = None, limits: dict | None = None,
                  session: aiohttp.ClientSession | None = None, send_gap_s: float = 1.1,
                  paper: dict | None = None, paper_urls: dict | None = None,
                  llm: "llm_mod.LlmClassifier | None" = None) -> dict:
    """Una pasada. `paper` = {"capital": USDT, "rearm": bool} activa el operador paper (None = solo datos);
    `llm` consulta a Claude las noticias dudosas (None = solo la capa rápida)."""
    from ..app import build_sources  # import tardío: evita el ciclo app ↔ research
    clock = clock or SystemClock()
    limits = limits if limits is not None else limits_mod.load()
    data = Path(data_dir)
    now = clock.time()
    ing = cfg["ingestion"]
    ttl_s = ing["dedup"]["ttl_hours"] * 3600
    state, recent = await asyncio.to_thread(_load, data, now, ttl_s)
    dedup = DedupIndex(clock, ttl_s=ttl_s, threshold=ing["dedup"]["threshold"])
    for e in recent:                                                  # reconstruye la ventana de deduplicación
        dedup.check(NewsItem(id=e["id"], source=e["source"], tier=e["tier"], kind="rss", title=e["title"],
                             url=e.get("url", "")))
    engine = IngestionEngine(EventBus(), clock, dedup, NoiseFilter(NoiseConfig(ing["noise"]["threshold"])),
                             default_max_age_s=ing["default_max_age_s"], max_future_skew_s=ing["max_future_skew_s"])
    specs = list(sources) if sources is not None else build_sources(cfg, env, limits)[0]
    tg = _Telegram(telegram, send_gap_s)
    own_session = session is None
    session = session or aiohttp.ClientSession()
    counts: Counter = Counter()
    new_events, official, health, bodies = [], [], {}, {}
    src_state = dict(state.get("sources", {}))
    try:
        polled = await asyncio.gather(*(_poll(s, session, clock, src_state) for s in specs))
        batch = []
        for spec, (p, items, first, err) in zip(specs, polled, strict=True):
            src_state[spec.id] = p.export_state()
            if err:
                health[spec.id] = {"ok": False, "error": err, "ts": round(now)}
                counts["fuente_con_error"] += 1
                continue
            health[spec.id] = {"ok": True, "items": len(items), "ts": round(now), "latency_ms": p.health.last_latency_ms}
            batch += [(it.published_at or it.received_at, spec, it, first) for it in items]
        batch.sort(key=lambda b: (b[0], b[2].source, b[2].id))       # la primera en publicar queda como original
        for _, spec, it, first in batch:
            ev = engine.evaluate(it, backlog=first, max_age_s=COLLECT_MAX_AGE_S)
            blocking = [r for r in ev.reasons if not r.startswith("duplicado_") and r != "ruido"]
            if blocking:
                for r in blocking:
                    counts[r] += 1
                continue
            found = assets.detect(f"{it.title} {it.body}")
            rec = {"v": SCHEMA, "id": it.id, "cluster": ev.dedup.cluster_id, "dedup": ev.dedup.kind,
                   "similarity": ev.dedup.similarity, "cluster_sources": ev.dedup.distinct_sources,
                   "source": it.source, "tier": it.tier, "category_hint": it.category_hint,
                   "categories": list(it.categories[:6]), "title": it.title, "snippet": it.body[:SNIPPET_CHARS],
                   "url": it.url, "published_at": it.published_at, "received_at": it.received_at,
                   "age_s": ev.age_s, "freshness": ev.freshness, "noise": ev.noise.score,
                   "is_noise": ev.noise.is_noise, "noise_reasons": list(ev.noise.reasons), "assets": found,
                   "symbols": assets.symbols_for(found, it.assets_hint)}
            new_events.append(rec)
            bodies[it.id] = (it.body, it.assets_hint)
            counts["nuevas" if ev.dedup.kind == "new" else "copias"] += 1
            counts["ruido"] += ev.noise.is_noise
            if (ev.dedup.kind == "new" and not ev.noise.is_noise and it.tier == TIER_OFFICIAL
                    and spec.category in ALERT_CATEGORIES):
                official.append(rec)

        # -- clasificación (capa rápida, determinista) ------------------------------------------
        corroborated = Counter()
        for e in new_events:                                          # copias vistas en esta misma corrida
            corroborated[e["cluster"]] = max(corroborated[e["cluster"]], e["cluster_sources"])
        signals = {}
        for e in new_events:
            body, hint = bodies[e["id"]]
            sig = classify(e["title"], body, tier=e["tier"], cluster_sources=corroborated[e["cluster"]],
                           hint_assets=hint)
            e["nlp"], signals[e["id"]] = sig.as_dict(), sig
            if e["dedup"] == "new" and not e["is_noise"]:
                counts[f"nlp_{sig.category}"] += 1
        llm_out = {"consultas": 0, "usadas": 0, "usd": 0.0, "errores": Counter()}
        if llm is not None and llm.enabled:
            llm.budget = llm_mod.Budget.from_dict(state.get("llm"))
            originals = [e for e in new_events if e["dedup"] == "new" and not e["is_noise"]]
            results = await llm_mod.refine(llm, [(e["title"], bodies[e["id"]][0], signals[e["id"]]) for e in originals])
            for e, res in zip(originals, results, strict=True):
                if res is None:
                    continue
                llm_out["consultas"] += 1
                llm_out["usd"] += res.usd
                if res.signal is None:
                    llm_out["errores"][res.error] += 1
                    e["nlp_llm"] = {"error": res.error}
                    continue
                llm_out["usadas"] += 1
                e["nlp_llm"] = {**res.signal.as_dict(), "motivo": (res.raw or {}).get("reason", "")[:200],
                                "latencia_ms": round(res.latency_ms)}
                signals[e["id"]] = res.signal                         # la IA propone; el núcleo decide (C-3)
            state["llm"] = llm.budget.as_dict()

        # -- etiquetado -----------------------------------------------------------------------
        pending = state.get("pending", []) + [
            {"id": e["id"], "received_at": e["received_at"], "published_at": e["published_at"], "symbols": e["symbols"]}
            for e in new_events if e["dedup"] == "new"]
        expired = [p for p in pending if now - p["received_at"] >= PENDING_MAX_AGE_S]
        ready = sorted((p for p in pending if READY_AFTER_S <= now - p["received_at"] < PENDING_MAX_AGE_S),
                       key=lambda p: p["received_at"])[:MAX_LABELS_PER_RUN]
        store = CandleStore(session, urls=candle_urls)
        needs: dict[str, list] = {}
        for p in ready:
            for sym in p["symbols"]:
                needs.setdefault(sym, []).append(window(p))
        await store.load(needs)
        reactions, done = [], set()
        for p in ready:
            rows = store.label(p, p["symbols"])
            if rows and all(r.get("retry") for r in rows):
                counts["etiqueta_reintento"] += 1                     # la fuente de velas no respondió
                continue
            reactions += [{**{k: v for k, v in r.items() if k != "retry"}, "v": SCHEMA, "labeled_at": round(now)}
                          for r in rows]
            done.add(p["id"])
        counts["etiquetadas"] += len(done)
        counts["etiqueta_vencida"] += len(expired)
        gone = done | {p["id"] for p in expired}
        state["pending"] = [p for p in pending if p["id"] not in gone]

        # -- Telegram ---------------------------------------------------------------------------
        if telegram is not None and not state.get("aviso_inicio"):
            if await tg.send(f"✅ <b>Recolector de TRADING activo</b> · v{esc(__version__)} · {len(specs)} fuentes · "
                             "cada 15 min. Junta noticias y la reacción del precio para entrenar la etapa 2. "
                             "Te aviso las noticias oficiales y te mando un resumen por día.", silent=False):
                state["aviso_inicio"] = round(now)
        for e in official[:MAX_OFFICIAL_ALERTS]:
            age = f"{e['age_s'] / 60:.0f} min" if e["age_s"] is not None else "sin hora"
            measured = ", ".join(s.removesuffix("USDT") for s in e["symbols"])
            await tg.send(f"🏛️ <b>{esc(e['source'])}</b> · {esc(e['title'][:200])}\npublicada hace {esc(age)} · "
                          f"se mide {esc(measured)}\n{esc(e['url'])}", silent=e["source"] not in LOUD_SOURCES)
        any_ok = any(h["ok"] for h in health.values())
        if health and not any_ok and not state.get("alerta_fuentes"):
            if await tg.send("⚠️ <b>Recolector</b>: ninguna fuente respondió en esta corrida "
                             f"({esc(', '.join(sorted(health)))}). Te aviso cuando vuelvan.", silent=False):
                state["alerta_fuentes"] = round(now)
        elif any_ok and state.get("alerta_fuentes"):
            if await tg.send("✅ <b>Recolector</b>: las fuentes volvieron a responder."):
                state.pop("alerta_fuentes")
        if store.errors and not store.candles and ready and not state.get("alerta_velas"):
            if await tg.send("⚠️ <b>Recolector</b>: no pude bajar velas de ninguna fuente (Binance ni Coinbase). "
                             "Las noticias quedan pendientes y se reintentan hasta 48 h.", silent=False):
                state["alerta_velas"] = round(now)
        elif store.candles and state.get("alerta_velas"):
            state.pop("alerta_velas")
        day = _day(now)
        last = state.get("digest_day")
        if last is not None and last != day and telegram is not None:   # primera corrida de un día nuevo
            try:
                evs, reac = await asyncio.to_thread(_digest_inputs, data, last, now)
                lines = [await asyncio.to_thread(shadow.paper_line, data, last, m)
                         for m in (paper or {}).get("mercados", MARKETS)] if paper is not None else []
                text = digest(last, evs, reac + reactions, health, "\n".join(x for x in lines if x))
            except Exception:                                      # el resumen nunca frena la recolección
                log.exception("no se pudo armar el resumen de %s", last)
            else:
                await tg.send(text)
        state["digest_day"] = day

        # -- escritura (todo al final) -------------------------------------------------------
        by_day = state.get("counts", {})
        by_day[day] = dict(Counter(by_day.get(day, {})) + counts)
        state.update(sources=src_state, health=health, counts=dict(sorted(by_day.items())[-14:]),
                     updated_at=round(now), version=__version__, schema=SCHEMA)
        await asyncio.to_thread(_save, data, state, new_events, reactions)

        # -- paper (después de guardar: si falla, los datos ya están) --------------------------
        paper_out = None
        if paper is not None:
            items = [(e, signals[e["id"]]) for e in new_events if e["dedup"] == "new" and not e["is_noise"]]
            paper_out = {}
            try:
                learned, newly = await asyncio.to_thread(learn.update, data, now)
                for key in newly:
                    await tg.send(f"🧠 <b>Aprendizaje</b>: apago {esc(learn.describe(learned, key))}. Evidencia "
                                  "negativa neta de comisiones: el paper deja de operar esa categoría de ese lado.")
            except Exception as e:                                     # sin aprendizaje se opera con los valores por
                log.exception("el aprendizaje falló")                  # defecto (que el dueño ya aprobó)
                learned = {"apagadas": [], "error": f"{type(e).__name__}"}
            for market in markets(paper, session, paper_urls, candle_urls):
                try:                                                   # un mercado que falla no frena a los otros
                    paper_out[market.name] = await shadow.run_paper(
                        data, items, clock=clock, session=session, limits=limits,
                        telegram=tg if telegram is not None else None, capital=float(paper.get("capital", 120.0)),
                        market=market, rearm=bool(paper.get("rearm")), disabled=tuple(learned["apagadas"]),
                        learned=learned)
                except Exception as e:                                 # el paper nunca rompe la corrida
                    log.exception("el operador paper de %s falló", market.name)
                    paper_out[market.name] = {"error": f"{type(e).__name__}: {redact(e)[:160]}"}
    finally:
        if own_session:
            await session.close()
    out = {"nuevas": counts["nuevas"], "copias": counts["copias"], "ruido": counts["ruido"],
            "etiquetadas": counts["etiquetadas"], "pendientes": len(state["pending"]),
            "fuentes_ok": sum(h["ok"] for h in health.values()), "fuentes": len(health),
            "descartes": {k: v for k, v in sorted(counts.items())
                          if v and k not in ("nuevas", "copias", "ruido", "etiquetadas") and not k.startswith("nlp_")},
            "categorias": {k.removeprefix("nlp_"): v for k, v in sorted(counts.items()) if k.startswith("nlp_")},
            "velas": {"pedidos": store.requests, **Counter(store.source.values()), "error": len(store.errors)},
            "telegram": tg.sent}
    if llm_out["consultas"]:
        out["llm"] = {**llm_out, "usd": round(llm_out["usd"], 6), "errores": dict(llm_out["errores"])}
    if paper_out is not None:
        out["paper"] = paper_out
    return out
