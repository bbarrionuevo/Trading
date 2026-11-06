"""Prueba de punta a punta, en el servidor de Tokio, de la regla "listados de Upbit al instante" (y del reparto del relé,
la deduplicación y el simulacro), SIN tocar nada de verdad:
- un agente aparte (TRADING_HOME=/tmp/prueba-tokio): simulacro de 100 USDT de mentira, sin bot de Telegram, sin
  fuentes y sin aprendizaje;
- un relé de prueba en otro puerto, sin Telegram (la sesión de Upbit sigue siendo solo del relé de verdad).
Le manda dos avisos de listado armados a mano (SOL y, 15 s después, ETH: la misma plantilla de Upbit), como si
Telegram los trajera 1 s después del sitio. Mide cuánto tarda Tokio en entrar (orden simulada contra el libro real de
Binance) e imprime la línea de tiempo y la segunda lectura de la IA (unas pocas consultas: centavos de dólar).
Al final borra todo. Nunca imprime claves.

Uso, en el servidor: `~/trading/.venv/bin/python ~/trading/deploy/tokio/prueba_listado.py`. Con el código de una rama,
solo para el agente de prueba: `SRC_PRUEBA=/tmp/src-prueba` (una copia de src/ de esa rama).
Medido el 6-nov: cada aviso entra a los ~60-70 ms de recibido (~1,07 s desde la hora del mensaje).
"""
import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

from tradingagent.core.events import NewsItem
from tradingagent.ingestion.catalog import DEFAULT_SOURCES
from tradingagent.relay import server

HOME = Path("/tmp/prueba-tokio")
PORT = 50016
AGENT_ENV = Path("/home/ubuntu/.trading/.env")
NOTICES = (("SOL", "솔라나"), ("ETH", "이더리움"))
BASE = HOME / "simulacro" / "binance-futuros"


def rows(path: Path) -> list[dict]:
    out = []
    for f in sorted(path.glob("*.jsonl")) if path.is_dir() else ([path] if path.exists() else []):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def prepare() -> tuple[bytes, dict]:
    """La carpeta de prueba: clave del relé de prueba, configuración sin fuentes ni Telegram, solo la IA y la clave de
    solo lectura (copiadas sin mostrarlas) y el simulacro armado con 100 USDT de mentira."""
    shutil.rmtree(HOME, ignore_errors=True)
    HOME.mkdir(parents=True, mode=0o700)
    key = secrets.token_bytes(32)
    (HOME / "relay.key").write_text(key.hex(), encoding="utf-8")
    (HOME / "relay.json").write_text(json.dumps({"host": "127.0.0.1", "port": PORT, "telemetria": False}))
    off = {s.id: {"enabled": False} for s in DEFAULT_SOURCES} | {"binance.ws": {"enabled": False}}
    (HOME / "config.yaml").write_text(yaml.safe_dump({"alerts": {"telegram": False}, "ingestion": {"sources": off},
                                                      "trader": {"aprender": False}}), encoding="utf-8")
    want = ("ANTHROPIC_API_KEY", "BINANCE_LECTURA_KEY", "BINANCE_LECTURA_SECRET")      # nada de Telegram ni de trading
    lines = [x for x in AGENT_ENV.read_text(encoding="utf-8").splitlines() if x.split("=", 1)[0].strip() in want]
    fd = os.open(HOME / ".env", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("TELEGRAM_")} | {"TRADING_HOME": str(HOME)}
    if os.environ.get("SRC_PRUEBA"):
        env["PYTHONPATH"] = os.environ["SRC_PRUEBA"]
    subprocess.run([sys.executable, "-m", "tradingagent", "armar-real", "--simulacro", "--capital", "100"], env=env,
                   check=True, capture_output=True)
    return key, env


def ready(relay) -> bool:
    hb_file = HOME / "state" / "heartbeat.json"
    hb = json.loads(hb_file.read_text(encoding="utf-8")) if hb_file.exists() else {}
    return bool(relay.live()) and bool((hb.get("real") or {}).get("simulacro"))


def notice(coin: str, name: str, now: float) -> NewsItem:
    return NewsItem(id="prueba-" + secrets.token_hex(4), source="upbit.avisos", tier=1, kind="telegram",
                    title=f"{name}({coin}) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)",
                    url=f"https://upbit.com/service_center/notice?id=prueba-{coin}", published_at=now - 1.0,
                    received_at=now)


def report(first_sent: float) -> int:
    """La línea de tiempo (ms desde el primer aviso), las entradas, la segunda lectura de la IA y los errores."""
    for line in (HOME / "logs" / "agent.log").read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if float(r.get("ts", 0)) >= first_sent - 0.5 and "HTTP Request" not in str(r.get("msg")):
            print(f"  {(float(r['ts']) - first_sent) * 1000:7.0f} ms  {r.get('level', '')[:4]:4} "
                  f"{str(r.get('msg'))[:150]}")
    entries = [t for t in rows(BASE / "operaciones") if t.get("tipo") == "entrada"]
    for t in entries:
        print(f"entrada simulada: {t.get('symbol')} {t.get('lado')} a {t.get('price')} · nocional {t.get('notional')} "
              f"USDT de mentira")
    for ab in rows(BASE / "variante-ia.jsonl"):
        print(f"segunda lectura de la IA ({ab.get('symbol')}): {ab.get('ia_s')} s · confirma {ab.get('confirma')} · "
              f"al instante {ab.get('entrada_al_instante')} · esperando a la IA {ab.get('precio_con_la_ia')}")
    errors = [json.loads(x) for x in (HOME / "logs" / "agent.log").read_text(encoding="utf-8").splitlines()
              if '"ERROR"' in x]
    print("errores del agente de prueba:", [e.get("msg") for e in errors][-3:] or "ninguno")
    return 0 if len(entries) >= len(NOTICES) else 1


async def main() -> int:
    key, env = prepare()
    loop = asyncio.get_running_loop()
    transport, relay = await loop.create_datagram_endpoint(lambda: server.RelayServer(key),
                                                           local_addr=("127.0.0.1", PORT))
    agent = await asyncio.create_subprocess_exec(sys.executable, "-m", "tradingagent", "run", "--seconds", "75",
                                                 env=env, stdout=asyncio.subprocess.DEVNULL,
                                                 stderr=asyncio.subprocess.DEVNULL)
    first_sent = None
    try:
        for _ in range(600):                                  # el agente de prueba, con el simulacro andando
            if ready(relay):
                break
            await asyncio.sleep(0.1)
        else:
            print("el agente de prueba no arrancó a tiempo")
            return 1
        await asyncio.sleep(2)
        for i, (coin, name) in enumerate(NOTICES):
            now = time.time()
            first_sent = first_sent or now
            assert relay.forward(notice(coin, name, now)), "el relé de prueba no tenía a quién mandar"
            print(f"aviso de {coin} mandado a los {(now - first_sent) * 1000:.0f} ms")
            await asyncio.sleep(15 if i < len(NOTICES) - 1 else 8)
        return report(first_sent)
    finally:
        if agent.returncode is None:
            agent.terminate()
            try:
                await asyncio.wait_for(agent.wait(), timeout=30)
            except TimeoutError:
                agent.kill()
        transport.close()
        shutil.rmtree(HOME, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
