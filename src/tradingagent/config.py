"""Configuración: valores por defecto + TRADING_HOME/config.yaml (lo que se escriba ahí pisa lo de acá).

Los secretos NO van en este archivo: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SEC_USER_AGENT y
WEBHOOK_SECRET se leen del entorno o de TRADING_HOME/.env (REGLAS G-1). `validate` rechaza valores
fuera de rango antes de arrancar (un error de tipeo no puede convertirse en una orden rara).
"""
import copy
from pathlib import Path

import yaml

# Mercados de la cuenta de práctica: futuros de Binance (el lugar de ejecución desde el 4-nov-2026), MEXC (la cuenta
# real quedó restringida) y Binance spot.
MARKETS = ("binance-futuros", "mexc-futuros", "binance-spot")

DEFAULTS = {
    "mode": "paper",                      # esta versión no tiene modo real (REGLAS A-2)
    "heartbeat_s": 15,
    "log_level": "INFO",
    "ingestion": {
        "default_max_age_s": 300,         # noticia más vieja que esto al llegar: no se opera
        "max_future_skew_s": 120,
        "dedup": {"ttl_hours": 6, "threshold": 0.6, "max_items": 20000},
        "noise": {"threshold": 1.0},
        "sources": {},                    # por id: {enabled, interval_s, max_age_s}
        "extra_sources": [],              # [{id, url, tier, interval_s, max_age_s, category, assets}]
        "webhook": {"enabled": False, "host": "127.0.0.1", "port": 8787, "senders": {}},
    },
    "paper": {
        "cash_usdt": 1000,
        "venue": "binance",               # de dónde salen los libros públicos para simular
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "latency_ms": 80,
        "participation": 0.8,
        "max_book_age_s": 2.0,
        "default_max_slippage_bps": 50,
        "stop_slippage_bps": 50,
        "seed": 0,
    },
    "execution": {"max_orders_per_min": 30, "max_queue": 100},
    # operador paper en vivo (live.py): reacciona a cada noticia y vigila las barreras cada segundo
    # llm_usd_dia: techo de seguridad del gasto en Claude (no es un límite de uso: la IA se consulta SIEMPRE
    # que su respuesta pueda cambiar una decisión y NUNCA si no; el techo solo frena un error que se dispare).
    # Por defecto, el del sobre (ai.max_llm_usd_per_day).
    # practica: la cuenta de práctica (paper). En Tokio va apagada desde el 6-nov (el dueño: el simulacro mide mejor):
    # los demás módulos corren en el simulacro junto al dinero real (SIMULACRO.json).
    "trader": {"enabled": True, "mercado": "binance-futuros", "capital_usdt": 120, "llm": True, "aprender": True,
               "llm_usd_dia": None, "practica": True},
    "alerts": {"telegram": True, "rate_per_s": 1.0, "burst": 3, "max_queue": 500},
}

RANGES = {
    ("heartbeat_s",): (2, 3600),
    ("ingestion", "default_max_age_s"): (10, 3600),
    ("ingestion", "max_future_skew_s"): (0, 3600),
    ("ingestion", "dedup", "ttl_hours"): (0.1, 72),
    ("ingestion", "dedup", "threshold"): (0.3, 0.95),
    ("ingestion", "noise", "threshold"): (0.3, 10),
    ("paper", "cash_usdt"): (10, 10_000_000),
    ("paper", "latency_ms"): (0, 5000),
    ("paper", "participation"): (0.05, 1.0),
    ("paper", "max_book_age_s"): (0.1, 60),
    ("paper", "default_max_slippage_bps"): (1, 500),
    ("paper", "stop_slippage_bps"): (1, 500),
    ("execution", "max_orders_per_min"): (1, 120),
    ("alerts", "rate_per_s"): (0.05, 30),
    ("trader", "capital_usdt"): (10, 10_000_000),
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _get(cfg, path):
    for k in path:
        cfg = cfg[k]
    return cfg


def validate(cfg: dict) -> list[str]:
    errs = []
    if cfg.get("mode") != "paper":
        errs.append("mode: esta versión solo opera en paper (el modo real no existe todavía)")
    for path, (lo, hi) in RANGES.items():
        try:
            v = _get(cfg, path)
        except (KeyError, TypeError):
            errs.append(f"{'.'.join(path)}: falta")
            continue
        if not isinstance(v, int | float) or isinstance(v, bool) or not lo <= v <= hi:
            errs.append(f"{'.'.join(path)}={v!r} fuera de rango [{lo}, {hi}]")
    for i, s in enumerate(cfg["ingestion"].get("extra_sources") or []):
        if not isinstance(s, dict) or not s.get("id") or not str(s.get("url", "")).startswith(("http://", "https://")):
            errs.append(f"ingestion.extra_sources[{i}]: necesita id y url http(s)")
    for sid, o in (cfg["ingestion"].get("sources") or {}).items():
        if isinstance(o, dict) and o.get("url") is not None and not str(o["url"]).startswith(("http://", "https://")):
            errs.append(f"ingestion.sources.{sid}.url: tiene que ser http(s)")
    if not cfg["paper"].get("symbols"):
        errs.append("paper.symbols: vacío")
    cap = cfg["trader"].get("llm_usd_dia")
    if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int | float) or not 0 <= cap <= 1000):
        errs.append(f"trader.llm_usd_dia={cap!r} fuera de rango [0, 1000] (o null = el del sobre)")
    if cfg["trader"].get("mercado") not in MARKETS:
        errs.append("trader.mercado: tiene que ser " + ", ".join(MARKETS))
    return errs


def load(path: Path | None = None) -> dict:
    over = {}
    if path is not None and Path(path).exists():
        over = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(over, dict):
            raise ValueError(f"{path}: se esperaba un mapa YAML")
    cfg = _merge(DEFAULTS, over)
    errs = validate(cfg)
    if errs:
        raise ValueError("configuración inválida:\n  - " + "\n  - ".join(errs))
    return cfg
