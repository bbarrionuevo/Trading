"""Redacción de secretos en cualquier texto que salga del proceso (logs, alertas, diario, errores).

Un error de aiohttp puede incluir la URL completa, y la URL de la API de Telegram lleva el token del
bot (/bot<token>/sendMessage). Todo texto de error pasa por `redact` antes de registrarse.
"""
import os
import re

# Nombres de variables de entorno que contienen secretos: su valor se oculta donde aparezca.
SECRET_ENV_HINTS = ("TOKEN", "SECRET", "KEY", "PASSWORD", "PASS")
_PATTERNS = (
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),            # token de bot de Telegram
    re.compile(r"(?i)(bot)(\d{6,12}:[A-Za-z0-9_-]{30,})"),
    re.compile(r"(?i)\b(sk|pk|api|key|secret)[-_][A-Za-z0-9]{16,}\b"),
)
MASK = "«oculto»"


def env_secrets() -> list[str]:
    return [v for k, v in os.environ.items() if v and len(v) >= 8 and any(h in k.upper() for h in SECRET_ENV_HINTS)]


def _clean(s: str, secrets) -> str:
    for secret in secrets:
        if secret and len(secret) >= 6:
            s = s.replace(secret, MASK)
    for p in _PATTERNS:
        s = p.sub(MASK, s)
    return s


def redact(text, secrets=()) -> str:
    return _clean(str(text), (*secrets, *env_secrets()))


def redactor(secrets=()):
    """`redact` para muchos textos seguidos: los secretos del entorno se leen una vez y no en cada texto. El tablero
    exportado de Tokio redacta cientos de líneas del registro cada 2 min y releer el entorno en cada una era el 85 % de
    su costo (6-nov): ese rato, en un hilo, demoraba el bucle del agente hasta ~95 ms. Mismo resultado que `redact`."""
    found = (*secrets, *env_secrets())
    return lambda text: _clean(str(text), found)
