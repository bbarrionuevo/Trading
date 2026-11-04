"""Alertas: prioridad, tipo y texto listo para Telegram (HTML con todo lo variable escapado).

Los textos son para el dueño, no para un programador: castellano simple, sin siglas (nada de SL, TP, PnL,
bps ni "circuit breaker"), números a la argentina (98.500,5) y siempre qué pasó, cuánto se ganó o se
puede perder y por qué. Si algo necesita una acción del dueño, el mensaje lo dice; si no, lo aclara.

Prioridades (la cola del despachador saca siempre la más urgente primero):
  RISK (0): cierres por límite u objetivo y frenos de seguridad: nunca se descartan.
  TRADE (1): cada operación. SYSTEM (2): inicio, fallas de fuentes. INFO (3): latidos y resúmenes.
"""
import html
import re
from dataclasses import dataclass, field
from decimal import Decimal as D
from enum import IntEnum

MAX_TELEGRAM_CHARS = 4096
AR_OFFSET_H = -3                      # hora argentina = UTC - 3 (sin horario de verano)


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


# -- castellano simple -------------------------------------------------------------------------------------
def num_es(x, dp: int | None = None) -> str:
    """Número a la argentina: miles con punto y decimales con coma (98.500,5 · 0,0002)."""
    if x is None:
        return "—"
    s = fmt_num(x) if dp is None else f"{D(str(x)):,.{dp}f}"
    return s.replace(",", "\0").replace(".", ",").replace("\0", ".")


def usdt_es(x, signed: bool = True) -> str:
    """Plata con dos decimales: +0,45 USDT · -1,20 USDT."""
    d = D(str(x or 0)).quantize(D("0.01"))
    return f"{'+' if signed and d > 0 else ''}{num_es(d, 2)} USDT"


def pct_es(x, signed: bool = True) -> str:
    d = D(str(x or 0)).quantize(D("0.1"))
    return f"{'+' if signed and d > 0 else ''}{num_es(d, 1)} %"


def asset_es(symbol) -> str:
    """BTC_USDT o BTCUSDT → BTC."""
    s = str(symbol)
    for suffix in ("_USDT", "USDT"):
        if s.endswith(suffix) and len(s) > len(suffix):
            return s[: -len(suffix)]
    return s


def mode_es(mode: str) -> str:
    """Etiqueta del mercado: siempre deja claro si es práctica o plata de verdad."""
    m = str(mode or "PAPER")
    if m.upper().startswith("SIMULACRO"):
        return "SIMULACRO de dinero real (sin plata)"
    if m.upper().startswith("PAPER"):
        where = m[5:].strip()
        return f"práctica{' en ' + where if where else ''} (sin plata real)"
    return "💵 DINERO REAL"


def minutes_es(m) -> str:
    m = int(m or 0)
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h" + (f" {m % 60} min" if m % 60 else "")


def ops_es(n: int) -> str:
    return f"{n} operación" if n == 1 else f"{n} operaciones"


def utc_hhmm_to_ar(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    return f"{(h + AR_OFFSET_H) % 24:02d}:{m:02d}"


CATEGORY_ES = {
    "hack": "un hackeo o robo", "deslistado": "que un exchange lo saca de la lista",
    "quiebra": "una quiebra o retiros frenados", "regulatorio": "una noticia de reguladores o juicios",
    "etf": "una noticia de ETF (fondos que cotizan en bolsa)", "macro": "una noticia de la Reserva Federal o de tasas",
    "desbloqueo": "un desbloqueo de tokens (más oferta)", "caida_red": "una caída de la red",
    "listado": "un listado nuevo en un exchange", "tesoreria": "una compra de cripto por una empresa o un país",
    "adopcion": "una alianza o un uso nuevo", "quema": "una quema o recompra de tokens",
    "mejora_red": "una mejora de la red", "precio": "un movimiento de precio", "otro": "una noticia",
    "resultados": "los resultados de una empresa", "fusion": "una fusión o compra de empresas",
}
SOURCE_ES = {"fed.press": "la Reserva Federal", "fed.monetary": "la Reserva Federal (tasas de interés)",
             "sec.press": "la SEC (el regulador de EE. UU.)", "sec.edgar.8k": "la SEC (informe de una empresa)",
             "cnbc.top": "CNBC", "cnbc.earnings": "CNBC (resultados de empresas)", "seekingalpha": "Seeking Alpha",
             "investing": "Investing.com", "binance.listados": "Binance (anuncio oficial)",
             "okx.listados": "OKX (anuncio oficial)", "bitget.listados": "Bitget (anuncio oficial)",
             "upbit.avisos": "Upbit (anuncio oficial, Corea)"}


def category_es(category: str) -> str:
    return CATEGORY_ES.get(str(category), "una noticia")


def side_es(side: str) -> str:
    """El lado de una apuesta, en palabras: "largo" → cuando apuesto a que sube."""
    return "cuando apuesto a que sube" if str(side) == "largo" else "cuando apuesto a que baja"


def _why_line(why: str, category: str = "", title: str = "", source: str = "") -> str:
    if title or category:
        what = category_es(category) if category else "una noticia"
        quote = f" — «{esc(title[:200])}»" if title else ""
        return f"📰 Por qué: {esc(what)}{quote}" + (f" ({esc(source)})" if source else "")
    return f"📰 Por qué: {esc(why[:220])}" + (f" ({esc(source)})" if source else "") if why else ""


# -- operaciones --------------------------------------------------------------------------------------------
def trade_alert(symbol, side, qty, price, *, why: str = "", impact=None, confidence=None, source=None,
                role: str = "entry", pnl=None, mode: str = "PAPER") -> Alert:
    """Aviso genérico de una orden ejecutada (lo usa el servicio de ejecución del venue de prueba)."""
    side = str(side)
    asset = asset_es(symbol)
    notional = D(str(qty)) * D(str(price)) if qty is not None and price is not None else None
    if role == "entry":
        head = f"🟢 <b>Compré {esc(asset)}</b>" if side == "buy" else f"🔴 <b>Vendí en corto {esc(asset)}</b>"
    else:
        head = f"⚪ <b>Cerré {esc(asset)}</b>"
    lines = [f"{head} · {esc(mode_es(mode))}",
             f"{esc(num_es(qty))} a {esc(num_es(price))}" + (f" (≈ {esc(usdt_es(notional, False))})" if notional else "")]
    if why:
        lines.append(_why_line(why, source=source or ""))
    if role != "entry" and pnl is not None:
        lines.append(f"Resultado: {esc(usdt_es(pnl))}")
    return Alert(Priority.TRADE, "trade" if role == "entry" else "exit", clip("\n".join(lines)),
                 meta={"symbol": symbol, "side": side})


def risk_alert(kind: str, symbol, side, qty, price, pnl=None, *, mode: str = "PAPER") -> Alert:
    asset = esc(asset_es(symbol))
    head = {"stop_loss": f"🔻 <b>Corté la pérdida en {asset}</b>",
            "take_profit": f"✅ <b>Cobré la ganancia en {asset}</b>"}.get(kind, f"⚠️ <b>Cerré {asset}</b>")
    text = (f"{head} · {esc(mode_es(mode))}\n{esc(num_es(qty))} a {esc(num_es(price))}"
            + (f"\nResultado: {esc(usdt_es(pnl))}" if pnl is not None else ""))
    return Alert(Priority.RISK, kind, clip(text), meta={"symbol": symbol})


def entry_alert(symbol, side: int, qty, price, notional, *, stop, tp, p: float, alpha: float = 0.0, why: str = "",
                source: str = "", order_id: str = "", mode: str = "PAPER", expiry_min: int | None = None,
                category: str = "", title: str = "", trail: float = 0.0, target: bool = True) -> Alert:
    """Aviso de entrada: qué hizo, con cuánto, cuándo sale (pierde poco o gana más) y por qué."""
    asset = esc(asset_es(symbol))
    price_d, notional_d = D(str(price)), D(str(notional))
    loss = notional_d * abs(price_d - D(str(stop))) / price_d if price_d else D(0)
    gain = notional_d * abs(D(str(tp)) - price_d) / price_d if price_d else D(0)
    if side > 0:
        head, bet = f"🟢 <b>Compré {asset}</b>", "Gano si sube."
        exits = (f"Si baja a {esc(num_es(stop))} vendo y pierdo ≈ {esc(usdt_es(loss, False))}; si sube a "
                 f"{esc(num_es(tp))} vendo y gano ≈ {esc(usdt_es(gain, False))}.")
    else:
        head, bet = f"🔴 <b>Vendí en corto {asset}</b>", "Gano si baja."
        exits = (f"Si sube a {esc(num_es(stop))} cierro y pierdo ≈ {esc(usdt_es(loss, False))}; si baja a "
                 f"{esc(num_es(tp))} cierro y gano ≈ {esc(usdt_es(gain, False))}.")
    if trail:                                       # sin techo: el stop acompaña al precio
        way = "sube" if side > 0 else "baja"
        exits = (f"Si va en contra hasta {esc(num_es(stop))} salgo y pierdo ≈ {esc(usdt_es(loss, False))}. Si "
                 f"{way}, el límite lo acompaña ({esc(pct_es(D(str(trail)) * 100, False))} del mejor precio) y salgo "
                 "cuando retroceda: no hay techo para la ganancia.")
    if not target and not trail:                    # salida por tiempo: sin techo y con stop fijo
        exits = (f"Si va en contra hasta {esc(num_es(stop))} salgo y pierdo ≈ {esc(usdt_es(loss, False))}. Si no, "
                 "salgo cuando se cumple el tiempo, con lo que haya ganado: no hay techo.")
    lines = [f"{head} · {esc(mode_es(mode))}",
             f"Puse {esc(usdt_es(notional_d, False))} con {asset} a {esc(num_es(price))} USDT. {bet}",
             exits + (f" Como mucho lo tengo {esc(minutes_es(expiry_min))}." if expiry_min else ""),
             f"Chance estimada de ganar: {round(float(p) * 100)} %."]
    why_line = _why_line(why, category, title, source)
    if why_line:
        lines.append(why_line)
    return Alert(Priority.TRADE, "trade", clip("\n".join(lines)), meta={"symbol": symbol, "side": side})


EXIT_REASON_ES = {
    "take_profit": "Llegó al precio objetivo{level} y cobré la ganancia.",
    "stop_loss": "Llegó al límite de pérdida{level} y corté. Es parte del plan: las pérdidas se cortan chicas.",
    "expiracion": "Pasó el tiempo máximo sin llegar al objetivo ni al límite.",
    "noticia_en_contra": "Salió una noticia en contra y preferí salir.",
    "freno": "Lo cerró el freno de seguridad (la cuenta perdió demasiado en el día).",
    "liquidacion": "El precio saltó de golpe más allá del límite y la posición se liquidó.",
    "stop_movil": "El precio retrocedió desde su mejor nivel y salí con la ganancia asegurada{level}.",
    "pedido_del_dueno": "La cerré porque lo pediste.",
    "cerrada_en_el_exchange": "MEXC ya la había cerrado sola (el stop de emergencia) o la cerraste a mano. La saqué "
                              "de la cuenta del programa con un precio estimado: el resultado exacto está en MEXC.",
}


def exit_alert(symbol, side: int, reason: str, qty, price, *, pnl, ret, slippage_bps: float | None = None,
               why: str = "", mode: str = "PAPER", level=None) -> Alert:
    """Aviso de salida: cuánto se ganó o perdió (neto de comisiones) y por qué se cerró."""
    asset = esc(asset_es(symbol))
    pnl_d = D(str(pnl or 0))
    pct = pct_es(D(str(ret or 0)) * 100)
    if pnl_d > 0:
        head = f"✅ <b>Gané {esc(usdt_es(pnl_d))} con {asset}</b> ({esc(pct)})"
    elif pnl_d < 0:
        head = f"🔻 <b>Perdí {esc(usdt_es(pnl_d))} con {asset}</b> ({esc(pct)})"
    else:
        head = f"⚪ <b>Cerré {asset} sin ganar ni perder</b>"
    if reason == "liquidacion":
        head = f"💥 <b>{asset} se liquidó: perdí {esc(usdt_es(pnl_d))}</b>"
    lvl = f" ({esc(num_es(level))})" if level is not None else ""
    explain = EXIT_REASON_ES.get(reason, "Cerré la posición.").format(level=lvl)
    lines = [f"{head} · {esc(mode_es(mode))}", explain]
    if why:
        lines.append(f"📰 {esc(why[:200])}")
    prio = (Priority.RISK if reason in ("stop_loss", "take_profit", "liquidacion", "freno", "cerrada_en_el_exchange",
                                        "stop_movil")
            else Priority.TRADE)
    return Alert(prio, reason, clip("\n".join(lines)), meta={"symbol": symbol})


# -- frenos, sistema e inicio ----------------------------------------------------------------------------------
def circuit_breaker_alert(detail: str, *, until: str = "") -> Alert:
    """Los frenos de seguridad (REGLAS A-4), explicados: qué pasó, qué hace el programa y si hace falta algo."""
    d = str(detail)
    if m := re.match(r"HALT: drawdown ([\d.]+)% ≥ ([\d.]+)%", d):
        text = (f"⛔ <b>Frené todo</b>: la cuenta cayó {esc(num_es(m.group(1)))} % desde su punto más alto (el tope "
                f"es {esc(num_es(m.group(2)))} %). No abro operaciones nuevas hasta que lo revisemos juntos.")
    elif m := re.match(r"pérdida del día ([\d.]+)%", d):
        text = (f"🛑 <b>Freno del día</b>: hoy la cuenta bajó {esc(num_es(m.group(1)))} %. No abro operaciones nuevas "
                "hasta las 21:00 (hora argentina) y cerré lo que estaba abierto. No hace falta que hagas nada.")
    elif m := re.match(r"pérdida de la semana ([\d.]+)%", d):
        text = (f"🛑 <b>Freno de la semana</b>: esta semana la cuenta bajó {esc(num_es(m.group(1)))} %. No abro "
                "operaciones nuevas hasta el domingo a las 21:00 (hora argentina). No hace falta que hagas nada.")
    elif m := re.match(r"(\d+) pérdidas seguidas: enfriamiento hasta (\d\d:\d\d) UTC", d):
        text = (f"⏸ <b>Pausa corta</b>: {m.group(1)} pérdidas seguidas. Vuelvo a operar a las "
                f"{utc_hhmm_to_ar(m.group(2))} (hora argentina). No hace falta que hagas nada.")
    else:
        text = f"🛑 <b>Freno de seguridad</b>: {esc(d)}" + (f"\nNo abro operaciones nuevas hasta {esc(until)}." if until else "")
    return Alert(Priority.RISK, "circuit_breaker", clip(text), key="circuit_breaker")


def system_alert(text: str, *, silent: bool = True, key: str = "") -> Alert:
    return Alert(Priority.SYSTEM, "system", clip(esc(text)), key=key, silent=silent)


def startup_alert(mode: str, sources_active: int, sources_total: int, version: str, extra: str = "") -> Alert:
    text = (f"✅ <b>TRADING está funcionando</b> · {esc(mode_es(mode))}\nLeo {sources_active} de {sources_total} "
            "fuentes de noticias. Te aviso cada operación y te mando un resumen por día. Mandame /estado para ver "
            "cómo va, /pausa para frenar o /ayuda para ver todas las órdenes."
            + (f"\n{esc(extra)}" if extra else ""))
    return Alert(Priority.SYSTEM, "startup", clip(text), key="startup")


def daily_alert(day: str, *, mode: str, closed: int, won: int, pnl, equity, initial, total_closed: int,
                needed: int = 60, llm: dict | None = None) -> Alert:
    """Resumen diario de la cuenta de la PC, en una mirada."""
    dd, mm = day[8:10], day[5:7]
    if closed:
        result = (f"{ops_es(closed)} cerrada{'s' if closed != 1 else ''} "
                  f"({won} ganada{'s' if won != 1 else ''}): {usdt_es(pnl)}.")
    else:
        result = "No hubo operaciones (ninguna noticia pasó todos los filtros)."
    lines = [f"📊 <b>Resumen del {dd}/{mm}</b> · {esc(mode_es(mode))}", esc(result),
             f"Cuenta: {esc(usdt_es(equity, False))} (empezó con {esc(usdt_es(initial, False))})."]
    if total_closed < needed:
        lines.append(f"Van {total_closed} operaciones cerradas: con {needed} ya se puede medir bien si el sistema "
                     "gana.")
    if llm:
        calls, usd, fails = llm.get("calls", 0), llm.get("usd", 0.0), llm.get("failures", 0)
        line = f"IA: {calls} consulta{'s' if calls != 1 else ''} ({esc(num_es(round(usd, 4)))} USD)."
        if fails and not calls:
            line += " ⚠️ No respondió: revisá la clave de Claude."
        lines.append(line)
    return Alert(Priority.INFO, "info", clip("\n".join(lines)), key=f"resumen:{day}", silent=True)
