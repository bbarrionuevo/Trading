"""Registro fiscal (ARCA): resultado y volumen operado por mes y por año de una cuenta, a partir de sus operaciones
(`operaciones/AAAA-MM.jsonl`). Lo pidió el primer informe de investigación del 5-nov (docs/PLAN.md, punto 4): las
ganancias en un exchange del exterior igual se declaran, y conviene tener el detalle listo para el contador.

Los meses son los de Argentina (UTC−3). Volumen = nominal de entrada + nominal de salida. El resultado es el neto que
registró el sistema (con comisiones y funding). Queda solo en la PC: nunca se publica (puede ser de la cuenta real).
"""
import csv
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

AR = timezone(timedelta(hours=-3))


def _rows(base: Path):
    folder = Path(base) / "operaciones"
    for f in sorted(folder.glob("*.jsonl")) if folder.exists() else []:
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def ledger(base: Path, year: int | None = None) -> dict[str, dict]:
    """{"AAAA-MM": {operaciones, ganadas, volumen, comisiones, resultado}} en USDT (texto, sin redondeos)."""
    out: dict[str, dict] = defaultdict(lambda: {"operaciones": 0, "ganadas": 0, "volumen": D(0), "comisiones": D(0),
                                                "resultado": D(0)})
    for r in _rows(base):
        when = datetime.fromtimestamp(float(r.get("ts") or 0), UTC).astimezone(AR)
        if year is not None and when.year != year:
            continue
        m = out[when.strftime("%Y-%m")]
        m["comisiones"] += D(str(r.get("fee") or 0))
        if r.get("tipo") == "entrada":
            m["volumen"] += D(str(r.get("notional") or 0))
        elif r.get("tipo") == "salida":
            m["operaciones"] += 1
            m["volumen"] += D(str(r.get("qty") or 0)) * D(str(r.get("price") or 0))
            pnl = D(str(r.get("pnl_neto") or 0))
            m["resultado"] += pnl
            m["ganadas"] += pnl > 0
    return {k: {**v, **{x: str(v[x]) for x in ("volumen", "comisiones", "resultado")}} for k, v in sorted(out.items())}


def render(led: dict[str, dict], cuenta: str) -> str:
    lines = [f"Registro fiscal de la cuenta {cuenta} (USDT; meses de Argentina)",
             "  Mes      Operaciones  Ganadas   Volumen operado   Comisiones   Resultado neto"]
    tot = {"operaciones": 0, "ganadas": 0, "volumen": D(0), "comisiones": D(0), "resultado": D(0)}
    for month, m in led.items():
        lines.append(f"  {month}  {m['operaciones']:11d}  {m['ganadas']:7d}  {D(m['volumen']):16.2f}  "
                     f"{D(m['comisiones']):11.4f}  {D(m['resultado']):+15.4f}")
        for k in tot:
            tot[k] += D(str(m[k])) if k in ("volumen", "comisiones", "resultado") else m[k]
    lines.append(f"  Total    {tot['operaciones']:11d}  {tot['ganadas']:7d}  {tot['volumen']:16.2f}  "
                 f"{tot['comisiones']:11.4f}  {tot['resultado']:+15.4f}")
    return "\n".join(lines)


def to_csv(led: dict[str, dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["mes", "operaciones", "ganadas", "volumen_usdt", "comisiones_usdt", "resultado_neto_usdt"])
        for month, m in led.items():
            w.writerow([month, m["operaciones"], m["ganadas"], m["volumen"], m["comisiones"], m["resultado"]])
