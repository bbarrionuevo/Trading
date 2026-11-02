"""Núcleo de seguridad (ruta protegida, REGLAS G-2): los frenos que ninguna estrategia ni modelo puede
saltear, en un solo lugar chico y auditable. La ejecución consulta acá antes de mandar cada orden.

- KILL (REGLAS E-1): con el archivo presente solo pasan órdenes reduce_only (salir siempre se puede).
- Tope de órdenes por minuto (D-6) y tope de precio de toda orden (D-2).
- Edad máxima de una noticia para operarla (B-2): ninguna fuente puede pasar el tope del sobre.
"""
from decimal import Decimal


def intent_block_reasons(intent, *, killed: bool, orders_last_min: int, limits: dict) -> list[str]:
    """Motivos por los que la intención NO puede salir (vacío = puede)."""
    r = []
    if killed and not intent.reduce_only:
        r.append("interruptor_de_emergencia")
    if orders_last_min >= limits["execution"]["max_orders_per_min"]:
        r.append("tope_de_ordenes_por_minuto")
    cap = Decimal(str(limits["per_trade"]["max_slippage_bps"]))
    if intent.max_slippage_bps is not None and intent.max_slippage_bps > cap:
        r.append(f"tope_de_precio_mayor_al_sobre ({intent.max_slippage_bps} > {cap} bps)")
    return r


def slippage_cap(requested_bps, limits: dict) -> Decimal:
    """Tope efectivo: el pedido, recortado por el sobre (y el del sobre si no se pidió ninguno)."""
    cap = Decimal(str(limits["per_trade"]["max_slippage_bps"]))
    return cap if requested_bps is None else min(Decimal(str(requested_bps)), cap)


def news_max_age(source_max_age_s: float, limits: dict) -> float:
    return min(float(source_max_age_s), float(limits["news"]["max_age_s"]))
