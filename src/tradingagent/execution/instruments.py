"""Instrumentos y sus reglas de exchange (tick, lote, mínimo nominal) y comisiones.

Los valores por defecto son de referencia y se pisan desde la configuración; en modo real se leerán
de exchangeInfo y de la API de comisiones antes de operar (REGLAS D-5: se usa la comisión mayor entre
la configurada y la informada, nunca una menor).
"""
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR
from decimal import Decimal as D

from ..marketdata.book import dec

BPS = D(10000)


@dataclass(frozen=True)
class Instrument:
    symbol: str
    venue: str
    base: str
    quote: str
    kind: str = "spot"            # spot (solo largo) | perp (lineal, permite corto)
    tick_size: D = D("0.01")
    step_size: D = D("0.00001")
    min_qty: D = D("0.00001")
    min_notional: D = D("5")
    maker_fee_bps: D = D("10")
    taker_fee_bps: D = D("10")
    max_leverage: D = D("1")

    def floor_qty(self, qty: D) -> D:
        return (dec(qty) / self.step_size).to_integral_value(ROUND_FLOOR) * self.step_size

    def round_price(self, price: D, up: bool) -> D:
        """Al tick: hacia arriba o hacia abajo (para que un tope nunca quede más laxo que lo pedido)."""
        mode = ROUND_CEILING if up else ROUND_FLOOR
        return (dec(price) / self.tick_size).to_integral_value(mode) * self.tick_size

    def fee(self, notional: D, liquidity: str) -> D:
        bps = self.taker_fee_bps if liquidity == "taker" else self.maker_fee_bps
        return dec(notional) * bps / BPS


# Referencia: Binance spot 0,10% maker/taker; MEXC spot 0% maker y 0,05% taker (verificado por API en
# el proyecto anterior); Binance USDⓈ-M 0,02%/0,05%. Confirmar antes de operar en real.
DEFAULT_INSTRUMENTS = (
    Instrument("BTCUSDT", "binance", "BTC", "USDT", tick_size=D("0.01"), step_size=D("0.00001")),
    Instrument("ETHUSDT", "binance", "ETH", "USDT", tick_size=D("0.01"), step_size=D("0.0001")),
    Instrument("SOLUSDT", "binance", "SOL", "USDT", tick_size=D("0.01"), step_size=D("0.001")),
    Instrument("BTCUSDT-PERP", "binance-usdm", "BTC", "USDT", kind="perp", tick_size=D("0.1"),
               step_size=D("0.001"), min_qty=D("0.001"), min_notional=D("100"), maker_fee_bps=D("2"),
               taker_fee_bps=D("5"), max_leverage=D("1")),
    Instrument("ETHUSDT-PERP", "binance-usdm", "ETH", "USDT", kind="perp", tick_size=D("0.01"),
               step_size=D("0.001"), min_qty=D("0.001"), min_notional=D("20"), maker_fee_bps=D("2"),
               taker_fee_bps=D("5"), max_leverage=D("1")),
)


class InstrumentRegistry:
    def __init__(self, instruments=DEFAULT_INSTRUMENTS):
        self._by_symbol = {i.symbol: i for i in instruments}

    def get(self, symbol: str) -> Instrument | None:
        return self._by_symbol.get(symbol)

    def add(self, inst: Instrument) -> None:
        self._by_symbol[inst.symbol] = inst

    def symbols(self) -> list[str]:
        return sorted(self._by_symbol)

    def by_asset(self, asset: str) -> list[Instrument]:
        """Instrumentos cuyo activo base es `asset` (para mapear entidades del NLP a algo operable)."""
        return [i for i in self._by_symbol.values() if i.base == asset.upper()]
