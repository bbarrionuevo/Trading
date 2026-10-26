"""Market making long-only con modelo de cola, para paper trading en vivo (Binance/MEXC).

Entrada: orden límite de compra en el mejor bid, al final de la cola visible. Modelo de llenado 5:
se cuenta llenada SOLO si se opera por debajo de nuestro precio (lo atraviesa); las operaciones justo a
nuestro precio no alcanzan, porque no se sabe si la cola de adelante se terminó (con el modelo 4, que
las contaba, el paper se llenaba mucho más seguido que el real y ganaba más). Si el mejor bid se mueve,
cancelamos y volvemos a entrar al final.

Salida: venta límite en max(mejor ask, piso). El piso = entrada * (1 + 2*maker + margen mínimo):
como maker NUNCA se vende a pérdida. Si el piso queda por encima del mejor ask, no conocemos la
cola en ese nivel y solo se llena si el precio lo ATRAVIESA (supuesto conservador).
Protecciones: stop a mercado (taker) y tiempo máximo de tenencia (taker al bid).
Métricas: wins = vueltas con PnL > 0 (no "salidas como maker").

python -m nexus.live_paper_maker OUT.jsonl SYM1,SYM2 minutos
"""
import json
import math
import sys
import threading
import time
from collections import deque

from .evidence import FILL_MODEL

WS = "wss://data-stream.binance.vision/stream?streams="


class Book:
    def __init__(self, sym, notional=40.0, maker=0.0, taker=0.1, stop_pct=1.0, min_edge_pct=0.0,
                 max_hold_s=1800, gate=None, now=time.time, on_closed=None, max_vol_1m_pct=None, context=None,
                 min_imbalance=None, improve_ticks=0, price_dp=None):
        self.on_closed = on_closed                            # callback(dict) por cada vuelta cerrada
        # mejorar el precio N ticks (compra arriba del bid, venta abajo del ask) para quedar primero en la
        # cola; price_dp = decimales del precio del par (tick = 10^-dp). Sin dp no se puede: no entra.
        self.improve, self.dp = improve_ticks or 0, price_dp
        self.max_vol = max_vol_1m_pct                         # no entrar si la volatilidad supera esto (%/min)
        self.min_imb = min_imbalance                          # entrar solo si bidQty/(bidQty+askQty) ≥ esto
        self.context = context                                # callable -> dict con el clima del mercado (BTC)
        self.sym, self.notional, self.maker, self.taker = sym, notional, maker, taker
        self.stop, self.min_edge, self.max_hold, self.gate, self.now = stop_pct, min_edge_pct, max_hold_s, gate, now
        self.bid = self.ask = self.bq = self.aq = None
        self.state, self.px, self.queue, self.qty = "flat", None, None, 0.0
        self.entry, self.entry_fee, self.entry_ts, self.eq = 0.0, 0.0, 0.0, 0.0
        self.rt = self.wins = self.stops = self.timeouts = self.requotes = 0
        self.mids = deque(maxlen=240)                         # (ts, precio medio) para la volatilidad
        self.entry_ctx = {}                                   # spread y volatilidad al entrar (diario)

    # -- cotización ---------------------------------------------------------------------------
    def _quote(self, price, queue):
        if price != self.px:
            self.px, self.queue = price, queue
            self.requotes += 1
            if self.gate:
                self.gate.record_order()

    def floor(self):
        return self.entry * (1 + (2 * self.maker + self.min_edge) / 100)

    def vol_1m(self):
        """Volatilidad realizada del precio medio, en % por minuto (None si hay pocos datos)."""
        if len(self.mids) < 10:
            return None
        pts = list(self.mids)
        var = sum(math.log(b / a) ** 2 for (_, a), (_, b) in zip(pts, pts[1:]) if a > 0 and b > 0)
        secs = pts[-1][0] - pts[0][0]
        return round(math.sqrt(var / secs * 60) * 100, 4) if secs > 0 else None

    def trend(self):
        """(efficiency ratio 0-1, variación % de la ventana): ~0 = lateral (va y viene), ~1 = tendencia."""
        if len(self.mids) < 10:
            return None, None
        pts = [m for _, m in self.mids]
        path = sum(abs(b - a) for a, b in zip(pts, pts[1:]))
        er = abs(pts[-1] - pts[0]) / path if path > 0 else 0.0
        return round(er, 3), round((pts[-1] / pts[0] - 1) * 100, 4)

    def on_book(self, b, bq, a, aq):
        self.bid, self.bq, self.ask, self.aq = b, bq, a, aq
        # cola visible adelante en nuestro nivel (nunca más que lo visible): ya no decide el llenado del
        # paper (modelo 5: solo si atraviesa), queda como dato del libro y para la orden real.
        if self.px is not None and self.queue is not None:
            ref, lvl = (b, bq) if self.state == "flat" else (a, aq)
            if ref is not None and lvl is not None and abs(ref - self.px) < 1e-12:
                self.queue = min(self.queue, lvl)
        if b > 0 and a > 0:
            self.mids.append((self.now(), (a + b) / 2))
        if self.state == "flat":
            if self.gate and not self.gate.allow_entry(self.notional, self.gate.exposure()):
                self.px, self.queue = None, None              # el riesgo veta: se cancela la compra
                return
            if self.max_vol is not None:
                v = self.vol_1m()
                if v is None or v > self.max_vol:              # sin datos suficientes o muy volátil: no compra
                    self.px, self.queue = None, None
                    return
            if self.min_imb is not None and (bq or 0) + (aq or 0) > 0 and bq / (bq + aq) < self.min_imb:
                self.px, self.queue = None, None              # más presión vendedora que compradora: no compra
                return
            if self.improve and self.dp is None:
                self.px, self.queue = None, None
                return
            px, q = self._bid_target(b, bq, a)
            if self.px != px:
                self._quote(px, q)
        elif self.state == "long":
            if b < self.entry * (1 - self.stop / 100):
                self._close(b, self.taker, kind="stop")
            elif self.now() - self.entry_ts > self.max_hold:
                self._close(b, self.taker, kind="timeout")
            else:
                self._quote(*self._ask_target(a, aq, b))

    def _tick(self):
        return 10 ** -self.dp

    def _bid_target(self, b, bq, a):
        """(precio, cola de adelante) de la compra: el mejor bid, al final de la cola visible. Con mejora,
        N ticks arriba (nadie adelante) si queda lugar para vender N ticks abajo del ask con margen; si
        nuestra orden ya ES el mejor bid (en real el libro la incluye) no se mueve: no se persigue sola."""
        if not self.improve:
            return b, bq
        t = self._tick()
        if self.px is not None and abs(self.px - b) < t / 2:
            return self.px, self.queue
        px = round(b + self.improve * t, self.dp)
        if a - b >= (2 * self.improve + 1) * t - t / 2:
            return px, 0.0
        return b, bq

    def _ask_target(self, a, aq, b):
        """(precio, cola) de la venta: max(mejor ask, piso). Con mejora, N ticks abajo del ask si sigue
        arriba del bid y del piso (nunca a pérdida como maker); si ya somos el mejor ask, no se mueve."""
        fl = self.floor()
        if self.improve and self.dp is not None:
            t = self._tick()
            if self.px is not None and abs(self.px - a) < t / 2 and self.px >= fl:
                return self.px, self.queue
            px = round(a - self.improve * t, self.dp)
            if px - b > t / 2 and px >= fl:
                return px, 0.0
        target = max(a, fl)
        return target, (aq if target == a else None)

    def on_trade(self, price, qty, buyer_is_maker):
        if self.px is None:
            return
        if self.state == "flat" and buyer_is_maker:           # vendedor agresor golpea bids
            if price < self.px:                               # modelo 5: solo si atraviesa
                self.state, self.entry, self.entry_ts = "long", self.px, self.now()
                er, ret = self.trend()
                self.entry_ctx = {"spread_pct": round((self.ask / self.bid - 1) * 100, 4)
                                  if self.bid and self.ask else None, "vol_1m_pct": self.vol_1m(),
                                  "er": er, "ret_window_pct": ret} | (self.context() if self.context else {})
                self.qty = self.notional / self.px
                self.entry_fee = self.notional * self.maker / 100
                self.eq -= self.entry_fee
                self.px, self.queue = None, None
                if self.ask is not None:
                    self._quote(*self._ask_target(self.ask, self.aq, self.bid))
        elif self.state == "long" and not buyer_is_maker:     # comprador agresor levanta asks
            if price > self.px:
                self._close(self.px, self.maker, kind="maker")

    def _close(self, px, fee, kind):
        exit_fee = self.qty * px * fee / 100
        gross = self.qty * (px - self.entry)
        self.eq += gross - exit_fee
        pnl = gross - exit_fee - self.entry_fee
        self.rt += 1
        self.wins += pnl > 0
        self.stops += kind == "stop"
        self.timeouts += kind == "timeout"
        if self.gate:
            self.gate.on_close(pnl, kind != "maker")
            if kind != "maker":
                self.gate.record_order()
        if self.on_closed:
            self.on_closed({"ts": self.now(), "sym": self.sym, "pnl": round(pnl, 6), "kind": kind,
                            "hold_s": round(self.now() - self.entry_ts, 1), "entry": self.entry, "exit": px,
                            "notional": self.notional, "maker": self.maker, "taker": self.taker}
                           | self.entry_ctx)
        self.state, self.px, self.queue, self.qty, self.entry_fee = "flat", None, None, 0.0, 0.0
        return pnl

    # -- reporte y persistencia -----------------------------------------------------------------
    def exposure(self):
        return self.notional if self.state == "long" else 0.0

    def mark(self):
        open_pnl = self.qty * (self.bid - self.entry) if self.state == "long" and self.bid else 0.0
        return {"sym": self.sym, "maker": self.maker, "pnl_eur": round(self.eq + open_pnl, 4),
                "realized_eur": round(self.eq + (self.entry_fee if self.state == "long" else 0), 4),
                "roundtrips": self.rt, "wins": self.wins, "stops": self.stops, "timeouts": self.timeouts,
                "requotes": self.requotes, "state": self.state}

    KEYS = ("state", "entry", "entry_fee", "entry_ts", "qty", "eq", "rt", "wins", "stops", "timeouts", "requotes")

    def to_dict(self):
        return {k: getattr(self, k) for k in self.KEYS} | {"model": FILL_MODEL}

    def load(self, d):
        """Restaura el libro. Un estado guardado con otro modelo de llenado se descarta: su resultado
        acumulado (y una posición simulada con reglas viejas) no es comparable con el modelo vigente."""
        if d.get("model") != FILL_MODEL:
            return False
        for k in self.KEYS:
            if k in d:
                setattr(self, k, d[k])
        self.px, self.queue = None, None                      # tras reiniciar, se re-cotiza desde cero
        return True


def run(syms, minutes, out):
    import websocket

    books = {s: [Book(s, maker=0.0), Book(s, maker=0.1)] for s in syms}
    streams = "/".join(f"{s.lower()}@bookTicker/{s.lower()}@aggTrade" for s in syms)

    def on_msg(_, m):
        d = json.loads(m)["data"]
        for bk in books.get(d["s"], []):
            if "b" in d and "B" in d and "a" in d:
                bk.on_book(float(d["b"]), float(d["B"]), float(d["a"]), float(d["A"]))
            elif d.get("e") == "aggTrade":
                bk.on_trade(float(d["p"]), float(d["q"]), d["m"])

    ws = websocket.WebSocketApp(WS + streams, on_message=on_msg)
    threading.Thread(target=ws.run_forever, kwargs={"ping_interval": 20}, daemon=True).start()
    t0 = time.time()
    while time.time() - t0 < minutes * 60:
        time.sleep(60)
        snap = {"ts": time.time(), "elapsed_min": round((time.time() - t0) / 60, 1), "model": "v2-floor",
                "books": [bk.mark() for v in books.values() for bk in v]}
        with open(out, "a") as f:
            f.write(json.dumps(snap) + "\n")
        print(json.dumps(snap), flush=True)
    ws.close()


if __name__ == "__main__":
    run(sys.argv[2].split(","), float(sys.argv[3]), sys.argv[1])
