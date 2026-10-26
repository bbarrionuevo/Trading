"""Comunicación sesión de Claude ↔ PC: canal de comandos de seguridad, chequeo previo y latido."""
import importlib.util
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _rep():
    spec = importlib.util.spec_from_file_location("report_status", ROOT / "local" / "windows" / "report_status.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def cmd(i, c, **kw):
    return {"id": f"c{i}", "cmd": c, "ts": kw.pop("ts", time.time())} | kw


def test_comandos_solo_reducen_riesgo_y_no_tocan_el_kill_del_duenio():
    from nexus import paths, remote
    kill = paths.kill_file()
    r = remote.process([cmd(1, "pausar", motivo="pérdidas raras"), cmd(2, "autorizar"), cmd(3, "rearmar")])
    assert r["c1"]["ok"] and kill.read_text().startswith("1 remoto")
    assert not r["c2"]["ok"] and not r["c3"]["ok"]                            # no existen
    remote.process([cmd(4, "cancelar")])
    assert kill.read_text().startswith("2 remoto")                             # sube de nivel
    remote.process([cmd(5, "pausar")])
    assert kill.read_text().startswith("2")                                    # nunca baja de nivel
    r = remote.process([cmd(6, "reanudar")])
    assert r["c6"]["ok"] and not kill.exists()
    kill.write_text("")                                                        # KILL del dueño (nivel 1)
    r = remote.process([cmd(7, "pausar"), cmd(8, "reanudar")])
    assert "no se toca" in r["c7"]["msg"] and not r["c8"]["ok"] and kill.read_text() == ""
    remote.process([cmd(9, "cancelar")])                                       # lo sube a 2...
    assert kill.read_text().startswith("2 remoto")
    remote.process([cmd(10, "reanudar")])                                      # ...y al reanudar vuelve el del dueño
    assert kill.exists() and kill.read_text() == ""


def test_comandos_viejos_repetidos_o_sin_fecha_no_se_ejecutan():
    from nexus import paths, remote
    r = remote.process([cmd(1, "pausar", ts=time.time() - 7 * 3600), {"id": "c2", "cmd": "pausar"}])
    assert not r["c1"]["ok"] and not r["c2"]["ok"] and not paths.kill_file().exists()
    remote.process([cmd(3, "pausar")])
    remote.process([cmd(4, "reanudar")])
    remote.process([cmd(3, "pausar"), cmd(4, "reanudar")])                     # ya ejecutados: no se repiten
    assert not paths.kill_file().exists()


class FakeClient:
    has_keys = True

    def account(self):
        return {"canTrade": True, "canWithdraw": False, "accountType": "SPOT",
                "balances": [{"asset": "USDT", "free": "21.5", "locked": "0"}, {"asset": "X", "free": "0.1", "locked": "0"}]}

    def _get(self, path, params=None, signed=False):
        assert path == "/api/v3/exchangeInfo"
        sym = params["symbol"]
        types = ["LIMIT", "LIMIT_MAKER", "MARKET"] if sym != "MALOUSDT" else ["LIMIT"]
        return {"symbols": [{"symbol": sym, "status": "1", "orderTypes": types, "quotePrecision": 4,
                             "baseAssetPrecision": 2, "quoteAmountPrecision": "5", "isSpotTradingAllowed": True}]}

    def book_ticker(self, sym):
        return {"bidPrice": "1.0000", "askPrice": "1.0020"}

    def trade_fee(self, sym):
        return {"data": {"makerCommission": 0, "takerCommission": 0.0005}}


def test_preflight_revisa_clave_saldo_reloj_y_pares():
    from nexus import paths, preflight
    from nexus.agent import live
    r = preflight.run(FakeClient(), ["XUSDT", "MALOUSDT"], clock=lambda n: 350)
    assert not r["ready"] and r["auth"] is None and r["account"]["usdt"] == 21.5 and r["clock_offset_ms"] == 350
    x, malo = r["symbols"]
    assert x["ok"] and x["spread_pct"] == 0.2 and x["tick_pct"] == 0.01 and x["fees_pct"] == (0.0, 0.05)
    assert not malo["ok"] and "no admite LIMIT_MAKER" in malo["problems"]
    now = time.time()
    paths.write_json_atomic(paths.state(live.AUTH_FILE), {"ts": now, "expires": now + 3600, "capital_eur": 20,
                                                           "symbols": ["XUSDT"]})
    assert preflight.run(FakeClient(), clock=lambda n: 100)["ready"]
    r = preflight.run(FakeClient(), clock=lambda n: 9000)
    assert not r["ready"] and "reloj" in r["problems"][0]
    assert "PROBLEMA" in preflight.describe(r) and json.loads(paths.state("preflight.json").read_text())["ts"]


def test_latido_publica_dinero_real_comandos_y_preflight(monkeypatch):
    from nexus import paths, preflight, remote
    from nexus.agent import live
    rep = _rep()
    lg = live.OrderLog()
    lg.write({"act": "enviar", "cid": "nx1XUSDT", "px": 1.0})
    live.journal_real({"ts": 1, "sym": "XUSDT", "pnl": 0.01})
    monkeypatch.setattr(remote, "fetch", lambda d: [cmd(1, "latido")])
    monkeypatch.setattr(preflight, "run", lambda now=None: paths.write_json_atomic(
        paths.state("preflight.json"), {"ts": now, "ready": False, "problems": []}))
    rep.housekeeping(paths.home())
    b = rep.build()
    assert b["remote"]["c1"]["ok"] and b["real"]["preflight"]["ready"] is False
    assert b["real"]["orders_tail"][0]["cid"] == "nx1XUSDT" and b["real"]["real_trades_tail"][0]["pnl"] == 0.01
    assert "allocator" in b and b["kill_text"] is None
    assert not rep.locked() and rep.locked()                                   # segundo latido simultáneo: se omite


def test_informe_y_vigilante_muestran_recomendado_calibracion_y_preflight():
    from nexus.monitor import alerts, digest
    st = {"ts": time.time(), "books": [], "recommended": [{"sym": "XUSDT", "score": 0.01, "n": 40, "mean": 0.03}],
          "evidence": {"calibration": {"XUSDT": {"hours": 5.0, "paper": {"n": 30, "per_hour": 6.0, "mean": 0.02},
                                                 "real": {"n": 4, "per_hour": 0.8, "mean": -0.01}}}}}
    md = digest({"status": st}, None, now=1_800_000_000)
    assert "XUSDT (cota 0.01" in md and "| XUSDT | 5.0 | 30 · 6.0 · 0.02 | 4 · 0.8 · -0.01 |" in md
    pc = {"ts": time.time(), "status": st, "real": {"preflight": {"auth": {"symbols": ["XUSDT"]}, "ready": False,
                                                                 "problems": ["reloj desfasado"], "symbols": []}}}
    assert any("chequeo previo falla: reloj desfasado" in a for a in alerts(pc))


def test_el_duenio_puede_apagar_el_canal():
    from nexus import paths, remote
    paths.state("remote_off").touch()
    r = remote.process([cmd(1, "pausar")])
    assert not r["c1"]["ok"] and "apagado" in r["c1"]["msg"] and not paths.kill_file().exists()


def test_comandos_llegan_por_issues_del_duenio_con_etiqueta(tmp_path, monkeypatch):
    from nexus import remote
    monkeypatch.setattr(remote, "owner_repo", lambda d: ("duenio", "nexus"))
    lab = [{"name": "pc-comando"}]
    issues = [
        {"number": 7, "title": "Pausar por pérdidas", "body": "3 stops seguidos", "user": {"login": "duenio"},
         "labels": lab, "created_at": "2026-10-25T15:00:00Z"},
        {"number": 8, "title": "reanudar", "user": {"login": "intruso"}, "labels": lab, "created_at": "2026-10-25T15:00:00Z"},
        {"number": 9, "title": "cancelar", "user": {"login": "duenio"}, "labels": [], "created_at": "2026-10-25T15:00:00Z"},
        {"number": 10, "title": "cancelar", "user": {"login": "duenio"}, "labels": lab, "pull_request": {},
         "created_at": "2026-10-25T15:00:00Z"}]
    seen = {}
    got = remote.fetch(tmp_path, get=lambda url, params: seen.update(url=url, params=params) or issues)
    assert got == [{"id": "issue7", "cmd": "pausar", "ts": 1792940400, "motivo": "3 stops seguidos"}]
    assert seen["url"].endswith("/repos/duenio/nexus/issues") and seen["params"]["creator"] == "duenio"
    assert remote.fetch(tmp_path, get=lambda u, p: {"message": "rate limit"}) == []
    assert remote.fetch(tmp_path, get=lambda u, p: (_ for _ in ()).throw(ConnectionError())) == []


def test_preflight_cuenta_lo_invertido_en_la_posicion():
    from nexus import paths, preflight
    from nexus.agent import live

    class Comprado(FakeClient):
        def account(self):
            return {"canTrade": True, "canWithdraw": False, "accountType": "SPOT",
                    "balances": [{"asset": "USDT", "free": "9.5", "locked": "0"}, {"asset": "X", "free": "11.4", "locked": "0"}]}

        def _get(self, path, params=None, signed=False):
            return {"symbols": [s | {"baseAsset": "X"} for s in super()._get(path, params, signed)["symbols"]]}
    now = time.time()
    paths.write_json_atomic(paths.state(live.AUTH_FILE), {"ts": now, "expires": now + 3600, "capital_eur": 20,
                                                           "symbols": ["XUSDT"]})
    r = preflight.run(Comprado(), clock=lambda n: 100)
    assert r["ready"] and r["account"]["in_positions_usdt"] == 11.4          # 9,5 + 11,4 ≥ 20: no es alarma
    class Vacio(Comprado):
        def account(self):
            return {"canTrade": True, "canWithdraw": False, "balances": [{"asset": "USDT", "free": "9.5", "locked": "0"}]}
    r = preflight.run(Vacio(), clock=lambda n: 100)
    assert not r["ready"] and "capital en la cuenta 9.50" in r["problems"][0]


def test_preflight_y_informe_en_modo_automatico():
    from nexus import paths, preflight
    from nexus.agent import live
    from nexus.monitor import auth_where
    now = time.time()
    paths.write_json_atomic(paths.state(live.AUTH_FILE), {"ts": now, "expires": now + 3600, "capital_eur": 20,
                                                           "symbols": [], "auto": 1})
    r = preflight.run(FakeClient(), clock=lambda n: 100)
    assert not r["ready"] and "sin pares promovidos" in r["problems"][0] and r["real_symbols"] == []
    paths.write_json_atomic(paths.state("allocator.json"), {"promoted": {"XUSDT": 1},
                                                            "scores": {"XUSDT": {"score": 0.01, "n": 30, "mean": 0.02}}})
    r = preflight.run(FakeClient(), clock=lambda n: 100)
    assert r["ready"] and r["real_symbols"] == ["XUSDT"] and r["symbols"][0]["sym"] == "XUSDT"
    assert auth_where({"symbols": [], "auto": 2}) == "modo automático (2 pares promovidos)"
    assert auth_where({"symbols": ["XUSDT"]}) == "XUSDT"
    from nexus.monitor import auth_amount
    assert auth_amount({"todo": True, "capital_eur": 100}) == "todo el capital de la subcuenta (hasta 100 €)"
    assert auth_amount({"capital_eur": 20}) == "hasta 20 €"
