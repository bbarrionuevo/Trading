"""Aprendizaje (REGLAS C-5): estadística por categoría neta de comisiones y apagado por evidencia negativa."""
import calendar
import json
import random

import pytest

from tradingagent.intelligence.classifier import VERSION
from tradingagent.research import learn

T0 = calendar.timegm((2026, 11, 2, 12, 0, 0))


def make(n, title, mean, *, sd=0.004, seed=1, sym="SOLUSDT", nlp=True, start=0):
    rng = random.Random(seed)
    events, reactions = [], []
    for i in range(start, start + n):
        eid = f"{title[:6]}{i}"
        e = {"id": eid, "dedup": "new", "is_noise": False, "title": title, "snippet": "", "tier": 2,
             "cluster_sources": 1, "published_at": T0 + i * 600 - 30, "received_at": T0 + i * 600}
        if nlp:
            e["nlp"] = {"category": "listado", "polarity": 0.6, "assets": [sym.removesuffix("USDT")],
                        "version": VERSION}
        events.append(e)
        r = rng.gauss(mean, sd)
        reactions.append({"event_id": eid, "symbol": sym, "r_recv_15m": r, "r_recv_60m": r})
        reactions.append({"event_id": eid, "symbol": "BTCUSDT", "r_recv_15m": 0.05, "r_recv_60m": 0.05})  # referencia
    return events, reactions


def test_cuantil_t_e_intervalo():
    assert learn.t_quantile(0.975, 30) == pytest.approx(2.0423, abs=1e-3)
    assert learn.t_quantile(0.975, 10) == pytest.approx(2.2281, abs=2e-3)
    s = learn.summarize([0.01, 0.02, 0.03])
    assert s["n"] == 3 and s["media"] == pytest.approx(0.02) and s["ic95"][0] < 0.02 < s["ic95"][1]
    assert learn.summarize([])["ic95"] is None and learn.summarize([0.1])["ic95"] is None


def test_apaga_solo_con_evidencia_negativa_y_n_minimo():
    ev_bad, re_bad = make(40, "Binance will list SOL", 0.0)                 # -0,2 % neto: pierde por comisiones
    ev_few, re_few = make(10, "Coinbase will list ETH", -0.02, sym="ETHUSDT", seed=2)   # muy malo pero n < 30
    ev_ok, re_ok = make(40, "Kraken will list XRP", 0.01, sym="XRPUSDT", seed=3)
    for e in ev_ok:
        e["nlp"]["category"] = "etf"
    res = learn.evaluate(ev_bad + ev_few + ev_ok, re_bad + re_few + re_ok)
    assert res["apagadas"] == ["listado:largo"]                             # n=50 en total, IC95 < 0
    st = res["categorias"]["listado:largo"]["r_recv_60m"]
    assert st["n"] == 50 and st["ic95"][1] < 0
    assert res["categorias"]["etf:largo"]["r_recv_15m"]["media"] == pytest.approx(0.008, abs=0.002)
    assert "BTCUSDT" not in json.dumps(res)                                 # la referencia no se mezcla


def test_lados_por_separado_y_copias_ruido_y_errores_afuera():
    events, reactions = make(35, "Exchange hacked, SOL drained", -0.01)    # noticia negativa: corto
    for e in events:
        e["nlp"].update(category="hack", polarity=-0.8)
    events.append({**events[0], "id": "copia", "dedup": "exact"})
    events.append({**events[0], "id": "ruido", "is_noise": True})
    reactions += [{"event_id": "copia", "symbol": "SOLUSDT", "r_recv_60m": 0.5},
                  {"event_id": "ruido", "symbol": "SOLUSDT", "r_recv_60m": 0.5},
                  {"event_id": events[1]["id"], "symbol": "SOLUSDT", "error": "velas incompletas"}]
    res = learn.evaluate(events, reactions)
    st = res["categorias"]["hack:corto"]["r_recv_60m"]
    assert st["n"] == 35 and st["media"] == pytest.approx(0.008, abs=0.002) and res["apagadas"] == []


def test_sin_nlp_guardado_reclasifica_con_la_version_actual():
    events, reactions = make(31, "Binance will list SOL for spot trading", -0.01, nlp=False)
    res = learn.evaluate(events, reactions)
    assert res["apagadas"] == ["listado:largo"] and res["nlp"] == VERSION


def test_update_cada_hora_y_avisa_lo_recien_apagado(tmp_path):
    data = tmp_path / "datos"
    events, reactions = make(31, "Binance will list SOL", -0.01)
    for kind, rows in (("eventos", events[:20]), ("reacciones", reactions)):
        (data / kind).mkdir(parents=True, exist_ok=True)
        (data / kind / "2026-11.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    res, newly = learn.update(data, T0)
    assert res["apagadas"] == [] and newly == []                            # 20 todavía no alcanzan
    with (data / "eventos" / "2026-11.jsonl").open("a", encoding="utf-8") as f:
        f.write("".join(json.dumps(r) + "\n" for r in events[20:]))
    assert learn.update(data, T0 + 600)[1] == []                            # antes de la hora: no recalcula
    res, newly = learn.update(data, T0 + 3600)
    assert newly == ["listado:largo"] and "IC95" in learn.describe(res, "listado:largo")
    assert learn.update(data, T0 + 7200)[1] == []                           # ya avisada
    saved = json.loads((data / "aprendizaje" / "estado.json").read_text(encoding="utf-8"))
    assert saved["apagadas"] == ["listado:largo"] and saved["calculado"] == T0 + 7200


def test_solo_aprende_de_lo_que_se_habria_operado():
    """3-nov en la PC: 46 de 47 muestras del meta-modelo eran noticias que nunca se iban a operar (BTC de
    referencia en noticias ajenas, alfas casi cero). Con expiración casi nunca tocan el TP: κ quedó en 0 y frenó
    todo. Esas no cuentan, ni para el meta-modelo ni para apagar categorías."""
    junk, re_junk = make(40, "Epidemic of hacked freezers spreads across Europe", -0.01, sym="BTCUSDT")
    for e in junk:
        e["nlp"].update(category="hack", polarity=-0.8)                         # clasificación vieja: BTC de referencia
    stale, re_stale = make(35, "Binance will list SOL", 0.0, sym="SOLUSDT", seed=4, start=100)
    for e in stale:
        e["published_at"] = e["received_at"] - 290                           # alfa casi vencido al entrar
    for r in re_junk + re_stale:
        r["tb_corto"] = r["tb_largo"] = 0                                     # expiró sin tocar nada
    res = learn.evaluate(junk + stale, re_junk + re_stale)
    assert "hack:corto" not in res["categorias"] and res["apagadas"] == []
    assert "global" not in res["meta"]                                        # nada que se hubiera operado: κ = 1
    assert learn.meta.MetaModel(res["meta"]).kappa("listado", "largo") == 1.0


def test_meta_modelo_aprende_de_la_triple_barrera_y_solo_baja():
    events, reactions = make(60, "Binance will list SOL", 0.0)
    for e in events:
        e["published_at"] = e["received_at"] - 5                             # noticia fresca: alfa alto
    rng = random.Random(9)
    for r in reactions:
        if r["symbol"] == "SOLUSDT":
            r["tb_largo"] = 1 if rng.random() < 0.2 else -1                  # en la realidad casi nunca tocó el TP
    res = learn.evaluate(events, reactions)
    fitted = res["meta"]["listado:largo"]
    assert fitted["n"] == 60 and fitted["tasa_tp"] < 0.35 and fitted["kappa"] < 0.5
    assert res["meta"]["global"]["n"] == 60
    for r in reactions:
        if r["symbol"] == "SOLUSDT":
            r["tb_largo"] = 1                                                 # siempre ganó: igual no sube de 1
    assert learn.evaluate(events, reactions)["meta"]["listado:largo"]["kappa"] == 1.0
