"""Paper EN VIVO de market making cubierto: maker en MEXC (0%) + cobertura taker en Binance.

Por cada par se cotiza el mejor bid y el mejor ask de MEXC, solo si al precio actual de Binance la
vuelta cubierta deja al menos `min_edge_pct`. Llenado con el mismo modelo que el resto del sistema
(modelo 5: solo cuando se opera atravesando nuestro precio).
En cada sondeo primero se evalúan las operaciones contra las órdenes que estaban puestas y después
se re-cotiza (al revés se esquivan llenados adversos). La cobertura NO se hace al precio del momento:
se usa el precio de Binance del sondeo SIGUIENTE a la detección, para incluir demora y movimiento adverso.
Inventario (lots): con capital chico no se puede llenar sin límite. Con lots=L, cada par arranca
con L lotes de moneda y L de USDT en cada exchange; una compra en MEXC gasta USDT de MEXC y moneda
de Binance. Si un lado se agota, ese lado deja de cotizar hasta que un llenado del lado contrario
lo rebalancee solo, o hasta el rebalanceo MANUAL del dueño (R-01: las claves no pueden retirar),
modelado cada `rebalance_h` horas con el costo real de las transferencias.
Resultado: llenados por hora, ganancia neta y minutos bloqueado por falta de inventario.

python -m nexus.live_paper_hedge OUT.jsonl SYM1,SYM2 minutos [taker_binance_pct] [lots] [nocional]
"""
import json
import sys
import time

import requests

from .evidence import FILL_MODEL

# Costo en USDT de un rebalanceo (moneda en un sentido + USDT en el otro), red más barata.
# Medido en el PC con capital/config el 24-oct-2026 (reportes/20261024-1250.md en pc-sesion).
TRANSFER_USDT = {"IOSTUSDT": 0.02, "QUICKUSDT": 0.08, "DCRUSDT": 0.15, "TREEUSDT": 0.10, "PORTOUSDT": 0.45}
DEFAULT_TRANSFER_USDT = 1.0                                   # conservador si no fue medido
MEXC = "https://api.mexc.com/api/v3"
BINANCE = "https://data-api.binance.vision/api/v3"


class Side:
    """Una orden maker en MEXC (compra en el bid o venta en el ask). Se llena solo si la atraviesan."""

    def __init__(self, kind):
        self.kind, self.px, self.queue = kind, None, None

    def quote(self, px, queue, own=0.0):
        """queue = cola visible adelante; own = tamaño de nuestra orden. La cola queda como dato (nunca
        más que lo visible); el llenado no depende de ella (modelo 5)."""
        if px != self.px:
            self.px, self.queue = px, queue + own
        elif self.queue is not None:
            self.queue = min(self.queue, queue + own)

    def cancel(self):
        self.px, self.queue = None, None

    def hit(self, price, qty, buyer_is_maker):
        """True si la operación pública llena esta orden."""
        if self.px is None:
            return False
        if self.kind == "buy" and buyer_is_maker:            # vendedor agresor golpea bids
            return price < self.px                           # modelo 5: solo si atraviesa (evidence.FILL_MODEL)
        if self.kind == "sell" and not buyer_is_maker:       # comprador agresor levanta asks
            return price > self.px
        return False


class HedgedPair:
    def __init__(self, sym, notional=40.0, taker_b_pct=0.1, min_edge_pct=0.05, lots=None, transfer_usdt=None):
        self.sym, self.notional, self.fb, self.min_edge = sym, notional, taker_b_pct / 100, min_edge_pct / 100
        self.buy, self.sell = Side("buy"), Side("sell")
        self.fresh = []                                      # llenados detectados en este sondeo
        self.pending = []                                    # llenados esperando cobertura (próximo sondeo)
        self.fills, self.pnl, self.adverse = 0, 0.0, 0
        self.b_bid = self.b_ask = None
        self.lots, self.pos = lots, 0                        # pos: lotes netos comprados en MEXC
        self.transfer = TRANSFER_USDT.get(sym, DEFAULT_TRANSFER_USDT) if transfer_usdt is None else transfer_usdt
        self.rebalances, self.transfer_cost = 0, 0.0
        self.blocked_s = {"buy": 0.0, "sell": 0.0}
        self.last_t, self.was_blocked = None, []
        self.mid0 = self.mid = None                          # precio de Binance al empezar y ahora

    def capital(self):
        """Capital que exige el par: L lotes de moneda y L de USDT en cada exchange."""
        return 4 * self.lots * self.notional if self.lots is not None else None

    def inventory_change(self):
        """Riesgo de precio: la cobertura mantiene constante la moneda total (2 L lotes entre los dos
        exchanges), así que su valor sube y baja con el precio. No es ganancia de la estrategia, pero
        hay que tenerlo en cuenta (se puede cubrir con un corto en futuros)."""
        if self.lots is None or not self.mid0 or not self.mid:
            return None
        return 2 * self.lots * self.notional * (self.mid / self.mid0 - 1)

    def can(self, kind):
        if self.lots is None:
            return True
        return self.pos < self.lots if kind == "buy" else self.pos > -self.lots

    def rebalance(self):
        """Rebalanceo manual del dueño: vuelve a inventario parejo pagando las transferencias."""
        if self.pos != 0:
            self.pos = 0
            self.rebalances += 1
            self.transfer_cost += self.transfer

    def on_books(self, m_bid, m_bq, m_ask, m_aq, b_bid, b_ask, now=None):
        now = time.time() if now is None else now
        if self.last_t is not None:                          # bloqueo visto en el sondeo anterior
            for kind in self.was_blocked:
                self.blocked_s[kind] += now - self.last_t
        self.last_t = now
        self.was_blocked = [k for k in ("buy", "sell") if not self.can(k)]
        # 1) cubrir los llenados del ciclo anterior al precio de Binance de AHORA (demora real)
        for kind, px, edge_then in self.pending:
            if kind == "buy":                                # compré en MEXC: vendo en Binance al bid
                r = b_bid * (1 - self.fb) / px - 1
            else:                                            # vendí en MEXC: compro en Binance al ask
                r = px / (b_ask * (1 + self.fb)) - 1
            self.pnl += self.notional * r
            self.fills += 1
            self.adverse += r < edge_then
        self.pending, self.fresh = self.fresh, []
        self.b_bid, self.b_ask = b_bid, b_ask
        self.mid = (b_bid + b_ask) / 2
        self.mid0 = self.mid0 or self.mid
        # 2) cotizar solo si la vuelta cubierta deja margen al precio actual
        if self.can("buy") and b_bid * (1 - self.fb) / m_bid - 1 >= self.min_edge:
            self.buy.quote(m_bid, m_bq, self.notional / m_bid)
        else:
            self.buy.cancel()
        if self.can("sell") and m_ask / (b_ask * (1 + self.fb)) - 1 >= self.min_edge:
            self.sell.quote(m_ask, m_aq, self.notional / m_ask)
        else:
            self.sell.cancel()

    def on_trade(self, price, qty, buyer_is_maker):
        for side in (self.buy, self.sell):
            if side.hit(price, qty, buyer_is_maker):
                if side.kind == "buy":
                    edge = self.b_bid * (1 - self.fb) / side.px - 1
                else:
                    edge = side.px / (self.b_ask * (1 + self.fb)) - 1
                self.fresh.append((side.kind, side.px, edge))
                self.pos += 1 if side.kind == "buy" else -1
                side.cancel()                                # se re-cotiza en el próximo ciclo

    def mark(self, minutes):
        net = self.pnl - self.transfer_cost
        cap, inv = self.capital(), self.inventory_change()
        return {"sym": self.sym, "model": FILL_MODEL, "fills": self.fills, "pnl_eur": round(self.pnl, 4),
                "capital_eur": cap, "inventory_eur": None if inv is None else round(inv, 4),
                "net_pct_day": round(net / max(minutes, 1e-9) * 1440 / cap * 100, 3) if cap else None,
                "per_fill_eur": round(self.pnl / self.fills, 4) if self.fills else None,
                "fills_per_hour": round(self.fills / max(minutes, 1e-9) * 60, 2), "adverse": self.adverse,
                "quoting": [s.kind for s in (self.buy, self.sell) if s.px is not None],
                "lots": self.lots, "pos": self.pos, "rebalances": self.rebalances,
                "transfer_cost": round(self.transfer_cost, 4), "net_eur": round(net, 4),
                "net_per_hour": round(net / max(minutes, 1e-9) * 60, 4),
                "blocked_min": {k: round(v / 60, 1) for k, v in self.blocked_s.items()}}


def to_markdown(snap):
    """Resumen de la última foto de una corrida, en español, ordenado por neto por hora."""
    ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(snap["ts"]))
    md = [f"# Market making cubierto MEXC→Binance (paper) — {ts}", "",
          f"{snap['elapsed_min']} min · nocional {snap['notional']} € por llenado · lotes por lado: "
          f"{snap['lots'] if snap['lots'] is not None else 'sin límite'} · taker Binance {snap['taker_b_pct']}% · "
          f"errores {snap['errors']}", "",
          "| par | llenados | por hora | € por llenado | adversos | transferencias € | neto € | neto €/h | "
          "% por día sobre el capital | inventario € (precio) | bloqueado compra/venta (min) |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in sorted(snap["pairs"], key=lambda p: -p.get("net_per_hour", 0)):
        b = p.get("blocked_min", {})
        md.append(f"| {p['sym']} | {p['fills']} | {p['fills_per_hour']} | {p['per_fill_eur'] if p['per_fill_eur'] is not None else '—'} | "
                  f"{p['adverse']} | {p.get('transfer_cost', 0)} | {p.get('net_eur', p['pnl_eur'])} | "
                  f"{p.get('net_per_hour', '—')} | {p.get('net_pct_day', '—')} | {p.get('inventory_eur', '—')} | "
                  f"{b.get('buy', 0)} / {b.get('sell', 0)} |")
    cap = next((p.get("capital_eur") for p in snap["pairs"] if p.get("capital_eur")), None)
    md += ["", "Bloqueado = minutos sin inventario para ese lado (con capital chico, el límite real). "
           "El rebalanceo es manual (las claves no pueden retirar) y se modela cada 12 h.",
           f"Capital por par: {cap} € (moneda y USDT en los dos exchanges). Inventario € = cuánto cambió el "
           "valor de la moneda que hay que tener (riesgo de precio, aparte de la ganancia). Modelo de llenado 5: solo si el precio atraviesa la orden."]
    return "\n".join(md) + "\n"


def pick_symbols(discovery_path, history_path, n=16, keep=4, min_edge_pct=0.1, days=2.0, now=None):
    """Pares para la corrida: los que más seguido dejan margen cubierto según el escáner (el bid de
    MEXC queda debajo del de Binance más la comisión; últimos `days` días, solo USDT: el capital está en
    USDT) y los `keep` que más ganaron en corridas anteriores. Antes la lista era fija y dejaba afuera
    pares con margen casi permanente (TFUEL, ATM, AT, HOME, PUNDIX, AWE, INIT el 25-oct)."""
    from collections import Counter, defaultdict
    now = now or time.time()
    seen = Counter()
    try:
        with open(discovery_path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if r.get("ts", now) < now - days * 86400:
                    continue
                for x in {x["pair"] for x in r.get("hedge_mexc_binance") or []
                          if x["net_pct"] > min_edge_pct and x["pair"].endswith("USDT")}:
                    seen[x] += 1
    except OSError:
        pass
    won = defaultdict(float)
    try:
        last = {}
        with open(history_path, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if last and r["elapsed_min"] < last["elapsed_min"]:   # terminó una corrida: su última foto
                    for p in last["pairs"]:
                        won[p["sym"]] += p.get("net_eur", p.get("pnl_eur", 0))
                last = r
        for p in (last or {}).get("pairs", []):
            won[p["sym"]] += p.get("net_eur", p.get("pnl_eur", 0))
    except OSError:
        pass
    best = [s for s, v in sorted(won.items(), key=lambda kv: -kv[1]) if v > 0][:keep]
    return list(dict.fromkeys(best + [s for s, _ in seen.most_common(n)]))[:n]


def run(syms, minutes, out, taker_b_pct=0.1, lots=None, notional=40.0, rebalance_h=12.0, dump_s=300):
    """Libros en lote (2 pedidos por ciclo) + operaciones por par. Rebalanceo manual cada rebalance_h."""
    s = requests.Session()
    pairs = {x: HedgedPair(x, notional=notional, taker_b_pct=taker_b_pct, lots=lots) for x in syms}
    seen = {x: set() for x in syms}
    t0 = last_dump = last_rebalance = time.time()
    errors = 0
    while time.time() - t0 < minutes * 60:
        try:
            m_all = {t["symbol"]: t for t in s.get(f"{MEXC}/ticker/bookTicker", timeout=5).json()}
            b_all = {t["symbol"]: t for t in s.get(f"{BINANCE}/ticker/bookTicker",
                                                    params={"symbols": json.dumps(list(pairs), separators=(",", ":"))},
                                                    timeout=5).json()}
        except Exception:
            errors += 1
            time.sleep(2)
            continue
        for x, hp in pairs.items():
            try:
                m, b = m_all[x], b_all[x]
                trades = s.get(f"{MEXC}/trades", params={"symbol": x, "limit": 100}, timeout=5).json()
                first = not seen[x]
                for t in sorted(trades, key=lambda t: t["time"]):  # contra las órdenes que estaban puestas
                    k = (t["time"], t["price"], t["qty"], t["isBuyerMaker"])
                    if k not in seen[x]:
                        seen[x].add(k)
                        if not first:                        # el primer lote es previo a toda orden
                            hp.on_trade(float(t["price"]), float(t["qty"]), t["isBuyerMaker"])
                hp.on_books(float(m["bidPrice"]), float(m["bidQty"]), float(m["askPrice"]), float(m["askQty"]),
                            float(b["bidPrice"]), float(b["askPrice"]))
                if len(seen[x]) > 4000:
                    seen[x] = set(sorted(seen[x])[-1500:])
            except Exception:
                errors += 1
        if time.time() - last_rebalance >= rebalance_h * 3600:
            last_rebalance = time.time()
            for hp in pairs.values():
                hp.rebalance()
        time.sleep(0.5)
        if time.time() - last_dump >= dump_s or time.time() - t0 >= minutes * 60:
            last_dump = time.time()
            mins = (time.time() - t0) / 60
            snap = {"ts": time.time(), "elapsed_min": round(mins, 1), "taker_b_pct": taker_b_pct, "lots": lots,
                    "notional": notional, "errors": errors, "pairs": [p.mark(mins) for p in pairs.values()]}
            with open(out, "a") as f:
                f.write(json.dumps(snap) + "\n")
            with open(str(out).replace(".jsonl", ".md"), "w", encoding="utf-8") as f:
                f.write(to_markdown(snap))
            print(json.dumps(snap), flush=True)


if __name__ == "__main__":
    a = sys.argv
    syms = a[2].split(",")
    if syms == ["auto"]:                         # elegir por persistencia del margen (data/discovery.jsonl)
        import os
        syms = pick_symbols(os.path.join(os.path.dirname(a[1]), "discovery.jsonl"), a[1])
        print("pares elegidos:", ",".join(syms), file=sys.stderr, flush=True)   # stdout va a /dev/null
    run(syms, float(a[3]), a[1], *(float(x) for x in a[4:5]),
        *(int(x) for x in a[5:6]), *(float(x) for x in a[6:7]))
