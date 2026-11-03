"""Tablero local: todo lo importante del agente de un vistazo, en http://127.0.0.1:18765 (solo en esta PC).

Lo sirve el supervisor, así sigue andando aunque el agente se reinicie o se esté actualizando. También:
  python -m tradingagent tablero [--puerto 18765] [--abrir]

Si otro programa usa el puerto, toma el siguiente libre (nunca lo comparte) y deja la dirección real en
`state/tablero.json` y en `tablero.html` (el acceso directo del escritorio abre ese archivo).

- **Solo lectura:** lee los archivos de TRADING_HOME (latido, cuenta paper, operaciones, decisiones,
  aprendizaje, supervisor, tests y log) y no tiene ninguna acción que cambie algo.
- **Solo esta PC:** escucha en 127.0.0.1 y rechaza cualquier otro Host (ni la red ni una página web
  pueden consultarlo). Todo texto de afuera (titulares de noticias) se escapa; CSP sin scripts en línea.
- **Solo biblioteca estándar:** el supervisor no carga dependencias, así pip puede actualizarlas en Windows.
"""
import html
import json
import os
import re
import socket
import threading
import time
from collections import Counter
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, paths
from .core.redact import redact
from .research import evaluation

PORT = 18765                        # lejos de los puertos comunes: el 8765 ya lo usa otro programa del dueño
PORT_TRIES = 20
HB_STALE_S = 60                     # el latido se escribe cada 15 s: un minuto sin latido es una caída
F3_TRADES = 60                      # REGLAS F-3: operaciones cerradas para pasar a dinero real
REFRESH_MS = 5000
REPO = Path(__file__).resolve().parents[2]


# -- datos ------------------------------------------------------------------------------------------------
def _open_shared(path: Path):
    """Abre para leer sin trabar al que escribe. En Windows un archivo abierto no se puede reemplazar ni
    renombrar salvo con FILE_SHARE_DELETE: sin esto, leer el estado justo cuando el agente lo guarda (o
    rota el log) le haría fallar el guardado."""
    if os.name != "nt":
        return open(path, "rb")
    import _winapi
    import msvcrt
    handle = _winapi.CreateFile(str(path), _winapi.GENERIC_READ, 0x7, 0, _winapi.OPEN_EXISTING, 0, 0)
    return open(msvcrt.open_osfhandle(handle, os.O_RDONLY), "rb")


def read_text(path: Path) -> str | None:
    try:
        with _open_shared(Path(path)) as f:
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def read_json(path: Path, default):
    text = read_text(path)
    try:
        return default if text is None else json.loads(text)
    except ValueError:
        return default


def _d(x, default=None):
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _rows(files: list[Path]) -> list[dict]:
    out = []
    for f in files:
        for line in (read_text(f) or "").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _tail(path: Path, max_bytes: int = 1_000_000) -> list[str]:
    try:
        with _open_shared(path) as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            return f.read().decode("utf-8", errors="replace").splitlines()[1 if size > max_bytes else 0:]
    except OSError:
        return []


def tests_summary(path: Path) -> dict | None:
    """Del final de la salida de pytest: {"passed": 165, "failed": 0, ...} y la línea tal cual."""
    lines = [x for x in _tail(path, 20_000) if x.strip()]
    for line in reversed(lines):
        counts = dict((k, int(n)) for n, k in re.findall(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)",
                                                         line))
        if counts:
            counts = {("error" if k == "errors" else k): n for k, n in counts.items()}
            return {**counts, "ok": not counts.get("failed") and not counts.get("error"),
                    "linea": line.strip(" =")[:200]}
    return None


def account(base: Path, now: float) -> dict | None:
    st = read_json(base / "estado.json", None)
    if not isinstance(st, dict) or "initial" not in st:
        return None
    trades = _rows(sorted((base / "operaciones").glob("*.jsonl")))
    exits = sorted((t for t in trades if t.get("tipo") == "salida"), key=lambda t: t.get("ts", 0))
    initial = _d(st["initial"], Decimal(0))
    equity = _d(st.get("equity"), _d(st.get("cash"), initial))
    marks = st.get("marks") or {}
    positions = []
    for sym, p in sorted((st.get("positions") or {}).items()):
        side = 1 if p.get("side") == "largo" else -1
        qty, entry, mark = abs(_d(p.get("qty"), Decimal(0))), _d(p.get("avg_price")), _d(marks.get(sym))
        upnl = (mark - entry) * qty * side if mark is not None and entry is not None else None
        positions.append({"symbol": sym, "lado": p.get("side"), "qty": str(qty), "entrada": p.get("avg_price"),
                          "marca": None if mark is None else str(mark), "stop": p.get("stop"), "tp": p.get("tp"),
                          "pnl_abierto": None if upnl is None else float(round(upnl, 4)),
                          "vence_en_s": round(float(p.get("until", now)) - now), "p": p.get("p"),
                          "alfa": p.get("alpha"), "categoria": p.get("category"), "nocional": p.get("notional"),
                          "riesgo_usd": p.get("risk_usd"), "liquidacion": p.get("liq"), "desde": p.get("entry_ts")})
    curve, run = [[st.get("created_at", 0), float(initial)]], initial
    for t in exits:
        run += _d(t.get("pnl_neto"), Decimal(0))
        curve.append([t.get("ts", 0), float(round(run, 4))])
    curve.append([now, float(round(equity, 4))])                 # el último punto incluye las abiertas
    stats = st.get("stats") or {}
    ev = evaluation.by_version(trades).get(st.get("strategy_tag", "")) or {}
    decisions = _rows(sorted((base / "decisiones").glob("*.jsonl"))[-2:])
    recent = decisions[-500:]
    reasons = Counter(m.split(":")[0] for d in recent if d.get("decision") != "entrar" for m in d.get("motivos", []))
    risk = st.get("risk") or {}
    won, lost = int(stats.get("ganadas", 0)), int(stats.get("perdidas", 0))
    return {"mercado": st.get("mercado", base.name), "inicial": float(initial), "equity": float(equity),
            "caja": float(_d(st.get("cash"), Decimal(0))), "pnl_neto": float(_d(stats.get("pnl_neto"), Decimal(0))),
            "retorno_pct": float(round((equity / initial - 1) * 100, 3)) if initial else 0.0,
            "comisiones": float(_d(st.get("fees"), Decimal(0))), "operaciones": int(stats.get("operaciones", 0)),
            "ganadas": won, "perdidas": lost, "acierto_pct": round(100 * won / (won + lost), 1) if won + lost else None,
            "dsr": ev.get("dsr"), "sharpe": ev.get("sharpe"), "veredicto": ev.get("veredicto"),
            "halt": risk.get("halted") or "", "racha_perdedora": risk.get("losses_in_row", 0),
            "enfriamiento_hasta": risk.get("cooldown_until") or 0, "posiciones": positions, "curva": curve[-400:],
            "ultimas_operaciones": trades[-20:][::-1], "ultimas_decisiones": recent[-30:][::-1],
            "motivos_frecuentes": reasons.most_common(8), "decisiones_recientes": len(recent),
            "entradas_recientes": sum(d.get("decision") == "entrar" for d in recent),
            "llm": st.get("llm"), "actualizado": st.get("updated_at")}


def log_errors(home: Path, n: int = 12) -> list[dict]:
    out = []
    for line in _tail(home / "logs" / "agent.log", 2_000_000):
        if '"WARNING"' not in line and '"ERROR"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("level") in ("WARNING", "ERROR"):
            out.append({"ts": d.get("ts"), "nivel": d["level"], "origen": d.get("logger", ""),
                        "mensaje": redact(d.get("msg", ""))[:400], "error": redact(d.get("exc", ""))[-600:]})
    return out[-n:][::-1]


def llm_limit(repo: Path = REPO) -> float | None:
    m = re.search(r"max_llm_usd_per_day:\s*([\d.]+)", read_text(repo / "config" / "limits.yaml") or "")
    return float(m.group(1)) if m else None


def gather(home: Path, now: float | None = None, repo: Path = REPO, include_real: bool = True) -> dict:
    """Todo lo que muestra el tablero, sin E/S de red. Lo que se publica para Claude va sin la cuenta de dinero
    real (`include_real=False`, REGLAS G-8: nunca saldos reales)."""
    now = time.time() if now is None else now
    hb = read_json(home / "state" / "heartbeat.json", None)
    sup = read_json(home / "state" / "supervisor.json", None) or {}
    accounts = [a for a in (account(d, now) for d in sorted((home / "paper-vivo").glob("*")) if d.is_dir()) if a]
    if include_real:
        for d in sorted((home / "real").glob("*")):
            a = account(d, now) if d.is_dir() else None
            if a:
                accounts.insert(0, {**a, "real": True})
    age = None if not isinstance(hb, dict) else round(now - float(hb.get("ts", 0)))
    kill = (home / "KILL").exists()
    halted = [a["mercado"] for a in accounts if a["halt"]]
    if age is None:
        status = ("sin_datos", "Sin datos todavía: el agente no escribió su primer latido")
    elif age > HB_STALE_S:
        status = ("caido", f"Sin latido hace {fmt_age(age)}: el agente no está corriendo")
    elif kill:
        status = ("pausado", "Pausado por KILL: no abre posiciones nuevas (las salidas siguen)")
    elif halted:
        status = ("halt", f"Frenado por drawdown (HALT) en {', '.join(halted)}")
    else:
        status = ("ok", "Funcionando: listo para operar la próxima noticia")
    learned = read_json(home / "datos" / "aprendizaje" / "estado.json", None) or {}
    trader = (hb or {}).get("trader") or {}
    return {"generado": round(now), "version": __version__, "estado": status[0], "estado_texto": status[1],
            "latido_hace_s": age, "kill": kill, "agente": hb, "cuentas": accounts, "supervisor": sup,
            "tests": tests_summary(home / "logs" / "tests.txt"), "errores": log_errors(home),
            "aprendizaje": {"apagadas": learned.get("apagadas", []), "meta": learned.get("meta") or {},
                            "calculado": learned.get("calculado")},
            "llm": {"tope_usd": llm_limit(repo),
                    **(trader.get("llm") or next((a["llm"] for a in accounts if a.get("llm")), None) or {}),
                    "evitadas": trader.get("ia_evitada", 0)}}


# -- formato ----------------------------------------------------------------------------------------------
e = html.escape


def fmt_age(s: float | None) -> str:
    if s is None:
        return "—"
    s = abs(int(s))
    if s < 90:
        return f"{s} s"
    if s < 5400:
        return f"{s // 60} min"
    if s < 172800:
        return f"{s // 3600} h {s % 3600 // 60:02d} min"
    return f"{s // 86400} días"


def fmt_ts(ts) -> str:
    try:
        return time.strftime("%d/%m %H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "—"


def fmt_num(x, digits: int = 2, signed: bool = False) -> str:
    if x is None:
        return "—"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return e(str(x))
    s = f"{v:+,.{digits}f}" if signed else f"{v:,.{digits}f}"
    return s.replace(",", " ")


def _delta_cls(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "neutral"
    return "up" if v > 0 else "down" if v < 0 else "neutral"


def _arrow(x) -> str:
    c = _delta_cls(x)
    return "▲" if c == "up" else "▼" if c == "down" else "■"


def tile(label: str, value: str, sub: str = "", delta: str = "", delta_cls: str = "neutral") -> str:
    d = f'<div class="delta {delta_cls}">{delta}</div>' if delta else ""
    s = f'<div class="sub">{sub}</div>' if sub else ""
    return f'<div class="tile"><div class="label">{e(label)}</div><div class="value">{value}</div>{d}{s}</div>'


def chart(curve: list, initial: float) -> str:
    """Curva de equity: una sola serie (sin leyenda; el título la nombra). La dibuja app.js al ancho real de la
    pantalla (texto nítido en la PC y en el celular); la tabla de operaciones es su versión en texto."""
    if len(curve) < 2:
        return '<p class="muted">La curva aparece con la primera operación cerrada.</p>'
    data = json.dumps({"base": initial, "pts": [[x, y, fmt_ts(x)] for x, y in curve]})
    label = f"Equity de la cuenta paper: de {fmt_num(curve[0][1])} a {fmt_num(curve[-1][1])} USDT"
    return (f'<div class="chart" data-curve="{e(data)}"><svg role="img" aria-label="{e(label)}"></svg>'
            f'<div class="tip" hidden></div></div>')


def _table(head: list[str], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f'<p class="muted">{e(empty)}</p>'
    th = "".join(f"<th>{e(x)}</th>" for x in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _n(k, word: str) -> str:
    return f"{k} {word}{'' if k == 1 else 's'}"


def _pnl(x, digits=4) -> str:
    return f'<span class="{_delta_cls(x)}">{fmt_num(x, digits, signed=True)}</span>' if x is not None else "—"


STATUS_ICON = {"ok": "✓", "pausado": "⏸", "halt": "⛔", "caido": "⚠", "sin_datos": "…"}


def fragment(s: dict) -> str:
    """El contenido del tablero (se recarga cada 5 s sin perder el scroll)."""
    parts = [f'<section class="status {e(s["estado"])}"><span class="icon">{STATUS_ICON.get(s["estado"], "?")}'
             f'</span><div><strong>{e(s["estado_texto"])}</strong><div class="muted">Último latido hace '
             f'{fmt_age(s["latido_hace_s"])} · versión {e(str((s["supervisor"] or {}).get("version", "?"))[:7])} · '
             f'actualizado {e(fmt_ts(s["generado"]))}</div></div></section>']
    hb = s.get("agente") or {}
    llm = s.get("llm") or {}
    for a in s["cuentas"]:
        gain = a["equity"] - a["inicial"]
        tiles = [
            tile("Equity (USDT)", fmt_num(a["equity"]), f"de {fmt_num(a['inicial'], 0)} iniciales · caja "
                 f"{fmt_num(a['caja'])}", f"{_arrow(gain)} {fmt_num(gain, 2, True)} ({fmt_num(a['retorno_pct'], 2, True)} %)",
                 _delta_cls(gain)),
            tile("PnL neto cerrado", _pnl(a["pnl_neto"], 2), f"comisiones pagadas {fmt_num(a['comisiones'], 4)}"),
            tile("Operaciones cerradas", str(a["operaciones"]), f"{_n(a['ganadas'], 'ganada')} · "
                 f"{_n(a['perdidas'], 'perdida')}"
                 + (f" · acierto {a['acierto_pct']} %" if a["acierto_pct"] is not None else "")),
            tile("Evidencia para dinero real", f"{min(a['operaciones'], F3_TRADES)}/{F3_TRADES}",
                 "DSR " + (fmt_num(a["dsr"], 2) if a["dsr"] is not None else "—") + " (F-3 pide ≥ 0,95)"),
            tile("Posiciones abiertas", str(len(a["posiciones"])),
                 "HALT: " + e(a["halt"]) if a["halt"] else f"racha perdedora {a['racha_perdedora']}"),
            tile("IA hoy (Claude)", f"{fmt_num(llm.get('usd', 0), 4)} USD", f"{_n(llm.get('calls', 0), 'consulta')} · "
                 f"{_n(llm.get('evitadas', 0), 'evitada')} (no cambiaban nada) · {_n(llm.get('failures', 0), 'falla')}"
                 + (f" · tope {fmt_num(llm['tope_usd'], 2)} USD por día" if llm.get("tope_usd") else "")),
        ]
        title = "💵 Cuenta de DINERO REAL" if a.get("real") else "Cuenta de práctica"
        parts.append(f'<h2>{title} · {e(a["mercado"])}</h2><div class="tiles">{"".join(tiles)}</div>')
        parts.append(f'<figure class="card"><figcaption>Equity de la cuenta (USDT): cierres y marca actual'
                     f'</figcaption>{chart(a["curva"], a["inicial"])}</figure>')
        rows = [[e(p["symbol"]), e(str(p["lado"])), e(p["qty"]), e(str(p["entrada"])), e(str(p["marca"] or "—")),
                 _pnl(p["pnl_abierto"]), e(str(p["stop"])), e(str(p["tp"])), e(fmt_age(p["vence_en_s"])),
                 e(str(p["p"])), e(str(p["categoria"]))] for p in a["posiciones"]]
        parts.append('<h3>Posiciones abiertas</h3>' + _table(
            ["Símbolo", "Lado", "Cantidad", "Entrada", "Marca", "PnL abierto", "Stop", "Take-profit", "Vence en",
             "p", "Categoría"], rows, "Sin posiciones abiertas."))
        rows = [[e(fmt_ts(t.get("ts"))), e(str(t.get("tipo", ""))), e(str(t.get("symbol", ""))),
                 e(str(t.get("lado", ""))), e(str(t.get("motivo", "") or t.get("category", ""))),
                 e(str(t.get("price", ""))), _pnl(t.get("pnl_neto")) if t.get("tipo") == "salida" else "—"]
                for t in a["ultimas_operaciones"]]
        parts.append('<h3>Últimas operaciones</h3>' + _table(
            ["Hora", "Tipo", "Símbolo", "Lado", "Motivo", "Precio", "PnL neto"], rows, "Todavía no operó."))
        why = ", ".join(f"{e(k)} ({n})" for k, n in a["motivos_frecuentes"]) or "—"
        parts.append(f'<h3>Por qué no entra (últimas {a["decisiones_recientes"]} decisiones, '
                     f'{a["entradas_recientes"]} entradas)</h3><p>{why}</p>')
        rows = []
        for d in a["ultimas_decisiones"]:
            sig = d.get("signal") or {}
            pol = sig.get("polarity")
            rows.append([e(fmt_ts(d.get("ts"))), e(str(d.get("source", ""))), e(str(d.get("title", ""))[:140]),
                         e(str(d.get("symbol", ""))), e(f"{sig.get('category', '')} {fmt_num(pol, 2, True)}"),
                         f'<span class="{"up" if d.get("decision") == "entrar" else "neutral"}">'
                         f'{e(str(d.get("decision", "")))}</span>', e(", ".join(d.get("motivos", []))[:200])])
        parts.append('<h3>Últimas decisiones</h3>' + _table(
            ["Hora", "Fuente", "Noticia", "Símbolo", "Señal", "Decisión", "Motivos"], rows,
            "Todavía no llegó ninguna noticia operable."))
    if not s["cuentas"]:
        parts.append('<p class="muted">Todavía no hay cuenta paper en vivo (aparece cuando el agente arranca).</p>')
    src, news, lag = hb.get("sources") or {}, hb.get("news") or {}, hb.get("loop_lag") or {}
    al, jr = hb.get("alerts") or {}, hb.get("journal") or {}
    failing = ", ".join(src.get("failing", [])) or "ninguna"
    health = [
        ["Fuentes de noticias", f"{src.get('ok', 0)}/{src.get('total', 0)} sanas · fallan: {e(failing)}"],
        ["Noticias", f"{news.get('aceptadas', 0)} aceptadas · {news.get('stale', 0)} viejas · "
                     f"{news.get('duplicadas', 0)} duplicadas · {news.get('ruido', 0)} ruido"],
        ["Velocidad", f"lag del bucle p99 {e(str(lag.get('p99_ms', '—')))} ms"],
        ["Telegram", f"{e(str(al.get('health', '—')))} · cola {al.get('queued', 0)}"
                     + (f" · último error: {e(str(al.get('last_error'))[:120])}" if al.get("last_error") else "")],
        ["Diario", f"{jr.get('written', 0)} registros · {jr.get('dropped', 0)} perdidos"],
        ["Encendido hace", e(fmt_age((hb.get("uptime_min") or 0) * 60))],
        ["KILL", "ACTIVO: no abre posiciones nuevas" if s["kill"] else "no (para pausar: crear el archivo KILL)"],
    ]
    parts.append('<h2>Salud del agente</h2>' + _table(["", ""], [[e(k), v] for k, v in health], ""))
    sup = s["supervisor"] or {}
    last = sup.get("ultima_revision") or {}
    pub = sup.get("publicacion") or {}
    t = s.get("tests")
    rows = [["Versión", e(str(sup.get("version", "?"))[:7])],
            ["Última revisión de versión", f"{e(fmt_ts(last.get('ts')))} · {e(str(last.get('resultado', '—')))}"],
            ["Tests en esta PC", (f'<span class="{"up" if t["ok"] else "down"}">{"✓" if t["ok"] else "✗"} '
                                  f'{e(t["linea"])}</span> (versión {e(str(sup.get("tests_version", "?")))})')
             if t else "todavía no corrieron"],
            ["Arranques del agente", str(sup.get("arranques", 0))],
            ["Publicación para Claude", f"{e(fmt_ts(pub.get('ts')))} · {e(str(pub.get('resultado')))}"
             if pub else "todavía no (empieza a los 2 minutos)"]]
    for u in (sup.get("actualizaciones") or [])[-5:][::-1]:
        rows.append(["Actualización", f"{e(fmt_ts(u.get('ts')))} · {e(str(u.get('de')))} → {e(str(u.get('a')))} · "
                                      f"{e(str(u.get('resultado')))} · {e(str(u.get('titulo') or u.get('error') or '')[:160])}"])
    for c in (sup.get("caidas") or [])[-5:][::-1]:
        rows.append(["Caída del agente", f"{e(fmt_ts(c.get('ts')))} · código {e(str(c.get('codigo')))} · vivió "
                                         f"{e(fmt_age(c.get('vivo_s')))}"])
    parts.append('<h2>Supervisor y actualizaciones</h2>' + _table(["", ""], rows, ""))
    learned = s["aprendizaje"]
    meta = ", ".join(f"{e(k)}: κ {v.get('kappa')} (n={v.get('n')})" for k, v in learned["meta"].items()) or \
        "sin datos suficientes todavía (n ≥ 30)"
    parts.append(f'<h2>Aprendizaje (solo reduce riesgo)</h2><p>Apagadas por evidencia: '
                 f'{e(", ".join(learned["apagadas"]) or "ninguna")} · Meta-modelo: {meta}</p>')
    rows = [[e(fmt_ts(x["ts"])), e(x["nivel"]), e(x["origen"]), e(x["mensaje"]) +
             (f'<details><summary>detalle</summary><pre>{e(x["error"])}</pre></details>' if x["error"] else "")]
            for x in s["errores"]]
    parts.append('<h2>Últimos avisos y errores del log</h2>' + _table(["Hora", "Nivel", "Origen", "Mensaje"], rows,
                                                                       "Sin avisos ni errores."))
    return "\n".join(parts)


def page(s: dict) -> str:
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>TRADING · tablero</title>'
            f'<link rel="stylesheet" href="/app.css"></head><body><header><h1>TRADING</h1><span class="muted">'
            f'tablero local · solo lectura · paper</span></header><main id="contenido" data-refresh="{REFRESH_MS}">'
            f'{fragment(s)}</main><footer class="muted">Se actualiza cada 5 s. Datos: TRADING_HOME (esta PC). '
            f'Pausa de emergencia: crear el archivo KILL en TRADING_HOME (salir siempre se puede).</footer>'
            f'<script src="/app.js"></script></body></html>')


CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--base:#c3c2b7;--border:rgba(11,11,11,.10);--series:#2a78d6;--up:#006300;--down:#d03b3b;
--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d0d0d;
--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--base:#383835;--border:rgba(255,255,255,.10);
--series:#3987e5;--up:#0ca30c;--down:#e66767}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
--grid:#2c2c2a;--base:#383835;--border:rgba(255,255,255,.10);--series:#3987e5;--up:#0ca30c;--down:#e66767}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header,main,footer{max-width:1200px;margin:0 auto;padding:12px 16px}
header{display:flex;gap:12px;align-items:baseline}h1{font-size:20px;margin:0}
h2{font-size:16px;margin:28px 0 10px}h3{font-size:14px;margin:18px 0 8px;color:var(--ink2)}
.muted{color:var(--muted)}.up{color:var(--up)}.down{color:var(--down)}.neutral{color:var(--ink2)}
.status{display:flex;gap:12px;align-items:center;padding:12px 14px;border:1px solid var(--border);
border-left:6px solid var(--muted);border-radius:8px;background:var(--surface)}
.status .icon{font-size:22px;width:28px;text-align:center}
.status.ok{border-left-color:var(--good)}.status.pausado{border-left-color:var(--warning)}
.status.halt,.status.caido{border-left-color:var(--critical)}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 12px}
.tile .label{color:var(--ink2);font-size:12px}.tile .value{font-size:22px;font-weight:600;margin-top:2px}
.tile .delta{font-size:13px;margin-top:2px}.tile .sub{color:var(--muted);font-size:12px;margin-top:4px}
.card{margin:12px 0 0;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 12px}
figcaption{color:var(--ink2);font-size:12px;margin-bottom:6px}
.chart{position:relative}.chart svg{display:block}
.grid{stroke:var(--grid);stroke-width:1}.base{stroke:var(--base);stroke-width:1}
.tick{fill:var(--muted);font-size:11px}.endlabel{fill:var(--ink);font-size:12px;font-weight:600}.line{fill:none;stroke:var(--series);stroke-width:2;stroke-linejoin:round;
stroke-linecap:round}.area{fill:var(--series);opacity:.1}.end{fill:var(--series);stroke:var(--surface);stroke-width:2}
.cross{stroke:var(--muted);stroke-width:1}
.tip{position:absolute;top:0;pointer-events:none;background:var(--surface);border:1px solid var(--border);
border-radius:6px;padding:4px 8px;font-size:12px;white-space:nowrap}.tip strong{display:block;font-size:14px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--grid);vertical-align:top}
th{color:var(--ink2);font-weight:600;font-size:12px}td{font-size:13px}
pre{white-space:pre-wrap;font-size:11px;max-height:240px;overflow:auto}
footer{font-size:12px;padding-bottom:28px}
"""

JS = """
(function(){
  var NS = 'http://www.w3.org/2000/svg';
  function el(name, attrs, parent){
    var n = document.createElementNS(NS, name);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    parent.appendChild(n);
    return n;
  }
  function nice(span){
    var raw = span / 4, mag = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / mag;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * mag;
  }
  function draw(box){
    var d = JSON.parse(box.getAttribute('data-curve')), svg = box.querySelector('svg'), tip = box.querySelector('.tip');
    var W = Math.max(box.clientWidth, 240), H = 220, pl = 48, pr = 52, pt = 12, pb = 26, pts = d.pts;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H); svg.setAttribute('width', W); svg.setAttribute('height', H);
    var xs = pts.map(function(p){ return p[0]; }), ys = pts.map(function(p){ return p[1]; });
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
    if (x1 <= x0) x1 = x0 + 1;
    var lo = Math.min(d.base, Math.min.apply(null, ys)), hi = Math.max(d.base, Math.max.apply(null, ys));
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    var step = nice(hi - lo), dec = step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
    lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
    function sx(x){ return pl + (x - x0) / (x1 - x0) * (W - pl - pr); }
    function sy(y){ return pt + (hi - y) / (hi - lo) * (H - pt - pb); }
    for (var v = lo; v <= hi + step / 2; v += step) {
      el('line', {x1: pl, x2: W - pr, y1: sy(v), y2: sy(v), 'class': 'grid'}, svg);
      el('text', {x: pl - 8, y: sy(v) + 4, 'text-anchor': 'end', 'class': 'tick'}, svg).textContent = v.toFixed(dec);
    }
    el('line', {x1: pl, x2: W - pr, y1: sy(d.base), y2: sy(d.base), 'class': 'base'}, svg);
    var line = pts.map(function(p){ return sx(p[0]).toFixed(1) + ',' + sy(p[1]).toFixed(1); }).join(' ');
    el('polygon', {points: sx(x0) + ',' + sy(lo) + ' ' + line + ' ' + sx(x1) + ',' + sy(lo), 'class': 'area'}, svg);
    el('polyline', {points: line, 'class': 'line'}, svg);
    var last = pts[pts.length - 1];
    el('circle', {cx: sx(last[0]), cy: sy(last[1]), r: 4, 'class': 'end'}, svg);
    el('text', {x: sx(last[0]) + 8, y: sy(last[1]) + 4, 'class': 'endlabel'}, svg).textContent = last[1].toFixed(2);
    el('text', {x: pl, y: H - 6, 'class': 'tick'}, svg).textContent = pts[0][2];
    el('text', {x: W - pr, y: H - 6, 'text-anchor': 'end', 'class': 'tick'}, svg).textContent = last[2];
    var cross = el('line', {x1: 0, x2: 0, y1: pt, y2: H - pb, 'class': 'cross', visibility: 'hidden'}, svg);
    var dot = el('circle', {r: 4, 'class': 'end', visibility: 'hidden'}, svg);
    svg.onpointermove = function(ev){
      var r = svg.getBoundingClientRect(), x = (ev.clientX - r.left) * W / r.width, best = pts[0];
      pts.forEach(function(p){ if (Math.abs(sx(p[0]) - x) < Math.abs(sx(best[0]) - x)) best = p; });
      var px = sx(best[0]), py = sy(best[1]);
      cross.setAttribute('x1', px); cross.setAttribute('x2', px); cross.setAttribute('visibility', 'visible');
      dot.setAttribute('cx', px); dot.setAttribute('cy', py); dot.setAttribute('visibility', 'visible');
      tip.textContent = '';
      var b = document.createElement('strong'); b.textContent = best[1].toFixed(2) + ' USDT';
      var t = document.createElement('span'); t.textContent = best[2];
      tip.appendChild(b); tip.appendChild(t); tip.hidden = false;
      tip.style.left = Math.max(0, Math.min(px + 12, W - tip.offsetWidth - 4)) + 'px';
      tip.style.top = Math.max(0, py - tip.offsetHeight - 10) + 'px';
    };
    svg.onpointerleave = function(){
      cross.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); tip.hidden = true;
    };
  }
  function bind(){ document.querySelectorAll('.chart[data-curve]').forEach(draw); }
  var main = document.getElementById('contenido'), every = +main.getAttribute('data-refresh') || 5000, timer = null;
  function refresh(){
    fetch('/fragmento', {cache: 'no-store'}).then(function(r){ return r.ok ? r.text() : null; })
      .then(function(h){ if (h !== null) { main.innerHTML = h; bind(); } }).catch(function(){})
      .finally(function(){ setTimeout(refresh, every); });
  }
  window.addEventListener('resize', function(){ clearTimeout(timer); timer = setTimeout(bind, 150); });
  bind(); setTimeout(refresh, every);
})();
"""

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class _Handler(BaseHTTPRequestHandler):
    server_version = "trading-tablero"
    sys_version = ""

    def log_message(self, *_a) -> None:              # sin ruido en la consola del supervisor
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:
        if self.headers.get("Host", "") not in self.server.allowed_hosts:   # DNS rebinding: solo esta PC
            return self._send(403, b"Host no permitido", "text/plain; charset=utf-8")
        path = self.path.split("?", 1)[0]
        try:
            if path == "/":
                return self._send(200, page(gather(self.server.home)).encode(), "text/html; charset=utf-8")
            if path == "/fragmento":
                return self._send(200, fragment(gather(self.server.home)).encode(), "text/html; charset=utf-8")
            if path == "/api/estado":
                body = json.dumps(gather(self.server.home), ensure_ascii=False, default=str).encode()
                return self._send(200, body, "application/json; charset=utf-8")
        except Exception as ex:                                  # noqa: BLE001 (el tablero nunca se cae)
            return self._send(500, redact(f"error armando el tablero: {type(ex).__name__}").encode(),
                              "text/plain; charset=utf-8")
        if path == "/app.css":
            return self._send(200, CSS.encode(), "text/css; charset=utf-8")
        if path == "/app.js":
            return self._send(200, JS.encode(), "text/javascript; charset=utf-8")
        return self._send(404, b"No existe", "text/plain; charset=utf-8")

    do_HEAD = do_GET

    def _refuse(self) -> None:
        self._send(405, b"Solo lectura", "text/plain; charset=utf-8")

    do_POST = do_PUT = do_DELETE = do_PATCH = _refuse


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"          # en Windows SO_REUSEADDR dejaría a otro usar el mismo puerto

    def server_bind(self) -> None:
        if os.name == "nt":                         # en exclusiva: nadie se lo puede enganchar después (los que ya
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)   # estaban los ve in_use)
        super().server_bind()


def start(home: Path, port: int = PORT, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Arranca el tablero en un hilo y devuelve el servidor (shutdown() para cerrarlo)."""
    srv = _Server((host, port), _Handler)
    srv.daemon_threads = True
    srv.home = Path(home)
    real = srv.server_address[1]
    srv.allowed_hosts = {f"127.0.0.1:{real}", f"localhost:{real}"}
    threading.Thread(target=srv.serve_forever, name="tablero", daemon=True).start()
    return srv


def url(srv: ThreadingHTTPServer) -> str:
    return f"http://127.0.0.1:{srv.server_address[1]}"


def in_use(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    """¿Ya atiende alguien en ese puerto? Windows deja enganchar 127.0.0.1:P aunque otro programa escuche en
    0.0.0.0:P (y le robaría sus conexiones locales): por eso se pregunta antes de tomar el puerto."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def start_free(home: Path, base: int = PORT, tries: int = PORT_TRIES, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """El tablero en el primer puerto libre desde `base`. Nunca uno que use otro programa del dueño."""
    last = None
    for port in range(base, base + tries) if base else (0,):
        if port and in_use(port, host):              # lo usa otro programa: ni se intenta
            last = OSError(f"{port} en uso")
            continue
        try:
            return start(home, port, host)
        except OSError as e:                         # ocupado: el siguiente
            last = e
    raise OSError(f"sin puerto libre entre {base} y {base + tries - 1}: {last}")


def announce(home: Path, srv: ThreadingHTTPServer) -> str:
    """Deja escrita la dirección real: la lee el instalador, y el acceso directo abre `tablero.html`, que
    redirige ahí (así sirve aunque el puerto cambie)."""
    address = url(srv) + "/"
    paths.write_json_atomic(Path(home) / "state" / "tablero.json", {"url": address, "ts": round(time.time())})
    page = (f'<!doctype html><meta charset="utf-8"><title>TRADING · tablero</title><meta http-equiv="refresh" '
            f'content="0; url={address}"><p>Abriendo el tablero: <a href="{address}">{address}</a></p>')
    (Path(home) / "tablero.html").write_text(page, encoding="utf-8")
    return address


def is_ours(address: str, timeout: float = 2.0) -> bool:
    """¿En esa dirección responde ESTE tablero (y no otro programa)?"""
    import urllib.request
    try:
        with urllib.request.urlopen(address.rstrip("/") + "/api/estado", timeout=timeout) as r:
            return "estado_texto" in json.load(r)
    except (OSError, ValueError):
        return False
