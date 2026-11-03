"""Línea de comandos del agente.

  python -m tradingagent run                 agente completo en paper (Ctrl+C para cerrar)
  python -m tradingagent telegram-test       verifica TELEGRAM_BOT_TOKEN/CHAT_ID y manda un mensaje de prueba
  python -m tradingagent telegram-chats      lista los chats que le escribieron al bot (para el CHAT_ID)
  python -m tradingagent telegram-chats --avisar   el bot le manda a cada chat que le escribió /id su número
  python -m tradingagent collect [--data DIR]       una pasada del recolector de datos de entrenamiento
  python -m tradingagent collect --paper [--capital 120] [--rearm] [--mercados binance-spot,mexc-futuros]
                                             ...y del operador paper (rearme tras un halt)
  python -m tradingagent mexc-check          prueba la API de futuros de MEXC (y las claves de lectura, si hay)
  python -m tradingagent llm-check           prueba la clave de Claude con una consulta mínima (centavos de centavo)
  python -m tradingagent sources [--check]   fuentes y su estado; --check lee cada una una vez
  python -m tradingagent journal-verify      verifica la cadena de hashes del diario
  python -m tradingagent paper-sim ESCENARIO reproduce libros + órdenes de un escenario JSONL

Todo lee TRADING_HOME/.env y TRADING_HOME/config.yaml (TRADING_HOME por defecto %LOCALAPPDATA%\\TRADING).
Este proyecto (TRADING) nunca usa ni toca las carpetas de nexus.
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


ASK_ID = ("/id", "/start")
MAX_NOTIFY = 5


def cmd_telegram_chats(args) -> int:
    _setup()
    from .alerts.model import system_alert
    from .alerts.telegram import TelegramTransport, api_base
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Falta TELEGRAM_BOT_TOKEN en", paths.home() / ".env")
        return 2

    async def main():
        t = TelegramTransport(token, "0", base_url=api_base(os.environ))
        try:
            me = await t.get_me()
            if not me.ok:
                print(f"El bot no responde con ese token: {me.error}")
                return 1
            chats = await t.recent_chats()
        finally:
            await t.close()
        if not args.avisar:
            if not chats:
                print("No hay mensajes recientes: escribile algo al bot (por ejemplo /start) y repetí el comando.")
            for c in chats:
                print(f"TELEGRAM_CHAT_ID={c['id']}   ({c['type']}: {c['name']})")
            return 0
        # --avisar: el número va POR TELEGRAM a quien lo pidió; en pantalla (log público en GitHub) solo cuentas
        bot = me.error                                              # "@usuario" del bot (es público)
        private = [c for c in chats if c["type"] == "private"]
        asked = [c for c in private if any(x.strip().lower().startswith(ASK_ID) for x in c["texts"])]
        print(f"Bot {bot} · Telegram devolvió {t.last_updates} mensajes de las últimas 24 h: {len(chats)} chats, "
              f"{len(private)} privados, {len(asked)} con /id o /start.")
        if not asked:
            if not t.last_updates:
                print(f"No le llegó ningún mensaje a {bot}. Si le escribiste /id a ESE bot hace un rato, otro programa "
                      "que usa el mismo bot (con getUpdates) se lo llevó primero. Alternativa: pedile tu número a "
                      "@userinfobot y guardalo como secreto TELEGRAM_CHAT_ID.")
            else:
                print(f"Ningún chat privado le mandó /id a {bot}: mandale /id a ese bot y volvé a correrlo, o pedile "
                      "tu número a @userinfobot.")
            return 1
        ok = 0
        for c in asked[:MAX_NOTIFY]:
            dm = TelegramTransport(token, str(c["id"]), base_url=api_base(os.environ))
            try:
                r = await dm.send(system_alert(
                    f"🔑 Tu TELEGRAM_CHAT_ID es <code>{c['id']}</code>\nGuardalo en GitHub: Settings → Secrets and "
                    "variables → Actions → New repository secret, con el nombre TELEGRAM_CHAT_ID.", silent=False))
            finally:
                await dm.close()
            ok += r.ok
        print(f"Avisé su número a {ok} de {min(len(asked), MAX_NOTIFY)} chats que lo pidieron.")
        return 0 if ok == min(len(asked), MAX_NOTIFY) else 1

    return asyncio.run(main())


def cmd_collect(args) -> int:
    _setup("WARNING")
    cfg = config.load(args.config or paths.home() / "config.yaml")
    from .alerts.telegram import TelegramTransport, api_base
    from .research.collector import collect
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    use_tg = bool(token and chat and cfg["alerts"].get("telegram") and not args.no_telegram)
    if not use_tg and not args.no_telegram:
        print("Telegram apagado: falta TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID (los datos se juntan igual).",
              file=sys.stderr)

    paper = None
    if args.paper:
        mexc_keys = (os.environ.get("MEXC_ACCESS_KEY"), os.environ.get("MEXC_SECRET_KEY"))
        paper = {"capital": args.capital, "rearm": args.rearm, "mercados": [m for m in args.mercados.split(",") if m],
                 "mexc_keys": mexc_keys if all(mexc_keys) else None}
    from . import limits as limits_mod
    from .intelligence.llm import LlmClassifier, api_key
    key = api_key(os.environ)
    if not key:
        print("IA apagada: falta ANTHROPIC_API_KEY (decide solo el clasificador local).", file=sys.stderr)

    async def main():
        t = TelegramTransport(token, chat, base_url=api_base(os.environ)) if use_tg else None
        llm = LlmClassifier(key, limit_usd=float(limits_mod.load()["ai"]["max_llm_usd_per_day"])) if key else None
        try:
            return await collect(args.data or paths.home() / "datos", cfg, os.environ, telegram=t, paper=paper,
                                 llm=llm)
        finally:
            if t is not None:
                await t.close()
            if llm is not None:
                await llm.close()

    print(json.dumps(asyncio.run(main()), ensure_ascii=False, sort_keys=True))
    return 0


def cmd_mexc_check(args) -> int:
    """Diagnóstico de MEXC futuros. Imprime solo datos públicos y si las claves leen la cuenta (nunca saldos)."""
    _setup("WARNING")
    import aiohttp

    from .marketdata import mexc
    access, secret = os.environ.get("MEXC_ACCESS_KEY"), os.environ.get("MEXC_SECRET_KEY")

    async def main():
        bad = 0
        async with aiohttp.ClientSession() as session:
            for sym in ("BTC_USDT", "ETH_USDT", "SOL_USDT"):
                try:
                    d = await mexc.detail(session, sym)
                    t = await mexc.ticker(session, sym)
                    f = await mexc.funding(session, sym)
                    print(f"OK   {sym}: contrato {d['contract_size']} · tick {d['tick']} · comisión pública "
                          f"{float(d['maker']) * 100:.3f}%/{float(d['taker']) * 100:.3f}% · último {t['last']} · "
                          f"funding {f['rate'] * 100:+.4f}% cada {f['cycle_h']} h")
                except mexc.MarketDataError as e:
                    bad += 1
                    print(f"MAL  {sym}: {e}")
            if not (access and secret):
                print("Claves de lectura: no configuradas (MEXC_ACCESS_KEY / MEXC_SECRET_KEY).")
                return 1 if bad else 0
            ro = mexc.ReadOnlyClient(session, access, secret)
            try:
                ok = await ro.account_ok()
                fees = await ro.fee_rates("BTC_USDT")
                print(f"Claves de lectura: {'OK' if ok else 'respuesta rara'} · comisión de la cuenta BTC_USDT "
                      f"{fees['maker'] * 100:.3f}%/{fees['taker'] * 100:.3f}%")
            except mexc.MarketDataError as e:
                bad += 1
                print(f"Claves de lectura: no funcionan ({e})")
        return 1 if bad else 0

    return asyncio.run(main())


def cmd_llm_check(args) -> int:
    _setup("WARNING")
    from .intelligence.llm import MODEL, LlmClassifier, api_key
    key = api_key(os.environ)
    if not key:
        print("Falta ANTHROPIC_API_KEY en", paths.home() / ".env")
        return 2

    async def main():
        c = LlmClassifier(key)
        try:
            r = await c.health_check()
        finally:
            await c.close()
        if r.error:
            print(f"Claude NO responde: {r.error} (decide el clasificador local)")
            return 1
        print(f"Claude OK · {MODEL} · {r.latency_ms:.0f} ms · {r.usd:.6f} USD · leyó: {r.signal.category} "
              f"{r.signal.polarity:+.2f} sobre {', '.join(r.signal.assets)}")
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
    tc = sub.add_parser("telegram-chats")
    tc.add_argument("--avisar", action="store_true", help="mandarle a cada chat que escribió /id su número")
    c = sub.add_parser("collect")
    c.add_argument("--data", help="carpeta de datos (por defecto TRADING_HOME/datos)")
    c.add_argument("--config")
    c.add_argument("--no-telegram", action="store_true")
    c.add_argument("--paper", action="store_true", help="operar en paper las noticias de la corrida")
    c.add_argument("--capital", type=float, default=120.0, help="USDT simulados al crear la cuenta paper")
    c.add_argument("--rearm", action="store_true", help="rearme humano tras un halt por drawdown (REGLAS A-4)")
    c.add_argument("--mercados", default="binance-spot,mexc-futuros", help="cuentas paper, separadas por comas")
    sub.add_parser("mexc-check")
    sub.add_parser("llm-check")
    s = sub.add_parser("sources")
    s.add_argument("--check", action="store_true")
    s.add_argument("--config")
    sub.add_parser("journal-verify")
    p = sub.add_parser("paper-sim")
    p.add_argument("scenario")
    p.add_argument("--cash", default="10000")
    args = ap.parse_args(argv)
    handlers = {"run": cmd_run, "telegram-test": cmd_telegram_test, "telegram-chats": cmd_telegram_chats,
                "sources": cmd_sources, "journal-verify": cmd_journal_verify, "paper-sim": cmd_paper_sim,
                "collect": cmd_collect, "mexc-check": cmd_mexc_check, "llm-check": cmd_llm_check}
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
