"""Gestor de riesgo independiente (reglas R-03, R-04, R-08, R-09, R-14, R-16).

- Tiene veto: las estrategias piden permiso (allow_entry) y nunca tocan los límites.
- Su estado se persiste de forma atómica: un reinicio NO borra la pérdida del día ni los stops.
- Las salidas (reducir riesgo) siempre se permiten; lo que se bloquea son las entradas nuevas.
- Halts "pegajosos" (drawdown total, estrategia perdedora 7 días) solo los levanta una persona:
  python -m nexus.agent.risk --rearmar [--real]   (pide confirmación escrita; categoría C)
"""
import sys
import time

from .. import paths


class RiskHalt(Exception):
    pass


REQUIRED = ("max_position_eur", "max_total_exposure_eur", "daily_loss_limit_eur", "weekly_loss_limit_eur",
            "max_drawdown_eur", "max_consecutive_stops", "stops_cooldown_min", "max_orders_per_minute",
            "losing_days_window")


def _day(t):
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def _week(t):
    return time.strftime("%G-W%V", time.gmtime(t))


class RiskManager:
    def __init__(self, cfg, state_path=None, kill_file=None, now=time.time):
        missing = [k for k in REQUIRED if k not in cfg]
        if missing:
            raise ValueError(f"config de riesgo incompleta: {missing}")
        self.cfg, self.now = cfg, now
        self.path = state_path or paths.state("risk.json")
        self.kill = kill_file or paths.kill_file()
        t = now()
        self.s = paths.read_json(self.path, None) or {
            "day": _day(t), "pnl_day": 0.0, "week": _week(t), "pnl_week": 0.0, "pnl_total": 0.0,
            "stops_row": 0, "last_stop_ts": 0.0, "daily": {}, "sticky_halt": None, "orders": []}
        self.last_reason = None

    # -- persistencia -------------------------------------------------------------------------
    def _save(self):
        paths.write_json_atomic(self.path, self.s)

    def _roll(self):
        t = self.now()
        if self.s["day"] != _day(t):
            self.s["daily"][self.s["day"]] = round(self.s["pnl_day"], 6)
            self.s["daily"] = dict(sorted(self.s["daily"].items())[-30:])
            self.s["day"], self.s["pnl_day"] = _day(t), 0.0
        if self.s["week"] != _week(t):
            self.s["week"], self.s["pnl_week"] = _week(t), 0.0

    # -- veto ----------------------------------------------------------------------------------
    def reasons_to_block(self, notional, exposure_now):
        """Lista de motivos por los que NO se permite una entrada nueva (vacía = permitido)."""
        c, s, t = self.cfg, self.s, self.now()
        self._roll()
        r = []
        if self.kill.exists():
            r.append(f"interruptor de emergencia activo ({self.kill})")
        if s["sticky_halt"]:
            r.append(f"halt permanente: {s['sticky_halt']} (requiere rearme humano)")
        if notional > c["max_position_eur"]:
            r.append(f"posición {notional} > máximo {c['max_position_eur']}")
        if exposure_now + notional > c["max_total_exposure_eur"]:
            r.append(f"exposición {exposure_now + notional:.2f} > máximo {c['max_total_exposure_eur']}")
        if s["pnl_day"] <= -c["daily_loss_limit_eur"]:
            r.append(f"pérdida del día {s['pnl_day']:.2f} alcanzó el límite")
        if s["pnl_week"] <= -c["weekly_loss_limit_eur"]:
            r.append(f"pérdida de la semana {s['pnl_week']:.2f} alcanzó el límite")
        if s["stops_row"] >= c["max_consecutive_stops"]:
            if t - s["last_stop_ts"] >= c["stops_cooldown_min"] * 60:
                s["stops_row"] = 0
                self._save()
            else:
                r.append(f"{s['stops_row']} stops seguidos: enfriamiento de {c['stops_cooldown_min']} min")
        s["orders"] = [x for x in s["orders"] if t - x < 60]
        if len(s["orders"]) >= c["max_orders_per_minute"]:
            r.append("límite de órdenes por minuto")
        return r

    def allow_entry(self, notional, exposure_now=0.0):
        r = self.reasons_to_block(notional, exposure_now)
        self.last_reason = "; ".join(r) or None
        return not r

    def record_order(self):
        """Cuenta una orden colocada (entrada, salida o re-cotización). Guarda solo el último minuto."""
        t = self.now()
        self.s["orders"] = [x for x in self.s["orders"] if t - x < 60] + [t]
        self._save()

    def on_close(self, pnl, was_stop):
        self._roll()
        s, c = self.s, self.cfg
        s["pnl_day"] += pnl
        s["pnl_week"] += pnl
        s["pnl_total"] += pnl
        if was_stop:
            s["stops_row"] += 1
            s["last_stop_ts"] = self.now()
        else:
            s["stops_row"] = 0
        if s["pnl_total"] <= -c["max_drawdown_eur"] and not s["sticky_halt"]:
            s["sticky_halt"] = f"drawdown total {s['pnl_total']:.2f} EUR alcanzó el máximo {c['max_drawdown_eur']}"
        w = c["losing_days_window"]
        days = list(s["daily"].values())[-w:]
        if len(days) >= w and sum(days) < 0 and not s["sticky_halt"]:
            s["sticky_halt"] = f"PnL neto negativo en los últimos {w} días: la estrategia deja de operar"
        self._save()

    def snapshot(self):
        t = self.now()
        return {k: v for k, v in self.s.items() if k != "orders"} | {
            "orders_last_min": sum(1 for x in self.s["orders"] if t - x < 60)}


def rearm(path=None):
    """Levanta un halt permanente. Solo para uso humano: exige escribir una frase exacta."""
    path = path or paths.state("risk.json")
    s = paths.read_json(path, None)
    if not s or not s.get("sticky_halt"):
        print("No hay halt permanente activo.")
        return
    print(f"Halt activo: {s['sticky_halt']}")
    phrase = "ACEPTO EL RIESGO Y REARMO NEXUS"
    if input(f'Escribí exactamente "{phrase}" para rearmar: ').strip() != phrase:
        print("Frase incorrecta. No se cambió nada.")
        return
    s["sticky_halt"], s["pnl_total"], s["daily"] = None, 0.0, {}
    paths.write_json_atomic(path, s)
    print("Rearmado. El drawdown total se cuenta desde cero.")


if __name__ == "__main__":
    # --real: el riesgo del dinero real (Fase 6) tiene su propio estado, separado del paper.
    target = paths.state("risk_real.json" if "--real" in sys.argv else "risk.json")
    if "--rearmar" in sys.argv:
        rearm(target)
    else:
        print(paths.read_json(target, {}))
