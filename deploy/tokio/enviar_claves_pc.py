"""Desde la PC: manda al servidor de Tokio, por SSH (cifrado), SOLO lo que el agente de allá necesita del .env de la PC
(la clave de SOLO LECTURA de Binance y el SEC_USER_AGENT), y arma ~/.trading/.env allá con
deploy/tokio/preparar_env.py (que suma la clave de la IA del .env del servidor). Nunca imprime valores.

    python deploy/tokio/enviar_claves_pc.py [--host 176.32.87.26] [--llave C:/Users/<yo>/.ssh/lightsail-tokio.pem]

La clave de TRADING de Binance no se manda ni se copia: el simulacro no la usa (fase 3, con REGLAS aprobadas).
"""
import argparse
import subprocess
from pathlib import Path

from tradingagent import paths

WANT = ("BINANCE_LECTURA_KEY", "BINANCE_LECTURA_SECRET", "SEC_USER_AGENT")


def read_env(path: Path) -> dict[str, str]:
    vals = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            if k.strip() in WANT:
                vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def main() -> int:
    conf = paths.read_json(paths.home() / "relay.json", None) or {}
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default=conf.get("host"))
    ap.add_argument("--usuario", default="ubuntu")
    ap.add_argument("--llave", default=str(Path.home() / ".ssh" / "lightsail-tokio.pem"))
    args = ap.parse_args()
    if not args.host:
        raise SystemExit("falta --host (o TRADING_HOME/relay.json)")
    vals = read_env(paths.home() / ".env")
    missing = [k for k in WANT if not vals.get(k)]
    if missing:
        raise SystemExit(f"faltan en el .env de la PC: {', '.join(missing)}")
    data = "".join(f"{k}={vals[k]}\n" for k in WANT).encode()
    cmd = ["ssh", "-i", args.llave, "-o", "BatchMode=yes", f"{args.usuario}@{args.host}",
           "/home/ubuntu/trading/.venv/bin/python /home/ubuntu/trading/deploy/tokio/preparar_env.py"]
    r = subprocess.run(cmd, input=data, capture_output=True, timeout=120)
    print(r.stdout.decode(errors="replace").strip() or r.stderr.decode(errors="replace").strip()[:300])
    print("mandadas desde la PC:", ", ".join(WANT))
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
