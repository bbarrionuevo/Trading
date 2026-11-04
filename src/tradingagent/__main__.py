"""Línea de comandos del agente.

  python -m tradingagent run                 agente completo en paper (Ctrl+C para cerrar)
  python -m tradingagent supervisar          el agente 24/7: lo relanza, lo actualiza solo y sirve el tablero
  python -m tradingagent tablero [--abrir]   tablero local en http://127.0.0.1:18765 (solo lectura)
  python -m tradingagent telegram-test       verifica TELEGRAM_BOT_TOKEN/CHAT_ID y manda un mensaje de prueba
  python -m tradingagent telegram-chats      lista los chats que le escribieron al bot (para el CHAT_ID)
  python -m tradingagent telegram-chats --avisar   el bot le manda a cada chat que le escribió /id su número
  python -m tradingagent collect [--data DIR]       una pasada del recolector de datos de entrenamiento
  python -m tradingagent collect --paper [--capital 120] [--rearm] [--mercados binance-spot,mexc-futuros] [--ia]
                                             ...y del operador paper (rearme tras un halt)
  python -m tradingagent mexc-check          prueba la API de futuros de MEXC (y las claves de lectura, si hay)
  python -m tradingagent mexc-real-check     prueba SIN RIESGO del modo real (órdenes que no se pueden ejecutar)
  python -m tradingagent binance-real-check  lo mismo en Binance Futures (valida sin ejecutar y una orden solo maker)
  python -m tradingagent armar-real          arma el DINERO REAL con todo el saldo (frase exacta; --desarmar)
  python -m tradingagent armar-real --simulacro --capital 100 --dias 2   lo mismo SIN plata (ninguna orden)
  python -m tradingagent cerrar-todo         emergencia: cancela, cierra todo lo real y desarma
  python -m tradingagent estudio-listados    cómo movieron los listados de Binance el precio (últimos 180 días)
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

EXIT_LOCKED = 3                                    # el supervisor lo reconoce: hay otro agente corriendo


def _utf8_console() -> None:
    """En Windows, con la salida redirigida a un archivo (el supervisor), Python escribe en cp1252 y un emoji
    lanzaría UnicodeEncodeError. UTF-8 y, por las dudas, reemplazar en vez de fallar."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _setup(level="INFO"):
    from .telemetry import logs
    _utf8_console()
    loaded = paths.load_env()
    logs.setup(paths.home() / "logs", level)
    return loaded


def cmd_run(args) -> int:
    _setup()
    from .core.instance import InstanceLock
    lock = InstanceLock(paths.state("agente.lock"))
    if not lock.acquire():                         # dos agentes sobre el mismo estado se pisarían
        print("Ya hay un agente corriendo con esta carpeta (TRADING_HOME): no arranco otro.")
        return EXIT_LOCKED
    paths.state("agente.pid").write_text(str(os.getpid()), encoding="utf-8")
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
    finally:
        lock.release()
    return 0


def cmd_supervisar(args) -> int:
    from .core.instance import InstanceLock
    from .telemetry import logs
    _utf8_console()
    paths.load_env()
    home = paths.home()
    logs.setup(home / "logs", "INFO", name="supervisor.log")
    lock = InstanceLock(home / "state" / "supervisor.lock")
    if not lock.acquire():
        print("Ya hay un supervisor corriendo con esta carpeta: no arranco otro.")
        return 0
    import signal

    from .supervisor import Supervisor

    def graceful(*_a):                             # `kill` o un apagado: cerrar en orden, como con Ctrl+C
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, graceful)
    try:
        from . import dashboard
        port = None if args.puerto == 0 else (args.puerto or dashboard.PORT)
        return Supervisor(home=home, port=port).run()
    except KeyboardInterrupt:
        return 0
    finally:
        lock.release()


def cmd_tablero(args) -> int:
    import time
    import webbrowser

    from . import dashboard
    known = paths.read_json(paths.state("tablero.json"), {}).get("url")
    if known and dashboard.is_ours(known):
        print(f"El tablero ya está abierto (lo sirve el supervisor): {known}")
        if args.abrir:
            webbrowser.open(known)
        return 0
    srv = dashboard.start_free(paths.home(), args.puerto or dashboard.PORT)
    url = dashboard.announce(paths.home(), srv)
    print(f"Tablero en {url} (Ctrl+C para cerrar)")
    if args.abrir:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()
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
            r = await t.send(system_alert("🔔 Prueba de TRADING: si ves este mensaje, los avisos te llegan bien.",
                                          silent=False))
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
    key = api_key(os.environ) if args.ia else None   # en GitHub, sin IA: cuesta plata y ahí el alfa ya pasó
    if args.ia and not key:
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


def _trading_keys() -> tuple[str | None, str | None]:
    return os.environ.get("MEXC_ACCESS_KEY"), os.environ.get("MEXC_SECRET_KEY")


def cmd_mexc_real_check(args) -> int:
    """Prueba sin riesgo del modo real (execution/live/check.py). Nunca muestra saldos."""
    _setup("WARNING")
    import aiohttp

    from .execution.live import arming
    from .execution.live.check import real_check
    from .execution.live.mexc_futures import TradingClient
    access, secret = _trading_keys()
    if not (access and secret):
        print("Faltan MEXC_ACCESS_KEY y MEXC_SECRET_KEY en", paths.home() / ".env")
        return 2

    async def main():
        async with aiohttp.ClientSession() as session:
            return await real_check(TradingClient(session, access, secret), session, args.simbolo)

    report = asyncio.run(main())
    arming.save_verification(paths.home(), report, access)
    for st in report["pasos"]:
        print(f"{'OK ' if st['ok'] else 'MAL'}  {st['paso']}" + (f": {st['detalle']}" if st["detalle"] else ""))
    if report["ok"]:
        print("Listo: MEXC entiende las órdenes como el programa las pide. Ya se puede armar el modo real.")
        return 0
    print("La prueba NO pasó: el modo real no se puede armar. No se dejó ninguna orden ni posición abierta.")
    return 1


def cmd_binance_real_check(args) -> int:
    """Prueba sin riesgo del modo real en Binance Futures (execution/live/binance_check.py). Nunca muestra saldos.
    `--demo`: la misma prueba en la DEMO de Binance (plata ficticia, claves *_TEST). No cuenta para armar."""
    _setup("WARNING")
    import time

    import aiohttp

    from .app import binance_keys
    from .execution.live import arming
    from .execution.live.binance_check import real_check
    from .execution.live.binance_futures import BASE, DEMO, BinanceClient
    key, secret = binance_keys(os.environ, demo=args.demo)
    if not (key and secret):
        print("Faltan", "BINANCE_API_KEY_TEST y BINANCE_API_SECRET_TEST" if args.demo
              else "BINANCE_API_KEY y BINANCE_API_SECRET", "en", paths.home() / ".env")
        return 2

    async def main():
        async with aiohttp.ClientSession() as session:
            return await real_check(BinanceClient(session, key, secret, base=DEMO if args.demo else BASE), args.simbolo)

    report = asyncio.run(main())
    name = "verificacion_binance_demo.json" if args.demo else arming.VERIFY_FILES["binance-futuros"]
    paths.write_json_atomic(paths.home() / "state" / name,
                            {"ok": bool(report["ok"]), "clave": arming.fingerprint(key), "ts": round(time.time()),
                             "simbolo": report.get("simbolo"), "pasos": report["pasos"], "stop": report.get("stop"),
                             **({"demo": True} if args.demo else {})})
    if args.demo:
        print("DEMO de Binance (plata ficticia):")
    for st in report["pasos"]:
        print(f"{'OK ' if st['ok'] else 'MAL'}  {st['paso']}" + (f": {st['detalle']}" if st["detalle"] else ""))
    if report["ok"]:
        print("Listo: Binance entiende las órdenes como el programa las pide y no quedó nada abierto."
              + (" (En la demo: no cuenta para armar el dinero real.)" if args.demo else ""))
        return 0
    print("La prueba NO pasó: el modo real no se puede armar. Ninguna orden quedó abierta.")
    return 1


def _restart_agent(home, why: str) -> None:
    """Pide al agente que se cierre prolijo: el supervisor lo relanza en segundos con el armado nuevo."""
    (home / "state").mkdir(parents=True, exist_ok=True)
    (home / "state" / "parar").write_text(why, encoding="utf-8")


def cmd_armar_real(args) -> int:
    """Armado local del dueño (REGLAS A-2): frase exacta, activos y vencimiento. Sin tope de capital: el real
    opera con todo el saldo de la cuenta de futuros. `--simulacro`: lo mismo sin plata en juego (en Binance, contra la
    DEMO de Binance; en MEXC, contra un exchange simulado sobre el libro real)."""
    _setup("WARNING")
    from . import limits as limits_mod
    from .app import binance_keys
    from .execution.live import arming
    home = paths.home()
    if args.desarmar:
        print("Desarmado: el modo real no abre nada nuevo (lo abierto sale normal)." if arming.disarm(home)
              else "No estaba armado.")
        return 0
    binance = args.exchange == "binance"
    venue, where = ("binance-futuros", "Binance") if binance else ("mexc-futuros", "MEXC")
    assets = tuple(a.strip().upper() for a in args.activos.split(",") if a.strip())
    current = paths.read_json(home / arming.ARM_FILE, None)
    real_armed = isinstance(current, dict) and not current.get("simulacro")
    if args.simulacro:                                # sin plata en juego: sin frase
        if real_armed:
            print("Ya hay DINERO REAL armado. Si querés volver al simulacro, primero: "
                  "python -m tradingagent armar-real --desarmar")
            return 1
        demo = binance and args.demo
        if demo and not all(binance_keys(os.environ, demo=True)):
            print("Para el simulacro en la demo de Binance faltan BINANCE_API_KEY_TEST y BINANCE_API_SECRET_TEST en",
                  home / ".env")
            return 2
        # Nunca el saldo real: el simulacro es "plata de mentira" y se publica en la rama pública `pc`. El 4-nov se
        # armó con el saldo real y quedó publicado unos minutos (REGLAS G-8): por eso, 100 USDT salvo que se pida otro.
        capital = 100.0 if args.capital is None else args.capital
        try:
            data = arming.arm(home, args.dias, "", "", assets=assets, simulacro=True, capital=capital, venue=venue,
                              mode=("demo" if demo else "local") if binance else None)
        except ValueError as e:
            print(f"No se armó el simulacro: {e}")
            return 1
        _restart_agent(home, "simulacro")
        against = ("contra la DEMO de Binance (órdenes de verdad, plata ficticia)" if demo
                   else "con el libro real de Binance y órdenes simuladas en la PC (ninguna orden sale)" if binance
                   else "contra un exchange simulado con el libro real de MEXC (no manda ninguna orden)")
        print(f"Simulacro armado: {data['capital_usdt']:g} USDT de mentira, activos {', '.join(data['activos'])}, "
              f"por {args.dias} días. Es el mismo programa que el dinero real, con los precios reales de {where}, "
              f"{against}. El agente se reinicia solo en segundos y te avisa por Telegram.")
        return 0
    if args.capital is not None:
        print(f"Con dinero real no hay tope: opera con TODO el saldo de la cuenta de futuros de {where}. "
              "--capital es solo para el simulacro.")
        return 1
    access = binance_keys(os.environ, demo=False)[0] if binance else _trading_keys()[0]
    if not access:
        print("Faltan", "BINANCE_API_KEY y BINANCE_API_SECRET" if binance else "MEXC_ACCESS_KEY y MEXC_SECRET_KEY",
              "en", home / ".env")
        return 2
    want = arming.phrase(args.dias)
    print(f"Vas a armar DINERO REAL con TODO el saldo de tu cuenta de futuros de {where} (y lo que se gane), activos "
          f"{', '.join(assets)}, por {args.dias} días. Cada operación arriesga como mucho "
          f"{limits_mod.load()['per_trade']['max_risk_pct']:g} % del saldo.\n"
          f"Para confirmar, escribí exactamente:  {want}")
    typed = input("> ")
    try:
        arming.arm(home, args.dias, access, typed, assets=assets, venue=venue)
    except ValueError as e:
        print(f"No se armó: {e}")
        return 1
    ok, why, _ = arming.status(home, access, venue=venue)
    _restart_agent(home, "armado real")
    check = "binance-real-check" if binance else "mexc-real-check"
    print("Armado con todo el saldo.", "El agente se reinicia solo en segundos y empieza." if ok
          else f"Todavía no opera: {why} (corré python -m tradingagent {check}).")
    return 0


def cmd_informe_simulacro(args) -> int:
    """Calibración: qué tan rápido llegan las noticias, qué tan rápido se entra, cuánto se acierta y cuánto se gana."""
    _setup("WARNING")
    from .research.calibracion import render, report
    home = paths.home()
    base = {"simulacro": home / "simulacro", "demo": home / "simulacro-demo", "practica": home / "paper-vivo",
            "real": home / "real"}[args.cuenta] / "binance-futuros"
    import time as _time
    rep = report(base, home / "journal", since=_time.time() - args.dias * 86400 if args.dias else None)
    print(render(rep, args.cuenta))
    return 0


def cmd_estudio_listados(args) -> int:
    """Estudio histórico: cómo movieron los listados de Binance a la moneda en MEXC (research/history.py)."""
    _setup("WARNING")
    import aiohttp

    from .research.history import listing_study

    async def main():
        async with aiohttp.ClientSession(trust_env=True) as session:          # respeta un proxy si lo hay
            return await listing_study(session, days=args.dias, exchange=args.exchange)

    try:
        rep = asyncio.run(main())
    except Exception as e:                                               # noqa: BLE001 (sin red, se avisa claro)
        print(f"No pude hacer el estudio (¿hay internet?): {type(e).__name__}: {str(e)[:160]}")
        return 1
    name = {"binance": "Binance", "bybit": "Bybit", "upbit": "Upbit"}[args.exchange]
    out = paths.home() / "datos" / "estudios" / ("listados.json" if args.exchange == "binance"
                                                 else f"listados-{args.exchange}.json")
    paths.write_json_atomic(out, rep)
    names = {"listado_spot": f"Listados spot de {name}", "futuros": f"Lanzamientos de futuros de {name}",
             "listado_krw": f"Altas en el mercado en wones de {name}", "deslistado": f"Fin del soporte en {name}",
             "alerta": f"Advertencias de {name}", "alerta_levantada": f"Advertencias levantadas en {name}"}
    for kind, stats in rep["resumen"].items():
        cells = " · ".join(f"{k[1:]} min {v['mediana'] * 100:+.1f} % ({v['positivos']}/{v['n']} suben)"
                           for k, v in stats.items() if k.startswith("r"))
        print(f"{names.get(kind, kind)}: {cells}")
    print("Detalle en", out)
    return 0


def cmd_cerrar_todo(args) -> int:
    """Emergencia: cancela todas las órdenes y stops y cierra todas las posiciones reales (con tope de precio), y
    desarma. Binance por defecto; `--demo` lo hace en la DEMO de Binance; `--exchange mexc`, en MEXC."""
    _setup("WARNING")
    import aiohttp

    from .execution.live import arming
    home = paths.home()
    if args.exchange == "binance":
        from .app import binance_keys
        from .execution.live.binance_futures import BASE, DEMO, BinanceClient
        from .execution.live.binance_venue import close_everything
        key, secret = binance_keys(os.environ, demo=args.demo)
        if not (key and secret):
            print("Faltan", "BINANCE_API_KEY_TEST y BINANCE_API_SECRET_TEST" if args.demo
                  else "BINANCE_API_KEY y BINANCE_API_SECRET", "en", home / ".env")
            return 2
        current = paths.read_json(home / arming.ARM_FILE, None) or {}
        if bool(current.get("simulacro")) == bool(args.demo):          # el armado de esa misma cuenta
            arming.disarm(home)

        async def close_binance():
            async with aiohttp.ClientSession() as session:
                client = BinanceClient(session, key, secret, base=DEMO if args.demo else BASE)
                return await close_everything(client, [x for x in args.simbolos.split(",") if x and "_" not in x])

        left = asyncio.run(close_binance())
        print(("DEMO de Binance: " if args.demo else "Desarmado. ")
              + ("Todo cerrado." if not left else f"Quedan {len(left)} posiciones: cerralas desde Binance."))
        return 0 if not left else 1
    from .execution.live.check import _clean_up
    from .execution.live.mexc_futures import TradingClient
    access, secret = _trading_keys()
    if not (access and secret):
        print("Faltan MEXC_ACCESS_KEY y MEXC_SECRET_KEY")
        return 2
    arming.disarm(home)

    async def main():
        async with aiohttp.ClientSession() as session:
            client = TradingClient(session, access, secret)
            wanted = args.simbolos.split(",") if args.simbolos else ["BTC_USDT", "ETH_USDT", "SOL_USDT"]
            symbols = sorted({p.get("symbol") for p in await client.positions()} | set(wanted))
            for sym in (x for x in symbols if x):
                await _clean_up(client, session, sym, client.base)
            return [p for p in await client.positions() if float(p.get("holdVol", 0) or 0) > 0]

    left = asyncio.run(main())
    print("Desarmado. " + ("Todo cerrado." if not left else f"Quedan {len(left)} posiciones: cerralas desde MEXC."))
    return 0 if not left else 1


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
    sv = sub.add_parser("supervisar")
    sv.add_argument("--puerto", type=int, help="puerto base del tablero, si está ocupado usa el siguiente libre "
                    "(0 = sin tablero)")
    tb = sub.add_parser("tablero")
    tb.add_argument("--puerto", type=int)
    tb.add_argument("--abrir", action="store_true", help="abrirlo en el navegador")
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
    c.add_argument("--ia", action="store_true", help="consultar a Claude las noticias dudosas (cuesta plata; en "
                   "GitHub está apagado porque la noticia llega tarde)")
    sub.add_parser("mexc-check")
    rc = sub.add_parser("mexc-real-check")
    rc.add_argument("--simbolo", default="BTC_USDT")
    bc = sub.add_parser("binance-real-check")
    bc.add_argument("--simbolo", default="XRPUSDT")
    bc.add_argument("--demo", action="store_true", help="en la DEMO de Binance (plata ficticia, claves *_TEST)")
    ar = sub.add_parser("armar-real")
    ar.add_argument("--capital", type=float, help="solo para el simulacro: USDT de mentira (100 si no se dice). "
                    "Con dinero real no hay tope: opera con todo el saldo de la cuenta")
    ar.add_argument("--dias", type=int, default=30, help="vencimiento del armado (hasta 30 días)")
    ar.add_argument("--activos", default="todos", help="todos (lo que mueva la noticia: criptos, acciones, índices, "
                    "oro, petróleo) o una lista, por ejemplo BTC,ETH,TSLA")
    ar.add_argument("--desarmar", action="store_true")
    ar.add_argument("--simulacro", action="store_true", help="probar el modo real SIN plata: mismo programa y "
                    "mismos precios; en Binance contra la DEMO (plata ficticia), en MEXC sin mandar órdenes")
    ar.add_argument("--exchange", choices=("binance", "mexc"), default="binance")
    ar.add_argument("--demo", action="store_true", help="el simulacro contra la DEMO de Binance (plata ficticia) "
                    "en vez del libro real con órdenes simuladas en la PC")
    inf = sub.add_parser("informe-simulacro", help="calibración: velocidad, acierto y ganancia")
    inf.add_argument("--cuenta", choices=("simulacro", "demo", "practica", "real"), default="simulacro")
    inf.add_argument("--dias", type=float, default=0, help="solo los últimos N días (0 = todo)")
    es = sub.add_parser("estudio-listados")
    es.add_argument("--dias", type=int, default=180)
    es.add_argument("--exchange", choices=("binance", "bybit", "upbit"), default="binance",
                    help="de quién son los avisos (Bybit y Upbit solo responden desde la PC)")
    ct = sub.add_parser("cerrar-todo")
    ct.add_argument("--simbolos", default="", help="además de los que tengan posición (BTCUSDT,ETHUSDT...)")
    ct.add_argument("--exchange", choices=("binance", "mexc"), default="binance")
    ct.add_argument("--demo", action="store_true", help="en la DEMO de Binance (plata ficticia)")
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
                "collect": cmd_collect, "mexc-check": cmd_mexc_check, "llm-check": cmd_llm_check,
                "supervisar": cmd_supervisar, "tablero": cmd_tablero, "mexc-real-check": cmd_mexc_real_check,
                "binance-real-check": cmd_binance_real_check,
                "armar-real": cmd_armar_real, "cerrar-todo": cmd_cerrar_todo,
                "estudio-listados": cmd_estudio_listados, "informe-simulacro": cmd_informe_simulacro}
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
