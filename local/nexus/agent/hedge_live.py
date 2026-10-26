"""Cobertura MEXC→Binance con DINERO REAL: maker en MEXC (0%) y cobertura inmediata en Binance.

Por cada par autorizado (`authorize --cobertura PAR`) se pone una compra en el mejor bid de MEXC y una
venta en el mejor ask (vistos SIN nuestras órdenes), solo si al precio actual de Binance la vuelta
cubierta deja al menos `min_edge_pct` después de la comisión tomadora de Binance. Cada llenado en MEXC
se cubre en el acto con una orden a mercado en Binance: lo comprado en MEXC se vende en Binance y lo
vendido se recompra. La moneda total no cambia: no se apuesta al precio; se gana la diferencia.

Inventario: hace falta USDT y moneda en los dos exchanges. Una compra en MEXC gasta USDT de MEXC y
moneda de Binance; una venta, al revés. Si un lado no tiene saldo, ese lado no cotiza (el rebalanceo
entre exchanges es manual: las claves no pueden retirar, R-01).

Protecciones (las mismas ideas que agent/live.py):
- R-06: ID propio (`hx`/`hb` + secuencia + par) guardado ANTES de enviar.
- R-05: si no se sabe si una orden llegó (corte de red), no se manda otra hasta conciliarla.
- Una cobertura que no se puede hacer (rechazos seguidos o lo llenado sin cubrir por más de
  MAX_UNHEDGED_S) bloquea el par: se cancelan sus órdenes en MEXC y queda para revisión humana.
- KILL 1 deja de cotizar (cancela las órdenes de MEXC; lo ya llenado se cubre igual); KILL 2 y 3 igual.
- Cada vuelta cubierta va al diario `hedge_trades.jsonl` y al gestor de riesgo (límites de pérdida).
"""
import logging
import math
import time

from .. import paths
from .live import ENDED, kill_level

log = logging.getLogger("nexus.hedge")
HEDGE_AUTH, HEDGE_STATE, HEDGE_UNBLOCK = "hedge_auth.json", "hedge_books.json", "hedge_unblock.json"
HEDGE_TRADES, HEDGE_ORDERS = "hedge_trades.jsonl", "hedge_orders.jsonl"
MAX_REJECTS, MAX_HEDGE_REJECTS, MAX_UNHEDGED_S = 5, 3, 30
MIN_LOT_USDT, MAX_LOT_USDT, MAX_HEDGE_PAIRS = 6.0, 25.0, 3


def hedge_authorization(now=None):
    """Autorización local vigente para la cobertura, o None. Solo la escribe una persona con
    `nexus.agent.authorize --cobertura`. Topes: lote ≤ MAX_LOT_USDT, ≤ MAX_HEDGE_PAIRS pares, ≤ 7 días."""
    from .live import MAX_DAYS
    now = now or time.time()
    a = paths.read_json(paths.state(HEDGE_AUTH), None)
    if not isinstance(a, dict) or a.get("expires", 0) <= now or not a.get("symbols"):
        return None
    if len(a["symbols"]) > MAX_HEDGE_PAIRS or not MIN_LOT_USDT <= float(a.get("lot_usdt", 0)) <= MAX_LOT_USDT:
        return None
    if a["expires"] - a.get("ts", 0) > MAX_DAYS * 86400:
        return None
    return a


class BinanceRules:
    """Filtros del par en Binance (exchangeInfo): paso de cantidad, mínimo y monto mínimo."""

    def __init__(self, info, symbol):
        s = next(x for x in info["symbols"] if x["symbol"] == symbol)
        if s.get("status") != "TRADING" or "MARKET" not in (s.get("orderTypes") or []):
            raise ValueError(f"{symbol}: en Binance no admite órdenes a mercado (status {s.get('status')})")
        f = {x["filterType"]: x for x in s.get("filters", [])}
        lot = f.get("MARKET_LOT_SIZE") if float((f.get("MARKET_LOT_SIZE") or {}).get("stepSize") or 0) > 0 else f.get("LOT_SIZE")
        self.step = float(lot["stepSize"])
        self.min_qty = float(lot.get("minQty") or 0)
        self.min_notional = float((f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}).get("minNotional") or 0)
        self.dp = max(0, round(-math.log10(self.step))) if self.step < 1 else 0
        self.base = s.get("baseAsset")

    def qty(self, x):
        return math.floor(x / self.step + 1e-9) * self.step

    def fq(self, x):
        return f"{self.qty(x):.{self.dp}f}"

    def ok(self, q, px):
        return q >= max(self.min_qty, self.step) and q * px >= self.min_notional


class HedgePair:
    def __init__(self, sym, mexc, binance, mrules, brules, olog, lot_usdt, min_edge_pct=0.1, taker_b_pct=0.1,
                 now=time.time, save=lambda: None, on_trade=None, gate=None):
        self.sym, self.m, self.b, self.mr, self.br, self.olog = sym, mexc, binance, mrules, brules, olog
        self.lot, self.min_edge, self.fb = min(lot_usdt, MAX_LOT_USDT), min_edge_pct / 100, taker_b_pct / 100
        self.now, self.save_cb, self.on_trade, self.gate = now, save, on_trade, gate
        self.orders = {"BUY": None, "SELL": None}       # orden maker de MEXC puesta por lado
        self.pending = {"BUY": [0.0, 0.0], "SELL": [0.0, 0.0]}   # llenado en MEXC sin cubrir: [cantidad, USDT]
        self.since = {"BUY": None, "SELL": None}        # desde cuándo hay algo sin cubrir
        self.hedge = None                               # cobertura en Binance con resultado DESCONOCIDO
        self.unknown = None                             # orden de MEXC con resultado desconocido
        self.inv = {"m_usdt": 0.0, "m_coin": 0.0, "b_usdt": 0.0, "b_coin": 0.0}
        self.seq, self.rejects, self.b_rejects, self.blocked = 0, 0, 0, None
        self.pnl, self.fills = 0.0, 0
        self.bid = self.ask = None
        self.quote_allowed = lambda: True               # autorización vigente para este par

    # -- persistencia ------------------------------------------------------------------------------
    KEYS = ("orders", "pending", "since", "hedge", "unknown", "seq", "rejects", "b_rejects", "blocked", "pnl", "fills")

    def to_dict(self):
        return {k: getattr(self, k) for k in self.KEYS}

    def load(self, d):
        for k in self.KEYS:
            if k in d:
                setattr(self, k, d[k])

    def _log(self, act, **kw):
        self.olog.write({"ts": round(self.now(), 3), "sym": self.sym, "act": act} | kw)

    # -- saldos --------------------------------------------------------------------------------------
    def balances(self, m_bal, b_bal):
        def get(bal, asset):
            return sum(float(x.get("free") or 0) for x in bal if x.get("asset") == asset)
        self.inv = {"m_usdt": get(m_bal, "USDT"), "m_coin": get(m_bal, self.mr.base),
                    "b_usdt": get(b_bal, "USDT"), "b_coin": get(b_bal, self.br.base)}

    def can(self, side, px):
        """¿Hay saldo para un lote en este lado, en los DOS exchanges (la cobertura incluida)?"""
        q = self.lot / px
        if side == "BUY":                               # compro en MEXC con USDT, vendo moneda en Binance
            return self.inv["m_usdt"] >= self.lot and self.inv["b_coin"] >= q
        return self.inv["m_coin"] >= q and self.inv["b_usdt"] >= self.lot * (1 + self.fb) * 1.01

    # -- ciclo -----------------------------------------------------------------------------------------
    def market(self, depth):
        """Mejor bid y ask de MEXC SIN nuestras órdenes (como LiveBook.market)."""
        half_tick, half_lot = 0.5 * 10 ** -self.mr.price_dp, 0.5 * 10 ** -self.mr.qty_dp

        def best(levels, side):
            o = self.orders[side]
            for p, q in levels:
                p, q = float(p), float(q)
                rest = (o["qty"] - o["exec_qty"]) if o else 0.0
                if o and abs(p - o["px"]) < half_tick and q >= rest - half_lot:
                    q -= rest
                if q > half_lot:
                    return p
            return None
        return best(depth.get("bids") or [], "BUY"), best(depth.get("asks") or [], "SELL")

    def step(self, depth, b_bid, b_ask):
        if self.unknown:
            self._resolve_unknown()
        for side in ("BUY", "SELL"):
            if self.orders[side] and not self.unknown:
                self._sync(side)
        if self.hedge:
            self._resolve_hedge()
        if not self.hedge:
            self._cover(b_bid, b_ask)
        self._check_unhedged()
        m_bid, m_ask = self.market(depth)
        self.bid, self.ask = m_bid, m_ask
        unhedged = any(self.pending[s][0] * (m_bid or 0) >= max(self.br.min_notional, 1.0) for s in ("BUY", "SELL"))
        stop = (self.blocked or self.unknown or self.hedge or unhedged or kill_level() >= 1
                or not self.quote_allowed() or None in (m_bid, m_ask, b_bid, b_ask))   # con algo sin cubrir no cotiza
        if not stop and self.gate is not None and not self.gate.allow_entry(self.lot, 0.0):
            stop = True                                 # el gestor de riesgo veta (límites de pérdida, halt)
        want = {"BUY": None, "SELL": None}
        if not stop:
            if b_bid * (1 - self.fb) / m_bid - 1 >= self.min_edge and self.can("BUY", m_bid):
                want["BUY"] = m_bid
            if m_ask / (b_ask * (1 + self.fb)) - 1 >= self.min_edge and self.can("SELL", m_ask):
                want["SELL"] = m_ask
        for side in ("BUY", "SELL"):
            self._place(side, want[side])

    # -- órdenes de MEXC -----------------------------------------------------------------------------
    def _place(self, side, want):
        o = self.orders[side]
        if self.unknown:
            return
        if o:
            if want is not None and abs(o["px"] - want) < 1e-12:
                return
            if not self._cancel(side):
                return
        if want is None:
            return
        qty = self.mr.qty(self.lot / want)
        if qty * want < self.mr.min_quote:
            return
        self.seq += 1
        cid = f"hx{self.seq}{self.sym}"[:32]
        self.orders[side] = {"cid": cid, "px": want, "qty": qty, "exec_qty": 0.0, "exec_quote": 0.0, "ts": self.now()}
        self.save_cb()                                  # R-06: registrado ANTES de enviar
        self._log("enviar", ex="MEXC", cid=cid, side=side, px=want, qty=qty)
        try:
            self.m.new_order(self.sym, side, "LIMIT_MAKER", self.mr.fq(qty), self.mr.fp(want), cid)
        except Exception as e:                          # noqa: BLE001
            if getattr(e, "status", None):              # rechazo: resultado conocido
                self.orders[side] = None
                self._reject(f"MEXC rechazó {cid}: {str(e)[:120]}", "MEXC")
            else:
                self.unknown = {"side": side, "cid": cid, "since": self.now(), "misses": 0}
                self._log("desconocido", ex="MEXC", cid=cid, error=type(e).__name__)
            self.save_cb()
            return
        self.rejects = 0
        self._log("aceptada", ex="MEXC", cid=cid)
        self.save_cb()

    def _apply(self, side, resp):
        o = self.orders[side]
        if o is None:
            return
        q, quote = float(resp.get("executedQty") or 0), float(resp.get("cummulativeQuoteQty") or 0)
        dq, dquote = q - o["exec_qty"], quote - o["exec_quote"]
        if dq > 0:
            p = self.pending[side]
            p[0], p[1] = p[0] + dq, p[1] + dquote
            self.since[side] = self.since[side] or self.now()
            k = (-1 if side == "BUY" else 1)            # el inventario de MEXC cambia ya
            self.inv["m_usdt"] += k * dquote
            self.inv["m_coin"] -= k * dq
            self._log("ejecutada", ex="MEXC", cid=o["cid"], side=side, qty=dq, quote=round(dquote, 8))
        o["exec_qty"], o["exec_quote"] = q, quote
        if resp.get("status") in ENDED:
            self.orders[side] = None
        self.save_cb()

    def _sync(self, side):
        o = self.orders[side]
        try:
            self._apply(side, self.m.get_order(self.sym, o["cid"]))
        except Exception as e:                          # noqa: BLE001
            if getattr(e, "not_found", False) and self.now() - o["ts"] > 10:
                self.unknown = {"side": side, "cid": o["cid"], "since": self.now(), "misses": 0}

    def _cancel(self, side):
        o = self.orders[side]
        self._log("cancelar", ex="MEXC", cid=o["cid"])
        try:
            self.m.cancel_order(self.sym, o["cid"])
        except Exception as e:                          # noqa: BLE001
            if not getattr(e, "status", None):
                self.unknown = {"side": side, "cid": o["cid"], "since": self.now(), "misses": 0}
                self.save_cb()
                return False
        try:
            self._apply(side, self.m.get_order(self.sym, o["cid"]))
        except Exception:                               # noqa: BLE001
            self.unknown = {"side": side, "cid": o["cid"], "since": self.now(), "misses": 0}
            self.save_cb()
            return False
        return self.orders[side] is None

    def _resolve_unknown(self):
        u = self.unknown
        try:
            resp = self.m.get_order(self.sym, u["cid"])
        except Exception as e:                          # noqa: BLE001
            if getattr(e, "not_found", False):
                u["misses"] += 1
                if u["misses"] >= 2 and self.now() - u["since"] >= 3:   # no se creó
                    self.orders[u["side"]], self.unknown = None, None
                    self._log("no_existe", ex="MEXC", cid=u["cid"])
                    self.save_cb()
            elif self.now() - u["since"] > 120 and not self.blocked:
                self._block(f"no se pudo conciliar la orden {u['cid']} de MEXC en 120 s")
            return
        self.unknown = None
        if self.orders[u["side"]] and self.orders[u["side"]]["cid"] == u["cid"]:
            self._apply(u["side"], resp)
        self._log("conciliada", ex="MEXC", cid=u["cid"], status=resp.get("status"))

    # -- cobertura en Binance ------------------------------------------------------------------------
    def _cover(self, b_bid, b_ask):
        for side in ("BUY", "SELL"):
            q_m, quote_m = self.pending[side]
            px = b_bid if side == "BUY" else b_ask
            q = self.br.qty(q_m)
            if not px or not self.br.ok(q, px):
                continue                                # migaja: se junta con el próximo llenado
            self.seq += 1
            hside = "SELL" if side == "BUY" else "BUY"
            cid = f"hb{self.seq}{self.sym}"[:36]
            self.hedge = {"cid": cid, "side": side, "qty": q, "m_quote": quote_m * q / q_m, "since": self.now(),
                          "misses": 0}
            self.save_cb()                              # R-06
            self._log("enviar", ex="Binance", cid=cid, side=hside, type="MARKET", qty=q)
            try:
                resp = self.b.new_order(self.sym, hside, "MARKET", self.br.fq(q), None, cid)
            except Exception as e:                      # noqa: BLE001
                if getattr(e, "status", None):
                    self.hedge = None
                    self._reject(f"Binance rechazó la cobertura {cid}: {str(e)[:120]}", "Binance")
                else:
                    self._log("desconocido", ex="Binance", cid=cid, error=type(e).__name__)
                self.save_cb()
                return
            self._covered(resp)
            return                                      # una cobertura por ciclo

    def _covered(self, resp):
        h, self.hedge = self.hedge, None
        q, quote = float(resp.get("executedQty") or 0), float(resp.get("cummulativeQuoteQty") or 0)
        if q <= 0:                                      # no se ejecutó nada (p. ej. vencida): se reintenta
            self._log("sin_ejecutar", ex="Binance", cid=h["cid"], status=resp.get("status"))
            self.save_cb()
            return
        side = h["side"]
        p = self.pending[side]
        m_quote = p[1] * min(q / p[0], 1.0) if p[0] > 0 else h["m_quote"]
        p[0], p[1] = max(p[0] - q, 0.0), max(p[1] - m_quote, 0.0)
        if p[0] <= 0:
            self.since[side] = None
        fee = quote * self.fb
        pnl = (quote - m_quote if side == "BUY" else m_quote - quote) - fee
        k = 1 if side == "BUY" else -1                  # Binance: vendí (BUY en MEXC) o compré moneda
        self.inv["b_usdt"] += k * quote - fee
        self.inv["b_coin"] -= k * q
        self.pnl += pnl
        self.fills += 1
        self.b_rejects = 0
        self._log("cubierta", ex="Binance", cid=h["cid"], qty=q, quote=round(quote, 8), pnl=round(pnl, 8))
        t = {"ts": self.now(), "sym": self.sym, "side": side, "qty": q, "m_quote": round(m_quote, 8),
             "b_quote": round(quote, 8), "fee": round(fee, 8), "pnl": round(pnl, 8), "notional": round(m_quote, 4)}
        if self.gate is not None:
            self.gate.on_close(pnl, False)
        if self.on_trade:
            self.on_trade(t)
        self.save_cb()

    def _resolve_hedge(self):
        h = self.hedge
        try:
            resp = self.b.get_order(self.sym, h["cid"])
        except Exception as e:                          # noqa: BLE001
            if getattr(e, "not_found", False):
                h["misses"] += 1
                if h["misses"] >= 2 and self.now() - h["since"] >= 3:   # no llegó: se reintenta
                    self._log("no_existe", ex="Binance", cid=h["cid"])
                    self.hedge = None
                    self.save_cb()
            return
        if resp.get("status") in ENDED:
            self._covered(resp)

    def _check_unhedged(self):
        for side in ("BUY", "SELL"):
            t = self.since[side]
            px = self.bid or 0
            if t and self.pending[side][0] * px >= max(self.br.min_notional, 1.0) and self.now() - t > MAX_UNHEDGED_S \
                    and not self.blocked:
                self._block(f"{self.pending[side][0]:g} {self.br.base} llenados en MEXC sin cubrir hace "
                            f"{self.now() - t:.0f} s")

    # -- bloqueo y cierre ------------------------------------------------------------------------------
    def _reject(self, msg, venue):
        """Rechazos seguidos: en MEXC (poner una orden) bloquean a los MAX_REJECTS; en Binance (cubrir lo
        ya comprado o vendido) a los MAX_HEDGE_REJECTS, porque ahí queda moneda sin cubrir."""
        if venue == "Binance":
            self.b_rejects += 1
            n, top = self.b_rejects, MAX_HEDGE_REJECTS
        else:
            self.rejects += 1
            n, top = self.rejects, MAX_REJECTS
        self._log("rechazada", ex=venue, error=msg[:200])
        log.warning("cobertura %s: %s", self.sym, msg)
        if n >= top:
            self._block(f"{n} rechazos seguidos en {venue}: {msg[:120]}")

    def _block(self, why):
        self.blocked = why
        self._log("bloqueado", why=why)
        log.error("cobertura %s BLOQUEADA: %s", self.sym, why)
        for side in ("BUY", "SELL"):
            if self.orders[side]:
                self._cancel(side)
        self.save_cb()

    def shutdown(self):
        for side in ("BUY", "SELL"):
            if self.orders[side] and not self.unknown:
                self._cancel(side)
        self.save_cb()

    def reconcile(self):
        """Al arrancar: cancela órdenes propias de MEXC que hayan quedado (prefijo hx) y concilia lo
        guardado. Lo llenado sin cubrir se cubre en el primer ciclo."""
        try:
            for o in self.m.open_orders(self.sym):
                cid = o.get("clientOrderId") or ""
                if cid.startswith("hx"):
                    self._log("huerfana", ex="MEXC", cid=cid)
                    try:
                        self.m.cancel_order(self.sym, cid)
                    except Exception:                   # noqa: BLE001
                        pass
        except Exception as e:                          # noqa: BLE001
            log.warning("cobertura %s: no se pudieron leer las órdenes abiertas (%s)", self.sym, type(e).__name__)
        for side in ("BUY", "SELL"):
            o = self.orders[side]
            if o:
                try:
                    self._apply(side, self.m.get_order(self.sym, o["cid"]))
                except Exception:                       # noqa: BLE001
                    self.unknown = {"side": side, "cid": o["cid"], "since": self.now(), "misses": 0}
                if self.orders[side] and self.orders[side].get("cid") == o["cid"]:
                    self.orders[side] = None            # quedó cancelada arriba

    def status(self):
        return {"sym": self.sym, "pnl": round(self.pnl, 4), "fills": self.fills, "blocked": self.blocked,
                "quoting": [s for s, o in self.orders.items() if o], "pending": {s: round(p[0], 8) for s, p in self.pending.items()},
                "inventory": {k: round(v, 4) for k, v in self.inv.items()}, "hedge_unknown": bool(self.hedge)}


class HedgeDesk:
    """Los pares cubiertos: datos (libro de MEXC sin nuestras órdenes y mejor precio de Binance),
    saldos de los dos exchanges cada minuto, guardado y estado para el tablero."""

    def __init__(self, mexc, binance, data, pairs, olog, journal):
        self.m, self.b, self.data, self.pairs, self.olog, self.journal = mexc, binance, data, pairs, olog, journal
        self.last_bal = 0.0

    def save(self):
        from .maker_agent import save_state
        save_state(HEDGE_STATE, {s: p.to_dict() for s, p in self.pairs.items()})

    def refresh_balances(self, every_s=60):
        if time.time() - self.last_bal < every_s:
            return
        try:
            mb, bb = self.m.account().get("balances", []), self.b.account().get("balances", [])
        except Exception as e:                          # noqa: BLE001
            log.warning("cobertura: no se pudieron leer los saldos (%s)", type(e).__name__)
            return
        self.last_bal = time.time()
        for p in self.pairs.values():
            p.balances(mb, bb)

    def unblock_requests(self):
        """Pedido humano (authorize --desbloquear-cobertura): el dueño revisó los saldos y, si quedó algo
        sin cubrir, lo cubrió a mano. El par vuelve a cero: sin bloqueo, sin pendientes ni desconocidos."""
        req = paths.state(HEDGE_UNBLOCK)
        if not req.exists():
            return
        for s in paths.read_json(req, {}).get("symbols") or []:
            p = self.pairs.get(s)
            if p:
                p.blocked, p.rejects, p.b_rejects, p.hedge, p.unknown = None, 0, 0, None, None
                p.pending, p.since = {"BUY": [0.0, 0.0], "SELL": [0.0, 0.0]}, {"BUY": None, "SELL": None}
                p.orders = {"BUY": None, "SELL": None}
                p._log("desbloqueado")
                log.warning("cobertura %s: desbloqueada por el dueño", s)
        req.unlink()
        self.last_bal = 0.0                             # saldos frescos antes de volver a cotizar
        self.save()

    def step(self):
        self.unblock_requests()
        self.refresh_balances()
        for s, p in self.pairs.items():
            try:
                depth = self.data._get("/api/v3/depth", {"symbol": s, "limit": 5})
                bt = self.b.book_ticker(s)
                p.step(depth, float(bt["bidPrice"]), float(bt["askPrice"]))
            except Exception as e:                      # noqa: BLE001
                log.warning("cobertura %s: error de ciclo (%s)", s, type(e).__name__)

    def shutdown(self):
        for p in self.pairs.values():
            try:
                p.shutdown()
            except Exception:                           # noqa: BLE001
                log.exception("cobertura %s: no se pudo cancelar al salir", p.sym)
        self.save()

    def status(self):
        return {"pairs": [p.status() for p in self.pairs.values()],
                "pnl": round(sum(p.pnl for p in self.pairs.values()), 4)}


def journal_hedge(entry):
    import json
    with open(paths.state(HEDGE_TRADES), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
