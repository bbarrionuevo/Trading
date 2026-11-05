"""Canal OFICIAL de Upbit en Telegram (@upbit_news, de Dunamu, abierto el 22-ago-2025): cada aviso sale por Telegram
0 a 2 s después de publicarse en el sitio (medido el 5-nov con 15 avisos: mediana 1 s), contra ~8 s del sondeo. Corre
en el servidor de Tokio (el sitio de Upbit bloquea las IP de AWS; Telegram no) y cada aviso de TRADING ("[거래]") entra
al relé como si lo hubiera leído el lector de Upbit (`upbit.avisos`, mismo título). Las reglas exactas de siempre
deciden (listado nuevo, alta en wones, fin del soporte, advertencia): ni palabras sueltas ni compras a ciegas.

Seguridad:
- Cuenta de Telegram de un número SECUNDARIO del dueño (nunca la personal). La sesión (`~/telegram/upbit.session`)
  da control total de esa cuenta: queda con permisos 600 y nunca sale del servidor.
- Credenciales de my.telegram.org en el .env del servidor (`TG_API_ID`, `TG_API_HASH`): se leen, nunca se muestran.
- El canal se fija por su ID INTERNO en el primer inicio de sesión (`~/telegram/upbit_canal.json`): si alguien se
  queda con el nombre @upbit_news, no cambia nada. Solo se aceptan mensajes de ese ID y con enlace al sitio de Upbit.

Uso en el servidor:
- La primera vez (interactivo: Telethon pide el número y el código que llega por Telegram):
      python -m tradingagent.relay.telegram_upbit --login
- Para ver que anda (muestra cada aviso con su demora, sin mandar nada):
      python -m tradingagent.relay.telegram_upbit --escuchar
- Después queda andando dentro del relé (servicio rele-tokio), que lo arranca solo si hay sesión.
"""
import argparse
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

from ..core.events import NewsItem, stable_id
from ..ingestion.catalog import DEFAULT_SOURCES

log = logging.getLogger("tradingagent.telegram")
CHANNEL = "upbit_news"
SOURCE = "upbit.avisos"                         # entra como el lector de Upbit de la PC (mismas reglas, sin duplicados)
SPEC = next(s for s in DEFAULT_SOURCES if s.id == SOURCE)
FOLDER = Path.home() / "telegram"
SESSION = FOLDER / "upbit"                      # Telethon le agrega ".session"
PIN_FILE = FOLDER / "upbit_canal.json"
ENV_FILE = Path.home() / ".env"
TAG = re.compile(r"^\[(?P<tag>[^\]]+)\]\s*(?P<title>.+)$")
NOTICE = re.compile(r"https?://(?:www\.)?upbit\.com/service_center/notice\?id=(?P<id>[0-9A-Za-z-]+)")
TRADE_TAG = "거래"                              # avisos de trading (la categoría "trade" que lee la PC)


def credentials(env_file: Path = ENV_FILE) -> tuple[int, str]:
    """TG_API_ID y TG_API_HASH del entorno o del .env del servidor. Nunca se imprimen."""
    vals: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    api_id = os.environ.get("TG_API_ID") or vals.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH") or vals.get("TG_API_HASH")
    if not api_id or not api_hash:
        raise SystemExit(f"faltan TG_API_ID y TG_API_HASH en {env_file}")
    return int(api_id), api_hash


def message_urls(msg) -> list[str]:
    """Los enlaces de un mensaje: los escondidos en el texto ("공지사항 확인하기"), los escritos y los de botones."""
    out = []
    try:
        pairs = msg.get_entities_text()             # Telethon resuelve bien los desplazamientos (UTF-16)
    except (AttributeError, TypeError):
        pairs = []
    for ent, txt in pairs:
        url = getattr(ent, "url", None)
        if url:
            out.append(url)
        elif type(ent).__name__ == "MessageEntityUrl":
            out.append(txt)
    for row in getattr(getattr(msg, "reply_markup", None), "rows", None) or []:
        for b in getattr(row, "buttons", []) or []:
            if getattr(b, "url", None):
                out.append(b.url)
    out += [m.group(0) for m in NOTICE.finditer(msg.message or "")]
    return out


def parse_post(text: str, urls: list[str], date_ts: float, now: float) -> NewsItem | None:
    """Un mensaje del canal → el aviso, si es de trading ("[거래] …") y trae el enlace al sitio de Upbit."""
    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    m = TAG.match(lines[0]) if lines else None
    if not m or m.group("tag").strip() != TRADE_TAG:
        return None                                 # depósitos, mantenimiento, análisis... no se operan
    link = next((u for u in (NOTICE.search(x) for x in urls) if u), None)
    if link is None:
        return None                                 # sin el enlace oficial: no se usa
    nid = link.group("id")
    return NewsItem(id=stable_id(SOURCE, f"upbit-tg:{nid}"), source=SOURCE, tier=SPEC.tier, kind="telegram",
                    title=m.group("title").strip()[:500], url=f"https://upbit.com/service_center/notice?id={nid}",
                    published_at=float(date_ts), received_at=now, assets_hint=SPEC.assets,
                    category_hint=SPEC.category)


def ready() -> bool:
    """Hay sesión iniciada y canal fijado (si no, el relé sigue sin Telegram)."""
    try:
        import telethon  # noqa: F401
    except ImportError:
        return False
    return SESSION.with_suffix(".session").exists() and PIN_FILE.exists()


def _client(env_file: Path = ENV_FILE):
    from telethon import TelegramClient
    api_id, api_hash = credentials(env_file)
    FOLDER.mkdir(mode=0o700, parents=True, exist_ok=True)
    return TelegramClient(str(SESSION), api_id, api_hash, auto_reconnect=True)


async def login(env_file: Path = ENV_FILE) -> None:
    """Primer inicio de sesión (interactivo) y el canal fijado por su ID interno."""
    from telethon import utils
    client = _client(env_file)
    await client.start()                            # pide el número, el código de Telegram y, si hay, la clave
    try:
        ent = await client.get_entity(CHANNEL)
        if not getattr(ent, "broadcast", False):
            raise SystemExit(f"@{CHANNEL} no es un canal: no lo fijo")
        pin = {"id": utils.get_peer_id(ent), "titulo": ent.title, "usuario": ent.username, "fijado": round(time.time())}
        PIN_FILE.write_text(json.dumps(pin, ensure_ascii=False), encoding="utf-8")
        os.chmod(PIN_FILE, 0o600)
        print(f"Listo: canal fijado «{ent.title}» (@{ent.username}, ID {pin['id']}).")
    finally:
        await client.disconnect()
    session = SESSION.with_suffix(".session")
    if session.exists():
        os.chmod(session, 0o600)


async def listen(on_item, stop: asyncio.Event, env_file: Path = ENV_FILE) -> None:
    """Escucha el canal fijado y pasa cada aviso de trading a `on_item` (async). Se reconecta solo."""
    from telethon import events
    pin = json.loads(PIN_FILE.read_text(encoding="utf-8"))
    peer = int(pin["id"])
    delay = 5.0
    while not stop.is_set():
        client = _client(env_file)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise SystemExit("la sesión de Telegram no está iniciada: corré --login")
            await client.get_input_entity(peer)     # el canal tiene que estar en la sesión (lo dejó el login)

            async def on_message(event):
                if event.chat_id != peer:           # solo el canal fijado
                    return
                msg, now = event.message, time.time()
                sent_at = msg.date.timestamp() if msg.date else now
                item = parse_post(msg.message or "", message_urls(msg), sent_at, now)
                log.info("Telegram %s: %.2f s desde el envío · %s", "aviso" if item else "ignorado", now - sent_at,
                         (msg.message or "").split("\n")[0][:100])
                if item is not None:
                    await on_item(item)

            client.add_event_handler(on_message, events.NewMessage(chats=[peer]))
            log.info("Telegram: escuchando «%s» (ID %s)", pin.get("titulo"), peer)
            delay = 5.0
            done = asyncio.ensure_future(stop.wait())
            gone = asyncio.ensure_future(client.disconnected)
            await asyncio.wait({done, gone}, return_when=asyncio.FIRST_COMPLETED)
            for f in (done, gone):
                f.cancel()
        except SystemExit:
            raise
        except Exception as e:                       # noqa: BLE001 (sin Telegram, el relé sigue con lo demás)
            log.warning("Telegram cortado (%s): reintento en %.0f s", type(e).__name__, delay)
        finally:
            await client.disconnect()
        if not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, 120.0)


async def _print_item(item) -> None:
    print(f"AVISO: {item.title} · {item.url}")


def cli() -> None:
    ap = argparse.ArgumentParser(description="Canal oficial de Upbit en Telegram (servidor de Tokio)")
    ap.add_argument("--login", action="store_true", help="primer inicio de sesión (interactivo) y fijar el canal")
    ap.add_argument("--escuchar", action="store_true", help="mostrar los avisos que llegan, sin mandar nada")
    ap.add_argument("--env", default=str(ENV_FILE), help="archivo .env con TG_API_ID y TG_API_HASH")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.login:
        asyncio.run(login(Path(args.env)))
    elif args.escuchar:
        asyncio.run(listen(_print_item, asyncio.Event(), Path(args.env)))
    else:
        ap.print_help()


if __name__ == "__main__":
    cli()
