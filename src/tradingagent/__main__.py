"""Línea de comandos del agente.

  python -m tradingagent run                 agente completo en paper (Ctrl+C para cerrar)
  python -m tradingagent telegram-test       verifica TELEGRAM_BOT_TOKEN/CHAT_ID y manda un mensaje de prueba
  python -m tradingagent telegram-chats      lista los chats que le escribieron al bot (para el CHAT_ID)
  python -m tradingagent sources [--check]   fuentes y su estado; --check lee cada una una vez
  python -m tradingagent journal-verify      verifica la cadena de hashes del diario
  python -m tradingagent paper-sim ESCENARIO reproduce libros + órdenes de un escenario JSONL

Todo lee TRADING_HOME/.env y TRADING_HOME/config.yaml (TRADING_HOME por defecto %LOCALAPPDATA%\\trading).
"""
import argparse
import asyncio
import json
import os
import sys
from decimal import Decimal as D

from . import __version__, config, paths


def _setup(level="INFO"):
    from .telemetry import logs
    loaded = paths.load_env()
    logs.setup(paths.home() / "logs", level)
    return loaded


def cmd_run(args) -> int:
    _setup()
    cfg = config.load(args.config or paths.home() / "config.yaml")
    from .app import Agent, install_signal_handlers
    agent = Agent(cfg, os.environ)

    async def main():
        install_signal_handlers(agent)
        if args.seconds:
            asyncio.get_running_loop().call_later(args.seconds, agent.stop)
        await agent.run()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    return 0


def cmd_telegram_test(args) -> int:
    _setup()
    from .alerts.model import system_alert
    from .alerts.telegram import TelegramTransport, api_base
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Faltan TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID en", paths.home() / ".env")
        return 2

    async def main():
        t = TelegramTransport(token, chat, base_url=api_base(os.environ))
        try:
            me = await t.get_me()
            if not me.ok:
                print(f"getMe FALLÓ: {me.error}")
                return 1
            print(f"Bot {me.error} OK ({me.latency_ms:.0f} ms)")
            r = await t.send(system_alert(f"🔔 Prueba de tradingagent v{__version__}: si ves esto, las alertas "
                                          "llegan.", silent=False))
            print("Mensaje de prueba enviado." if r.ok else f"sendMessage FALLÓ: {r.error}")
            return 0 if r.ok else 1
        finally:
            await t.close()

    return asyncio.run(main())


def cmd_telegram_chats(args) -> int:
    _setup()
    from .alerts.telegram import TelegramTransport, api_base
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Falta TELEGRAM_BOT_TOKEN en", paths.home() / ".env")
        return 2

    async def main():
        t = TelegramTransport(token, "0", base_url=api_base(os.environ))
        try:
            chats = await t.recent_chats()
        finally:
            await t.close()
        if not chats:
            print("No hay mensajes recientes: escribile algo al bot (por ejemplo /start) y repetí el comando.")
        for c in chats:
            print(f"TELEGRAM_CHAT_ID={c['id']}   ({c['type']}: {c['name']})")
        return 0

    return asyncio.run(main())


def cmd_sources(args) -> int:
    _setup("WARNING")
    cfg = config.load(args.config or paths.home() / "config.yaml")
    from .app import build_sources
    enabled, off = build_sources(cfg, os.environ)
    for s in enabled:
        print(f"ON   {s.id:16} nivel {s.tier} cada {s.interval_s:>4.0f}s máx {s.max_age_s:>4.0f}s  {s.url}")
    for sid, why in off.items():
        print(f"OFF  {sid:16} {why}")
    if not args.check:
        return 0

    async def check():
        import aiohttp

        from .core.clock import SystemClock
        from .ingestion.sources import PollingSource

        async def nothing(*_a):
            return None

        bad = 0
        async with aiohttp.ClientSession() as session:
            for s in enabled:
                p = PollingSource(s, session, SystemClock(), nothing)
                try:
                    items, _ = await p.poll_once()
                    newest = max((i.published_at for i in items if i.published_at), default=None)
                    age = f"{(SystemClock().time() - newest) / 60:.0f} min" if newest else "sin hora"
                    print(f"OK   {s.id:16} {len(items):3d} ítems · el más nuevo hace {age} · "
                          f"{p.health.last_latency_ms} ms")
                except Exception as e:                       # noqa: BLE001 (diagnóstico)
                    bad += 1
                    print(f"MAL  {s.id:16} {e}")
        return 1 if bad else 0

    return asyncio.run(check())


def cmd_journal_verify(args) -> int:
    from .telemetry.journal import verify
    ok, n, problem = verify(paths.home() / "journal")
    print(f"Diario {'ÍNTEGRO' if ok else 'ALTERADO'}: {n} registros verificados" + (f" · {problem}" if problem else ""))
    return 0 if ok else 1


def cmd_paper_sim(args) -> int:
    from .execution.harness import run_scenario
    report = asyncio.run(run_scenario(args.scenario, cash=D(args.cash)))
    print(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tradingagent", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--config")
    r.add_argument("--seconds", type=float, help="cerrar solo después de N segundos")
    sub.add_parser("telegram-test")
    sub.add_parser("telegram-chats")
    s = sub.add_parser("sources")
    s.add_argument("--check", action="store_true")
    s.add_argument("--config")
    sub.add_parser("journal-verify")
    p = sub.add_parser("paper-sim")
    p.add_argument("scenario")
    p.add_argument("--cash", default="10000")
    args = ap.parse_args(argv)
    handlers = {"run": cmd_run, "telegram-test": cmd_telegram_test, "telegram-chats": cmd_telegram_chats,
                "sources": cmd_sources, "journal-verify": cmd_journal_verify, "paper-sim": cmd_paper_sim}
    if args.cmd not in handlers:
        ap.print_help()
        return 2
    try:
        return handlers[args.cmd](args)
    except ValueError as e:
        print(f"Error: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
