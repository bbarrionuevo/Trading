"""Arbitraje estadístico (pares cointegrados) SIN dinero real: backtest fuera de muestra y paper en vivo.

Idea (informes que mandó el dueño el 26-oct-2026): dos monedas que suelen moverse juntas a veces se
separan un rato y después vuelven. Se compra la que quedó barata respecto de la otra y se cierra cuando
la diferencia vuelve a lo normal. Opera con velas de 15 minutos: la distancia de la PC al exchange
(lo que hundió al market making) casi no importa.

Reglas fijadas de antemano (no se ajustan mirando resultados):
- Universo: las UNIVERSE monedas con más volumen en Binance contra USDT (sin stablecoins, envueltas
  ni apalancadas).
- Formación (FORM_DAYS días): por cada par (A, B), regresión log A = a + b·log B (b > 0). El residuo es
  la "diferencia". Es candidato si la prueba ADF del residuo da menos que ADF_CRIT (Engle-Granger al 1%:
  se prueban ~300 pares y al 5% aparecían 2 o 3 pares falsos por azar en cada formación),
  si tarda entre HL_MIN y HL_MAX velas en volver a la mitad (vida media) y si un movimiento de Z_IN
  desvíos paga al menos EDGE_X veces la comisión de la vuelta. Se eligen los TOP_K de ADF más negativo,
  sin repetir moneda.
- Operación (los TRADE_DAYS días siguientes, FUERA de la muestra de formación): z = (residuo − media)
  / desvío, con los valores de la formación. Entra con |z| ≥ Z_IN; sale cuando z vuelve a 0; stop con
  |z| ≥ Z_STOP o al pasar MAX_HOLD_HL vidas medias; cierra todo al terminar la ventana. Lo que se decide
  al cierre de una vela se ejecuta en la apertura de la siguiente.
- Dos versiones de la misma señal:
  - neutral: compra la barata y vende en corto la cara, montos según b. Mide la ventaja pura. Necesita
    margen o futuros: hoy las claves no lo permiten (R-01), habilitarlo lo decide el dueño.
  - contado: compra solo la barata con USDT (lo único que se puede hacer hoy). Queda expuesta a que
    todo el mercado baje.
- Costos por lado y por pata: COST_SPOT / COST_PERP en el backtest (comisión taker + medio spread); el
  paper en vivo compra al ask y vende al bid reales y suma solo la comisión.

python -m nexus.research_pairs backtest OUT_DIR [días]
python -m nexus.research_pairs live OUT_DIR minutos      (lo corre research-pairs.yml)
"""
import json
import math
import sys
import time
from itertools import combinations
from pathlib import Path

from .discovery.sources import BINANCE_DATA, _get, binance_klines

BAR_S = 900                                              # velas de 15 minutos
UNIVERSE, TOP_K = 25, 5
FORM_DAYS, TRADE_DAYS = 14, 3
ADF_CRIT = -3.90                                         # 1%: con 300 pares, al 5% salen ~3 falsos por azar
HL_MIN, HL_MAX = 4, 96                                   # de 1 hora a 1 día
Z_IN, Z_STOP, MAX_HOLD_HL = 2.0, 4.0, 3
EDGE_X = 3
COST_SPOT, COST_PERP = 0.0012, 0.0007                    # backtest: taker 0,10% / 0,05% + medio spread
FEE_SPOT, FEE_PERP = 0.0010, 0.0005                      # paper en vivo: el spread ya está en bid/ask
STABLE = {"USDC", "FDUSD", "TUSD", "DAI", "USDP", "BUSD", "EUR", "AEUR", "EURI", "USDE", "PYUSD", "USD1",
          "RLUSD", "XUSD", "BFUSD", "USDS", "U", "WBTC", "WBETH", "BETH", "PAXG"}
VARIANTS = ("neutral", "contado")


# ------------------------------------------------------------------ estadística (sin numpy)
def _solve(m, v):
    """Resuelve m·x = v (eliminación gaussiana con pivoteo); None si es singular."""
    n = len(v)
    a = [row[:] + [v[i]] for i, row in enumerate(m)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(a[r][c]))
        if abs(a[p][c]) < 1e-15:
            return None
        a[c], a[p] = a[p], a[c]
        for r in range(n):
            if r != c:
                f = a[r][c] / a[c][c]
                a[r] = [x - f * y for x, y in zip(a[r], a[c])]
    return [a[i][n] / a[i][i] for i in range(n)]


def ols(y, xs):
    """Mínimos cuadrados con constante: y = b0 + b1·x1 + ... Devuelve (coeficientes, errores estándar)."""
    rows = [[1.0] + [x[i] for x in xs] for i in range(len(y))]
    k = len(rows[0])
    xtx = [[sum(r[i] * r[j] for r in rows) for j in range(k)] for i in range(k)]
    b = _solve(xtx, [sum(r[i] * yy for r, yy in zip(rows, y)) for i in range(k)])
    if b is None:
        return None, None
    res = [yy - sum(bi * ri for bi, ri in zip(b, r)) for r, yy in zip(rows, y)]
    s2 = sum(e * e for e in res) / max(len(y) - k, 1)
    se = []
    for i in range(k):
        col = _solve(xtx, [1.0 if j == i else 0.0 for j in range(k)])
        se.append(math.sqrt(max(col[i] * s2, 0.0)) if col else float("inf"))
    return b, se


def adf(s):
    """Estadístico ADF (con constante y un rezago): Δs_t = a + γ·s_{t−1} + d·Δs_{t−1}. Más negativo = vuelve más."""
    ds = [s[i] - s[i - 1] for i in range(1, len(s))]
    b, se = ols(ds[1:], [s[1:-1], ds[:-1]])
    if not b or not se[1] or se[1] == float("inf"):
        return 0.0
    return b[1] / se[1]


def half_life(s):
    """Velas que tarda la diferencia en volver a la mitad (AR(1)); inf si no vuelve."""
    b, _ = ols([s[i] - s[i - 1] for i in range(1, len(s))], [s[:-1]])
    g = b[1] if b else 0.0
    return -math.log(2) / math.log(1 + g) if -1 < g < 0 else float("inf")


# ------------------------------------------------------------------ formación y simulación
def form(closes, i0, i1, top_k=TOP_K, cost=COST_PERP):
    """Pares cointegrados en las velas [i0, i1). closes: {sym: [cierre]} alineados."""
    logs = {s: [math.log(x) for x in c[i0:i1]] for s, c in closes.items()}
    cands = []
    for a, b in combinations(sorted(logs), 2):
        coef, _ = ols(logs[a], [logs[b]])
        if not coef or coef[1] <= 0:
            continue
        res = [ya - coef[0] - coef[1] * yb for ya, yb in zip(logs[a], logs[b])]
        mu = sum(res) / len(res)
        sd = math.sqrt(sum((r - mu) ** 2 for r in res) / (len(res) - 1))
        if sd <= 0 or Z_IN * sd / (1 + coef[1]) < EDGE_X * 2 * cost:
            continue                                        # la vuelta no pagaría la comisión
        stat, hl = adf(res), half_life(res)
        if stat < ADF_CRIT and HL_MIN <= hl <= HL_MAX:
            cands.append({"a": a, "b": b, "alpha": coef[0], "beta": coef[1], "mu": mu, "sd": sd,
                          "hl": round(hl, 1), "adf": round(stat, 2)})
    cands.sort(key=lambda c: c["adf"])
    used, out = set(), []
    for c in cands:
        if c["a"] not in used and c["b"] not in used:
            out.append(c)
            used |= {c["a"], c["b"]}
        if len(out) == top_k:
            break
    return out


def zscore(p, pa, pb):
    return (math.log(pa) - p["alpha"] - p["beta"] * math.log(pb) - p["mu"]) / p["sd"]


def pnl(p, side, a0, b0, a1, b1, cost_spot=COST_SPOT, cost_perp=COST_PERP):
    """Resultado en % del capital del par. side=+1: diferencia baja (A barata, compra A); −1: al revés.
    a0/b0 = precios de entrada, a1/b1 = de salida."""
    ra, rb = a1 / a0 - 1, b1 / b0 - 1
    wa, wb = 1 / (1 + p["beta"]), p["beta"] / (1 + p["beta"])
    neutral = side * (wa * ra - wb * rb) - 2 * cost_perp
    contado = (ra if side > 0 else rb) - 2 * cost_spot
    return {"neutral": round(neutral * 100, 4), "contado": round(contado * 100, 4)}


def decide(p, z, pos, held):
    """Señal con las reglas fijas: +1/−1 para abrir, 0 para cerrar, None para no hacer nada."""
    if pos is None:
        if z <= -Z_IN:
            return +1
        if z >= Z_IN:
            return -1
        return None
    side = pos["side"]
    if side * z >= 0:
        return 0                                            # volvió a la media
    if abs(z) >= Z_STOP or held >= MAX_HOLD_HL * p["hl"]:
        return 0
    return None


def simulate(data, pairs, i0, i1):
    """Opera los pares en las velas [i0, i1). data: {sym: (aperturas, cierres)}. Ejecuta en la apertura siguiente."""
    trades = []
    for p in pairs:
        oa, ca = data[p["a"]]
        ob, cb = data[p["b"]]
        pos = None
        for i in range(i0, i1):
            last = i == i1 - 1 or i + 1 >= len(oa)
            z = zscore(p, ca[i], cb[i])
            held = i - pos["i"] if pos else 0
            d = decide(p, z, pos, held)
            if pos and (d == 0 or last):
                j = min(i + 1, len(oa) - 1)
                why = "media" if d == 0 and pos["side"] * z >= 0 else ("fin de ventana" if d != 0 else "stop")
                trades.append({"a": p["a"], "b": p["b"], "side": pos["side"], "bars": j - pos["i"], "why": why,
                               **pnl(p, pos["side"], pos["pa"], pos["pb"], oa[j], ob[j])})
                pos = None
            elif pos is None and d in (1, -1) and not last:
                pos = {"side": d, "i": i + 1, "pa": oa[i + 1], "pb": ob[i + 1]}
    return trades


def stats(xs):
    if not xs:
        return {"n": 0}
    n, m = len(xs), sum(xs) / len(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    return {"n": n, "win": round(sum(x > 0 for x in xs) / n, 3), "mean": round(m, 4), "total": round(sum(xs), 3),
            "low": round(m - sd / math.sqrt(n), 4) if n > 1 else None}


def walk_forward(data, n_bars):
    """Ventanas sucesivas: forma con FORM_DAYS, opera los TRADE_DAYS siguientes, avanza TRADE_DAYS."""
    form_n, trade_n = FORM_DAYS * 86400 // BAR_S, TRADE_DAYS * 86400 // BAR_S
    closes = {s: c for s, (_, c) in data.items()}
    windows, i = [], form_n
    while i + trade_n <= n_bars:
        pairs = form(closes, i - form_n, i)
        tr = simulate(data, pairs, i, i + trade_n)
        windows.append({"start_bar": i, "pairs": [f"{p['a']}/{p['b']}" for p in pairs], "trades": tr})
        i += trade_n
    return windows


def summarize(windows, days):
    out = {}
    for v in VARIANTS:
        xs = [t[v] for w in windows for t in w["trades"]]
        per_w = [sum(t[v] for t in w["trades"]) / TOP_K for w in windows]
        s = stats(xs)
        s["capital_pct"] = round(sum(per_w), 3)                  # capital repartido en TOP_K pares
        s["per_day_pct"] = round(sum(per_w) / days, 4) if days else None
        s["windows_up"] = f"{sum(x > 0 for x in per_w)}/{len(per_w)}"
        out[v] = s
    return out


# ------------------------------------------------------------------ datos
def universe(n=UNIVERSE):
    rows = [t for t in _get(f"{BINANCE_DATA}/api/v3/ticker/24hr") if t["symbol"].endswith("USDT")]
    def ok(sym):
        base = sym[:-4]
        return base not in STABLE and not any(base.endswith(x) for x in ("UP", "DOWN", "BULL", "BEAR"))
    rows = [t for t in rows if ok(t["symbol"]) and float(t.get("count") or 0) > 0]
    rows.sort(key=lambda t: -float(t["quoteVolume"]))
    return [t["symbol"] for t in rows[:n]]


def history(symbols, days, klines=binance_klines, now=None):
    """{sym: (aperturas, cierres)} alineados en el tiempo, solo velas cerradas."""
    now = now or time.time()
    need, raw = days * 86400 // BAR_S, {}
    for s in symbols:
        rows, end = {}, None
        while len(rows) < need:
            k = klines(s, "15m", 1000, end)
            if not k:
                break
            rows.update({r[0]: (r[1], r[4]) for r in k if r[0] / 1000 + BAR_S <= now})
            end = k[0][0] - 1
            if len(k) < 1000:
                break
        if len(rows) >= need * 0.98:                        # las listadas hace poco acortarían todo
            raw[s] = rows
    common = sorted(set.intersection(*(set(r) for r in raw.values())))[-need:] if raw else []
    return {s: ([r[t][0] for t in common], [r[t][1] for t in common]) for s, r in raw.items()}, common


# ------------------------------------------------------------------ paper en vivo
def live_step(state, closes, books, now, close_all=False):
    """Un ciclo tras el cierre de cada vela. closes: {sym: último cierre}; books: {sym: (bid, ask)}.
    close_all: fin de la ventana (se vuelven a elegir los pares), como en el backtest."""
    state.setdefault("open", {})
    state.setdefault("closed", [])
    for key, pos in list(state["open"].items()):
        p = pos["p"]
        if not all(s in closes and s in books for s in (p["a"], p["b"])):
            continue
        z = zscore(p, closes[p["a"]], closes[p["b"]])
        pos["z"] = round(z, 2)
        if decide(p, z, pos, (now - pos["ts"]) / BAR_S) == 0 or close_all:
            (ab, aa), (bb, ba) = books[p["a"]], books[p["b"]]
            a1 = ab if pos["side"] > 0 else aa                  # vende lo comprado al bid, recompra el corto al ask
            b1 = ba if pos["side"] > 0 else bb
            why = "media" if pos["side"] * z >= 0 else ("stop" if not close_all else "fin de ventana")
            state["closed"].append({"pair": key, "side": pos["side"], "opened": pos["ts"], "closed": now,
                                    "z_in": pos["z_in"], "z_out": round(z, 2), "why": why,
                                    **pnl(p, pos["side"], pos["pa"], pos["pb"], a1, b1, FEE_SPOT, FEE_PERP)})
            del state["open"][key]
    if close_all:
        return state
    busy = {s for pos in state["open"].values() for s in (pos["p"]["a"], pos["p"]["b"])}
    for p in state.get("pairs", []):
        key = f"{p['a']}/{p['b']}"
        if key in state["open"] or {p["a"], p["b"]} & busy or not all(s in closes and s in books for s in (p["a"], p["b"])):
            continue
        z = zscore(p, closes[p["a"]], closes[p["b"]])
        d = decide(p, z, None, 0)
        if d:
            (ab, aa), (bb, ba) = books[p["a"]], books[p["b"]]
            state["open"][key] = {"p": p, "side": d, "ts": now, "z_in": round(z, 2), "z": round(z, 2),
                                  "pa": aa if d > 0 else ab, "pb": bb if d > 0 else ba}
            busy |= {p["a"], p["b"]}
    return state


def books_now(symbols):
    q = json.dumps(sorted(symbols), separators=(",", ":"))
    return {t["symbol"]: (float(t["bidPrice"]), float(t["askPrice"]))
            for t in _get(f"{BINANCE_DATA}/api/v3/ticker/bookTicker", params={"symbols": q})}


def run_live(out_dir, minutes, now=time.time, sleep=time.sleep):
    out = Path(out_dir)
    path = out / "paper_pairs.json"
    state = json.loads(path.read_text()) if path.exists() else {}
    end = now() + minutes * 60
    while True:
        due = now() - state.get("formed", 0) >= TRADE_DAYS * 86400
        syms = {s for p in state.get("pairs", []) for s in (p["a"], p["b"])} | \
               {s for pos in state.get("open", {}).values() for s in (pos["p"]["a"], pos["p"]["b"])}
        try:
            if syms:
                closes = {s: binance_klines(s, "15m", 2)[-2][4] for s in syms}   # última vela cerrada
                live_step(state, closes, books_now(syms), now(), close_all=due)
            if due:
                data, ts = history(universe(), FORM_DAYS)
                state["pairs"] = form({s: c for s, (_, c) in data.items()}, 0, len(ts)) if ts else []
                state["formed"] = now()
        except Exception as e:                                       # noqa: BLE001 (sin red: reintenta en la vela siguiente)
            print(f"ciclo fallido: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        state["ts"] = now()
        path.write_text(json.dumps(state, indent=1))
        (out / "paper_pairs.md").write_text(report_live(state))
        nxt = (now() // BAR_S + 1) * BAR_S + 20                   # 20 s después del cierre de la vela
        if nxt >= end:
            return state
        sleep(max(nxt - now(), 1))


# ------------------------------------------------------------------ informes
def _fmt(s):
    if not s.get("n"):
        return "sin vueltas"
    low = "—" if s.get("low") is None else f"{s['low']:+.3f}%"
    return (f"{s['n']} vueltas · ganadoras {s['win'] * 100:.0f}% · media {s['mean']:+.3f}% · cota inferior {low}")


def report_backtest(summary, windows, days, symbols):
    lines = [f"# Arbitraje estadístico: backtest fuera de muestra ({days} días, velas de 15 min)", "",
             f"Universo ({len(symbols)}): {', '.join(s[:-4] for s in symbols)}.",
             f"Forma {FORM_DAYS} días, opera los {TRADE_DAYS} siguientes; entra con |z| ≥ {Z_IN:g}, sale en 0, "
             f"stop {Z_STOP:g}. Costos por lado y pata: contado {COST_SPOT * 100:.2f}%, neutral {COST_PERP * 100:.2f}%.",
             "", "| Versión | Vueltas | Ganadoras | Media/vuelta | Cota inferior | Capital total | Por día | Ventanas + |",
             "|---|---|---|---|---|---|---|---|"]
    for v in VARIANTS:
        s = summary[v]
        if not s.get("n"):
            lines.append(f"| {v} | 0 | — | — | — | — | — | {s['windows_up']} |")
            continue
        low = "—" if s["low"] is None else f"{s['low']:+.3f}%"
        lines.append(f"| {v} | {s['n']} | {s['win'] * 100:.0f}% | {s['mean']:+.3f}% | {low} | "
                     f"{s['capital_pct']:+.2f}% | {s['per_day_pct']:+.3f}% | {s['windows_up']} |")
    lines += ["", "Neutral necesita margen o futuros (hoy no habilitado). Contado = solo comprar la barata.", "",
              "## Ventanas", ""]
    for w in windows:
        tot = {v: sum(t[v] for t in w["trades"]) / TOP_K for v in VARIANTS}
        lines.append(f"- {', '.join(w['pairs']) or 'sin pares'}: {len(w['trades'])} vueltas · "
                     f"neutral {tot['neutral']:+.2f}% · contado {tot['contado']:+.2f}%")
    return "\n".join(lines) + "\n"


def report_live(state):
    closed = state.get("closed", [])
    pairs = ", ".join("%s/%s (vida media %g velas)" % (p["a"], p["b"], p["hl"]) for p in state.get("pairs", []))
    lines = ["# Arbitraje estadístico: paper en vivo (precios reales de Binance, sin dinero)", "",
             f"Pares vigentes: {pairs or 'ninguno'}.", ""]
    for v in VARIANTS:
        lines.append(f"- **{v}:** {_fmt(stats([t[v] for t in closed]))}")
    lines += ["", "## Abiertas", ""]
    for k, pos in state.get("open", {}).items():
        lines.append(f"- {k}: {'compra A' if pos['side'] > 0 else 'compra B'} · z entrada {pos['z_in']} · z ahora {pos['z']}")
    lines += ["", "## Últimas cerradas", ""]
    for t in closed[-10:][::-1]:
        lines.append(f"- {time.strftime('%d/%m %H:%M', time.gmtime(t['closed']))} {t['pair']} ({t['why']}): "
                     f"neutral {t['neutral']:+.3f}% · contado {t['contado']:+.3f}%")
    return "\n".join(lines) + "\n"


def run_backtest(out_dir, days=60):
    out = Path(out_dir)
    syms = universe()
    data, ts = history(syms, days)
    windows = walk_forward(data, len(ts))
    trade_days = len(windows) * TRADE_DAYS
    summary = summarize(windows, trade_days)
    (out / "pairs_backtest.json").write_text(json.dumps({"ts": time.time(), "days": trade_days, "symbols": syms,
                                                         "summary": summary, "windows": windows}, indent=1))
    (out / "pairs_backtest.md").write_text(report_backtest(summary, windows, trade_days, syms))
    return summary


if __name__ == "__main__":
    mode, out_dir = sys.argv[1], sys.argv[2]
    if mode == "backtest":
        print(json.dumps(run_backtest(out_dir, int(sys.argv[3]) if len(sys.argv) > 3 else 60), indent=1))
    else:
        run_live(out_dir, float(sys.argv[3]))
