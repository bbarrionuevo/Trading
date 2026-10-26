"""Agente autónomo de market making — SOLO PAPER (no existe código que envíe órdenes).

- Datos públicos de MEXC (REST). Si hay claves en NEXUS_HOME/.env, verifica la comisión real al
  arrancar y usa la mayor entre la configurada y la real (nunca una menor).
- Toda entrada pasa por el veto del RiskManager (R-03); las salidas siempre se permiten.
- Datos viejos => se cancela la compra pendiente (R-07).
- Estado del paper, riesgo y latido en NEXUS_HOME/state (R-09); logs rotados en NEXUS_HOME/logs.
- Diario de cada vuelta cerrada (state/trades.jsonl) y evaluación de los criterios R-15.
- Laboratorio: además de los pares operados, mide en paper 10-12 pares de MEXC elegidos solos por
  spread y volumen (se renuevan cada pocas horas). No pasan por el riesgo ni afectan al capital:
  solo generan evidencia para decidir qué pares operar. Un par cuyas últimas N vueltas pierden en
  promedio se pausa (no vuelve a elegirse) por unas horas.
- Los pedidos de cada ciclo van en paralelo: desde el PC cada uno tarda ~270 ms y en serie las
  órdenes simuladas quedaban viejas (más llenados adversos y stops tardíos que un bot real).
python -m nexus.agent.maker_agent
"""
import json
import logging
import os
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml

from .. import allocator, evidence, paths, selector
from ..exchanges.mexc import MexcReadOnly, parse_fee
from ..live_paper_maker import Book
from .risk import RiskManager

CFG = Path(__file__).resolve().parents[2] / "config" / "agent.yaml"
log = logging.getLogger("nexus.agent")
POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="datos")


def fetch_many(client, syms, timeout=15):
    """Libro y operaciones de varios pares EN PARALELO: {par: (libro, operaciones) | Exception}."""
    futs = {s: (POOL.submit(client.book_ticker, s), POOL.submit(client.trades, s)) for s in syms}
    out = {}
    for s, (fb, ft) in futs.items():
        try:
            out[s] = (fb.result(timeout=timeout), ft.result(timeout=timeout))
        except Exception as e:                                        # noqa: BLE001 (se informa)
            out[s] = e
    return out


def draining(max_age_s=2400):
    """True si el actualizador pospuso hace poco una versión validada SOLO por posiciones abiertas.

    Entonces no se abren posiciones nuevas: las abiertas cierran (como maker, stop o tiempo máximo)
    y en el siguiente ciclo la actualización entra. Si pospuso por otra causa (CI, aprobación), no.
    """
    u = paths.read_json(paths.state("update.json"), {})
    reasons = u.get("reasons") or []
    return (u.get("result") == "pospuesta" and time.time() - u.get("ts", 0) < max_age_s
            and len(reasons) == 1 and reasons[0].startswith("hay posiciones abiertas"))


class Gate:
    """Adaptador entre los libros (estrategia) y el RiskManager (veto)."""

    def __init__(self, risk):
        self.risk, self.books = risk, {}

    def allow_entry(self, notional, exposure):
        if draining():
            self.risk.last_reason = "vaciando posiciones para instalar una actualización"
            return False
        return self.risk.allow_entry(notional, exposure)

    def exposure(self):
        return sum(b.exposure() for b in self.books.values())

    def record_order(self):
        self.risk.record_order()

    def on_close(self, pnl, was_stop):
        self.risk.on_close(pnl, was_stop)
        log.info(json.dumps({"event": "close", "pnl": round(pnl, 4), "stop": was_stop,
                             "day_pnl": round(self.risk.s["pnl_day"], 4)}))


def setup_logging():
    h = RotatingFileHandler(paths.logs("agent.log"), maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handlers = [h] + ([logging.StreamHandler()] if sys.stderr and sys.stderr.isatty() else [])
    logging.basicConfig(level=logging.INFO, handlers=handlers)


def load_config():
    cfg = yaml.safe_load(CFG.read_text(encoding="utf-8"))
    if cfg.get("live") is not False:
        raise SystemExit("live debe ser false: el modo real no existe en esta versión (R-02).")
    return cfg


def verified_fees(client, cfg):
    maker, taker = cfg["maker_fee_pct"], cfg["taker_fee_pct"]
    if not client.has_keys:
        log.warning("sin claves MEXC: comisiones no verificadas, se usan las configuradas")
        return maker, taker
    for sym in cfg["symbols"]:
        try:
            f = parse_fee(client.trade_fee(sym))
        except Exception as e:
            log.warning("no se pudo verificar comisión de %s: %s", sym, type(e).__name__)
            continue
        if f:
            maker, taker = max(maker, f[0]), max(taker, f[1])
    log.info("comisiones en uso maker=%.4f%% taker=%.4f%%", maker, taker)
    return maker, taker


def feed(bk, trades, seen):
    """Pasa al libro solo las operaciones NUEVAS de MEXC (defecto 3).

    Se llama ANTES de on_book: las operaciones ocurridas desde el sondeo anterior se evalúan contra la
    orden que estaba puesta en ese intervalo, y recién después se mueve la orden al libro nuevo. Al
    revés, el paper esquivaba compras perdedoras y stops (sesgo optimista).
    En el primer sondeo de un libro (arranque, reinicio o par nuevo del laboratorio) las operaciones son
    anteriores a cualquier orden nuestra: solo se marcan como vistas.
    """
    apply_trades(bk, fresh_trades(trades, seen))


def live_auth():
    from .live import authorization
    return authorization()


def calibration(auth, trades_path=None, real_path=None, now=None):
    """Mismo par, mismo período: lo que dio el paper contra lo que dio el dinero real, en % del monto de
    cada vuelta (n, vueltas por hora, media %). Es el objetivo de la calibración: si el real llena mucho
    menos o gana menos que el paper, el modelo de paper miente."""
    from .live import REAL_TRADES
    if not auth:
        return None
    now = now or time.time()
    since = auth.get("ts", now)
    hours = max((now - since) / 3600, 1e-9)
    paper = [t for t in evidence.load(trades_path or paths.state("trades.jsonl"), since=since)
             if t.get("strategy") == STRATEGY["tag"]]
    real = [t for t in evidence.load(real_path or paths.state(REAL_TRADES), since=since, min_model=0)]
    def ret(t):                                      # % del monto de la vuelta (paper y real usan montos distintos)
        return t["pnl"] / (t.get("notional") or 40.0) * 100

    out = {}
    for s in dict.fromkeys(real_symbols(auth) + sorted({t["sym"] for t in real})):   # modo auto: los que operó
        p = [ret(t) for t in paper if t["sym"] == s]
        r = [ret(t) for t in real if t["sym"] == s]
        out[s] = {"hours": round(hours, 2),
                  "paper": evidence.stats(p) | {"per_hour": round(len(p) / hours, 2)},
                  "real": evidence.stats(r) | {"per_hour": round(len(r) / hours, 2)}}
    return out


def books_snapshot(gate, desk, hedge=None):
    """Libros para paper_books.json: el actualizador lee de acá si hay posiciones abiertas (R-13),
    así que los reales van también (clave PAR#real) aunque su estado completo esté en live_books.json.
    La cobertura figura como posición (PAR#cobertura) mientras tenga algo llenado sin cubrir."""
    return {x: b.to_dict() for x, b in gate.books.items()} | (
        {f"{x}#real": b.to_dict() for x, b in desk.books.items()} if desk else {}) | (
        {f"{x}#cobertura": {"state": "long" if (p.hedge or any(v[0] for v in p.pending.values())) else "flat"}
         for x, p in hedge.pairs.items()} if hedge else {})


def rotate(desk, cfg, client, maker, taker):
    """Si el selector cambió las combinaciones (par, variante) del dinero real, se rearma el escritorio,
    pero SOLO cuando ningún libro tiene posición ni estado desconocido (nunca se abandona una posición).
    Una compra puesta sin llenar no impide el cambio: el cierre del escritorio la cancela (antes se
    exigía "sin órdenes" y, como casi siempre hay una compra puesta, el cambio casi nunca ocurría)."""
    want = real_choices()
    have = {s: getattr(b, "strategy", None) for s, b in desk.books.items()}
    idle = all(b.state == "flat" and not b.unknown and b.pos_qty * (b.bid or b.entry or 0) < b.rules.min_quote
               for b in desk.books.values())
    if want == have or not idle:
        return desk
    log.info("real: cambian las combinaciones %s -> %s", have, want)
    desk.shutdown()
    return build_desk(cfg, client, maker, taker)


def variant_spec(cfg, tag):
    """Parámetros de una variante para operarla en real: la vigente usa la config base, el control no
    filtra volatilidad y las demás vienen de lab.variants."""
    if tag in (None, STRATEGY["tag"]):
        return {}
    if tag == CONTROL_TAG:
        return {"max_vol_1m_pct": None}
    return dict(((cfg.get("lab") or {}).get("variants") or {}).get(tag) or {})


def real_variants(cfg):
    return {STRATEGY["tag"], CONTROL_TAG} | set(((cfg.get("lab") or {}).get("variants") or {}))


def selector_tick(cfg, desk, now=None):
    """Cada 5 min: puntúa las combinaciones del laboratorio, descarta las que el real perdió y elige
    de nuevo (nexus/selector.py). Lo usa real_choices(); rotate() aplica el cambio sin posición."""
    from .live import REAL_TRADES
    now = now or time.time()
    since = now - selector.DAYS * 86400
    paper = evidence.load(paths.state("trades.jsonl"), since=since)
    real = evidence.load(paths.state(REAL_TRADES), since=since, min_model=0)
    auth = live_auth()
    capital = sum(b.notional for b in desk.books.values()) if desk else float((auth or {}).get("capital_eur") or 0)
    blocked = [s for s, b in (desk.books.items() if desk else []) if b.blocked and b.state == "flat"]
    st = selector.update(paths.read_json(paths.state("selector.json"), {}), selector.combo_scores(paper, now), auth,
                         now, real, paper, capital, STRATEGY["tag"], real_variants(cfg), blocked=blocked)
    for h in st["history"][-5:]:
        if h["ts"] == now:
            log.warning("selector: %s %s %s (%s)", h["event"], h["sym"], h["strategy"], h.get("why") or h.get("score"))
    save_state("selector.json", st)
    return st


def live_wanted():
    """Hay autorización vigente o quedó una posición/orden real guardada que hay que gestionar."""
    from .live import STATE_FILE
    saved = paths.read_json(paths.state(STATE_FILE), {})
    return bool(live_auth()) or any(isinstance(d, dict) and (d.get("state") == "long" or d.get("order"))
                                    for d in saved.values())


STRATEGY = {"tag": None}                                       # versión de la estrategia (agent.yaml)
CONTROL_TAG = "control"                                        # misma estrategia SIN filtro de volatilidad


class Market:
    """Clima general medido con BTC/USDT en tiempo real: volatilidad y variación de los últimos 15 min.
    Pocas medidas y simples a propósito: describir el mercado, no predecirlo."""

    def __init__(self, now=time.time):
        self.now, self.mids = now, deque(maxlen=900)

    def update(self, bt):
        b, a = float(bt["bidPrice"]), float(bt["askPrice"])
        if b > 0 and a > 0:
            self.mids.append((self.now(), (a + b) / 2))

    def snapshot(self):
        if len(self.mids) < 10:
            return {}
        t1, p1 = self.mids[-1]
        old = [(t, m) for t, m in self.mids if t >= t1 - 900]
        pts = list(self.mids)[-60:]
        import math
        var = sum(math.log(b / a) ** 2 for (_, a), (_, b) in zip(pts, pts[1:]))
        secs = pts[-1][0] - pts[0][0]
        return {"btc_ret15_pct": round((p1 / old[0][1] - 1) * 100, 3) if old else None,
                "btc_vol_1m_pct": round(math.sqrt(var / secs * 60) * 100, 4) if secs > 0 else None}


MARKET = Market()


def fresh_trades(trades, seen):
    """Operaciones que no se habían visto todavía (y las marca como vistas)."""
    out = []
    for t in sorted(trades, key=lambda t: t["time"]):
        k = (t["time"], t["price"], t["qty"], t["isBuyerMaker"])
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


def apply_trades(bk, new):
    """Aplica operaciones nuevas a un libro; su primer lote solo lo prepara (defecto 3)."""
    if not getattr(bk, "primed", False):
        bk.primed = True
        return
    for t in new:
        bk.on_trade(float(t["price"]), float(t["qty"]), t["isBuyerMaker"])


def journal(entry, strategy=None):
    """Agrega una vuelta cerrada al diario (una línea JSON). Tolerante a bloqueos breves.
    `model` marca la versión del modelo de llenado y `strategy` la de la estrategia: cada versión
    tiene que probarse por sí misma (la evidencia de una no cuenta para otra)."""
    line = json.dumps(entry | {"model": evidence.FILL_MODEL, "strategy": strategy or STRATEGY["tag"]}) + "\n"
    for _ in range(5):
        try:
            with open(paths.state("trades.jsonl"), "a", encoding="utf-8") as f:
                f.write(line)
            return
        except OSError:
            time.sleep(0.2)
    log.warning("no se pudo escribir el diario de vueltas")


STABLE = ("USDC", "USDT", "FDUSD", "TUSD", "DAI", "USDE", "PYUSD", "USD1", "EUR", "EURC", "EURQ", "USDQ")


def select_lab(client, lab_cfg, exclude, notional=40.0):
    """Elige pares USDT de MEXC con volumen suficiente y spread en rango (datos públicos).

    Orden: ganancia bruta esperable por hora = spread % × llenados posibles por hora. Con el modelo de
    llenado 3 una orden se llena recién cuando el volumen cubre la cola visible Y la orden entera, así
    que los llenados por hora se estiman como (volumen por hora / 2, la mitad golpea el bid) dividido
    (cola del mejor bid + nuestra orden), con tope de 60. Es una fórmula fija (no se ajusta mirando
    resultados) para explorar primero donde puede haber evidencia; promover sigue siendo por evidencia."""
    vol = {t["symbol"]: float(t.get("quoteVolume") or 0) for t in client._get("/api/v3/ticker/24hr")}
    out = []
    for t in client._get("/api/v3/ticker/bookTicker"):
        sym = t["symbol"]
        if not sym.endswith("USDT") or sym in exclude:
            continue
        base = sym[:-4]
        if base in STABLE or base.endswith(("3L", "3S", "5L", "5S", "UP", "DOWN")):
            continue
        b, a = float(t.get("bidPrice") or 0), float(t.get("askPrice") or 0)
        if b <= 0 or a <= 0:
            continue
        spread = (a / b - 1) * 100
        if lab_cfg["min_spread_pct"] <= spread <= lab_cfg["max_spread_pct"] \
                and vol.get(sym, 0) >= lab_cfg["min_quote_volume_usdt"]:
            queue = float(t.get("bidQty") or 0) * b
            fills_h = min(vol[sym] / 24 / 2 / (queue + notional), 60.0)
            out.append((spread * fills_h if lab_cfg.get("rank", "edge") == "edge" else vol[sym], sym, round(spread, 3)))
    out.sort(reverse=True)
    return [(s, sp) for _, s, sp in out[:lab_cfg["max_symbols"]]]


def save_state(name, data, tries=5):
    """Escritura tolerante: en Windows un antivirus o un lector puede trabar el archivo un instante."""
    for i in range(tries):
        try:
            paths.write_json_atomic(paths.state(name), data)
            return True
        except OSError as e:
            if i == tries - 1:
                log.warning("no se pudo guardar %s: %s", name, e)
            time.sleep(0.2)
    return False


def real_choices(auth=None, max_age_s=1800):
    """{par: variante} que el dinero real puede operar ahora, según el selector (nexus/selector.py):
    con pares fijos, la mejor variante de cada par autorizado; en modo automático (`--auto N`), las N
    mejores combinaciones del laboratorio. Si el selector todavía no corrió (o su estado es viejo): los
    pares autorizados con la vigente, o los N promovidos por el asignador."""
    auth = live_auth() if auth is None else auth
    if not auth:
        return {}
    st = paths.read_json(paths.state("selector.json"), {})
    if time.time() - (st.get("ts") or 0) < max_age_s:
        ch = {s: c["strategy"] for s, c in (st.get("choices") or {}).items()}
        if auth.get("auto"):
            return dict(list(ch.items())[:auth["auto"]])
        return {s: t for s, t in ch.items() if s in (auth.get("symbols") or [])}
    picks = [r["sym"] for r in allocator.recommend(paths.read_json(paths.state("allocator.json"), {}),
                                                    top=auth.get("auto") or 0)] if auth.get("auto") else []
    return {s: STRATEGY["tag"] for s in dict.fromkeys(list(auth.get("symbols") or []) + picks)}


def real_symbols(auth=None):
    """Pares que el dinero real puede operar ahora (ver real_choices)."""
    return list(real_choices(auth))


def desk_symbols(auth, saved):
    """Pares del escritorio real: los que puede operar ahora y los que quedaron con posición, orden o
    estado desconocido guardados (hay que seguir gestionándolos aunque ya no estén autorizados)."""
    pending = [s for s, d in saved.items() if isinstance(d, dict)
               and (d.get("state") == "long" or d.get("order") or d.get("unknown"))]
    return sorted(set(real_symbols(auth)) | set(pending))


def n_real_pairs(auth, syms):
    """Entre cuántos pares se reparte el capital: los autorizados o, en modo automático, los del
    escritorio (si se suma otro promovido, rotate() rearma con el reparto nuevo, siempre sin posición)."""
    if (auth or {}).get("auto"):
        return max(1, len(syms))
    return max(1, len((auth or {}).get("symbols") or syms))


def real_notional(auth, cfg, balances, books, n_pairs):
    """Monto por par del dinero real: el capital autorizado, sin pasar lo que de verdad hay en la cuenta
    (USDT libre + en órdenes + lo invertido en las posiciones, con 2% de margen para que MEXC no rechace
    por saldo), repartido entre los pares y sin pasar el máximo por posición de la config de riesgo.
    Con `authorize --todo` el capital autorizado es el tope (100 €): se opera todo lo que haya y las
    ganancias se reinvierten. Contar lo invertido evita que un reinicio con una posición abierta achique
    las compras (25-oct: 9,15 en vez de 20 tras reiniciar comprado en ZRO)."""
    from . import live
    from .hedge_live import hedge_authorization
    usdt = sum(float(b.get("free") or 0) + float(b.get("locked") or 0) for b in balances if b.get("asset") == "USDT")
    h = hedge_authorization()                           # la cobertura necesita su USDT en MEXC: no se toca
    reserve = float(h["lot_usdt"]) * len(h["symbols"]) if h else 0.0
    avail = max(usdt - reserve, 0.0) + sum(bk.pos_cost for bk in books.values())
    cap = float((auth or {}).get("capital_eur") or live.MAX_CAPITAL_EUR)
    return round(min(min(cap, avail * 0.98) / n_pairs, cfg["risk"]["max_position_eur"]), 2)


def resize(desk, cfg, balances):
    """Cada 10 min, con el saldo ya leído: el monto por par sigue al capital de la cuenta (ganancias
    reinvertidas, depósitos nuevos, pérdidas). Solo en libros sin posición: nunca a mitad de una vuelta
    (la compra puesta se re-cotiza sola con el monto nuevo)."""
    auth = live_auth()
    if not auth or balances is None:
        return
    new = real_notional(auth, cfg, balances, desk.books, n_real_pairs(auth, list(desk.books)))
    for bk in desk.books.values():
        if bk.state == "flat" and not bk.unknown and abs(bk.notional - new) >= 0.01:
            log.info("real %s: monto por vuelta %.2f -> %.2f", bk.sym, bk.notional, new)
            bk.notional = new


def build_desk(cfg, client, maker, taker):
    """Libros con DINERO REAL (Fase 6), solo si hay autorización local vigente del dueño o quedó una
    posición/orden real de antes (hay que seguir gestionándola aunque la autorización venza)."""
    from . import live
    auth = live.authorization()
    saved = paths.read_json(paths.state(live.STATE_FILE), {})
    choices = real_choices(auth)
    syms = desk_symbols(auth, saved)
    if not syms:
        return None
    from ..exchanges.mexc_trade import MexcTrader
    trader = MexcTrader()
    try:
        if not trader.has_keys:
            raise RuntimeError("no hay clave de MEXC en .env")
        acc = trader.account()
        if acc.get("canTrade") is False:
            raise RuntimeError("la clave de MEXC no tiene permiso de trading")
        if acc.get("canWithdraw") and not (auth or {}).get("sin_retiro_confirmado"):
            raise RuntimeError("R-01: la cuenta informa retiros y el dueño no confirmó que la clave no los tenga")
    except Exception as e:                                           # noqa: BLE001
        log.error("MODO REAL NO ACTIVADO: %s", e)
        return None
    lmaker, ltaker = verified_fees(client, {**cfg, "symbols": syms})
    rgate = Gate(RiskManager(cfg["risk"], state_path=paths.state("risk_real.json")))
    olog, books = live.OrderLog(), {}
    notional = 1.0                                   # se fija abajo con el saldo real de la cuenta
    for s in syms:
        try:
            rules = live.Rules(trader.exchange_info(s), s)
        except Exception as e:                                       # noqa: BLE001
            log.error("real %s: sin reglas del par (%s): no se opera", s, e)
            continue
        tag = choices.get(s) or STRATEGY["tag"]                 # un par con posición guardada: la vigente
        spec = variant_spec(cfg, tag)
        bk = live.LiveBook(s, trader, rules, olog, save=lambda: None, notional=notional, maker=lmaker, taker=ltaker,
                           stop_pct=cfg["stop_pct"], min_edge_pct=cfg["min_edge_pct"],
                           max_hold_s=cfg["max_hold_min"] * 60, gate=rgate,
                           on_closed=lambda e, t=tag: live.journal_real(e | {"strategy": t}),
                           max_vol_1m_pct=spec.get("max_vol_1m_pct", cfg.get("max_vol_1m_pct")),
                           min_imbalance=spec.get("min_imbalance"), improve_ticks=spec.get("improve_ticks", 0),
                           price_dp=rules.price_dp, context=MARKET.snapshot)
        if s in saved:
            bk.load(saved[s])
        bk.strategy = tag
        bk.entry_allowed = lambda s=s, t=tag: real_choices().get(s) == t
        books[s] = rgate.books[s] = bk
    if not books:
        return None
    desk = live.Desk(trader, client, rgate, books, olog)
    notional = real_notional(auth, cfg, acc.get("balances", []), books, n_real_pairs(auth, syms))
    for bk in books.values():
        bk.notional = notional
        bk.save_cb = desk.save
        try:
            px = float(client.book_ticker(bk.sym)["bidPrice"])
        except Exception:                                            # noqa: BLE001
            px = bk.entry or 0.0
        bk.reconcile(acc.get("balances", []), px)
    log.warning("MODO REAL activo en %s (autorización: %s)", list(books), auth)
    return desk


def build_hedge_desk(cfg, client):
    """Cobertura MEXC→Binance con dinero real (agent/hedge_live.py), solo con autorización local vigente
    (`authorize --cobertura`) o si quedó algo llenado sin cubrir de antes (hay que cubrirlo igual)."""
    from . import hedge_live as hl
    from . import live
    auth = hl.hedge_authorization()
    saved = paths.read_json(paths.state(hl.HEDGE_STATE), {})
    pending = [s for s, d in saved.items() if isinstance(d, dict)
               and (any((d.get("pending") or {}).get(k, [0])[0] for k in ("BUY", "SELL")) or d.get("hedge"))]
    syms = sorted(set((auth or {}).get("symbols") or []) | set(pending))
    if not syms:
        return None
    from ..exchanges.binance_trade import BinanceTrader
    from ..exchanges.mexc_trade import MexcTrader
    mexc, binance = MexcTrader(), BinanceTrader()
    try:
        if not mexc.has_keys or not (binance.key and binance.secret):
            raise RuntimeError("faltan las claves de MEXC o de Binance en .env")
        acc = binance.account()
        if acc.get("canTrade") is False:
            raise RuntimeError("la clave de Binance no tiene permiso de trading spot")
        # R-01 verificado, no confiado: Binance dice qué permisos tiene la clave de verdad
        from ..check_keys import key_warnings
        perms = binance.api_restrictions()
        if perms.get("enableWithdrawals"):
            raise RuntimeError("R-01: la clave de Binance PERMITE RETIROS. Desactivalo en Binance")
        if not perms.get("enableSpotAndMarginTrading"):
            raise RuntimeError("la clave de Binance no tiene trading spot habilitado")
        for w in key_warnings(perms):
            log.warning("clave de Binance: %s", w)
    except Exception as e:                                           # noqa: BLE001
        log.error("COBERTURA NO ACTIVADA: %s", e)
        return None
    lot = float((auth or {}).get("lot_usdt") or hl.MIN_LOT_USDT)
    hcfg = cfg.get("hedge") or {}
    gate = Gate(RiskManager(cfg["risk"], state_path=paths.state("risk_hedge.json")))
    olog, pairs = live.OrderLog(paths.state(hl.HEDGE_ORDERS)), {}
    for s in syms:
        try:
            mr, br = live.Rules(mexc.exchange_info(s), s), hl.BinanceRules(binance.exchange_info(s), s)
        except Exception as e:                                       # noqa: BLE001
            log.error("cobertura %s: sin reglas del par en MEXC o Binance (%s): no se opera", s, e)
            continue
        hp = hl.HedgePair(s, mexc, binance, mr, br, olog, lot_usdt=lot, min_edge_pct=hcfg.get("min_edge_pct", 0.1),
                          taker_b_pct=hcfg.get("taker_fee_pct", 0.1), on_trade=hl.journal_hedge, gate=gate)
        if s in saved:
            hp.load(saved[s])
        pairs[s] = hp
    if not pairs:
        return None
    desk = hl.HedgeDesk(mexc, binance, client, pairs, olog, hl.journal_hedge)
    for s, hp in pairs.items():
        hp.save_cb = desk.save
        hp.quote_allowed = lambda s=s: s in ((hl.hedge_authorization() or {}).get("symbols") or [])
        hp.reconcile()
    desk.refresh_balances(every_s=0)
    log.warning("COBERTURA activa en %s (lote %s USDT)", list(pairs), lot)
    return desk


def single_instance(path=None):
    """Un solo agente por PC. El 25-oct, un Stop-ScheduledTask detuvo el bucle de PowerShell pero no el
    proceso de Python: ese agente huérfano tomó la autorización y operó la cuenta real a la vez que el
    nuevo (IDs repetidos, rechazos 30004/30005). Lock del sistema operativo sobre state/agent.lock: lo
    libera el sistema si el proceso muere. Devuelve el archivo (mantenerlo abierto) o None si ya hay otro."""
    f = open(path or paths.state("agent.lock"), "a+")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    paths.state("agent.pid").write_text(str(os.getpid()))
    return f


def main(wait_if_busy=60):
    paths.load_env()
    setup_logging()
    lock = single_instance()
    if lock is None:
        log.error("ya hay otro agente de nexus corriendo en esta PC: este se cierra sin operar")
        time.sleep(wait_if_busy)
        return
    try:
        run()
    finally:
        lock.close()


def run():
    cfg = load_config()
    STRATEGY["tag"] = cfg.get("strategy_tag")
    client = MexcReadOnly()
    maker, taker = verified_fees(client, cfg)
    risk = RiskManager(cfg["risk"])
    gate = Gate(risk)
    saved = paths.read_json(paths.state("paper_books.json"), {})
    for x in cfg["symbols"]:
        bk = Book(x, notional=cfg["risk"]["max_position_eur"], maker=maker, taker=taker, stop_pct=cfg["stop_pct"],
                  min_edge_pct=cfg["min_edge_pct"], max_hold_s=cfg["max_hold_min"] * 60, gate=gate,
                  on_closed=lambda e: journal(e | {"lab": False}), max_vol_1m_pct=cfg.get("max_vol_1m_pct"),
                  context=MARKET.snapshot)
        if x in saved:
            bk.load(saved[x])
        gate.books[x] = bk
    lab = Lab(client, cfg, maker, taker)
    selector_tick(cfg, None)
    desk = build_desk(cfg, client, maker, taker)
    version = os.environ.get("NEXUS_VERSION", "desconocida")
    log.info("arranque version=%s simbolos=%s home=%s", version, cfg["symbols"], paths.home())
    seen = {x: set() for x in gate.books}
    last_ok = {x: 0.0 for x in gate.books}
    errors, last_status = [], 0.0
    log.info("comisiones maker=%s taker=%s; estado cargado: %s", maker, taker,
             {x: b.state for x, b in gate.books.items()})
    hedge = build_hedge_desk(cfg, client)
    last_eval, ev, last_bal, last_desk, last_sel = 0.0, {}, time.time(), time.time(), time.time()
    last_hedge = time.time()
    while True:
        if time.time() - last_hedge >= 60:          # la cobertura arranca sola cuando se autoriza
            last_hedge = time.time()
            from .hedge_live import hedge_authorization
            if hedge is None and hedge_authorization():
                hedge = build_hedge_desk(cfg, client)
        if time.time() - last_sel >= 300:
            last_sel = time.time()
            try:
                selector_tick(cfg, desk)
            except Exception:                        # noqa: BLE001
                log.exception("selector: no se pudo actualizar (se sigue con la elección anterior)")
        if desk is None and time.time() - last_desk >= 60 and live_wanted():
            last_desk = time.time()                  # p. ej. MEXC no respondía al arrancar con una posición real
            desk = build_desk(cfg, client, maker, taker)
        elif desk is not None and time.time() - last_desk >= 60:
            last_desk = time.time()
            desk = rotate(desk, cfg, client, maker, taker)
        try:
            if step(gate, client, cfg, seen, last_ok, errors) == "restart":
                lab.save()
                if hedge:
                    hedge.shutdown()
                if desk:
                    desk.shutdown()
                    save_state("paper_books.json", books_snapshot(gate, desk, hedge))
                return
            if hedge:
                hedge.step()
            if desk:
                desk.step(cfg["stale_data_s"])
                if time.time() - last_bal >= 600:
                    last_bal = time.time()
                    resize(desk, cfg, desk.check_balances())
            lab.step()
            if desk:                                 # el par real se atiende de nuevo tras el laboratorio:
                desk.step(cfg["stale_data_s"])       # menos tiempo fuera del libro entre re-cotizaciones
        except Exception:
            log.exception("error inesperado en el ciclo (el agente sigue)")
            errors.append(time.time())
            time.sleep(5)
        if time.time() - last_status >= 30:
            last_status = time.time()
            errors[:] = [e for e in errors if time.time() - e < 3600]
            save_state("paper_books.json", books_snapshot(gate, desk, hedge))
            lab.save()
            if desk:
                desk.save()
            if time.time() - last_eval >= 300:
                last_eval = time.time()
                try:
                    ev = evidence.evaluate(paths.state("trades.jsonl"), strategy=STRATEGY["tag"])
                    ev.pop("daily", None)
                    ev["calibration"] = calibration(live_auth())
                except Exception:
                    log.exception("no se pudo evaluar la evidencia")
            save_state("status.json", {
                "ts": time.time(), "version": version, "mode": "real+paper" if desk else "paper", "fees": [maker, taker],
                "books": [b.mark() for b in gate.books.values()], "risk": risk.snapshot(),
                "risk_block": risk.last_reason, "errors_last_hour": len(errors),
                "data_age_s": {x: round(time.time() - t, 1) for x, t in last_ok.items()},
                "lab": lab.status(), "evidence": ev, "market": MARKET.snapshot(),
                "recommended": allocator.recommend(lab.alloc),
                "live": desk.status(live_auth()) if desk else None,
                "hedge": hedge.status() if hedge else None})
        time.sleep(cfg["poll_seconds"])


class Lab:
    """Pares de laboratorio: paper puro (sin riesgo ni capital), sondeados de a pocos por ciclo."""

    def __init__(self, client, cfg, maker, taker):
        self.client, self.cfg, self.maker, self.taker = client, cfg.get("lab") or {}, maker, taker
        self.main = set(cfg["symbols"])
        self.base = cfg
        self.books, self.seen, self.order, self.i, self.selected_at = {}, {}, [], 0, 0.0
        self.saved = paths.read_json(paths.state("lab_books.json"), {})
        self.recent = {}                                      # últimas vueltas por par (pausa)
        self.recent_kinds = {}                                # tipos de cierre recientes (pausa rápida)
        # Variantes sobre los MISMOS datos que la estrategia vigente: control = sin filtro de volatilidad;
        # las demás vienen de lab.variants (reglas fijas en la config). Cada una tiene sus propias pausas.
        self.arms = {tag: {} for tag in self.variants()}
        self.paused = {s: t for s, t in paths.read_json(paths.state("lab_paused.json"), {}).items()
                       if t > time.time()}
        self.alloc = paths.read_json(paths.state("allocator.json"), {})   # promociones (explorar/explotar)
        self.dp = {}                                          # decimales de precio por par (variantes con tick)

    def variants(self):
        v = dict(self.cfg.get("variants") or {})
        if self.cfg.get("control_group", True):
            v = {CONTROL_TAG: {"max_vol_1m_pct": None}} | v
        return v

    @property
    def control(self):
        return self.arms.get(CONTROL_TAG, {})

    def is_paused(self, sym):
        if sym.split("#")[0] in self.twins():                     # el gemelo del par real no se pausa
            return False
        return self.paused.get(sym, 0) > time.time()

    def twins(self):
        """Pares con dinero real autorizado: siempre en el laboratorio, para comparar paper con real."""
        return [s for s in real_symbols() if s not in self.main]

    def on_closed(self, e, variant=None):
        """Registra la vuelta y pausa el par si sus últimas N vueltas pierden en promedio, o antes si
        acumula muchos stops seguidos de cerca (CHOMP perdió 12,7 € en 15 vueltas antes de pausarse).
        variant=<variante> (control u otra de lab.variants): la vuelta es de esa variante, con sus pausas."""
        journal(e | {"lab": True}, strategy=variant)
        key = e["sym"] if variant is None else f"{e['sym']}#{variant}"
        n = self.cfg.get("pause_window", 15)
        dq = self.recent.setdefault(key, deque(maxlen=n))
        dq.append(e["pnl"])
        kinds = self.recent_kinds.setdefault(key, deque(maxlen=self.cfg.get("fast_pause_window", 6)))
        kinds.append(e.get("kind"))
        fast = sum(k == "stop" for k in kinds) >= self.cfg.get("fast_pause_stops", 3)
        if (len(dq) == n and sum(dq) < 0) or fast:
            kinds.clear()
            self.paused[key] = time.time() + self.cfg.get("pause_hours", 24) * 3600
            dq.clear()
            save_state("lab_paused.json", self.paused)
            log.info("laboratorio: %s pausado %s h (últimas %s vueltas pierden)", key,
                     self.cfg.get("pause_hours", 24), n)

    def _book(self, sym, arm=None):
        spec = self.variants().get(arm, {}) if arm else {}
        bk = Book(sym, notional=self.base["risk"]["max_position_eur"], maker=self.maker, taker=self.taker,
                  stop_pct=self.base["stop_pct"], min_edge_pct=self.base["min_edge_pct"],
                  max_hold_s=self.base["max_hold_min"] * 60,
                  on_closed=(lambda e: self.on_closed(e, arm)) if arm else self.on_closed,
                  max_vol_1m_pct=spec.get("max_vol_1m_pct", self.base.get("max_vol_1m_pct")),
                  min_imbalance=spec.get("min_imbalance"), improve_ticks=spec.get("improve_ticks", 0),
                  price_dp=self.price_dp(sym) if spec.get("improve_ticks") else None,
                  context=lambda: MARKET.snapshot() | {"sel": allocator.status(self.alloc, sym)})
        key = f"{sym}#{arm}" if arm else sym
        if key in self.saved:
            bk.load(self.saved[key])
        return bk

    def price_dp(self, sym):
        """Decimales del precio del par (exchangeInfo de MEXC), para las variantes que mejoran el precio
        un tick. Sin el dato, esa variante no entra en el par (no se mezcla con la vigente)."""
        cache = self.dp
        if sym not in cache:
            try:
                info = self.client._get("/api/v3/exchangeInfo", {"symbol": sym})
                cache[sym] = int(next(x for x in info["symbols"] if x["symbol"] == sym)["quotePrecision"])
            except Exception as e:                                    # noqa: BLE001
                log.warning("laboratorio %s: sin decimales de precio (%s)", sym, type(e).__name__)
                return None
        return cache[sym]

    def promote(self, now=None):
        """Califica los pares con sus vueltas y devuelve los promovidos que se conservan (explotar)."""
        now = now or time.time()
        if not self.cfg.get("allocator", True):
            return []
        c = self.cfg
        sc = allocator.scores(evidence.load(paths.state("trades.jsonl")), STRATEGY["tag"], now,
                              c.get("score_window", 60), c.get("score_min_n", 20), c.get("score_days", 7))
        paused = {s for s in self.paused if self.is_paused(s)}
        self.alloc, keep = allocator.decide(self.alloc, sc, now, c.get("keep_max", 6), exclude=self.main | paused,
                                            eval_max=c.get("eval_max", 3), eval_min_n=c.get("eval_min_n", 5))
        save_state("allocator.json", self.alloc)
        return keep

    def reselect(self):
        promoted = self.promote()
        picked = select_lab(self.client, self.cfg, self.main | set(promoted) |
                            {s for s in self.paused if self.is_paused(s)}, self.base["risk"]["max_position_eur"])
        keep = {s for s, b in self.books.items() if b.state == "long"} | \
            {s for a in self.arms.values() for s, b in a.items() if b.state == "long"}   # no se abandonan posiciones
        twins = self.twins()
        promoted = twins + [s for s in promoted if s not in twins]
        names = promoted + [s for s, _ in picked if s not in twins][:max(0, self.cfg["max_symbols"] - len(promoted))]
        for s in list(self.books):
            if s not in names and s not in keep:
                self.saved[s] = self.books.pop(s).to_dict()
                for tag, a in self.arms.items():
                    if s in a:
                        self.saved[f"{s}#{tag}"] = a.pop(s).to_dict()
        for s in names:
            if s not in self.books:
                self.books[s] = self._book(s)
                self.seen[s] = set()
            for tag, a in self.arms.items():
                if s not in a:
                    a[s] = self._book(s, tag)
        self.order = list(self.books)
        self.selected_at = time.time()
        log.info("laboratorio: conservados (promovidos/en evaluación/gemelos) %s · explorando %s", promoted,
                 [s for s in names if s not in promoted])

    def step(self):
        if not self.cfg.get("enabled"):
            return
        if time.time() - self.selected_at > self.cfg["reselect_hours"] * 3600:
            try:
                self.reselect()
            except Exception as e:
                log.warning("no se pudo elegir pares de laboratorio: %s", type(e).__name__)
                self.selected_at = time.time() - self.cfg["reselect_hours"] * 3600 + 600
        syms = []
        for _ in range(min(self.cfg["per_loop"], len(self.order))):
            sym = self.order[self.i % len(self.order)]
            self.i += 1
            off = {key: self.is_paused(key) and b.state == "flat" for key, b in self.lab_books(sym)}
            for key, b in self.lab_books(sym):
                if off[key]:
                    b.px, b.queue = None, None                   # pausado: no abre; si estaba comprado, sale
            if not all(off.values()):
                syms.append(sym)
        data = fetch_many(self.client, syms)
        for sym in syms:
            bk = self.books[sym]
            try:
                if isinstance(data[sym], Exception):
                    raise data[sym]
                bt, trades = data[sym]
                new = fresh_trades(trades, self.seen[sym])
                for key, b in self.lab_books(sym):
                    if self.is_paused(key) and b.state == "flat":
                        continue
                    apply_trades(b, new)                  # primero, contra la orden que estaba puesta
                    b.on_book(float(bt["bidPrice"]), float(bt["bidQty"]), float(bt["askPrice"]), float(bt["askQty"]))
                if len(self.seen[sym]) > 3000:
                    self.seen[sym] = set(sorted(self.seen[sym])[-1000:])
            except Exception as e:
                log.warning("laboratorio %s: %s", sym, type(e).__name__)

    def lab_books(self, sym):
        """[(clave de pausa, libro)]: la versión vigente y cada variante del par."""
        return [(sym, self.books[sym])] + [(f"{sym}#{tag}", a[sym]) for tag, a in self.arms.items() if sym in a]

    def save(self):
        if self.books:
            save_state("lab_books.json", self.saved | {s: b.to_dict() for s, b in self.books.items()}
                       | {f"{s}#{tag}": b.to_dict() for tag, a in self.arms.items() for s, b in a.items()})

    def status(self):
        out = []
        for b in self.books.values():
            c = self.control.get(b.sym)
            sc = (self.alloc.get("scores") or {}).get(b.sym) or {}
            out.append(b.mark() | {"paused": self.is_paused(b.sym), "vol_1m_pct": b.vol_1m(), "er": b.trend()[0],
                                   "sel": allocator.status(self.alloc, b.sym), "score": sc.get("score"),
                                   "control": {"pnl_eur": c.mark()["pnl_eur"], "roundtrips": c.rt} if c else None,
                                   "arms": {t: {"pnl_eur": a[b.sym].mark()["pnl_eur"], "roundtrips": a[b.sym].rt}
                                            for t, a in self.arms.items() if t != CONTROL_TAG and b.sym in a}})
        return out


def step(gate, client, cfg, seen, last_ok, errors):
    """Un ciclo: datos de cada par, modelo de cola y pedido de reinicio. Devuelve "restart" si hay que salir."""
    data = fetch_many(client, list(gate.books) + ["BTCUSDT"])
    if not isinstance(data.get("BTCUSDT"), Exception):
        MARKET.update(data["BTCUSDT"][0])                   # clima general (no es un libro operado)
    for x, bk in gate.books.items():
        try:
            if isinstance(data[x], Exception):
                raise data[x]
            bt, trades = data[x]
            feed(bk, trades, seen[x])          # primero, contra la orden que estaba puesta
            bk.on_book(float(bt["bidPrice"]), float(bt["bidQty"]), float(bt["askPrice"]), float(bt["askQty"]))
            if len(seen[x]) > 5000:
                seen[x] = set(sorted(seen[x])[-2000:])
            last_ok[x] = time.time()
        except Exception as e:
            errors.append(time.time())
            log.warning("error de datos %s: %s", x, type(e).__name__)
        if time.time() - last_ok[x] > cfg["stale_data_s"] and bk.state == "flat" and bk.px is not None:
            bk.px, bk.queue = None, None
            log.warning("datos viejos en %s: se cancela la compra pendiente", x)
    restart = paths.state("RESTART")
    if restart.exists():                             # pedido del actualizador (R-13)
        save_state("paper_books.json", {x: b.to_dict() for x, b in gate.books.items()})
        restart.unlink()
        log.info("reinicio controlado solicitado: estado guardado, saliendo")
        return "restart"
    return None


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            setup_logging()
        except Exception:
            pass
        log.exception("el agente se detuvo por un error al arrancar o fatal")
        raise
