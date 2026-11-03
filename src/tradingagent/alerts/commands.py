"""Órdenes del dueño por Telegram (pedido del 3-nov: "poder realizar modificaciones rápidas de ser necesario").

- Solo se obedece al chat del dueño (`TELEGRAM_CHAT_ID`): cualquier otro chat se ignora sin responder.
- Solo hay órdenes que BAJAN el riesgo o informan. Armar dinero real por acá no se puede (REGLAS A-2: solo en
  la PC, con la frase exacta).
- Lo pendiente al arrancar se descarta: una orden vieja nunca se ejecuta después de un reinicio. La posición
  leída (`offset`) queda en `state/telegram-offset.json`.

/estado · /pausa · /seguir · /desarmar · /cerrar SI · /ayuda
"""
import asyncio
import logging
import time
from pathlib import Path

from .. import paths

log = logging.getLogger("tradingagent.comandos")
OFFSET_FILE = "telegram-offset.json"
POLL_S = 25                                     # long polling de getUpdates
HELP = ("Órdenes:\n/estado: cómo va todo\n/pausa: no abro nada nuevo (lo abierto sigue con sus límites)\n"
        "/seguir: vuelvo a operar\n/desarmar: apago el dinero real (lo abierto sale normal)\n"
        "/cerrar SI: emergencia, cierro todo lo real ya y lo apago\n"
        "Armar dinero real no se puede desde acá: solo en la PC.")


class Commands:
    def __init__(self, transport, home: Path, actions, *, chat_id: str, clock=None):
        self.transport, self.home, self.actions = transport, Path(home), actions
        self.chat_id = str(chat_id)
        self.clock = clock
        self.offset_path = self.home / "state" / OFFSET_FILE
        self.offset: int | None = (paths.read_json(self.offset_path, None) or {}).get("offset")
        self.handled = 0

    def _now(self) -> float:
        return self.clock.time() if self.clock else time.time()

    async def _save_offset(self) -> None:
        await asyncio.to_thread(paths.write_json_atomic, self.offset_path, {"offset": self.offset,
                                                                             "ts": round(self._now())})

    async def reply(self, text: str) -> None:
        await self.transport.call("sendMessage", {"chat_id": self.chat_id, "text": text,
                                                  "disable_web_page_preview": True})

    async def poll_once(self, timeout_s: int = POLL_S) -> int:
        """Una lectura de mensajes nuevos; devuelve cuántas órdenes atendió."""
        first = self.offset is None
        payload = {"timeout": 0 if first else timeout_s, "allowed_updates": ["message"]}
        if not first:
            payload["offset"] = self.offset
        data, _, err = await self.transport.call("getUpdates", payload)
        if not data or not data.get("ok"):
            raise RuntimeError(err or str((data or {}).get("description", "getUpdates falló"))[:120])
        updates = data.get("result") or []
        if updates:
            self.offset = max(int(u.get("update_id", 0)) for u in updates) + 1
        elif first:
            self.offset = 0
        if first:                                          # lo de antes de arrancar no se ejecuta nunca
            await self._save_offset()
            return 0
        done = 0
        for u in updates:
            msg = u.get("message") or {}
            if str((msg.get("chat") or {}).get("id")) != self.chat_id:
                continue                                   # otro chat: ni se contesta
            text = str(msg.get("text") or "").strip()
            if text.startswith("/"):
                await self.handle(text)
                done += 1
        if updates:
            await self._save_offset()
        self.handled += done
        return done

    async def handle(self, text: str) -> None:
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        try:
            if cmd == "/estado":
                await self.reply(await self.actions.status_text())
            elif cmd == "/pausa":
                (self.home / "KILL").write_text("pausa pedida por Telegram", encoding="utf-8")
                await self.reply("⏸ Pausado: no abro nada nuevo. Lo abierto sigue con sus límites. /seguir para "
                                 "volver a operar.")
            elif cmd == "/seguir":
                (self.home / "KILL").unlink(missing_ok=True)
                await self.reply("▶️ Listo, vuelvo a operar.")
            elif cmd == "/desarmar":
                await self.reply(await self.actions.disarm())
            elif cmd == "/cerrar":
                if arg.strip().upper() != "SI":
                    await self.reply("Para cerrar TODO lo real ya y apagarlo, mandá exactamente: /cerrar SI")
                else:
                    await self.reply("🛑 Cerrando todo lo real…")
                    await self.reply(await self.actions.close_all("pedido del dueño por Telegram"))
            else:
                await self.reply(HELP)
        except Exception as e:
            log.exception("falló la orden %s", cmd)
            await self.reply(f"No pude hacer {cmd}: {type(e).__name__}. Probá en la PC (docs/OPERACION.md).")

    async def run(self, stop: asyncio.Event) -> None:
        delay = 0.0
        while not stop.is_set():
            if delay:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                    return
                except TimeoutError:
                    pass
            try:
                await self.poll_once()
                delay = 0.0
            except Exception as e:                         # noqa: BLE001 (sin Telegram se reintenta)
                log.warning("órdenes por Telegram: %s", str(e)[:120])
                delay = min(60.0, (delay or 2.0) * 2)
