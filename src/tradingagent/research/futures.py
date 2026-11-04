"""Futuros perpetuos de MEXC en paper: el venue que el dueño puede usar desde Argentina.

- Contratos USDT-M, largos y cortos, con apalancamiento ≤ `per_trade.max_leverage` del sobre (1x hoy).
- Comisiones: la mayor entre la configurada (0,02 % maker y 0,05 % taker, la referencia de Binance
  Futures), la pública del contrato y la de la cuenta si hay claves de solo lectura (REGLAS D-5).
- Funding: cada `collectCycle` horas (8 h en general). Con tasa positiva paga el largo y cobra el corto.
  Se cobra vela por vela entre corridas, solo mientras la posición estuvo abierta.
- Liquidación con margen aislado: largo a P·(1 − 1/apal + mmr), corto a P·(1 + 1/apal − mmr). A 1x
  queda lejísimos del stop; si un salto la pasara, la salida se registra como liquidación a ese precio.
Sin claves ni órdenes reales: los datos son públicos y la cuenta solo se lee (marketdata/mexc.py).
"""
import logging
import time
from decimal import Decimal as D

from ..execution.instruments import Instrument, InstrumentRegistry
from ..marketdata import binance_futures as bfd
from ..marketdata import mexc
from ..marketdata import universe as universe_mod
from ..marketdata.providers import MarketDataError
from . import labeler
from .labeler import MIN, CandleStore

log = logging.getLogger("tradingagent.futures")
INFO_TTL_S = 86_400
FUNDING_TTL_S = 600                 # la tasa vigente se vuelve a leer cada 10 min y siempre después de cada cobro


def liquidation_price(entry: float, side: int, leverage: float, mmr: float) -> float:
    return entry * (1 - side * (1 / max(leverage, 1e-9) - mmr))


class MexcFutures:
    name, kind, allows_short, label = "mexc-futuros", "perp", True, "PAPER futuros MEXC"
    venue_es, universe_file = "MEXC", "universo-mexc.json"
    maker_bps, taker_bps = D("2"), D("5")

    def __init__(self, base: str = mexc.BASE, readonly: "mexc.ReadOnlyClient | None" = None, max_leverage: int = 1):
        self.base, self.readonly, self.max_leverage = base, readonly, max_leverage
        self.sizes: dict[str, D] = {}
        self.mmr: dict[str, float] = {}
        self._funding: dict[str, dict] = {}
        self.universe = universe_mod.Universe()

    def symbol(self, asset: str) -> str:
        """El contrato de un activo: del universo de MEXC (TSLA → TESLA_USDT, XAU → XAU_USDT) o el de siempre."""
        return self.universe.symbol(asset) or mexc.contract(asset)

    async def load_universe(self, session, cache, now: float | None = None) -> int:
        """Todo lo operable en MEXC futuros (criptos, acciones, índices, metales, petróleo) para que el agente
        opere lo que mueva la noticia. Se renueva solo cada 6 h (caché en disco)."""
        from ..research import assets as asset_mod
        uni = await universe_mod.load(session, cache, base=self.base, now=now)
        if len(uni):
            self.universe = uni
            asset_mod.register(uni.by_key.values())
        return len(self.universe)

    async def instruments(self, session, symbols, cache: dict, now: float) -> InstrumentRegistry:
        for sym in symbols:
            if sym in cache and now - cache[sym].get("ts", 0) < INFO_TTL_S:
                continue
            try:
                info = {**await mexc.detail(session, sym, self.base), "ts": round(now)}
            except MarketDataError as e:
                log.warning("sin especificación de %s: %s", sym, e)
                continue
            if self.readonly is not None:                         # D-5: también lo que cobra la cuenta
                try:
                    acc = await self.readonly.fee_rates(sym)
                    info["maker_cuenta"], info["taker_cuenta"] = str(acc["maker"]), str(acc["taker"])
                except (MarketDataError, KeyError, TypeError, ValueError) as e:
                    log.warning("no se pudo leer la comisión de la cuenta para %s: %s", sym, e)
            cache[sym] = info
        reg = InstrumentRegistry(())
        for sym, f in cache.items():
            if not f.get("trading", True):
                continue
            maker = max(self.maker_bps, D(f["maker"]) * 10_000, D(f.get("maker_cuenta", "0")) * 10_000)
            taker = max(self.taker_bps, D(f["taker"]) * 10_000, D(f.get("taker_cuenta", "0")) * 10_000)
            self.sizes[sym], self.mmr[sym] = D(f["contract_size"]), float(f["mmr"])
            reg.add(Instrument(sym, "mexc-futuros", f["base"], f["quote"], kind="perp", tick_size=D(f["tick"]),
                               step_size=D(f["step"]), min_qty=D(f["min_qty"]), min_notional=D("1"),
                               maker_fee_bps=maker, taker_fee_bps=taker,
                               max_leverage=D(min(self.max_leverage, int(f["max_leverage"])))))
        return reg

    def candle_store(self, session) -> CandleStore:
        return CandleStore(session, providers=(("mexc", mexc.klines, f"{self.base}/api/v1/contract/kline/{{symbol}}"),))

    def books(self, session, clock):
        return mexc.ContractBooks(session, clock, self.sizes, base=self.base)

    async def ticker(self, session, sym: str) -> dict:
        return await mexc.ticker(session, sym, self.base)

    def mmr_for(self, sym: str) -> float:
        return self.mmr.get(sym, 0.005)

    async def _rate(self, session, sym: str, now_ms: int) -> dict:
        """Tasa vigente y próximo cobro. Antes quedaban en memoria para siempre: el agente de la PC corre días, y
        una entrada de hoy recibía el "próximo cobro" de ayer (y se le cobraba funding de horas en que no estaba)."""
        f = self._funding.get(sym)
        if f is None or now_ms >= f["next_ms"] or now_ms - f["_leido"] > FUNDING_TTL_S * 1000:
            f = self._funding[sym] = {**await self._fetch_funding(session, sym), "_leido": now_ms}
        return f

    async def _fetch_funding(self, session, sym: str) -> dict:
        """{"rate", "next_ms", "cycle_h"} del exchange."""
        return await mexc.funding(session, sym, self.base)

    def funding_cost(self, sym: str, rate: float | None, side: int, now: float, horizon_s: float) -> float:
        """Funding EN CONTRA (fracción del nominal) si la posición llega a su expiración después de uno o más
        cobros: un costo más de la operación. Lo que se cobraría a favor no se cuenta (conservador)."""
        if not rate or not side:
            return 0.0
        now_ms = int(now * 1000)
        f = self._funding.get(sym)
        if f is not None and f["next_ms"] > now_ms:
            nxt, cycle = f["next_ms"], int(f["cycle_h"]) * 3600_000
        else:                                                      # sin dato fresco: la grilla estándar de 8 h
            cycle = 8 * 3600_000
            nxt = (now_ms // cycle + 1) * cycle
        end = now_ms + int(horizon_s * 1000)
        times = 0 if end < nxt else 1 + (end - nxt) // cycle
        return max(0.0, float(rate) * side) * times

    async def on_entry(self, run, sym: str, meta: dict) -> None:
        """Al abrir: precio de liquidación y próximo cobro de funding con su tasa vigente."""
        side = 1 if meta["side"] == "largo" else -1
        lev = float(run.reg.get(sym).max_leverage)
        meta["liq"] = str(round(liquidation_price(float(meta["avg_price"]), side, lev, self.mmr_for(sym)), 8))
        now_ms = int(run.now * 1000)
        try:
            f = await self._rate(run.session, sym, now_ms)
            nxt, cycle = f["next_ms"], int(f["cycle_h"]) * 3600_000
            while nxt <= now_ms:                                       # nunca un cobro anterior a la entrada
                nxt += cycle
            meta.update(funding_next=nxt, funding_rate=f["rate"], funding_cycle_h=f["cycle_h"])
        except MarketDataError as e:                               # sin dato: se asume el peor caso razonable
            log.warning("sin funding de %s: %s", sym, e)
            nxt = (int(run.now * 1000) // (8 * 3600_000) + 1) * 8 * 3600_000
            meta.update(funding_next=nxt, funding_rate=0.0001 * side, funding_cycle_h=8)

    async def settle(self, run, sym: str, meta: dict, until_ms: int) -> None:
        """Cobros de funding vencidos hasta `until_ms` (la salida o ahora), al precio de esa vela."""
        side = 1 if meta["side"] == "largo" else -1
        while meta.get("funding_next") and meta["funding_next"] <= until_ms:
            m = meta["funding_next"] // MIN * MIN
            c = (run.store.candles.get(sym) or {}).get(m)
            price = D(str(c[0])) if c else run.mark(sym)              # en vivo: el precio del libro
            qty = abs(run.pf.qty(sym))
            pay = round(qty * price * D(str(meta["funding_rate"])) * side, 8)    # + = la posición paga
            run.pf.cash -= pay
            meta["funding_paid"] = str(D(meta.get("funding_paid", "0")) + pay)
            run.trades.append({"tipo": "funding", "symbol": sym, "lado": meta["side"], "tasa": meta["funding_rate"],
                               "precio": str(price), "pago": str(pay), "ts": m / 1000, "mercado": self.name,
                               "modo": "paper"})
            run.counts["funding"] += 1
            meta["funding_next"] += int(meta.get("funding_cycle_h", 8)) * 3600_000
            try:                                                   # la tasa del próximo cobro: la vigente
                meta["funding_rate"] = (await self._rate(run.session, sym, int(run.now * 1000)))["rate"]
            except MarketDataError:
                pass


class BinanceFutures(MexcFutures):
    """Futuros perpetuos de Binance USDⓈ-M en paper: el lugar de ejecución desde el 4-nov-2026 (MEXC restringió la
    cuenta). Criptos y TradFi (acciones, ETF, oro, petróleo...), las 24 h. Largos y cortos a 1x, con el mismo
    funding y la misma liquidación que MEXC; los datos son los públicos de Binance (marketdata/binance_futures.py).

    - Comisión: 0,02 % maker y 0,05 % taker (la estándar de USDⓈ-M, VIP 0), salvo que el contrato pida más.
    - El catálogo entero viene en un pedido: se guarda en memoria 6 h y se vuelve a pedir antes si una noticia
      nombra un contrato que no está (un listado nuevo), como mucho una vez por minuto.
    """
    name, label = "binance-futuros", "PAPER futuros Binance"
    venue_es, universe_file = "Binance", "universo-binance.json"
    CATALOG_TTL_S, CATALOG_RETRY_S, HOURS_TTL_S = 6 * 3600, 60, 6 * 3600

    def __init__(self, base: str = bfd.BASE, max_leverage: int = 1):
        super().__init__(base=base, readonly=None, max_leverage=max_leverage)
        self._catalog: dict[str, dict] = {}
        self._catalog_at = float("-inf")
        self._hours: dict[str, int] = {}
        self._hours_at = float("-inf")

    def symbol(self, asset: str) -> str:
        """El contrato de un activo: del universo de Binance (PEPE → 1000PEPEUSDT, SP500 → SPYUSDT) o ACTIVOUSDT."""
        return self.universe.symbol(asset) or f"{str(asset).upper()}USDT"

    async def load_universe(self, session, cache, now: float | None = None) -> int:
        from ..research import assets as asset_mod
        uni = await universe_mod.load_binance(session, cache, base=self.base, now=now)
        if len(uni):
            self.universe = uni
            asset_mod.register(uni.by_key.values())
        return len(self.universe)

    async def _contracts(self, session, now: float, want: list[str]) -> dict[str, dict]:
        age = now - self._catalog_at
        missing = [s for s in want if s not in self._catalog]
        if age > self.CATALOG_TTL_S or (missing and age > self.CATALOG_RETRY_S):
            try:
                self._catalog, self._catalog_at = await bfd.catalog(session, self.base), now
            except MarketDataError as e:
                log.warning("sin catálogo de Binance: %s", e)
        return self._catalog

    async def instruments(self, session, symbols, cache: dict, now: float) -> InstrumentRegistry:
        stale = [s for s in symbols if not (s in cache and now - cache[s].get("ts", 0) < INFO_TTL_S)]
        if stale:
            contracts = await self._contracts(session, now, stale)
            for sym in stale:
                if sym in contracts and contracts[sym].get("subyacente") not in bfd.SKIP_UNDERLYING:
                    cache[sym] = {**contracts[sym], "ts": round(now)}
                elif sym not in cache:
                    log.warning("sin especificación de %s en Binance", sym)
        reg = InstrumentRegistry(())
        for sym, f in cache.items():
            if not f.get("trading", True) or "step" not in f:
                continue
            self.mmr[sym] = float(f.get("mmr", 0.005))
            reg.add(Instrument(sym, "binance-futuros", f["base"], f["quote"], kind="perp", tick_size=D(f["tick"]),
                               step_size=D(f["step"]), min_qty=D(f["min_qty"]),
                               min_notional=D(str(f.get("min_notional", "5"))), maker_fee_bps=self.maker_bps,
                               taker_fee_bps=self.taker_bps,
                               max_leverage=D(min(self.max_leverage, int(f.get("max_leverage", 1))))))
        return reg

    def candle_store(self, session) -> CandleStore:
        return CandleStore(session, providers=(("binance-futuros", labeler.binance, f"{self.base}/fapi/v1/klines"),))

    def books(self, session, clock):
        return bfd.books(session, clock, self.base)

    async def ticker(self, session, sym: str) -> dict:
        return await bfd.ticker(session, sym, self.base)

    async def _fetch_funding(self, session, sym: str) -> dict:
        now = time.time()
        if now - self._hours_at > self.HOURS_TTL_S:
            try:
                self._hours, self._hours_at = await bfd.funding_hours(session, self.base), now
            except MarketDataError as e:                           # sin el dato, el intervalo estándar
                log.warning("sin intervalos de funding de Binance: %s", e)
        p = await bfd.premium(session, sym, self.base)
        hours = int(self._hours.get(sym, 8))
        nxt = p["next_ms"] or (int(now * 1000) // (hours * 3600_000) + 1) * hours * 3600_000
        return {"rate": p["rate"], "next_ms": nxt, "cycle_h": hours}
