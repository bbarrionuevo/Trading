import pytest
from decimal import Decimal as D
from nexus.core.fees import CostModel, net_profit, breakeven_spread_pct
from nexus.core.compliance import ComplianceGuard, ComplianceError
from nexus.exchanges.base import ReadOnlyViolation
from nexus.exchanges.binance import BinanceReadOnly

COST = CostModel(D("0.001"), D("0.001"), D("0.005"), D("1"), D("0"))
LIM = {"safety_margin": 0.6, "eu": {"cumulative_eur_cap": 10000, "max_tx_per_day": 10, "max_tx_per_hour": 3},
       "ar": {"monthly_ars_cap": 50000000, "per_tx_ars_cap": 2500000}}


def test_small_spread_loses_money():
    assert not net_profit(100, 1.00, 1.005, COST).executable


def test_breakeven_is_zero_roi():
    be = breakeven_spread_pct(100, COST)
    r = net_profit(100, 1, D(1) * (1 + be / 100), COST)
    assert abs(r.net_roi_pct) < D("0.0001")


def test_ar_per_tx_limit(tmp_path):
    g = ComplianceGuard(LIM, tmp_path / "l.json")
    with pytest.raises(ComplianceError):
        g.check("ARS", 2_000_000)  # > 60% de 2.5M


def test_eu_hourly_frequency(tmp_path):
    g = ComplianceGuard(LIM, tmp_path / "l.json")
    for _ in range(3):
        g.record("EUR", 10)
    with pytest.raises(ComplianceError):
        g.check("EUR", 10)


def test_readonly_blocks_orders():
    with pytest.raises(ReadOnlyViolation):
        BinanceReadOnly("k", "s")._get("/api/v3/order")


def test_ars_scanner_finds_and_rejects():
    from nexus.core.ars_scanner import scan
    q = {"A": {"totalAsk": 1600, "totalBid": 1590}, "B": {"totalAsk": 1650, "totalBid": 1640}}
    ops = scan(q, 160000, 1.0, 0.3)
    assert ops[0].buy_at == "A" and ops[0].sell_at == "B" and ops[0].executable
    assert not scan(q, 160000, 5.0, 0.3)[0].executable   # fee fijo alto la anula


def test_scanner_discards_outlier():
    from nexus.core.ars_scanner import scan
    q = {"A": {"totalAsk": 1640, "totalBid": 1636}, "B": {"totalAsk": 1642, "totalBid": 1638},
         "C": {"totalAsk": 1641, "totalBid": 1637}, "X": {"totalAsk": 1650, "totalBid": 1800}}
    assert all(o.sell_at != "X" and o.buy_at != "X" for o in scan(q, 160000, 1.0))


def test_triangular_detects_mispricing():
    from nexus.discovery.triangular import cycles
    book = {"BTC/EUR": ("BTC", "EUR", 100, 100, 0), "ETH/EUR": ("ETH", "EUR", 10, 10, 0),
            "ETH/BTC": ("ETH", "BTC", 0.11, 0.11, 0)}  # ETH caro en BTC
    best = cycles(book, "EUR")[0]
    assert best["path"] == "EUR>ETH>BTC>EUR" and abs(best["gross_pct"] - 10) < 1e-9


def test_cross_exchange():
    from nexus.discovery.cross import cross
    r = cross({"a": {"X/EUR": (99, 100)}, "b": {"X/EUR": (102, 103)}}, {"a": 0.4, "b": 0.4})[0]
    assert r["buy"] == "a" and r["sell"] == "b" and 1 < r["net_pct"] < 2


def test_backtester_runs_on_synthetic():
    import math
    from nexus.backtest_trading import evaluate
    k = [(i, 0, 100 + 10 * math.sin(i / 20) + 1, 100 + 10 * math.sin(i / 20) - 1,
          100 + 10 * math.sin(i / 20), 1) for i in range(600)]
    res, bh = evaluate(k, folds=3)
    assert len(res) == 3 and all("oos_pct" in r for r in res)


def test_peg_scanner_finds_premium_bid():
    from nexus.discovery.peg import scan_peg
    book = {"EUR/USD": ("EUR", "USD", 1.12, 1.12, 0.2, 1e6, 1e6, 1e9),
            "EURQ/USD": ("EURQ", "USD", 1.155, 1.16, 0.2, 50, 50, 1000)}
    top = scan_peg(book)[0]
    assert top["pair"] == "EURQ/USD" and top["side"] == "sell" and top["net_pct"] > 2.5


def test_peg_ignores_depegged_and_maker_scan():
    from nexus.discovery.peg import scan_peg
    from nexus.discovery.maker import scan_maker
    book = {"EUR/USD": ("EUR", "USD", 1.12, 1.12, 0.2, 1e6, 1e6, 1e9),
            "EURR/EUR": ("EURR", "EUR", 0.6, 0.67, 0.2, 50, 50, 1000)}
    assert scan_peg(book) == []
    m = scan_maker([("X/EUR", 100, 101, 50000), ("Y/EUR", 100, 100.1, 50000)], 0.25)
    assert [r["pair"] for r in m] == ["X/EUR"]


# ---------------------------------------------------------------- riesgo (R-03, R-08, R-09, R-14, R-16)
RISK = {"max_position_eur": 40, "max_total_exposure_eur": 80, "daily_loss_limit_eur": 2,
        "weekly_loss_limit_eur": 4, "max_drawdown_eur": 8, "max_consecutive_stops": 3,
        "stops_cooldown_min": 60, "max_orders_per_minute": 30, "losing_days_window": 7}


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _risk(tmp_path, clock=None):
    from nexus.agent.risk import RiskManager
    return RiskManager(RISK, tmp_path / "risk.json", tmp_path / "KILL", now=clock or Clock())


def test_regresion_defecto1_consultar_no_cuenta_como_orden(tmp_path):
    r = _risk(tmp_path)
    assert all(r.allow_entry(40) for _ in range(1000))


def test_limite_de_ordenes_por_minuto(tmp_path):
    r = _risk(tmp_path)
    for _ in range(30):
        r.record_order()
    assert not r.allow_entry(40) and "órdenes por minuto" in r.last_reason


def test_limites_de_posicion_y_exposicion(tmp_path):
    r = _risk(tmp_path)
    assert not r.allow_entry(41)
    assert not r.allow_entry(40, exposure_now=50)
    assert r.allow_entry(40, exposure_now=40)


def test_perdida_diaria_sobrevive_reinicio(tmp_path):
    clock = Clock()
    r = _risk(tmp_path, clock)
    r.on_close(-1.2, True)
    r.on_close(-0.9, True)
    r2 = _risk(tmp_path, clock)              # "reinicio": nuevo objeto, mismo archivo
    assert not r2.allow_entry(40) and "pérdida del día" in r2.last_reason
    clock.t += 86400                         # día siguiente: se puede volver a operar
    assert r2.allow_entry(40)


def test_stops_seguidos_y_enfriamiento(tmp_path):
    clock = Clock()
    r = _risk(tmp_path, clock)
    for _ in range(3):
        r.on_close(-0.1, True)
    assert not r.allow_entry(40)
    clock.t += 3601
    assert r.allow_entry(40)


def test_drawdown_total_es_permanente(tmp_path):
    clock = Clock()
    r = _risk(tmp_path, clock)
    for i in range(5):
        clock.t += 86400 * 2                 # repartido en días/semanas para no tocar otros límites
        r.on_close(-1.7, False)
    assert r.s["sticky_halt"] and not r.allow_entry(40)
    clock.t += 86400 * 30
    assert not _risk(tmp_path, clock).allow_entry(40)


def test_estrategia_perdedora_7_dias_se_apaga(tmp_path):
    clock = Clock()
    r = _risk(tmp_path, clock)
    for _ in range(8):
        r.on_close(-0.05, False)
        clock.t += 86400
    r.on_close(0.0, False)
    assert r.s["sticky_halt"] and "7 días" in r.s["sticky_halt"]


def test_interruptor_de_emergencia(tmp_path):
    r = _risk(tmp_path)
    (tmp_path / "KILL").touch()
    assert not r.allow_entry(40) and "emergencia" in r.last_reason


# ---------------------------------------------------------------- modelo de cola / estrategia
def test_llenado_solo_si_atraviesa_y_ganancia_real():
    from nexus.live_paper_maker import Book
    b = Book("X", notional=100, maker=0.0)                 # orden de 10 unidades
    b.on_book(10.0, 50, 10.1, 50)
    b.on_trade(10.0, 10_000, True)                         # mucho volumen JUSTO a nuestro precio: no alcanza
    assert b.state == "flat"
    b.on_trade(9.99, 1, True)                              # se operó por debajo: nos atravesó
    assert b.state == "long" and b.entry == 10.0
    b.on_trade(10.1, 10_000, False)
    assert b.state == "long"
    b.on_trade(10.11, 1, False)
    assert b.state == "flat" and b.wins == 1 and b.mark()["pnl_eur"] > 0


def test_regresion_defecto6_a_nuestro_precio_no_alcanza():
    """Con el modelo 4 (cola visible que avanza) el gemelo paper se llenaba de 2 a 30 veces más seguido
    que el real y ganaba más por vuelta en todos los pares (26-oct: AGT +0,60% paper contra −0,23% real).
    Modelo 5: solo cuenta si el precio atraviesa la orden, en el laboratorio y en la cobertura."""
    from nexus.evidence import FILL_MODEL
    from nexus.live_paper_hedge import Side
    from nexus.live_paper_maker import Book
    assert FILL_MODEL == 5
    b = Book("X", notional=40, maker=0.0)
    b.on_book(10.0, 30, 10.01, 1000)
    b.on_trade(10.0, 10_000, True)
    assert b.state == "flat"
    s = Side("buy")
    s.quote(10.0, 0, own=4)                                # aun sin nadie adelante
    assert not s.hit(10.0, 10_000, True) and s.hit(9.99, 1, True)
    v = Side("sell")
    v.quote(10.01, 0, own=4)
    assert not v.hit(10.01, 10_000, False) and v.hit(10.02, 1, False)


def test_regresion_defecto2_no_vende_bajo_entrada_como_maker():
    from nexus.live_paper_maker import Book
    b = Book("X", notional=100, maker=0.0, stop_pct=1.0)
    b.on_book(10.00, 1, 10.01, 1)
    b.on_trade(9.99, 1, True)
    b.on_book(9.95, 1, 9.96, 1)              # cae 0.5%: el ask queda bajo la entrada
    assert b.px == 10.00                     # la venta se mantiene en el piso, no persigue
    b.on_trade(9.97, 5, False)
    assert b.state == "long" and b.rt == 0


def test_piso_incluye_comisiones():
    from nexus.live_paper_maker import Book
    b = Book("X", notional=100, maker=0.1)
    b.on_book(10.0, 1, 10.01, 1)
    b.on_trade(9.99, 1, True)
    assert abs(b.px - 10.02) < 1e-9          # 10 * (1 + 0.2%) > ask 10.01


def test_stop_y_timeout_cuentan_como_perdida():
    from nexus.live_paper_maker import Book
    clock = Clock()
    b = Book("X", notional=100, maker=0.0, taker=0.1, stop_pct=1.0, max_hold_s=60, now=clock)
    b.on_book(10.0, 1, 10.01, 1); b.on_trade(9.99, 1, True)
    b.on_book(9.89, 1, 9.9, 1)
    assert b.stops == 1 and b.wins == 0 and b.mark()["pnl_eur"] < 0
    b.on_book(10.0, 1, 10.01, 1); b.on_trade(9.99, 1, True)
    clock.t += 61
    b.on_book(9.99, 1, 10.0, 1)
    assert b.timeouts == 1 and b.state == "flat"


def test_veto_de_riesgo_cancela_compra(tmp_path):
    from nexus.agent.maker_agent import Gate
    from nexus.live_paper_maker import Book
    r = _risk(tmp_path)
    g = Gate(r)
    b = Book("X", notional=40, gate=g)
    g.books["X"] = b
    b.on_book(10.0, 1, 10.01, 1)
    assert b.px == 10.0
    (tmp_path / "KILL").touch()
    b.on_book(10.0, 1, 10.01, 1)
    assert b.px is None


def test_estado_del_libro_persiste():
    from nexus.live_paper_maker import Book
    b = Book("X", notional=100)
    b.on_book(10.0, 1, 10.01, 1); b.on_trade(9.99, 1, True)
    b2 = Book("X", notional=100)
    b2.load(b.to_dict())
    assert b2.state == "long" and b2.entry == 10.0 and b2.px is None


# ---------------------------------------------------------------- configuración, rutas, credenciales
def test_agente_rechaza_live(tmp_path, monkeypatch):
    import nexus.agent.maker_agent as ma
    p = tmp_path / "agent.yaml"
    p.write_text("live: true\n")
    monkeypatch.setattr(ma, "CFG", p)
    with pytest.raises(SystemExit):
        ma.load_config()


def test_rutas_fuera_del_repo_y_escritura_atomica(tmp_path, monkeypatch):
    from nexus import paths
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "home"))
    assert paths.kill_file() == tmp_path / "home" / "KILL"
    paths.write_json_atomic(paths.state("x.json"), {"a": 1})
    assert paths.read_json(paths.state("x.json"), None) == {"a": 1}


def test_env_no_pisa_variables(tmp_path, monkeypatch):
    from nexus import paths
    (tmp_path / ".env").write_text("NEXUS_T1=archivo\nNEXUS_T2='dos'\n")
    monkeypatch.setenv("NEXUS_T1", "ya")
    monkeypatch.delenv("NEXUS_T2", raising=False)
    paths.load_env(tmp_path / ".env")
    import os
    assert os.environ["NEXUS_T1"] == "ya" and os.environ["NEXUS_T2"] == "dos"


def test_mexc_solo_lectura_y_comisiones():
    from nexus.exchanges.mexc import MexcReadOnly, parse_fee
    c = MexcReadOnly("k", "s")
    for path in ("/api/v3/order", "/api/v3/capital/withdraw", "/api/v3/order/test"):
        with pytest.raises(ReadOnlyViolation):
            c._get(path)
    assert parse_fee({"data": {"makerCommission": 0, "takerCommission": 0.0005}}) == (0.0, 0.05)
    assert parse_fee({"x": 1}) is None


def test_ordenes_solo_en_el_cliente_de_trading_y_retiros_en_ninguna_parte():
    """Fase 6: los endpoints de órdenes existen SOLO en exchanges/mexc_trade.py y exchanges/binance_trade.py
    (este último, solo para cubrir), que solo importa el agente real. Retiros, transferencias, margen y
    futuros no existen en ningún módulo."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "nexus"
    traders = ("mexc_trade.py", "binance_trade.py")
    order = ("/api/v3/order", "/api/v3/openOrders", "AddOrder", "newOrder")
    never = ("withdraw", "Withdraw", "/capital/transfer", "sub-account", "/margin", "/futures", "universalTransfer",
             "/sapi/v1/asset")
    hits = [f"{p.relative_to(root)}:{b}" for p in root.rglob("*.py") for b in order
            if b in p.read_text(encoding="utf-8") and p.name not in traders]
    assert hits == []
    endpoints = ("capital/withdraw", "/0/private/Withdraw", "/sapi/v1/capital/withdraw", "/api/v3/capital/transfer")
    assert [f"{p.name}:{b}" for p in root.rglob("*.py") for b in endpoints if b in p.read_text(encoding="utf-8")] == []
    for t in traders:
        src = (root / "exchanges" / t).read_text(encoding="utf-8")
        assert [b for b in never if b in src] == [], t
    importers = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.py")
                       if any(t[:-3] in p.read_text(encoding="utf-8") for t in traders) and p.name not in traders)
    assert importers == ["agent/maker_agent.py"]


def test_estrategias_no_importan_clientes_de_exchange():
    """R-03: la lógica de estrategia no puede tocar clientes autenticados."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "nexus"
    for name in ("live_paper_maker.py", "research_maker.py", "backtest_trading.py"):
        assert "exchanges" not in (root / name).read_text(encoding="utf-8")


def test_cobertura_detecta_bid_atrasado_y_descarta_tickers_distintos():
    from nexus.discovery.hedge import scan_hedge
    mexc = {"AIUSDT": (0.995, 1.0015, 10, 10), "OTROUSDT": (5.0, 5.01, 1, 1)}
    binance = {"AIUSDT": (1.000, 1.001, 500, 500), "OTROUSDT": (1.0, 1.001, 1, 1)}   # mismo ticker, otro activo
    h = scan_hedge(mexc, binance, 0.0, 0.1)
    assert {x["pair"] for x in h} == {"AIUSDT"}
    top = h[0]
    assert top["side"] == "buy_A_sell_B" and 0.39 < top["net_pct"] < 0.41 and top["hedge_depth_usdt"] == 500
    assert h[1]["side"] == "sell_A_buy_B" and h[1]["net_pct"] < 0


def test_funding_ordena_por_neto_y_filtra_volumen():
    from nexus.discovery.funding import scan_funding, annualize
    rows = [("MEXC", "A_USDT", 0.0003, 3, 5e6), ("MEXC", "B_USDT", 0.001, 3, 10), ("Gate", "C_USDT", -0.0001, 3, 5e6)]
    f = scan_funding(rows)
    assert [x["pair"] for x in f] == ["A_USDT", "C_USDT"] and round(annualize(0.0003), 2) == 32.85
    assert f[0]["net_pct"] > 0 > f[1]["net_pct"]


def test_cobertura_en_vivo_cubre_con_el_precio_siguiente():
    from nexus.live_paper_hedge import HedgedPair
    hp = HedgedPair("X", notional=100, taker_b_pct=0.1, min_edge_pct=0.05)
    hp.on_books(0.990, 10, 1.000, 10, 1.000, 1.001)          # bid MEXC 1% bajo Binance: cotiza compra
    assert hp.buy.px == 0.990 and hp.sell.px is None          # el ask de MEXC no deja margen
    hp.on_trade(0.989, 1, True)                               # sondeo 2: se operó bajo nuestra compra: llenada
    hp.on_books(0.990, 10, 1.000, 10, 1.000, 1.001)           # libros del sondeo 2: todavía no se cubre
    assert hp.fills == 0 and hp.pending                       # se cubre en el sondeo siguiente
    hp.on_books(0.990, 10, 1.000, 10, 0.995, 0.996)           # sondeo 3: Binance cayó antes de cubrir
    assert hp.fills == 1 and hp.adverse == 1
    assert abs(hp.pnl - 100 * (0.995 * 0.999 / 0.990 - 1)) < 1e-9 and hp.pnl > 0


def test_transferencias_rutas_habilitadas_y_costo():
    from nexus.transfers import check, norm
    assert norm("BEP20(BSC)") == "BSC" and norm("Ethereum(ERC20)") == "ETH" and norm("TRC20") == "TRX"
    mexc = [{"coin": "BMT", "networkList": [
        {"netWork": "BEP20(BSC)", "depositEnable": True, "withdrawEnable": True, "withdrawFee": "5"},
        {"netWork": "ERC20", "depositEnable": True, "withdrawEnable": False, "withdrawFee": "40"}]},
        {"coin": "XYZ", "networkList": [{"netWork": "SOL", "depositEnable": False, "withdrawEnable": False,
                                          "withdrawFee": "1"}]}]
    binance = [{"coin": "BMT", "networkList": [
        {"network": "BSC", "depositEnable": True, "withdrawEnable": True, "withdrawFee": "3"},
        {"network": "ETH", "depositEnable": True, "withdrawEnable": True, "withdrawFee": "30"}]},
        {"coin": "XYZ", "networkList": [{"network": "SOL", "depositEnable": True, "withdrawEnable": True,
                                          "withdrawFee": "1"}]}]
    r = check(["BMT", "XYZ"], mexc, binance, prices={"BMT": 0.1})
    assert r["BMT"]["MEXC->Binance"]["net"] == "BSC" and r["BMT"]["MEXC->Binance"]["fee_usdt"] == 0.5
    assert r["BMT"]["Binance->MEXC"]["net"] == "BSC" and r["BMT"]["ok_both_ways"]
    assert r["XYZ"]["MEXC->Binance"] is None and not r["XYZ"]["ok_both_ways"]   # retiro MEXC suspendido


def test_clientes_siguen_sin_endpoints_de_ordenes_ni_retiros():
    from nexus.exchanges.binance import BinanceReadOnly
    from nexus.exchanges.mexc import MexcReadOnly
    for c, bad in ((MexcReadOnly("k", "s"), ("/api/v3/order", "/api/v3/capital/withdraw/apply")),
                   (BinanceReadOnly("k", "s"), ("/api/v3/order", "/sapi/v1/capital/withdraw/apply"))):
        for path in bad:
            with pytest.raises(ReadOnlyViolation):
                c._get(path)


def test_carry_con_historial_real_detecta_intervalo_y_racha():
    from nexus.discovery.funding_history import carry_stats, verdict
    now = 1_800_000_000
    h = [(now - i * 4 * 3600, 0.0005) for i in range(200)][::-1]          # cada 4 h, 0,05% fijo
    s = carry_stats(h, roundtrip_cost_pct=0.2)
    assert s["interval_h"] == 4 and s["d7"]["n"] == 42 and s["d7"]["positive_share"] == 1.0
    assert 105 < s["d7"]["apr_pct"] < 115 and s["d7"]["net_pct"] == round(42 * 0.05 - 0.2, 3)
    assert verdict(s) == "candidato"
    spike = h[:-10] + [(h[-10][0], -0.08)] + h[-9:]                      # un día con -8%: se descarta como candidato
    assert verdict(carry_stats(spike)) != "candidato"
    flip = [(t, r if i % 2 else -0.001) for i, (t, r) in enumerate(h)]  # se da vuelta seguido
    s2 = carry_stats(flip)
    assert s2["worst_24h_pct"] < 0 and verdict(s2) == "descartado"
    assert carry_stats([(now, 0.001)]) is None and verdict(None) == "sin datos"


def test_redes_publicas_de_varios_exchanges():
    from nexus.discovery import networks as nw
    mexc = nw.parse_mexc({"data": [{"currency": "bmt", "coins": [
        {"chain": "BEP20(BSC)", "fee": 5, "is_deposit_enabled": True, "is_withdraw_enabled": False}]}]})
    binance = nw.parse_binance({"data": [{"coin": "BMT", "networkList": [
        {"network": "BSC", "depositEnable": True, "withdrawEnable": True, "withdrawFee": "3"}]}]})
    gate = nw.parse_gate([{"currency": "BMT", "chains": [{"name": "BSC", "deposit_disabled": False,
                                                          "withdraw_disabled": False}]},
                          {"currency": "OLD", "delisted": True}])
    kucoin = nw.parse_kucoin({"data": [{"currency": "BMT", "chains": [
        {"chainName": "BEP20", "isDepositEnabled": False, "isWithdrawEnabled": False, "withdrawalMinFee": "4"}]},
        {"currency": "X", "chains": None}]})
    bitget = nw.parse_bitget({"data": [{"coin": "BMT", "chains": [
        {"chain": "ArbitrumOne", "rechargeable": "true", "withdrawable": "false", "withdrawFee": "1"}]}]})
    htx = nw.parse_htx({"data": [{"currency": "bmt", "chains": [
        {"displayName": "BEP20", "depositStatus": "allowed", "withdrawStatus": "prohibited"}]}]})
    assert mexc["BMT"]["BSC"] == {"deposit": True, "withdraw": False, "fee": 5.0}
    assert nw.status(mexc, "BMT") == "retiro cerrado" and nw.status(binance, "BMT") == "abierto"
    assert nw.status(gate, "BMT") == "abierto" and "OLD" not in gate
    assert nw.status(kucoin, "BMT") == "depósito y retiro cerrados" and nw.status(kucoin, "X") == "depósito y retiro cerrados"
    assert "ARB" in bitget["BMT"] and nw.status(htx, "BMT") == "retiro cerrado"
    assert nw.status(binance, "ZZZ") == "no listada"
    assert nw.best_route(mexc, binance, "BMT") is None                     # MEXC no deja retirar
    assert nw.best_route(binance, mexc, "BMT") == ("BSC", 3.0)


def test_informe_de_viabilidad_se_arma_sin_red():
    from nexus.research_feasibility import base_of, to_markdown
    assert base_of("BMTUSDT") == "BMT" and base_of("ZECUSD1") == "ZEC" and base_of("USDT") is None
    funding = [{"exchange": "MEXC", "pair": "JCT_USDT", "rate_now_pct": 0.07, "cycle_h": 4, "volume_24h_usdt": 5e6,
                "stats": {"interval_h": 4, "days_covered": 30, "worst_24h_pct": -0.1,
                          "d7": {"apr_pct": 80, "positive_share": 0.9, "net_pct": 1.3},
                          "d30": {"apr_pct": 60, "positive_share": 0.85, "net_pct": 4.7}}, "verdict": "candidato"},
               {"exchange": "Gate", "pair": "-", "error": "timeout"}]
    hedge = [{"coin": "BMT", "edge": (1.2, "BMTUSDT", "buy_A_sell_B"), "status": {"Gate": "abierto"}}]
    md = to_markdown(funding, hedge, {"MEXC": "HTTPError"}, ["Gate"])
    assert "**candidato**" in md and "BMT" in md and "error: timeout" in md and "HTTPError" in md


def test_paper_carry_cobra_solo_lo_liquidado_despues_de_abrir():
    from nexus import paper_carry as pc
    t0 = 1_800_000_000
    hist = {"AGT_USDT": [(t0 - 3600, 0.01)], "BAD_USDT": [(t0 - 3600, 0.001)]}
    prices = {"AGT_USDT": (0.999, 1.0, 1.001, 1.002), "BAD_USDT": (1.0, 1.0, 0.99, 0.991)}
    st = pc.step({}, [("AGT_USDT", "candidato"), ("BAD_USDT", "candidato"), ("X_USDT", "inestable")],
                 prices, lambda s: hist.get(s, []), now=t0)
    assert list(st["open"]) == ["AGT_USDT"]                      # BAD: perpetuo 1% bajo el contado
    assert st["open"]["AGT_USDT"]["settlements"] == 0            # el cobro previo a la apertura no cuenta
    hist["AGT_USDT"] += [(t0 + 4 * 3600, 0.001), (t0 + 8 * 3600, 0.002)]
    st = pc.step(st, [("AGT_USDT", "candidato")], {"AGT_USDT": (1.0, 1.001, 1.001, 1.001)},
                 lambda s: hist.get(s, []), now=t0 + 9 * 3600)
    p = st["open"]["AGT_USDT"]
    assert p["settlements"] == 2 and abs(p["funding"] - 30 * 0.003) < 1e-12
    assert p["mark"]["basis"] == round(30 * (1.0 / 1.0 - 1.001 / 1.001), 4)
    assert abs(p["mark"]["net"] - (0.09 - 2 * 30 * 0.0012)) < 1e-4
    hist["AGT_USDT"] += [(t0 + h * 3600, -0.001) for h in (12, 16, 20)]   # tres cobros negativos: cierra
    st = pc.step(st, [("AGT_USDT", "candidato")], {"AGT_USDT": (1.0, 1.001, 1.001, 1.001)},
                 lambda s: hist.get(s, []), now=t0 + 21 * 3600)
    assert st["open"] == {} and st["closed"][0]["sym"] == "AGT_USDT" and st["summary"]["closed"] == 1
    assert "AGT_USDT (cerrada)" in pc.to_markdown(st)


def test_cobertura_con_inventario_limitado_se_bloquea_y_rebalancea():
    from nexus.live_paper_hedge import HedgedPair
    hp = HedgedPair("IOSTUSDT", notional=25, taker_b_pct=0.1, min_edge_pct=0.05, lots=1)
    hp.on_books(0.990, 10, 1.000, 10, 1.000, 1.001, now=0)       # MEXC barato: cotiza compra
    hp.on_trade(0.989, 1, True)                                   # compra llenada: usa el único lote
    assert hp.pos == 1 and not hp.can("buy") and hp.can("sell")
    hp.on_books(0.990, 10, 1.000, 10, 1.000, 1.001, now=60)
    assert hp.buy.px is None and hp.fills == 0                    # sin USDT en MEXC: no cotiza compra
    hp.on_books(0.990, 10, 1.000, 10, 1.000, 1.001, now=180)
    assert hp.blocked_s["buy"] == 120 and hp.blocked_s["sell"] == 0
    hp.rebalance()                                                # transferencia manual del dueño
    assert hp.pos == 0 and hp.rebalances == 1 and hp.transfer_cost == 0.02
    m = hp.mark(3)
    assert m["net_eur"] == round(hp.pnl - 0.02, 4) and m["blocked_min"]["buy"] == 2.0
    libre = HedgedPair("X", lots=None)                            # modelo anterior: sin límite
    assert libre.can("buy") and libre.can("sell") and libre.transfer == 1.0


def test_cobertura_modelo_5_capital_y_riesgo_del_inventario():
    from nexus.live_paper_hedge import HedgedPair, Side, to_markdown
    s = Side("buy")
    s.quote(1.0, 1000, own=25)
    s.quote(1.0, 10, own=25)                                      # los demás cancelaron: quedan 10 adelante
    assert s.queue == 35 and not s.hit(1.0, 10_000, True) and s.hit(0.999, 1, True)   # solo si atraviesa
    hp = HedgedPair("IOSTUSDT", notional=25, taker_b_pct=0.1, min_edge_pct=0.05, lots=1)
    hp.on_books(0.990, 10, 1.000, 10, 1.000, 1.002, now=0)
    hp.on_books(0.990, 10, 1.000, 10, 0.950, 0.952, now=60)      # la moneda cae 5% en Binance
    m = hp.mark(1440)
    assert m["capital_eur"] == 100 and m["inventory_eur"] == round(50 * (0.951 / 1.001 - 1), 4)
    assert m["model"] == 5 and m["net_pct_day"] == 0.0
    md = to_markdown({"ts": 1_800_000_000, "elapsed_min": 60, "notional": 25, "lots": 1, "taker_b_pct": 0.1,
                      "errors": 0, "pairs": [m]})
    assert "Capital por par: 100 €" in md and "riesgo de precio" in md


def test_resumen_de_cobertura_en_espanol():
    from nexus.live_paper_hedge import HedgedPair, to_markdown
    hp = HedgedPair("IOSTUSDT", notional=25, lots=1)
    snap = {"ts": 1_800_000_000, "elapsed_min": 60, "notional": 25, "lots": 1, "taker_b_pct": 0.1, "errors": 0,
            "pairs": [hp.mark(60)]}
    md = to_markdown(snap)
    assert "IOSTUSDT" in md and "bloqueado" in md.lower() and "neto" in md


def test_ordenes_por_minuto_en_el_reporte_no_crecen(tmp_path):
    """El informe mostraba órdenes acumuladas de varios minutos (visto en el PC: 48 con límite 30)."""
    from nexus.agent.risk import RiskManager
    t = [1_800_000_000.0]
    cfg = {"max_position_eur": 40, "max_total_exposure_eur": 80, "daily_loss_limit_eur": 2,
           "weekly_loss_limit_eur": 4, "max_drawdown_eur": 8, "max_consecutive_stops": 3,
           "stops_cooldown_min": 60, "max_orders_per_minute": 30, "losing_days_window": 7}
    r = RiskManager(cfg, state_path=tmp_path / "risk.json", kill_file=tmp_path / "KILL", now=lambda: t[0])
    for _ in range(20):
        r.record_order()
        t[0] += 10                                             # 20 órdenes en 200 s: ~6 por minuto
    assert r.snapshot()["orders_last_min"] <= 6 and len(r.s["orders"]) <= 7


def test_cobertura_elige_pares_por_persistencia_y_conserva_los_ganadores(tmp_path):
    import json
    from nexus.live_paper_hedge import pick_symbols
    now = 1_800_000_000
    disc = tmp_path / "discovery.jsonl"
    rows = [{"ts": now - 3600 * i, "hedge_mexc_binance": [
        {"pair": "TFUELUSDT", "net_pct": 1.0}, {"pair": "ATMUSDT", "net_pct": 0.9 if i % 2 else 0.05},
        {"pair": "MANTAUSDC", "net_pct": 1.1}, {"pair": "TFUELUSDT", "net_pct": 0.8}]} for i in range(10)]
    rows.append({"ts": now - 5 * 86400, "hedge_mexc_binance": [{"pair": "VIEJOUSDT", "net_pct": 3.0}]})
    disc.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    hist = tmp_path / "live_paper_hedge.jsonl"
    snaps = [{"elapsed_min": 330, "pairs": [{"sym": "QUICKUSDT", "net_eur": 4.1}, {"sym": "LUNAUSDT", "net_eur": -0.1}]},
             {"elapsed_min": 5, "pairs": [{"sym": "DCRUSDT", "net_eur": 0.2}]},
             {"elapsed_min": 330, "pairs": [{"sym": "DCRUSDT", "net_eur": 0.7}, {"sym": "QUICKUSDT", "net_eur": 1.0}]}]
    hist.write_text("\n".join(json.dumps(r) for r in snaps) + "\n")
    got = pick_symbols(disc, hist, n=4, keep=2, now=now)
    assert got == ["QUICKUSDT", "DCRUSDT", "TFUELUSDT", "ATMUSDT"]     # ganadores primero; sin USDC ni viejos
    assert pick_symbols(tmp_path / "no", tmp_path / "no", now=now) == []
