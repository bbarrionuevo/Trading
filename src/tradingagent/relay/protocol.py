"""Relé de Tokio (Tier-0): datagramas UDP FIRMADOS entre el servidor de Tokio y la PC.

Un "BUY TICKER" crudo por UDP lo puede falsificar cualquiera que conozca la IP y el puerto (UDP no tiene conexión):
le haría abrir operaciones a la PC o, con un latido falso, desviaría las señales a otro lado. Por eso cada datagrama
lleva HMAC-SHA256 con una clave que solo tienen la PC y el servidor (`relay.key`, 32 bytes al azar), la hora (no más
de 30 s de diferencia) y un número único que no se acepta dos veces (repetición).

Formato: JSON compacto con "h" (el HMAC en hex) sobre el mismo JSON sin "h". Tipos ("t"):
- "hb": latido de la PC (cada 10 s): abre y mantiene el túnel del NAT; el servidor guarda de dónde vino.
- "ack": respuesta del servidor al latido (la PC mide la ida y vuelta).
- "sig": un aviso oficial nuevo que vio el servidor (fuente, título, hora de publicación...): la PC decide.
"""
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import OrderedDict
from pathlib import Path

MAX_SKEW_S = 30.0               # diferencia máxima de hora entre la PC y el servidor (los dos con NTP)
MAX_BYTES = 1400                # un datagrama sin fragmentar


class NonceCache:
    """Los números únicos vistos en los últimos `ttl_s` segundos: un datagrama repetido se descarta."""

    def __init__(self, ttl_s: float = 2 * MAX_SKEW_S, max_items: int = 10_000):
        self.ttl_s, self.max_items = ttl_s, max_items
        self._seen: OrderedDict[str, float] = OrderedDict()

    def add(self, nonce: str, now: float) -> bool:
        """True si es nuevo (y lo guarda); False si ya se vio."""
        while self._seen and (now - next(iter(self._seen.values())) > self.ttl_s or len(self._seen) > self.max_items):
            self._seen.popitem(last=False)
        if nonce in self._seen:
            return False
        self._seen[nonce] = now
        return True


def load_key(path: Path) -> bytes:
    key = bytes.fromhex(Path(path).read_text(encoding="utf-8").strip())
    if len(key) < 32:
        raise ValueError("la clave del relé tiene que tener 32 bytes")
    return key


def new_key(path: Path) -> None:
    """Crea la clave del relé (32 bytes al azar, en hex) si no existe. Nunca la imprime."""
    path = Path(path)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(secrets.token_hex(32))


def _mac(body: bytes, key: bytes) -> str:
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def encode(payload: dict, key: bytes, now: float | None = None) -> bytes:
    """El datagrama firmado: agrega la hora y un número único."""
    msg = {**payload, "ts": round(time.time() if now is None else now, 3), "n": secrets.token_hex(8)}
    body = json.dumps(msg, separators=(",", ":"), ensure_ascii=False, sort_keys=True).encode("utf-8")
    data = json.dumps({**msg, "h": _mac(body, key)}, separators=(",", ":"), ensure_ascii=False,
                      sort_keys=True).encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ValueError(f"datagrama de {len(data)} bytes (máximo {MAX_BYTES})")
    return data


def decode(data: bytes, key: bytes, seen: NonceCache, now: float | None = None) -> dict | None:
    """El mensaje si la firma, la hora y el número único están bien; si no, None (y no se dice por qué afuera)."""
    now = time.time() if now is None else now
    if len(data) > MAX_BYTES:
        return None
    try:
        msg = json.loads(data.decode("utf-8"))
        mac = msg.pop("h")
        body = json.dumps(msg, separators=(",", ":"), ensure_ascii=False, sort_keys=True).encode("utf-8")
        if not hmac.compare_digest(mac, _mac(body, key)):
            return None
        if abs(now - float(msg["ts"])) > MAX_SKEW_S or not seen.add(str(msg["n"]), now):
            return None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return msg
