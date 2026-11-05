"""Canal oficial de Upbit en Telegram (relay/telegram_upbit.py): solo avisos de trading ("[거래]") con enlace al sitio
de Upbit, con el mismo título que lee la PC (así se deduplica). Sin Telethon: la lectura de mensajes no lo necesita."""
import json
import secrets

import pytest

from tradingagent.relay import protocol, server, telegram_upbit

LISTING = "[거래] 콜렉터크립트(CARDS) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)\n🔗 공지사항 확인하기"
DEPOSIT = "[입출금] 엔진코인(ENJ) 입출금 일시 중단 안내 (11/09 20:00 ~)\n🔗 공지사항 확인하기"
LINK = "https://www.upbit.com/service_center/notice?id=f519b4f2-d7a9-4d8d-84c4-e9db8405829d&view=share"
T = 1_793_854_984.0                                            # 5-nov 06:03:04 UTC


class TextUrl:                                                  # como MessageEntityTextUrl de Telethon
    def __init__(self, url):
        self.url = url


class Msg:
    def __init__(self, text, links):
        self.message, self._links, self.reply_markup = text, links, None

    def get_entities_text(self):
        return [(TextUrl(u), "공지사항 확인하기") for u in self._links]


def test_un_listado_entra_como_el_lector_de_upbit_con_el_mismo_titulo():
    it = telegram_upbit.parse_post(LISTING, telegram_upbit.message_urls(Msg(LISTING, [LINK])), T, T + 0.4)
    assert it.source == "upbit.avisos" and it.kind == "telegram"
    assert it.title == "콜렉터크립트(CARDS) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)"     # = el título del sitio
    assert it.url == "https://upbit.com/service_center/notice?id=f519b4f2-d7a9-4d8d-84c4-e9db8405829d"
    assert it.published_at == T and it.received_at == T + 0.4 and it.tier == telegram_upbit.SPEC.tier


def test_lo_que_no_es_de_trading_o_no_tiene_el_enlace_oficial_no_entra():
    assert telegram_upbit.parse_post(DEPOSIT, [LINK], T, T) is None                     # depósitos: no se opera
    assert telegram_upbit.parse_post(LISTING, [], T, T) is None                         # sin enlace
    assert telegram_upbit.parse_post(LISTING, ["https://upbit.fake/service_center/notice?id=x"], T, T) is None
    assert telegram_upbit.parse_post("", [LINK], T, T) is None


def test_credenciales_del_env_sin_mostrarlas(tmp_path, monkeypatch):
    monkeypatch.delenv("TG_API_ID", raising=False)
    monkeypatch.delenv("TG_API_HASH", raising=False)
    env = tmp_path / ".env"
    env.write_text('TG_API_ID=12345\nTG_API_HASH="abcdef"\nAPI_Key_Binance=no-se-lee\n', encoding="utf-8")
    assert telegram_upbit.credentials(env) == (12345, "abcdef")
    monkeypatch.setenv("TG_API_ID", "999")
    assert telegram_upbit.credentials(env)[0] == 999                                    # el entorno manda
    monkeypatch.delenv("TG_API_ID", raising=False)
    with pytest.raises(SystemExit):                                                     # sin credenciales: lo dice
        telegram_upbit.credentials(tmp_path / "no-existe.env")


async def test_el_rele_manda_el_aviso_de_telegram_a_la_pc():
    key = secrets.token_bytes(32)
    sent = []

    class Transport:
        def sendto(self, data, addr):
            sent.append((data, addr))

    relay = server.RelayServer(key)
    relay.transport = Transport()
    relay.peer, relay.peer_at = ("203.0.113.7", 40000), relay.clock.time()
    it = telegram_upbit.parse_post(LISTING, [LINK], relay.clock.time() - 1, relay.clock.time())
    await relay.on_telegram(it)
    msg = protocol.decode(sent[0][0], key, protocol.NonceCache())
    assert msg["t"] == "sig" and msg["src"] == "upbit.avisos" and msg["title"] == it.title
    assert relay.counts["telegram"] == 1 and json.loads(sent[0][0])["h"]
