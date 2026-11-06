"""Telemetría del agente de Tokio → el tablero de la PC, por el relé local (UDP firmado, relay/protocol.py).

- Cada 15 s, un resumen ("agente"): versión, encendido, memoria, fuentes, noticias, lag, peso de Binance, IA del día,
  pausa, y cada cuenta (simulacro / práctica) con su equity, PnL y posiciones abiertas.
- Cada alerta (lo que en la PC iría a Telegram: entradas, salidas, frenos, errores), apenas sale ("alerta").

Cada mensaje entra en un datagrama (≤ 1400 bytes): las listas van recortadas. La telemetría va FIRMADA pero NO
cifrada: con dinero real (fase 3) no lleva montos, solo porcentajes y símbolos (REGLAS G-8: nunca saldos reales a la
vista de terceros). Nunca frena al agente: si no se puede mandar, se pierde ese mensaje y listo.
"""
import asyncio
import logging
import os
import re
import time
from collections import deque

from .. import __version__, paths
from ..relay import protocol

log = logging.getLogger("tradingagent.telemetria")
EVERY_S = 15.0
RETRY_S = 1.0                                          # sin conexión con el relé todavía: se reintenta enseguida
MAX_POSITIONS = 4
POS_FIELDS = ("s", "lado", "px", "stop", "nocional", "desde", "cat")    # cada posición va como lista (entra más)
TEXT_CUTS = (600, 300, 100, 0)
_TAG = re.compile(r"<[^>]+>")


def rss_mb() -> float | None:
    """Memoria residente del proceso (Linux: /proc; en otro sistema, None)."""
    try:
        with open("/proc/self/statm", encoding="ascii") as f:
            pages = int(f.read().split()[1])
        return round(pages * os.sysconf("SC_PAGE_SIZE") / 2**20, 1)
    except (OSError, ValueError, AttributeError, IndexError):
        return None


LOW_MEMORY_MB = 120                                   # menos que esto libre en el servidor: aviso en el registro
LOW_MEMORY_WARN_S = 600


def system_free_mb(meminfo: str = "/proc/meminfo") -> float | None:
    """Memoria disponible del sistema (Linux: MemAvailable; en otro sistema, None). El 6-nov, la actualización
    automática de Ubuntu dejó al servidor de Tokio con 59 MB libres durante minutos (el 5-nov, con poca memoria, el
    servidor viejo se quedó sin red): por eso se mide en cada latido."""
    try:
        with open(meminfo, encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        return None
    return None


def _num(x, digits: int = 4):
    try:
        return round(float(x), digits)
    except (TypeError, ValueError):
        return None


def account(trader) -> dict:
    """Una cuenta en pocas cifras. Dinero real: solo el retorno en % y los símbolos (sin montos)."""
    st = trader.st
    real = bool(getattr(trader, "real", False)) and not bool(getattr(trader, "simulated", False))
    kind = "real" if real else "simulacro" if getattr(trader, "real", False) else "practica"
    ini = _num(st.get("initial"))
    eq = _num(st.get("equity") or st.get("cash"))
    ret = round((eq / ini - 1) * 100, 3) if eq is not None and ini else None
    out = {"tipo": kind, "mercado": st.get("mercado") or trader.market.name, "ret_pct": ret,
           "ops": (st.get("stats") or {}).get("operaciones", 0), "halt": bool((st.get("risk") or {}).get("halted")),
           "n_pos": len(st.get("positions") or {})}
    pos = []
    for sym, p in list((st.get("positions") or {}).items())[:MAX_POSITIONS]:
        money = (None, None, None) if real else (_num(p.get("avg_price"), 8), _num(p.get("stop"), 8),
                                                 _num(p.get("notional"), 2))
        pos.append([sym, p.get("side"), *money, _num(p.get("entry_ts"), 0), p.get("category")])
    out["pos"] = pos
    if not real:
        out |= {"eq": _num(eq, 4), "ini": _num(ini, 2), "pnl": _num((st.get("stats") or {}).get("pnl_neto"), 4)}
    return out


class RemoteTelemetry:
    def __init__(self, agent, every_s: float = EVERY_S):
        self.agent, self.every_s = agent, every_s
        self.client = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.pending: deque = deque(maxlen=20)               # alertas de antes de que el relé esté listo
        self.sent = self.lost = 0
        self._low_memory_at = float("-inf")

    def attach(self, client) -> None:
        self.client = client

    @property
    def real(self) -> bool:
        t = getattr(self.agent, "real_trader", None)
        return t is not None and not t.simulated

    # -- resumen -----------------------------------------------------------------------------------
    def summary(self) -> dict:
        a = self.agent
        hb = a.collect()
        src, news = hb.get("sources") or {}, hb.get("news") or {}
        llm = ((hb.get("trader") or {}).get("llm")) or {}
        peso = hb.get("peso_binance") or {}
        out = {"t": "tel", "k": "agente", "v": __version__[:12], "up": round(time.time() - a.started),
               "mem": rss_mb(), "mem_libre": hb.get("mem_libre_mb"), "src": [src.get("ok", 0), src.get("total", 0)],
               "fallan": (src.get("failing") or [])[:4],
               "news": [news.get("aceptadas", 0), news.get("duplicadas", 0), news.get("stale", 0)],
               "lag": (hb.get("loop_lag") or {}).get("p99_ms"), "peso": [peso.get("usado"), peso.get("limite")],
               "ia": [_num(llm.get("usd"), 5), llm.get("calls", 0), llm.get("failures", 0)],
               "kill": paths.kill_file().exists(), "cuentas": []}
        for t in (a.real_trader, a.trader):
            if t is not None:
                out["cuentas"].append(account(t))
        return out

    def _send(self, payload: dict) -> bool:
        if self.client is None or not self.client.send(payload):
            self.lost += 1
            return False
        self.sent += 1
        return True

    def watch_memory(self, free_mb: float | None, now: float | None = None) -> bool:
        """Aviso en el registro si el servidor se queda con poca memoria (como mucho uno cada 10 min): así la
        vigilancia lo ve aunque el pico dure pocos minutos."""
        now = time.monotonic() if now is None else now
        if free_mb is None or free_mb >= LOW_MEMORY_MB or now - self._low_memory_at < LOW_MEMORY_WARN_S:
            return False
        self._low_memory_at = now
        log.warning("poca memoria en el servidor: %.0f MB libres (el agente usa %s MB)", free_mb, rss_mb())
        return True

    def send_summary(self) -> bool:
        try:
            msg = self.summary()
        except Exception:                                        # la telemetría nunca frena al agente
            log.warning("no pude armar el resumen para la PC", exc_info=True)
            return False
        self.watch_memory(msg.get("mem_libre"))
        while True:
            try:
                protocol.encode(msg, b"\0" * 32)                   # ¿entra en un datagrama?
                break
            except ValueError:
                cut = next((c for c in msg["cuentas"] if c["pos"]), None)
                if cut is None:
                    msg["fallan"] = []
                    if msg.get("cuentas"):
                        msg["cuentas"] = msg["cuentas"][:1]
                    break
                cut["pos"] = cut["pos"][:-1]
        return self._send(msg)

    # -- alertas -----------------------------------------------------------------------------------
    def ready(self) -> bool:
        return self.loop is not None and self.client is not None and self.client.transport is not None

    def alert(self, alert) -> None:
        """Lo llama AlertTee desde cualquier hilo: se manda en el bucle del agente (antes de que haya conexión con
        el relé, queda guardada: el aviso de arranque, por ejemplo)."""
        if not self.ready():
            self.pending.append(alert)
            return
        try:
            self.loop.call_soon_threadsafe(self._send_alert, alert)
        except RuntimeError:                                     # el bucle ya cerró
            pass

    def _send_alert(self, alert) -> None:
        text = _TAG.sub("", str(getattr(alert, "text", "")))
        if self.real:
            text = "(dinero real: el detalle queda en Tokio)"
        for cut in TEXT_CUTS:
            msg = {"t": "tel", "k": "alerta", "tipo": str(getattr(alert, "kind", "")), "texto": text[:cut]}
            try:
                protocol.encode(msg, b"\0" * 32)
            except ValueError:
                continue
            self._send(msg)
            return

    async def run(self, stop: asyncio.Event) -> None:
        self.loop = asyncio.get_running_loop()
        while not stop.is_set():
            ok = False
            if self.ready():
                while self.pending:
                    self._send_alert(self.pending.popleft())
                ok = self.send_summary()
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.every_s if ok else RETRY_S)
            except TimeoutError:
                pass


class AlertTee:
    """El despachador de alertas de siempre, más una copia de cada alerta a la PC (telemetría)."""

    def __init__(self, inner, on_alert):
        self.inner, self.on_alert = inner, on_alert

    def submit(self, alert) -> bool:
        ok = self.inner.submit(alert)
        try:
            self.on_alert(alert)
        except Exception:                                        # la copia nunca frena la alerta
            log.debug("no pude copiar la alerta a la PC", exc_info=True)
        return ok

    def __getattr__(self, name):
        return getattr(self.inner, name)
