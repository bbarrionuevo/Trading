"""Estado de riesgo entre corridas: circuit breakers (REGLAS A-4) con los valores del sobre (limits.yaml).

- Pérdida del día ≥ `loss.daily_pct` ⇒ no abre más hasta las 00:00 UTC.
- Pérdida de la semana ≥ `loss.weekly_pct` ⇒ no abre más hasta el lunes 00:00 UTC.
- Drawdown desde el máximo ≥ `loss.max_drawdown_pct` ⇒ halt: solo vuelve con rearme humano.
- `loss.max_consecutive_losses` pérdidas seguidas ⇒ enfriamiento de `loss.cooldown_min`.
Salir de una posición siempre se puede: los frenos solo bloquean entradas.
"""
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _week(ts: float) -> str:
    y, w, _ = datetime.fromtimestamp(ts, UTC).isocalendar()
    return f"{y}-W{w:02d}"


@dataclass
class RiskState:
    day: str = ""
    day_start: float = 0.0
    week: str = ""
    week_start: float = 0.0
    peak: float = 0.0
    losses_in_row: int = 0
    cooldown_until: float = 0.0
    halted: str = ""                                  # motivo del halt; vacío = operando
    alerted: list = field(default_factory=list)       # frenos ya avisados (una alerta por freno y período)

    @classmethod
    def from_dict(cls, d: dict | None) -> "RiskState":
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def as_dict(self) -> dict:
        return asdict(self)

    def roll(self, now: float, equity: float) -> None:
        """Abre el día y la semana nuevos con el equity actual como referencia."""
        if _day(now) != self.day:
            self.day, self.day_start = _day(now), equity
        if _week(now) != self.week:
            self.week, self.week_start = _week(now), equity
        self.peak = max(self.peak, equity)

    def on_close(self, pnl: float, now: float, limits: dict) -> None:
        if pnl < 0:
            self.losses_in_row += 1
            if self.losses_in_row >= limits["loss"]["max_consecutive_losses"]:
                self.cooldown_until = now + limits["loss"]["cooldown_min"] * 60
                self.losses_in_row = 0
        else:
            self.losses_in_row = 0

    def blocks(self, now: float, equity: float, limits: dict) -> list[tuple[str, str]]:
        """[(clave para avisar una sola vez, motivo legible)] de lo que hoy impide abrir posiciones."""
        lo = limits["loss"]
        out = []
        dd = (self.peak - equity) / self.peak * 100 if self.peak > 0 else 0.0
        if not self.halted and dd >= lo["max_drawdown_pct"]:
            self.halted = f"drawdown {dd:.1f}% ≥ {lo['max_drawdown_pct']}% del máximo"
        if self.halted:
            out.append(("halt", f"HALT: {self.halted}. Vuelve solo con rearme del dueño"))
        day_loss = (self.day_start - equity) / self.day_start * 100 if self.day_start > 0 else 0.0
        if day_loss >= lo["daily_pct"]:
            out.append((f"dia:{self.day}", f"pérdida del día {day_loss:.1f}% ≥ {lo['daily_pct']}%: sin entradas hasta "
                                           "las 00:00 UTC"))
        week_loss = (self.week_start - equity) / self.week_start * 100 if self.week_start > 0 else 0.0
        if week_loss >= lo["weekly_pct"]:
            out.append((f"semana:{self.week}", f"pérdida de la semana {week_loss:.1f}% ≥ {lo['weekly_pct']}%: sin "
                                               "entradas hasta el lunes 00:00 UTC"))
        if now < self.cooldown_until:
            out.append((f"enfriamiento:{round(self.cooldown_until)}",
                        f"{lo['max_consecutive_losses']} pérdidas seguidas: enfriamiento hasta "
                        f"{time.strftime('%H:%M UTC', time.gmtime(self.cooldown_until))}"))
        return out

    def new_alerts(self, blocks: list[tuple[str, str]]) -> list[str]:
        """Motivos que todavía no se avisaron (y los marca como avisados)."""
        fresh = [why for key, why in blocks if key not in self.alerted]
        self.alerted = (self.alerted + [key for key, _ in blocks if key not in self.alerted])[-50:]
        return fresh

    def rearm(self) -> None:
        self.halted, self.peak, self.losses_in_row, self.cooldown_until = "", 0.0, 0, 0.0
        self.alerted = [a for a in self.alerted if a != "halt"]
