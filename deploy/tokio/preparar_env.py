"""Arma ~/.trading/.env (600) del agente de Tokio con SOLO lo que necesita. Nunca imprime valores, solo nombres.

- De ~/.env del servidor (lo maneja el dueño): API_IA -> ANTHROPIC_API_KEY.
- Por la entrada estándar (desde la PC, por SSH: enviar_claves_pc.py): BINANCE_LECTURA_KEY / BINANCE_LECTURA_SECRET /
  SEC_USER_AGENT y, con `--telegram` allá, TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (el bot pasa a Tokio).
- La clave de TRADING de Binance (API_Key_Binance / SECRET_Key_Binance -> BINANCE_API_KEY / BINANCE_API_SECRET) pasa
  SOLO con `--trading`, cuando el dueño lo pide para operar dinero real desde Tokio (REGLAS A-3 y G-9). Sin `--trading`
  se saca del agente si estaba: el simulacro no la usa.

    python deploy/tokio/preparar_env.py [--trading] < claves_de_la_pc
"""
import argparse
import os
import sys
from pathlib import Path

HOME = Path.home()
DEST = HOME / ".trading" / ".env"
MAP = {"API_IA": "ANTHROPIC_API_KEY"}
TRADING = {"API_Key_Binance": "BINANCE_API_KEY", "SECRET_Key_Binance": "BINANCE_API_SECRET"}
FROM_PC = ("BINANCE_LECTURA_KEY", "BINANCE_LECTURA_SECRET", "SEC_USER_AGENT", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


def parse(text):
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="El .env del agente de Tokio (solo lo que necesita)")
    ap.add_argument("--trading", action="store_true", help="pasar también la clave de trading (dinero real, fase 3)")
    args = ap.parse_args()
    src = parse((HOME / ".env").read_text(encoding="utf-8"))
    pc = {} if sys.stdin.isatty() else parse(sys.stdin.read())
    mapping = {**MAP, **(TRADING if args.trading else {})}
    vals = {new: src[old] for old, new in mapping.items() if src.get(old)}
    vals.update({k: v for k, v in pc.items() if k in FROM_PC and v})
    DEST.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    cur = parse(DEST.read_text(encoding="utf-8")) if DEST.exists() else {}
    if not args.trading:
        for k in TRADING.values():
            cur.pop(k, None)
    elif not all(vals.get(k) for k in TRADING.values()):
        print("falta API_Key_Binance o SECRET_Key_Binance en ~/.env: no cambié nada")
        return 1
    cur.update(vals)
    tmp = DEST.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# Agente de Tokio (deploy/tokio/preparar_env.py): solo lo que necesita"
                + (". CON la clave de trading (dinero real).\n" if args.trading else ". Sin la clave de trading.\n"))
        for k in sorted(cur):
            f.write(f"{k}={cur[k]}\n")
    os.replace(tmp, DEST)
    os.chmod(DEST, 0o600)
    print("~/.trading/.env (600):", ", ".join(sorted(cur)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
