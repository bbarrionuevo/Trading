"""Alertas: prioridad, tipo y texto listo para Telegram (HTML con todo lo variable escapado).

Prioridades (la cola del despachador saca siempre la más urgente primero):
  RISK (0): stop-loss, take-profit, circuit breaker y fallas de protección: nunca se descartan.
  TRADE (1): cada orden ejecutada. SYSTEM (2): inicio, fallas de fuentes. INFO (3): latidos y resúmenes.
"""
import html
from dataclasses import dataclass, field
from decimal import Decimal as D
from enum import IntEnum

MAX_TELEGRAM_CHARS = 4096


class Priority(IntEnum):
    RISK = 0
    TRADE = 1
    SYSTEM = 2
    INFO = 3


@dataclass(frozen=True)
class Alert:
    priority: Priority
    kind: str                    # trade | exit | stop_loss | take_profit | circuit_breaker | system | info | startup
    text: str                    # HTML ya escapado
    key: str = ""                # alertas con la misma clave en la cola se fusionan (la más nueva gana)
    silent: bool = False         # disable_notification (sin sonido)
    meta: dict = field(default_factory=dict)


def esc(x) -> str:
    return html.escape(str(x), quote=False)


def fmt_num(x, max_dp: int = 8) -> str:
    """Número legible: miles con coma y sin ceros de más (1234.5 → 1,234.5)."""
    if x is None:
        return "—"
    d = D(str(x))
    if d == d.to_integral_value() and abs(d) >= 1:
        return f"{d:,.0f}"
    q = f"{d:,.{max_dp}f}".rstrip("0").rstrip(".")
    return q or "0"


def fmt_money(x) -> str:
    if x is None:
        return "—"
    d = D(str(x))
    return f"{d:+,.2f}" if d else "0.00"


def clip(text: str, limit: int = MAX_TELEGRAM_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def trade_alert(symbol, side, qty, price, *, why: str = "", impact=None, confidence=None, source=None,
                role: str = "entry", pnl=None, mode: str = "PAPER") -> Alert:
    side = str(side)
    notional = D(str(qty)) * D(str(price)) if qty is not None and price is not None else None
    if role == "entry":
        head = "🟢 <b>LONG</b>" if side == "buy" else "🔴 <b>SHORT</b>"
    else:
        head = "⚪ <b>CIERRE</b>"
    line1 = (f"{head} <b>{esc(symbol)}</b> · {esc(fmt_num(qty))} (≈ {esc(fmt_num(round(notional, 2)) if notional else '—')} "
             f"USDT) @ {esc(fmt_num(price))} · {esc(mode)}")
    parts = []
    if impact is not None:
        parts.append(f"impacto {float(impact):+.2f}")
    if confidence is not None:
        parts.append(f"conf. {float(confidence):.2f}")
    detail = " · ".join(parts)
    reason = esc(why[:220]) if why else "sin resumen del análisis"
    src = f" ({esc(source)})" if source else ""
    line2 = f"🧠 {detail + ' — ' if detail else ''}{reason}{src}"
    lines = [line1, line2]
    if role != "entry" and pnl is not None:
        lines.append(f"PnL {esc(fmt_money(pnl))} USDT")
    return Alert(Priority.TRADE, "trade" if role == "entry" else "exit", clip("\n".join(lines)),
                 meta={"symbol": symbol, "side": side})


def risk_alert(kind: str, symbol, side, qty, price, pnl=None, *, mode: str = "PAPER") -> Alert:
    head = {"stop_loss": "🛑 <b>STOP-LOSS</b>", "take_profit": "🎯 <b>TAKE-PROFIT</b>"}.get(kind, f"⚠️ <b>{esc(kind)}</b>")
    verb = "vendió" if str(side) == "sell" else "compró"
    text = (f"{head} <b>{esc(symbol)}</b> · {verb} {esc(fmt_num(qty))} @ {esc(fmt_num(price))} · {esc(mode)}"
            + (f"\nPnL {esc(fmt_money(pnl))} USDT" if pnl is not None else ""))
    return Alert(Priority.RISK, kind, clip(text), meta={"symbol": symbol})


def circuit_breaker_alert(detail: str, *, until: str = "") -> Alert:
    text = f"🚨 <b>CIRCUIT BREAKER</b> · {esc(detail)}" + (f"\nEntradas bloqueadas hasta {esc(until)}" if until else "")
    return Alert(Priority.RISK, "circuit_breaker", clip(text), key="circuit_breaker")


def system_alert(text: str, *, silent: bool = True, key: str = "") -> Alert:
    return Alert(Priority.SYSTEM, "system", clip(esc(text)), key=key, silent=silent)


def startup_alert(mode: str, sources_active: int, sources_total: int, version: str, extra: str = "") -> Alert:
    text = (f"✅ <b>Agente iniciado</b> · modo {esc(mode)} · {sources_active}/{sources_total} fuentes activas · "
            f"v{esc(version)}" + (f"\n{esc(extra)}" if extra else ""))
    return Alert(Priority.SYSTEM, "startup", clip(text), key="startup")
