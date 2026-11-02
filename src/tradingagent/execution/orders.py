"""Tipos de la capa de ejecución. La inteligencia y el riesgo solo producen `OrderIntent`; quién la
ejecuta (paper hoy, un exchange mañana) es intercambiable detrás de `ExecutionVenue`."""
from dataclasses import dataclass, field
from decimal import Decimal as D
from enum import StrEnum
from typing import Protocol


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(StrEnum):
    MARKET = "market"            # siempre con tope de precio (max_slippage_bps): REGLAS D-2
    LIMIT = "limit"
    STOP_MARKET = "stop_market"  # stop-loss: se dispara contra el mejor precio del lado que lo ejecuta


class TimeInForce(StrEnum):
    IOC = "ioc"                  # lo que no se llena al llegar se cancela
    GTC = "gtc"                  # queda en el libro hasta llenarse, cancelarse o vencer


class OrderStatus(StrEnum):
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"

    @property
    def is_final(self) -> bool:
        return self in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED)


@dataclass(frozen=True)
class OrderIntent:
    client_id: str                         # idempotencia: el mismo id nunca genera dos órdenes (REGLAS D-1)
    symbol: str
    side: Side
    type: OrderType = OrderType.MARKET
    qty: D | None = None                   # en moneda base; o bien notional (en cotización)
    notional: D | None = None
    limit_price: D | None = None
    stop_price: D | None = None
    tif: TimeInForce = TimeInForce.IOC
    max_slippage_bps: D | None = None      # tope para MARKET: se convierte en límite agresivo
    reduce_only: bool = False
    ref_price: D | None = None             # precio que vio quien decidió: mide el deslizamiento real
    oco_group: str | None = None           # al llenarse una del grupo se cancelan las demás (stop + TP)
    expire_at: float | None = None
    tags: dict = field(default_factory=dict)   # decision_id, role (entry|stop_loss|take_profit|exit), motivo


@dataclass(frozen=True)
class Fill:
    order_id: str
    client_id: str
    symbol: str
    side: Side
    qty: D
    price: D
    fee: D
    liquidity: str                         # taker | maker
    ts: float
    realized_pnl: D = D(0)
    slippage_bps: D | None = None          # contra ref_price (+ = peor que lo esperado)


@dataclass
class Order:
    order_id: str
    intent: OrderIntent
    status: OrderStatus
    created_at: float
    qty: D                                 # cantidad pedida (ya redondeada al lote)
    filled_qty: D = D(0)
    avg_price: D | None = None
    fees: D = D(0)
    reason: str = ""                       # motivo de rechazo / cancelación
    updated_at: float = 0.0
    triggered: bool = False                # para STOP_MARKET
    limit_price: D | None = None           # efectivo, ya redondeado al tick (la intención no se modifica)
    ref_price: D | None = None             # precio que vio quien decidió (mide el deslizamiento)
    fills: list = field(default_factory=list)

    @property
    def remaining(self) -> D:
        return self.qty - self.filled_qty

    def snapshot(self) -> dict:
        i = self.intent
        return {"order_id": self.order_id, "client_id": i.client_id, "symbol": i.symbol, "side": str(i.side),
                "type": str(i.type), "status": str(self.status), "qty": str(self.qty),
                "filled_qty": str(self.filled_qty), "avg_price": str(self.avg_price) if self.avg_price else None,
                "fees": str(self.fees), "reason": self.reason, "role": i.tags.get("role", ""),
                "decision_id": i.tags.get("decision_id", "")}


@dataclass(frozen=True)
class OrderEvent:
    kind: str                              # accepted | fill | filled | canceled | rejected | expired | triggered
    order: dict                            # snapshot del estado de la orden
    fill: Fill | None = None
    ts: float = 0.0


class ExecutionVenue(Protocol):
    async def submit(self, intent: OrderIntent) -> Order: ...
    async def cancel(self, client_id: str, reason: str = "") -> Order | None: ...
    def open_orders(self) -> list[Order]: ...
