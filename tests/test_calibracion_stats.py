from tradingagent.research.calibracion import _stats


def test_p90_nunca_menor_que_la_mediana():
    # 6-nov: binance.ws con 2 avisos (41,2 s y 1,17 s) daba mediana 21,19 y "p90" 1,17.
    for xs in ([41.2, 1.17], [17.28, 3.0, 5.0], [39.7, 37.4], [1.0]):
        s = _stats(xs)
        assert s["p90"] >= s["mediana"]
    assert _stats([41.2, 1.17])["p90"] == 41.2


def test_p90_rango_mas_cercano():
    assert _stats([float(i) for i in range(1, 11)])["p90"] == 9.0
    assert _stats([float(i) for i in range(1, 101)])["p90"] == 90.0
    assert _stats([None, None]) is None
