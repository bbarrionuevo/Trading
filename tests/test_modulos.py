"""Los módulos del agente en el tablero y los datos para la mejora continua (pedido del dueño, 6-nov): el catálogo
(research/modulos.py), el resumen por módulo de cada cuenta (dashboard.by_module), la sección del tablero y el camino
del precio de cada posición (shadow.track)."""
import json

from tradingagent import dashboard, paths
from tradingagent.execution.live import arming
from tradingagent.research import modulos, shadow

NOW = 1_794_000_000.0


def test_el_camino_del_precio_de_una_posicion():
    meta = {"avg_price": "100", "entry_ts": NOW}
    for dt, px in ((1, 101), (5, 112), (31, 108), (61, 95), (301, 104)):
        shadow.track(meta, px, 1, NOW + dt)
    tr = shadow.trajectory(meta)
    assert tr["max_pct"] == 12.0 and tr["min_pct"] == -5.0 and tr["t_max_s"] == 5.0
    assert tr["en_pct"] == {"30": 8.0, "60": -5.0, "300": 4.0}                 # a los 30 s, 1 y 5 min
    short = {"avg_price": "100", "entry_ts": NOW}
    shadow.track(short, 90, -1, NOW + 2)                                        # corto: bajar es ganar
    assert shadow.trajectory(short)["max_pct"] == 10.0
    assert shadow.trajectory({"avg_price": "100"}) is None                      # sin camino todavía


def test_los_datos_extra_de_una_decision():
    class Watch:
        def total_ms(self):
            return 41
    got = shadow.resolved({"aviso": {"demora_s": 1.17}, "_reloj": Watch()})
    assert got == {"aviso": {"demora_s": 1.17}, "camino_ms": 41} and shadow.resolved(None) == {}


def test_resumen_por_modulo():
    decisions = [
        {"decision": "entrar", "signal": {"category": "listado_upbit"}, "aviso": {"demora_s": 1.0}, "camino_ms": 30},
        {"decision": "no_entrar", "signal": {"category": "listado_upbit"}, "aviso": {"demora_s": 3.4},
         "motivos": ["listado_upbit_tarde:3.4s"]},
        {"decision": "no_entrar", "signal": {"category": "listado_binance"}, "motivos": ["ya_subio:+9.10%"]},
        {"decision": "no_entrar", "signal": {"category": "etf"}, "motivos": ["p_baja:0.4"]}]
    trades = [
        {"tipo": "entrada", "category": "listado_upbit", "symbol": "SOLUSDT"},
        {"tipo": "salida", "category": "listado_upbit", "symbol": "SOLUSDT", "retorno_neto": "0.12", "pnl_neto": "3.6",
         "motivo": "expiracion", "ts": NOW, "trayectoria": {"max_pct": 18.0, "t_max_s": 14.0, "en_pct": {"30": 15.0}}},
        {"tipo": "salida", "category": "listado_upbit", "symbol": "ETHUSDT", "retorno_neto": "-0.05", "pnl_neto": "-1.5",
         "motivo": "stop_loss", "ts": NOW + 1}]
    got = dashboard.by_module(trades, decisions, {"XRPUSDT": {"category": "listado_binance"}})
    up = got["listado_upbit"]
    assert (up["decisiones"], up["entradas"], up["cerradas"], up["ganadas"], up["acierto_pct"]) == (2, 1, 2, 1, 50)
    assert up["mediana_pct"] == 3.5 and up["pnl"] == 2.1 and up["pico_mediano_pct"] == 18.0
    assert up["demora_mediana_s"] == 2.2 and up["camino_mediano_ms"] == 30
    assert up["motivos"] == [("listado_upbit_tarde", 1)] and up["ultimas"][0]["symbol"] == "ETHUSDT"
    assert got["listado_binance"]["abiertas"] == 1 and got["otras"]["decisiones"] == 1      # lo demás, "otras"


def test_el_estado_de_cada_modulo_sale_de_los_armados(tmp_path):
    states = {m["id"]: m["estado"] for m in modulos.catalog(tmp_path)}
    assert set(states.values()) == {"práctica"}                                  # sin armado: la práctica
    arming.arm(tmp_path, 3, "k", arming.phrase(3), venue="binance-futuros", strategies=("listado_upbit", "listado_binance"))
    arming.arm(tmp_path, 3, "", "", simulacro=True, capital=100.0, venue="binance-futuros", mode="local",
               file=arming.SIM_FILE)
    states = {m["id"]: m["estado"] for m in modulos.catalog(tmp_path)}
    assert states["listado_upbit"] == states["listado_binance"] == "dinero real"   # como Tokio desde el 6-nov
    assert states["deslistado_corto"] == states["otras"] == "simulacro"
    binance = next(m for m in modulos.catalog(tmp_path) if m["id"] == "listado_binance")
    assert ["Llega a tiempo", "hasta 2,2 s de publicado"] in binance["parametros"]  # las constantes del código
    assert ["Tamaño", "0,3 veces el saldo: 3 % al stop"] in binance["parametros"]


def _account(home, folder, *, real=False):
    base = home / folder / "binance-futuros"
    paths.write_json_atomic(base / "estado.json", {
        "mercado": "binance-futuros", "initial": "100", "cash": "100", "equity": "103", "created_at": NOW - 86400,
        "updated_at": NOW, "stats": {"operaciones": 1, "ganadas": 1, "perdidas": 0, "pnl_neto": "3"}, "positions": {}})
    (base / "operaciones").mkdir(parents=True)
    (base / "operaciones" / "2026-11.jsonl").write_text(json.dumps(
        {"tipo": "salida", "category": "listado_upbit", "symbol": "SOLUSDT", "retorno_neto": "0.0911",
         "pnl_neto": "3.1234", "motivo": "expiracion", "ts": NOW - 60, "lado": "largo",
         "trayectoria": {"max_pct": 16.4, "t_max_s": 15.0, "en_pct": {"30": 12.0, "900": 9.1}}}) + "\n")
    (base / "decisiones").mkdir(parents=True)
    (base / "decisiones" / "2026-11.jsonl").write_text(json.dumps(
        {"decision": "entrar", "signal": {"category": "listado_upbit"}, "ts": NOW - 900,
         "aviso": {"demora_s": 1.05}, "camino_ms": 31}) + "\n")


def test_el_tablero_muestra_cada_modulo_y_no_publica_la_plata_real(tmp_path):
    arming.arm(tmp_path, 3, "k", arming.phrase(3), venue="binance-futuros", strategies=("listado_upbit", "listado_binance"))
    arming.arm(tmp_path, 3, "", "", simulacro=True, capital=100.0, venue="binance-futuros", mode="local",
               file=arming.SIM_FILE)
    _account(tmp_path, "real", real=True)
    _account(tmp_path, "simulacro")
    modulos.write_catalog(tmp_path, NOW)
    local = dashboard.gather(tmp_path, NOW, include_real=True)
    html = dashboard.fragment(local)
    assert "Módulos (estrategias)" in html and "Listados de Upbit al instante" in html
    assert 'class="badge real">dinero real' in html and "3 s en un alta en wones" in html
    assert "+9.11 %" in html and "+16.4 % a los 15 s" in html and "1.05 s" in html and "31 ms" in html
    assert "3.12" in html                                                        # el PnL real se ve solo en la PC
    public = dashboard.gather(tmp_path, NOW, include_real=False)
    assert all(a.get("tipo") != "real" for a in public["cuentas"])               # G-8: la cuenta real no se publica
    html = dashboard.fragment(public)
    assert "dinero real" in html and "Listados de Upbit al instante" in html      # el estado sí (sin montos)
    assert "simulacro" in html


def test_la_pantalla_simple_muestra_el_dinero_real_y_cada_modulo_con_su_estado(tmp_path):
    """El dueño, 7-nov: todo en una ventana, el dinero real y cómo varía, y un rectángulo por módulo con ganancias,
    operaciones y si es dinero real o simulacro."""
    arming.arm(tmp_path, 3, "k", arming.phrase(3), venue="binance-futuros", strategies=("listado_upbit", "listado_binance"))
    arming.arm(tmp_path, 3, "", "", simulacro=True, capital=100.0, venue="binance-futuros", mode="local",
               file=arming.SIM_FILE)
    _account(tmp_path, "real", real=True)
    _account(tmp_path, "simulacro")
    modulos.write_catalog(tmp_path, NOW)
    html = dashboard.simple_fragment(dashboard.gather(tmp_path, NOW, include_real=True))
    assert 'class="hero">103.00 <span class="unit">USDT' in html                 # el saldo real, la cifra principal
    assert "Ganancia de las operaciones" in html and "+3.12" in html and "data-curve" in html
    assert 'Listados de Upbit</strong><span class="badge real">DINERO REAL' in html
    assert 'Deslistados de Binance</strong><span class="badge sim">SIMULACRO' in html
    assert html.index("Listados de Binance") < html.index("Deslistados de Binance")   # primero los de plata real
    assert "1 cerradas · 1 ganadas" in html and "ver el detalle" in html
    public = dashboard.simple_fragment(dashboard.gather(tmp_path, NOW, include_real=False))
    assert "no se publica" in public and "103.00" not in public                  # G-8
