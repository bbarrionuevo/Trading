"""Prueba de punta a punta de la regla "listados de Upbit al instante" (y del reparto del relé, la deduplicación y el
simulacro), SIN tocar nada de verdad:
- un agente aparte (otra carpeta TRADING_HOME): simulacro de 100 USDT de mentira, sin bot de Telegram, sin fuentes y
  sin aprendizaje;
- un relé de prueba en otro puerto, sin Telegram (la sesión de Upbit sigue siendo solo del relé de verdad).
Le manda dos avisos de listado armados a mano (SOL y, 15 s después, ETH: la misma plantilla de Upbit), como si
Telegram los trajera 1 s después del sitio, y un deslistado de Binance (DOGE, por la lista de anuncios: entra corto).
Mide cuánto tarda en entrar (orden simulada contra el libro) e imprime la línea de tiempo y la segunda lectura de la IA
(unas pocas consultas: centavos de dólar). Al final borra todo. Nunca imprime claves.

Dos formas:
- **En la PC, sin cargar Tokio** (pedido del dueño, 6-nov): `python deploy/tokio/prueba_listado.py --pc`, desde el repo.
  El agente corre en la PC contra un Binance falso local (`tests/fake_binance.py`) que contesta en ~3 ms, como Binance
  desde Tokio (2-5 ms; desde la PC son ~320 ms). Mide el camino del agente con los tiempos de Tokio; no mide la red ni
  el libro real de Tokio. Solo usa la clave de la IA del `.env` de la PC (sin Binance ni Telegram). Es la de siempre
  después de cada cambio.
- **En el servidor de Tokio** (cuando cambia el servidor, Python o las dependencias): `~/trading/.venv/bin/python
  ~/trading/deploy/tokio/prueba_listado.py`. Usa el Binance real (lectura) y suma ~150 MB mientras corre. Con el código
  de una rama, solo para el agente de prueba: `SRC_PRUEBA=/tmp/src-prueba` (una copia de src/ de esa rama).
Medido el 6-nov en Tokio: cada listado entra a los 22-59 ms de recibido (~1,07 s desde la hora del mensaje) y el
deslistado a los 18 ms.
"""
import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

from tradingagent.core.events import NewsItem
from tradingagent.ingestion.catalog import DEFAULT_SOURCES
from tradingagent.relay import server

PC = "--pc" in sys.argv[1:]
REPO = Path(__file__).resolve().parents[2]
HOME = Path(tempfile.mkdtemp(prefix="prueba-tokio-")) if PC else Path("/tmp/prueba-tokio")
PORT = 50016
AGENT_ENV = (Path(os.environ.get("LOCALAPPDATA", "")) / "TRADING" / ".env") if PC else Path("/home/ubuntu/.trading/.env")
NOTICES = (("SOL", "솔라나"), ("ETH", "이더리움"))
DELIST = "Binance Will Delist DOGE on 2026-12-20"                # la otra regla al instante: corto en el perpetuo
BASE = HOME / "simulacro" / "binance-futuros"
TOKIO_MS = 3.0                                                  # Binance desde Tokio: 2-5 ms por pedido
# El Binance falso de los tests, en otro proceso (no comparte el bucle del agente), con la demora de Tokio por pedido.
FAKE = """
import asyncio, random, sys
sys.path.insert(0, sys.argv[1])
from aiohttp import web
import fake_binance as fb
fb.SPECS["DOGEUSDT"] = ("0.00001", "1", "1", "5", "PERPETUAL", "COIN", "DOGE")
fb.PRICES["DOGEUSDT"] = 0.2
ms = float(sys.argv[3])

@web.middleware
async def tokio(request, handler):
    await asyncio.sleep(ms / 1000 * random.uniform(0.7, 1.6))
    return await handler(request)

app = fb.FakeBinance().app()
app.middlewares.append(tokio)
web.run_app(app, host="127.0.0.1", port=int(sys.argv[2]), print=None)
"""


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
    """La carpeta de prueba: clave del relé de prueba, configuración sin fuentes ni Telegram, solo la IA (y en Tokio la
    clave de solo lectura), copiadas sin mostrarlas, y el simulacro armado con 100 USDT de mentira."""
    shutil.rmtree(HOME, ignore_errors=True)
    HOME.mkdir(parents=True, mode=0o700)
    key = secrets.token_bytes(32)
    (HOME / "relay.key").write_text(key.hex(), encoding="utf-8")
    (HOME / "relay.json").write_text(json.dumps({"host": "127.0.0.1", "port": PORT, "telemetria": False}))
    off = {s.id: {"enabled": False} for s in DEFAULT_SOURCES} | {"binance.ws": {"enabled": False}}
    (HOME / "config.yaml").write_text(yaml.safe_dump({"alerts": {"telegram": False}, "ingestion": {"sources": off},
                                                      "trader": {"aprender": False}}), encoding="utf-8")
    want = ("ANTHROPIC_API_KEY",) if PC else ("ANTHROPIC_API_KEY", "BINANCE_LECTURA_KEY", "BINANCE_LECTURA_SECRET")
    lines = ([x for x in AGENT_ENV.read_text(encoding="utf-8").splitlines() if x.split("=", 1)[0].strip() in want]
             if AGENT_ENV.exists() else [])                        # nada de Telegram ni de trading
    fd = os.open(HOME / ".env", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("TELEGRAM_", "BINANCE_", "MEXC_", "GITHUB_"))} | {"TRADING_HOME": str(HOME)}
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
    entered = {t.get("symbol") for t in entries}
    for d in rows(BASE / "decisiones"):                         # por qué un aviso de la prueba no terminó en entrada
        if d.get("symbol") in ("SOLUSDT", "ETHUSDT", "DOGEUSDT") and d.get("symbol") not in entered:
            print(f"no entró {d.get('symbol')} ({(d.get('signal') or {}).get('category')}): {d.get('decision')} · "
                  f"{', '.join(d.get('motivos') or [])[:200]}")
    for ab in rows(BASE / "variante-ia.jsonl"):
        print(f"segunda lectura de la IA ({ab.get('symbol')}): {ab.get('ia_s')} s · confirma {ab.get('confirma')} · "
              f"al instante {ab.get('entrada_al_instante')} · esperando a la IA {ab.get('precio_con_la_ia')}")
    errors = [json.loads(x) for x in (HOME / "logs" / "agent.log").read_text(encoding="utf-8").splitlines()
              if '"ERROR"' in x]
    print("errores del agente de prueba:", [e.get("msg") for e in errors][-3:] or "ninguno")
    return 0 if len(entries) >= len(NOTICES) + 1 else 1


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def start_agent(env: dict):
    """(cómo pararlo, cómo esperarlo). En Tokio, un proceso aparte con el Binance real. En la PC, en este mismo proceso
    contra el Binance falso local (otro proceso), porque el Binance de la prueba solo se elige por código."""
    if not PC:
        agent = await asyncio.create_subprocess_exec(sys.executable, "-m", "tradingagent", "run", "--seconds", "75",
                                                     env=env, stdout=asyncio.subprocess.DEVNULL,
                                                     stderr=asyncio.subprocess.DEVNULL)
        return agent, None
    port = free_port()
    fake = await asyncio.create_subprocess_exec(sys.executable, "-c", FAKE, str(REPO / "tests"), str(port),
                                                str(TOKIO_MS), stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.DEVNULL)
    for _ in range(100):                                     # que el Binance falso esté escuchando
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            await asyncio.sleep(0.1)
    os.environ.clear()
    os.environ.update(env)
    from tradingagent import config, paths
    from tradingagent.app import Agent
    from tradingagent.telemetry import logs
    paths.load_env()                                         # la IA del .env de prueba
    logs.setup(HOME / "logs", "INFO")
    agent = Agent(config.load(HOME / "config.yaml"), os.environ, printer=lambda *_: None,
                  binance_base=f"http://127.0.0.1:{port}")
    task = asyncio.create_task(agent.run())
    asyncio.get_running_loop().call_later(75, agent.stop)
    return task, (agent, fake)


async def stop_agent(handle, extra) -> None:
    if extra is None:
        if handle.returncode is None:
            handle.terminate()
            try:
                await asyncio.wait_for(handle.wait(), timeout=30)
            except TimeoutError:
                handle.kill()
        return
    agent, fake = extra
    agent.stop()
    try:
        await asyncio.wait_for(handle, timeout=30)
    finally:
        fake.terminate()
        await asyncio.wait_for(fake.wait(), timeout=10)


async def main() -> int:
    key, env = prepare()
    loop = asyncio.get_running_loop()
    transport, relay = await loop.create_datagram_endpoint(lambda: server.RelayServer(key),
                                                           local_addr=("127.0.0.1", PORT))
    handle, extra = await start_agent(env)
    first_sent = None
    try:
        for _ in range(900):                                  # el agente de prueba, con el simulacro andando
            if ready(relay):
                break
            await asyncio.sleep(0.1)
        else:
            print("el agente de prueba no arrancó a tiempo")
            return 1
        print("en la PC, contra un Binance falso con los tiempos de Tokio (~3 ms)" if PC else "en Tokio, Binance real")
        await asyncio.sleep(2)
        for coin, name in NOTICES:
            now = time.time()
            first_sent = first_sent or now
            assert relay.forward(notice(coin, name, now)), "el relé de prueba no tenía a quién mandar"
            print(f"aviso de {coin} mandado a los {(now - first_sent) * 1000:.0f} ms")
            await asyncio.sleep(15)
        now = time.time()
        assert relay.forward(NewsItem(id="prueba-" + secrets.token_hex(4), source="binance.listados", tier=1,
                                      kind="api", title=DELIST, url="https://www.binance.com/en/support/announcement/prueba",
                                      published_at=now - 1.5, received_at=now))
        print(f"deslistado de DOGE mandado a los {(now - first_sent) * 1000:.0f} ms")
        await asyncio.sleep(8)
        return report(first_sent)
    finally:
        await stop_agent(handle, extra)
        transport.close()
        if PC:
            import logging
            logging.shutdown()                                 # Windows: el registro abierto no deja borrar la carpeta
        shutil.rmtree(HOME, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
