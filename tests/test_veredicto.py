"""Cada estrategia se juzga con sus propias operaciones (F-3): con 30 o más cerradas y DSR < 0,80, deja de abrir
operaciones y avisa una vez. Lo mismo vale para las que no pasan por el camino general de noticias (resultados,
perpetuos nuevos, deslistados)."""
import asyncio
import json

import aiohttp

from conftest import serve
from fake_binance import FakeBinance
from test_launches import T0, decisions, market, new_contract, trader  # noqa: F401 (new_contract es un fixture)
from tradingagent.core.clock import ManualClock


def losses(base, category, n=30):
    f = base / "operaciones" / "2026-11.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"tipo": "salida", "notional_entrada": "10", "pnl_neto": str(-0.5 if i % 2 else -0.2),
             "category": category, "strategy_tag": "x"} for i in range(n)]
    f.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


async def test_una_estrategia_que_viene_perdiendo_deja_de_abrir(tmp_path, new_contract):  # noqa: F811
    clock = ManualClock(T0 + 905)
    fake = FakeBinance(price=market(0.95), clock=clock)                  # el perpetuo nuevo ya bajó: entraría
    fake.onboard["NEWUSDT"] = T0 * 1000
    home = tmp_path / "home"
    async with serve(fake.app()) as url, aiohttp.ClientSession() as s:
        t = await trader(s, url, clock, home)
        losses(t.base, "lanzamiento_corto")
        await t.refresh_verdicts()
        assert t.discarded == {"lanzamiento_corto"}
        await t.check_launches()
        await asyncio.gather(*t.pending)
        assert t.st["positions"] == {}
        await t.refresh_verdicts()                                       # el aviso sale una sola vez
    why = [r["motivos"] for r in decisions(home) if r["signal"].get("category") == "lanzamiento_corto"]
    assert why and "categoria_descartada_por_resultados:lanzamiento_corto" in why[0]
    alerts = [a for a in t.alerts.items if "lanzamiento_corto" in a.text]
    assert len(alerts) == 1
