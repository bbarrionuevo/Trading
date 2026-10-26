"""Límites duros anti-bloqueo (N26 Italia / CVU Argentina). Estado persistido en JSON."""
import json
import time
from decimal import Decimal as D
from pathlib import Path


class ComplianceError(Exception):
    pass


class ComplianceGuard:
    def __init__(self, limits: dict, state_path="state/ledger.json", now=time.time):
        m = D(str(limits["safety_margin"]))
        self.eu_cap = D(str(limits["eu"]["cumulative_eur_cap"])) * m
        self.max_day = int(limits["eu"]["max_tx_per_day"])
        self.max_hour = int(limits["eu"]["max_tx_per_hour"])
        self.ar_month = D(str(limits["ar"]["monthly_ars_cap"])) * m
        self.ar_tx = D(str(limits["ar"]["per_tx_ars_cap"])) * m
        self.path = Path(state_path)
        self.now = now
        self.ledger = json.loads(self.path.read_text()) if self.path.exists() else []

    def _sum(self, ccy, since):
        return sum((D(e["amount"]) for e in self.ledger if e["ccy"] == ccy and e["ts"] >= since), D(0))

    def check(self, ccy: str, amount) -> None:
        """Lanza ComplianceError si la operación viola algún límite. No registra nada."""
        a, t = D(str(amount)), self.now()
        if ccy == "EUR":
            # Acumulado conservador: ventana móvil de 365 días
            if self._sum("EUR", t - 365 * 86400) + a > self.eu_cap:
                raise ComplianceError("EUR: acumulado superaría el tope anti-bloqueo (N26)")
            ev = [e for e in self.ledger if e["ccy"] == "EUR"]
            if sum(e["ts"] >= t - 86400 for e in ev) >= self.max_day:
                raise ComplianceError("EUR: demasiadas transacciones en 24h")
            if sum(e["ts"] >= t - 3600 for e in ev) >= self.max_hour:
                raise ComplianceError("EUR: demasiadas transacciones en 1h (alta frecuencia)")
        elif ccy == "ARS":
            if a > self.ar_tx:
                raise ComplianceError("ARS: monto por transacción supera el límite")
            if self._sum("ARS", t - 30 * 86400) + a > self.ar_month:
                raise ComplianceError("ARS: acumulado mensual superaría el límite UIF/ARCA")
        else:
            raise ComplianceError(f"Moneda no soportada: {ccy}")

    def record(self, ccy: str, amount) -> None:
        self.check(ccy, amount)
        self.ledger.append({"ts": self.now(), "ccy": ccy, "amount": str(amount)})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.ledger))
