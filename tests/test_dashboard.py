"""Tablero local (dashboard.py): muestra lo importante, escapa los titulares de afuera, es de solo lectura y
solo responde a esta PC (Host 127.0.0.1/localhost, CSP sin scripts en línea)."""
import json
import time
import urllib.error
import urllib.request

import pytest

from tradingagent import dashboard, paths

NOW = 1_793_000_000.0
EVIL = '<script>alert("x")</script> Bitcoin ETF approved'


def fixture_home(home, *, beat_age=5, kill=False, halted=""):
    st = home / "state"
    paths.write_json_atomic(st / "heartbeat.json", {
        "ts": NOW - beat_age, "mode": "PAPER", "uptime_min": 125,
        "sources": {"total": 6, "ok": 5, "failing": ["sec"], "off": {}},
        "news": {"aceptadas": 40, "stale": 3, "duplicadas": 7, "ruido": 2}, "loop_lag": {"p99_ms": 4},
        "alerts": {"health": "ok", "queued": 0}, "journal": {"written": 321, "dropped": 0},
        "trader": {"llm": {"day": "2026-11-03", "usd": 0.0123, "calls": 9, "failures": 1, "tope_usd": 0.3},
                   "ia_evitada": 14}})
    paths.write_json_atomic(st / "supervisor.json", {
        "version": "abcdef1234", "arranques": 2, "tests_version": "abcdef1",
        "ultima_revision": {"ts": NOW - 60, "resultado": "al_dia"},
        "actualizaciones": [{"ts": NOW - 3600, "de": "1111111", "a": "abcdef1", "resultado": "ok",
                             "titulo": "Merge <b>PR</b>"}],
        "caidas": [{"ts": NOW - 7200, "codigo": 1, "vivo_s": 30}]})
    base = home / "paper-vivo" / "mexc-futuros"
    paths.write_json_atomic(base / "estado.json", {
        "mercado": "mexc-futuros", "initial": "120", "cash": "95.5", "equity": "121.75", "fees": "0.12",
        "created_at": NOW - 86400, "strategy_tag": "eventos-v1", "updated_at": NOW - 10,
        "stats": {"operaciones": 2, "ganadas": 1, "perdidas": 1, "pnl_neto": "1.5"},
        "risk": {"halted": halted, "losses_in_row": 1},
        "positions": {"BTC_USDT": {"side": "corto", "qty": "-0.0002", "avg_price": "100000", "stop": "101500",
                                   "tp": "97000", "until": NOW + 1800, "p": 0.63, "alpha": 0.3,
                                   "category": "hack", "notional": "20", "entry_ts": NOW - 600}},
        "marks": {"BTC_USDT": "99000"}})
    (base / "operaciones").mkdir(parents=True)
    trades = [{"tipo": "entrada", "symbol": "ETH_USDT", "lado": "largo", "price": "4000", "ts": NOW - 9000},
              {"tipo": "salida", "symbol": "ETH_USDT", "lado": "largo", "motivo": "take_profit", "price": "4100",
               "pnl_neto": "2.5", "notional_entrada": "40", "ts": NOW - 8000, "strategy_tag": "eventos-v1"},
              {"tipo": "salida", "symbol": "SOL_USDT", "lado": "corto", "motivo": "stop_loss", "price": "150",
               "pnl_neto": "-1.0", "notional_entrada": "30", "ts": NOW - 4000, "strategy_tag": "eventos-v1"}]
    (base / "operaciones" / "2026-11.jsonl").write_text("\n".join(json.dumps(t) for t in trades) + "\n")
    (base / "decisiones").mkdir(parents=True)
    rows = [{"ts": NOW - 100, "source": "coindesk", "title": EVIL, "symbol": "BTC_USDT", "decision": "no_entrar",
             "motivos": ["alfa_obsoleta:400s", "p_baja:0.41"], "signal": {"category": "etf", "polarity": 0.6}},
            {"ts": NOW - 50, "source": "theblock", "title": "Exchange hacked", "symbol": "BTC_USDT",
             "decision": "entrar", "motivos": [], "signal": {"category": "hack", "polarity": -0.8}}]
    (base / "decisiones" / "2026-11.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    paths.write_json_atomic(home / "datos" / "aprendizaje" / "estado.json",
                            {"apagadas": ["listado:largo"], "meta": {"global": {"kappa": 0.8, "n": 41}},
                             "calculado": NOW - 500})
    (home / "logs").mkdir(parents=True, exist_ok=True)
    (home / "logs" / "tests.txt").write_text("....F\nFAILED tests/test_x.py::test_y\n1 failed, 164 passed in 31.2s\n")
    (home / "logs" / "agent.log").write_text(
        json.dumps({"ts": NOW - 30, "level": "WARNING", "logger": "tradingagent.llm", "msg": "Claude no respondió "
                    "(timeout)"}, ensure_ascii=False) + "\n"
        + json.dumps({"ts": NOW - 20, "level": "INFO", "logger": "x", "msg": "todo bien"}) + "\n", encoding="utf-8")
    if kill:
        (home / "KILL").write_text("")


def test_los_errores_del_tablero_ocultan_los_secretos_del_entorno(tmp_path, monkeypatch):
    home = tmp_path / "h"
    fixture_home(home)
    monkeypatch.setenv("UNA_API_KEY", "clave-secreta-de-prueba-123")
    with (home / "logs" / "agent.log").open("a", encoding="utf-8") as f:
        for i in range(3):
            f.write(json.dumps({"ts": NOW - 10 + i, "level": "ERROR", "logger": "x",
                                "msg": f"falló {i} con clave-secreta-de-prueba-123"}) + "\n")
    errs = dashboard.gather(home, NOW)["errores"]
    assert len(errs) == 4 and all("clave-secreta" not in x["mensaje"] for x in errs)
    assert errs[0]["mensaje"] == "falló 2 con «oculto»"


def test_junta_todo_lo_importante(tmp_path):
    home = tmp_path / "h"
    fixture_home(home)
    s = dashboard.gather(home, NOW)
    assert s["estado"] == "ok" and s["latido_hace_s"] == 5
    a = s["cuentas"][0]
    assert a["equity"] == 121.75 and a["retorno_pct"] == pytest.approx(1.458, abs=1e-3)
    assert a["operaciones"] == 2 and a["acierto_pct"] == 50.0 and a["pnl_neto"] == 1.5
    pos = a["posiciones"][0]
    assert pos["pnl_abierto"] == pytest.approx(0.2) and pos["vence_en_s"] == 1800       # corto: ganó 1000 × 0,0002
    assert [round(p[1], 2) for p in a["curva"]] == [120.0, 122.5, 121.5, 121.75]
    assert dict(a["motivos_frecuentes"]) == {"alfa_obsoleta": 1, "p_baja": 1} and a["entradas_recientes"] == 1
    assert s["tests"]["failed"] == 1 and s["tests"]["passed"] == 164 and not s["tests"]["ok"]
    assert [x["nivel"] for x in s["errores"]] == ["WARNING"]
    assert s["llm"]["calls"] == 9 and s["llm"]["tope_usd"] == 0.3 and s["llm"]["evitadas"] == 14   # el de la PC
    assert s["aprendizaje"]["apagadas"] == ["listado:largo"]


@pytest.mark.parametrize("kw,estado", [({"beat_age": 500}, "caido"), ({"kill": True}, "pausado"),
                                       ({"halted": "drawdown 20%"}, "halt")])
def test_el_estado_dice_si_esta_listo_para_operar(tmp_path, kw, estado):
    home = tmp_path / "h"
    fixture_home(home, **kw)
    assert dashboard.gather(home, NOW)["estado"] == estado
    assert dashboard.gather(tmp_path / "vacio", NOW)["estado"] == "sin_datos"


def test_html_escapa_lo_de_afuera_y_muestra_las_secciones(tmp_path):
    home = tmp_path / "h"
    fixture_home(home)
    page = dashboard.page(dashboard.gather(home, NOW))
    assert "<script>alert" not in page and "&lt;script&gt;alert(&quot;x&quot;)" in page
    assert "Merge &lt;b&gt;PR&lt;/b&gt;" in page and "<b>PR</b>" not in page
    for text in ("Funcionando", "Cuenta de práctica · mexc-futuros", "Posiciones abiertas", "Últimas decisiones",
                 "Por qué no entra", "Salud del agente", "Supervisor y actualizaciones", "1 failed, 164 passed",
                 "Claude no respondió", "listado:largo", "data-curve="):
        assert text in page, text
    assert page.count("<script") == 1 and '<script src="/app.js">' in page              # ningún script en línea
    assert "primera operación cerrada" in dashboard.chart([[NOW, 120.0]], 120.0)


def get(url, host=None, method="GET"):
    req = urllib.request.Request(url, method=method, headers={"Host": host} if host else {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


def test_servidor_solo_lectura_y_solo_esta_pc(tmp_path):
    home = tmp_path / "h"
    fixture_home(home, beat_age=0)
    paths.write_json_atomic(home / "state" / "heartbeat.json",
                            {**paths.read_json(home / "state" / "heartbeat.json", {}), "ts": time.time()})
    srv = dashboard.start(home, 0)
    try:
        assert srv.server_address[0] == "127.0.0.1"
        base = dashboard.url(srv)
        code, headers, body = get(base + "/")
        assert code == 200 and "Funcionando" in body
        assert "script-src 'self'" in headers["Content-Security-Policy"] and headers["X-Frame-Options"] == "DENY"
        assert headers["Cache-Control"] == "no-store"
        code, _, body = get(base + "/api/estado")
        assert code == 200 and json.loads(body)["cuentas"][0]["mercado"] == "mexc-futuros"
        code, _, body = get(base + "/fragmento")
        assert code == 200 and "Salud del agente" in body and "<html" not in body
        code, _, body = get(base + "/detalle")                                         # el detalle de todo
        assert code == 200 and "Salud del agente" in body and 'data-src="/fragmento"' in body
        code, _, body = get(base + "/resumen")                                         # la pantalla simple, sin <html>
        assert code == 200 and "ver el detalle" in body and "<html" not in body
        assert get(base + "/app.js")[0] == 200 and get(base + "/app.css")[0] == 200
        assert get(base + "/", host=f"localhost:{srv.server_address[1]}")[0] == 200
        assert get(base + "/", host="evil.example")[0] == 403                          # DNS rebinding: afuera
        assert get(base + "/api/estado", host="evil.example:80")[0] == 403
        assert get(base + "/", method="POST")[0] == 405                                # nada se cambia desde acá
        assert get(base + "/", method="DELETE")[0] == 405
        assert get(base + "/../.env")[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()


def test_formatos():
    assert dashboard.fmt_age(None) == "—" and dashboard.fmt_age(42) == "42 s" and dashboard.fmt_age(600) == "10 min"
    assert dashboard.fmt_age(7300) == "2 h 01 min" and dashboard.fmt_age(3 * 86400) == "3 días"
    assert dashboard.fmt_num(1234.5) == "1 234.50" and dashboard.fmt_num(-0.5, 2, True) == "-0.50"
    assert dashboard.fmt_num(None) == "—" and dashboard.fmt_ts("x") == "—"
    assert dashboard.tests_summary(paths.home() / "nada.txt") is None


def test_leer_no_traba_al_agente_que_guarda(tmp_path, monkeypatch):
    import os
    import threading

    f = tmp_path / "estado.json"
    paths.write_json_atomic(f, {"v": 1})
    reader = dashboard._open_shared(f)                  # el tablero lee justo cuando el agente guarda
    assert json.loads(reader.read()) == {"v": 1}
    threading.Timer(0.08, reader.close).start()         # y suelta enseguida
    paths.write_json_atomic(f, {"v": 2})                # en Windows reintenta hasta que suelta
    assert dashboard.read_json(f, None) == {"v": 2}

    real, denied = os.replace, []

    def busy(src, dst):                                 # Windows: "acceso denegado" mientras alguien lee
        if len(denied) < 3:
            denied.append(dst)
            raise PermissionError(5, "Access is denied")
        real(src, dst)

    monkeypatch.setattr(paths.os, "replace", busy)
    paths.write_json_atomic(f, {"v": 3})
    assert len(denied) == 3 and dashboard.read_json(f, None) == {"v": 3}
    monkeypatch.setattr(paths.os, "replace", real)
    log = tmp_path / "agent.log"
    log.write_text("uno\n")
    with dashboard._open_shared(log):
        log.rename(tmp_path / "agent.log.1")             # la rotación del log tampoco se traba
    assert dashboard.read_json(f, None) == {"v": 3} and dashboard.read_json(tmp_path / "no.json", "x") == "x"
    assert dashboard.read_text(tmp_path / "no.txt") is None


def test_nunca_usa_un_puerto_que_ya_usa_otro_programa(tmp_path):
    import socket

    other = socket.socket()
    other.bind(("0.0.0.0", 0))                          # otro programa del dueño (como el que usa el 8765)
    other.listen()
    busy = other.getsockname()[1]
    try:
        srv = dashboard.start_free(tmp_path, busy, tries=5)
        try:
            assert srv.server_address[1] != busy           # no lo comparte: toma el siguiente libre
            u = dashboard.announce(tmp_path, srv)
            assert json.loads((tmp_path / "state" / "tablero.json").read_text(encoding="utf-8"))["url"] == u
            assert f"url={u}" in (tmp_path / "tablero.html").read_text(encoding="utf-8")
            assert dashboard.is_ours(u)
            assert not dashboard.is_ours(f"http://127.0.0.1:{busy}/", timeout=0.5)   # el otro no es el tablero
        finally:
            srv.shutdown()
            srv.server_close()
    finally:
        other.close()
    assert dashboard.PORT != 8765


def test_arriba_solo_las_cuentas_que_corren_y_las_viejas_abajo(tmp_path):
    """4-nov: el tablero mostraba primero el simulacro viejo de MEXC (MEXC ya no corría). Corren la práctica del
    mercado del latido y la cuenta del armado; las demás quedan abajo, como inactivas."""
    home = tmp_path / "home"
    now = 1_793_900_000
    for folder in ("paper-vivo", "simulacro"):
        for mkt in ("binance-futuros", "mexc-futuros"):
            paths.write_json_atomic(home / folder / mkt / "estado.json", {
                "mercado": mkt, "initial": "100", "cash": "100", "updated_at": now - 60,
                "stats": {"operaciones": 0, "ganadas": 0, "perdidas": 0, "pnl_neto": "0"}})
    paths.write_json_atomic(home / "state" / "heartbeat.json", {"ts": now - 5, "trader": {"mercado": "binance-futuros"},
                                                                "real": {"mercado": "binance-futuros", "simulacro": True}})
    paths.write_json_atomic(home / "ARMADO.json", {"venues": ["binance-futuros"], "simulacro": True, "modo": "local",
                                                   "capital_usdt": 100, "vence": now + 86400})
    s = dashboard.gather(home, now=now)
    assert [(a["tipo"], a["mercado"]) for a in s["cuentas"]] == [("simulacro", "binance-futuros"),
                                                                ("practica", "binance-futuros")]
    assert {(a["tipo"], a["mercado"]) for a in s["inactivas"]} == {("simulacro", "mexc-futuros"),
                                                                   ("practica", "mexc-futuros")}
    html_ = dashboard.fragment(s)
    assert "Simulacro del dinero real (precios reales, ninguna orden) · binance-futuros" in html_
    assert "Cuentas que ya no corren" in html_ and html_.index("binance-futuros") < html_.index("mexc-futuros")
