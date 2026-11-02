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


def redact(text, secrets=()) -> str:
    s = str(text)
    for secret in (*secrets, *env_secrets()):
        if secret and len(secret) >= 6:
            s = s.replace(secret, MASK)
    for p in _PATTERNS:
        s = p.sub(MASK, s)
    return s
