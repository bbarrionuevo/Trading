"""Relé de Tokio (relay/): datagramas UDP firmados, latido de la PC que abre el túnel del NAT y avisos que el
servidor manda a la PC. Con el servidor y el cliente de verdad, por UDP en 127.0.0.1."""
import asyncio
import secrets

from tradingagent.core.clock import SystemClock
from tradingagent.core.events import NewsItem
from tradingagent.relay import client, protocol, server

KEY = secrets.token_bytes(32)
NOW = 1_793_900_000.0


def test_firma_hora_y_numero_unico():
    seen = protocol.NonceCache()
    data = protocol.encode({"t": "hb"}, KEY, NOW)
    assert protocol.decode(data, KEY, seen, NOW + 1)["t"] == "hb"
    assert protocol.decode(data, KEY, seen, NOW + 1) is None                       # repetido
    assert protocol.decode(protocol.encode({"t": "hb"}, KEY, NOW), secrets.token_bytes(32),
                           protocol.NonceCache(), NOW) is None                     # otra clave
    old = protocol.encode({"t": "hb"}, KEY, NOW - 120)
    assert protocol.decode(old, KEY, protocol.NonceCache(), NOW) is None             # vieja
    forged = protocol.encode({"t": "sig", "src": "upbit.avisos"}, KEY, NOW).replace(b"upbit", b"xxxxx")
    assert protocol.decode(forged, KEY, protocol.NonceCache(), NOW) is None          # modificada


def test_la_clave_se_crea_una_vez_y_no_se_pisa(tmp_path):
    f = tmp_path / "relay.key"
    protocol.new_key(f)
    k = protocol.load_key(f)
    protocol.new_key(f)
    assert len(k) == 32 and protocol.load_key(f) == k


async def test_latido_aviso_y_datagrama_falso():
    loop = asyncio.get_running_loop()
    clock = SystemClock()
    got = []

    async def on_item(item, max_age_s):
        got.append((item, max_age_s))

    srv_transport, srv = await loop.create_datagram_endpoint(lambda: server.RelayServer(KEY, clock),
                                                             local_addr=("127.0.0.1", 0))
    port = srv_transport.get_extra_info("sockname")[1]
    pc = client.RelayClient("127.0.0.1", port, KEY, on_item, clock, heartbeat_s=0.05)
    stop = asyncio.Event()
    task = asyncio.create_task(pc.run(stop))
    try:
        for _ in range(100):                                         # el latido abre el túnel
            if srv.live() and pc.counts["acks"]:
                break
            await asyncio.sleep(0.02)
        assert srv.live() and pc.rtt_ms is not None
        item = NewsItem(id="abc123", source="upbit.avisos", tier=1, kind="api", title="[거래] 신규 거래지원 안내 (FOO)",
                        published_at=clock.time() - 3, received_at=clock.time())
        assert srv.forward(item)
        for _ in range(100):
            if got:
                break
            await asyncio.sleep(0.02)
        it, max_age = got[0]
        assert (it.id, it.source, it.title, it.kind) == ("abc123", "upbit.avisos", item.title, "relay")
        assert it.tier == client.SPECS["upbit.avisos"].tier and max_age == client.SPECS["upbit.avisos"].max_age_s
        other = NewsItem(id="x", source="coindesk", tier=3, kind="rss", title="no oficial", received_at=clock.time())
        srv.forward(other)                                           # fuente fuera de la lista: la PC la ignora
        fake = protocol.encode({"t": "sig", "src": "upbit.avisos", "id": "evil", "title": "BUY EVIL"},
                               secrets.token_bytes(32))              # sin la clave
        srv_transport.sendto(fake, pc.transport.get_extra_info("sockname"))
        await asyncio.sleep(0.2)
        assert [g[0].id for g in got] == ["abc123"]
        assert pc.counts["ignorados"] == 1 and pc.counts["rechazados"] >= 1
    finally:
        stop.set()
        await task
        srv_transport.close()
