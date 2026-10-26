"""Tablero local: datos que muestra, lectura incremental del diario y servidor de solo lectura."""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest


def _write(path, lines):
    with open(path, "a", encoding="utf-8") as f:
        for x in lines:
            f.write((x if isinstance(x, str) else json.dumps(x)) + "\n")


def _trade(ts, pnl, strategy, model=None, **kw):
    from nexus import evidence
    model = evidence.FILL_MODEL if model is None else model
    return {"ts": ts, "sym": "BONKUSDT", "pnl": pnl, "kind": "maker", "hold_s": 30, "model": model,
            "strategy": strategy, "lab": False, "vol_1m_pct": 0.1} | kw


@pytest.fixture
def dash():
    from nexus import dashboard
    dashboard.TRADES.update(path=None)
    dashboard.RESEARCH.update(ts=0, data={}, error=None)
    return dashboard


def test_estado_curvas_pausas_kill_y_avisos(dash):
    from nexus import paths
    now = 1_800_000_000
    paths.write_json_atomic(paths.state("status.json"), {"ts": now - 30, "version": "abc1234",
                                                          "evidence": {"strategy": "v3"}})
    paths.write_json_atomic(paths.state("lab_paused.json"), {"AIUSDT": now + 3600, "OLDUSDT": now - 1})
    _write(paths.state("trades.jsonl"), [
        _trade(now - 500, 5.0, "v2", model=2),               # modelo de llenado viejo: no cuenta
        _trade(now - 400, 0.10, "v3"), _trade(now - 300, -0.04, "control"), "{roto",
        _trade(now - 200, 0.05, "v3"), {"ts": now - 100, "model": 99}])  # sin pnl: se ignora
    paths.kill_file().write_text("1")
    _write(paths.logs("agent.log"), ["2026-10-25 10:00:00 INFO nada",
                                     "2026-10-25 10:00:01 WARNING clave " + "A" * 40 + " filtrada"])
    d = dash.state_payload(now=now)
    assert d["status_age_s"] == 30 and d["kill_switch"] is True
    assert d["curves"]["vigente"] == [[now - 400, 0.1], [now - 200, 0.15]]
    assert d["curves"]["control"] == [[now - 300, -0.04]]
    assert [t["pnl"] for t in d["last_trades"]] == [0.05, -0.04, 0.10]
    assert list(d["paused"]) == ["AIUSDT"]
    assert len(d["warnings"]) == 1 and "[oculto]" in d["warnings"][0] and "A" * 40 not in d["warnings"][0]
    json.dumps(d)                                             # serializable tal cual


def test_diario_incremental_linea_partida_y_reinicio(dash):
    from nexus import paths
    p = paths.state("trades.jsonl")
    _write(p, [_trade(1, 1.0, "v3")])
    assert dash.trades("v3")[0]["vigente"] == [[1, 1.0]]
    line = json.dumps(_trade(2, 0.5, "v3"))
    with open(p, "a", encoding="utf-8") as f:
        f.write(line[:10])                                    # el agente está escribiendo la línea
    assert dash.trades("v3")[0]["vigente"] == [[1, 1.0]]
    with open(p, "a", encoding="utf-8") as f:
        f.write(line[10:] + "\n")
    assert dash.trades("v3")[0]["vigente"] == [[1, 1.0], [2, 1.5]]
    p.write_text(json.dumps(_trade(3, 0.2, "v3")) + "\n")    # archivo reemplazado (más corto): relee
    assert dash.trades("v3")[0]["vigente"] == [[3, 0.2]]


def test_curva_se_achica_pero_conserva_el_ultimo_punto(dash):
    from nexus import paths
    _write(paths.state("trades.jsonl"), [_trade(i, 0.01, "v3") for i in range(1, 1001)])
    pts = dash.trades("v3", max_points=100)[0]["vigente"]
    assert len(pts) <= 101 and pts[0][0] == 1 and pts[-1] == [1000, 10.0]


def test_investigacion_conserva_lo_ultimo_si_falla(dash):
    radar = {"ts": 5, "rulo": {"routes": [{"route": "MEP"}]}, "p2p_spreads": [{"platform": str(i)} for i in range(9)],
             "defi": [{"project": "a", "bluechip": True}, {"project": "b", "bluechip": False}]}
    files = {"radar.json": radar, "paper_xfunding.json": {"open": {}}, "paper_carry.json": {"open": {}},
             "stable_mm.json": {"pairs": [{"pair": "USDT/ARS"}]}}
    dash.refresh_research(fetch=files.__getitem__)
    r = dash.research_payload()
    assert r["error"] is None and r["radar_ts"] == 5 and len(r["p2p"]) == 5 and [x["project"] for x in r["defi"]] == ["a"]

    def broken(name):
        raise ConnectionError(name)
    dash.refresh_research(fetch=broken)
    r = dash.research_payload()
    assert "radar.json: ConnectionError" in r["error"] and r["radar_ts"] == 5 and r["stable"] == [{"pair": "USDT/ARS"}]


def test_servidor_y_botones_seguros(dash):
    server = dash.Server(("127.0.0.1", 0), dash.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    urlopen = urllib.request.build_opener(urllib.request.ProxyHandler({})).open   # sin proxy del sistema
    try:
        with urlopen(base + "/") as r:
            html = r.read().decode()
            assert r.status == 200 and "default-src 'self'" in r.headers["Content-Security-Policy"]
            assert r.headers["Cache-Control"] == "no-store"
        for path in ("/api/state", "/api/research", "/api/health"):
            with urlopen(base + path) as r:
                assert r.status == 200 and isinstance(json.loads(r.read()), dict)
        with pytest.raises(urllib.error.HTTPError) as e:
            urlopen(base + "/../.env")
        assert e.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as e:   # POST solo en /api/action
            urlopen(urllib.request.Request(base + "/api/state", data=b"x", method="POST"))
        assert e.value.code == 404
        port = server.server_address[1]
        with urlopen(base + "/api/session") as r:
            token = json.loads(r.read())["token"]

        def post(body, headers=None):
            h = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"} | (headers or {})
            req = urllib.request.Request(base + "/api/action", data=json.dumps(body).encode(), headers=h, method="POST")
            try:
                with urlopen(req) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, None
        assert post({"action": "estado", "token": token})[0] == 200
        assert post({"action": "kill", "token": "otro"})[0] == 403                          # sin la sesión
        assert post({"action": "kill", "token": token}, {"Origin": "https://malo.com"})[0] == 403   # otra página
        assert post({"action": "kill", "token": token}, {"Host": f"malo.com:{port}"})[0] == 403     # DNS rebinding
        assert post({"action": "kill", "token": token}, {"Content-Type": "text/plain"})[0] == 415   # formulario
        with pytest.raises(urllib.error.HTTPError) as e:
            urlopen(urllib.request.Request(base + "/api/session", headers={"Host": f"malo.com:{port}"}))
        assert e.value.code == 403
        assert not dash.paths.kill_file().exists()                   # ninguno de los pedidos malos hizo nada
        code, r = post({"action": "kill", "params": {"nivel": 1}, "token": token})
        assert code == 200 and r["ok"] and dash.paths.kill_file().read_text().startswith("1 dueño")
        code, r = post({"action": "reanudar", "token": token})
        assert r["ok"] and not dash.paths.kill_file().exists()
    finally:
        server.shutdown()
        server.server_close()
    # la página no carga nada de afuera: ni scripts, ni estilos, ni datos
    assert "<script src" not in html and "<link" not in html
    assert [u for u in ("http://", "https://") if html.count(u) != html.count(u + "www.w3.org/2000/svg")] == []


def test_escucha_solo_en_esta_pc(dash, monkeypatch):
    seen = {}

    class Fake:
        def __init__(self, addr, handler):
            seen["addr"] = addr
            raise RuntimeError("alcanza")
    monkeypatch.setattr(dash, "Server", Fake)
    with pytest.raises(RuntimeError):
        dash.main(9999)
    assert seen["addr"] == ("127.0.0.1", 9999)


def test_puerto_ocupado_espera_y_sale(dash):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    out = []
    t = threading.Thread(target=lambda: out.append(dash.main(s.getsockname()[1], busy_wait=0)), daemon=True)
    try:
        t.start()
        t.join(10)                                            # nunca colgar los tests del PC
        assert out == [1]
    finally:
        s.close()


def test_se_cierra_si_cambia_la_version(dash, monkeypatch):
    versions = iter(["aaa", "bbb"])
    monkeypatch.setattr(dash, "installed_version", lambda: next(versions))
    monkeypatch.setattr(dash, "refresh_research", lambda: None)
    stop = threading.Event()
    t = threading.Thread(target=dash._background, args=("aaa", stop, 0.01), daemon=True)
    t.start()
    t0 = time.time()
    while not stop.is_set() and time.time() - t0 < 5:
        time.sleep(0.05)
    assert stop.is_set()
