"""Arma ~/.trading/.env (600) del agente de Tokio con SOLO lo que necesita. Nunca imprime valores, solo nombres.

- De ~/.env del servidor (lo maneja el dueño): API_IA -> ANTHROPIC_API_KEY. La clave de TRADING de Binance
  (API_Key_Binance / SECRET_Key_Binance) NO se copia: el simulacro no la usa (fase 3, con REGLAS aprobadas).
- Por la entrada estándar (desde la PC, por SSH): BINANCE_LECTURA_KEY / BINANCE_LECTURA_SECRET / SEC_USER_AGENT.
"""
import os
import sys
from pathlib import Path

HOME = Path.home()
DEST = HOME / ".trading" / ".env"
MAP = {"API_IA": "ANTHROPIC_API_KEY"}
FROM_PC = ("BINANCE_LECTURA_KEY", "BINANCE_LECTURA_SECRET", "SEC_USER_AGENT")


def parse(text):
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


src = parse((HOME / ".env").read_text(encoding="utf-8"))
pc = parse(sys.stdin.read())
vals = {new: src[old] for old, new in MAP.items() if src.get(old)}
vals.update({k: v for k, v in pc.items() if k in FROM_PC and v})
DEST.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
cur = parse(DEST.read_text(encoding="utf-8")) if DEST.exists() else {}
cur.update(vals)
tmp = DEST.with_suffix(".tmp")
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write("# Agente de Tokio: solo lo que necesita (simulacro). Sin la clave de trading de Binance.\n")
    for k in sorted(cur):
        f.write(f"{k}={cur[k]}\n")
os.replace(tmp, DEST)
os.chmod(DEST, 0o600)
print("~/.trading/.env (600):", ", ".join(sorted(cur)))
