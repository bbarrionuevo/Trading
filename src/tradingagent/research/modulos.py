"""Los módulos del agente: cada estrategia con su regla medida, para el tablero y la mejora continua (pedido del dueño,
6-nov: "me gustaría que el dashboard muestre los diferentes módulos de mi programa (o inversiones por ej upbit y el
nuevo de binance) [...] el programa en producción debe guardar la info que nos sirva para aplicar mejora continua e ir
mejorando y visualizando las inversiones y parámetros, siempre todo cuidando la memoria y performance").

`catalog(home)` arma, para cada módulo: nombre, estado (dinero real, simulacro o práctica, según los armados), de dónde
lee, sus parámetros (las constantes del código: lo que de verdad usa) y lo que dijo su estudio (para comparar con lo que
pasa en vivo). El agente lo escribe en `state/modulos.json` (unos KB) cada vez que exporta el tablero; el tablero solo
lo lee: el supervisor de la PC no carga las dependencias de las reglas.
"""
import time
from pathlib import Path

from .. import paths

FILE = "modulos.json"


def _num(x: float) -> str:
    """Un número para el dueño: con coma y sin ceros de más (2,2; 0,3; 16)."""
    return f"{round(float(x), 4):g}".replace(".", ",")


def _armed(home: Path, name: str, now: float) -> dict | None:
    data = paths.read_json(Path(home) / name, None)
    if not isinstance(data, dict) or float(data.get("vence") or 0) < now:
        return None
    return data


def status_of(home: Path, now: float | None = None) -> tuple[set | None, set | None, bool]:
    """(estrategias del dinero real, del simulacro, ¿práctica prendida?). None: todas; set vacío: ninguna."""
    now = time.time() if now is None else now
    main, side = _armed(home, "ARMADO.json", now), _armed(home, "SIMULACRO.json", now)
    real = sim = set()
    if main is not None:
        only = set(main.get("estrategias") or []) or None
        if main.get("simulacro"):
            sim = only
        else:
            real = only
    if side is not None:
        sim = set(side.get("estrategias") or []) or None
    return real, sim, main is None and side is None


def _state(cat: str, real, sim, practice: bool) -> str:
    if real is None or cat in real:
        return "dinero real"
    if sim is None or cat in sim:
        return "simulacro"
    return "práctica" if practice else "apagado"


def _exits(cat: str) -> list[list[str]]:
    from ..decision.policy import EXITS
    p = EXITS.get(cat)
    if p is None or not p.stop:
        return [["Stop y objetivo", "por volatilidad: stop a 1,5σ, objetivo a 3σ (A-5)"]]
    hold = p.hold_s
    hold_txt = f"{_num(hold / 3600)} h" if hold >= 3600 else f"{_num(hold / 60)} min"
    return [["Stop", f"fijo de {_num(p.stop * 100)} %"], ["Salida", f"a las {hold_txt}" if hold >= 3600 else f"a los {hold_txt}"],
            ["Tamaño", f"{_num(p.max_x)} veces el saldo: {_num(round(p.max_x * p.stop * 100, 1))} % al stop"]]


def catalog(home: Path, now: float | None = None) -> list[dict]:
    from ..decision import binance_listings as bl
    from ..decision import delistings as dl
    from ..decision import earnings as er
    from ..decision import launches as la
    from ..decision import upbit_listings as ul
    real, sim, practice = status_of(home, now)
    dsl, vig = dl.BY_CATEGORY["deslistado_corto"], dl.BY_CATEGORY["vigilancia_corto"]
    mods = [
        {"id": ul.CATEGORY, "nombre": "Listados de Upbit al instante", "lado": "largo",
         "fuente": "canal oficial de Upbit en Telegram (@upbit_news), por el relé de Tokio",
         "ia": "lee después de entrar; si no confirma, cierra",
         "parametros": [["Llega a tiempo", f"hasta {_num(ul.MAX_LATE_S)} s del mensaje"],
                        ["Freno de precio", f"no entra si ya subió más de {_num(ul.MAX_RISE * 100)} %"], *_exits(ul.CATEGORY)],
         "estudio": {"texto": "31 listados (may-oct 2026), entrando a 1 s de Telegram: mediana +9,16 %, media "
                              "+12,02 %, 71 % positivas", "mediana_pct": 9.16, "acierto_pct": 71,
                     "donde": "estudios/upbit_salidas.py"}},
        {"id": bl.CATEGORY, "nombre": "Listados de Binance al instante", "lado": "largo",
         "fuente": "canal de anuncios de Binance (WebSocket) y su catálogo de listados",
         "ia": "lee después de entrar; si no confirma, cierra",
         "parametros": [["Llega a tiempo", f"hasta {_num(bl.MAX_LATE_S)} s de publicado"],
                        ["Freno de precio", f"no entra si ya subió más de {_num(bl.MAX_RISE * 100)} %"], *_exits(bl.CATEGORY)],
         "estudio": {"texto": "66 monedas de 58 avisos (2025-2026), comprando a los 2 s: mediana +7,50 %, media "
                              "+18,58 %, 77 % positivas; a los 2,5 s, +2,0 %", "mediana_pct": 7.5, "acierto_pct": 77,
                     "donde": "estudios/binance_listados_segundos.py"}},
        {"id": dsl.category, "nombre": "Deslistados de Binance (corto)", "lado": "corto",
         "fuente": "canal de anuncios de Binance y su catálogo",
         "ia": "lee después de entrar; si no confirma, cierra",
         "parametros": [["Llega a tiempo", f"hasta {_num(dl.MAX_LATE_S)} s de publicado"],
                        ["Freno de precio", f"no entra si ya cayó más de {_num(dsl.max_drop * 100)} %"], *_exits(dsl.category)],
         "estudio": {"texto": "34 monedas de 17 avisos desde 2025: a los 2 s mediana +18,2 %, media +12,8 %, 79 % "
                              "positivas", "mediana_pct": 18.2, "acierto_pct": 79, "donde": "docs/ESTUDIO-DESLISTADOS.md"}},
        {"id": vig.category, "nombre": "Etiqueta de vigilancia de Binance (corto)", "lado": "corto",
         "fuente": "canal de anuncios de Binance y su catálogo",
         "ia": "lee después de entrar; si no confirma, cierra",
         "parametros": [["Llega a tiempo", f"hasta {_num(dl.MAX_LATE_S)} s de publicado"],
                        ["Freno de precio", f"no entra si ya cayó más de {_num(vig.max_drop * 100)} %"], *_exits(vig.category)],
         "estudio": {"texto": "31 monedas de 14 avisos desde 2025: a los 2 s, si no cayó 2 %, mediana +3,6 %, 85 % "
                              "positivas", "mediana_pct": 3.6, "acierto_pct": 85, "donde": "docs/ESTUDIO-DESLISTADOS.md"}},
        {"id": er.CATEGORY, "nombre": "Resultados de empresas con impulso", "lado": "largo o corto",
         "fuente": "8-K de resultados de la SEC (EDGAR), en los perpetuos TradFi de Binance",
         "ia": "no hace falta: la dirección la da el precio a los 2 min",
         "parametros": [["Cuándo", f"a los {_num(er.ENTRY_DELAY_S / 60)} min del 8-K, hasta los {_num(er.MAX_LATE_S / 60)} min"],
                        ["Impulso mínimo", f"{_num(er.THRESHOLD * 100)} % a favor"], *_exits(er.CATEGORY)],
         "estudio": {"texto": "88 resultados de 36 empresas: mediana +1,01 %, media +1,17 %, 39 de 60 positivas",
                     "mediana_pct": 1.01, "acierto_pct": 65, "donde": "docs/ESTUDIO-RESULTADOS.md"}},
        {"id": la.CATEGORY, "nombre": "Perpetuos nuevos que ya bajaban (corto)", "lado": "corto",
         "fuente": "catálogo de contratos de Binance (cada 60 s)",
         "ia": "no hace falta: no hay texto que leer",
         "parametros": [["Cuándo", f"a los {_num(la.ENTRY_DELAY_S / 60)} min del inicio, hasta los {_num(la.MAX_LATE_S / 60)} min"],
                        ["Condición", "el precio quedó en o debajo del primero"], *_exits(la.CATEGORY)],
         "estudio": {"texto": "521 lanzamientos desde 2023; los 250 que ya bajaban: mediana +7,4 %, media +4,2 %, 62 % "
                              "positivas", "mediana_pct": 7.4, "acierto_pct": 62, "donde": "docs/ESTUDIO-LANZAMIENTOS.md"}},
        {"id": "listado", "nombre": "Listados con la IA (los que llegan tarde)", "lado": "largo",
         "fuente": "anuncios de Binance, sitio de Upbit y otros", "ia": "lee antes de entrar",
         "parametros": [["Llega a tiempo", "hasta 120 s"], *_exits("listado")],
         "estudio": {"texto": "listados de Binance esperando a la IA (3,5 s): mediana −0,47 %, media +8,9 % (pocos "
                              "pagan mucho)", "mediana_pct": -0.47, "acierto_pct": 48,
                     "donde": "estudios/binance_listados_segundos.py"}},
        {"id": "otras", "nombre": "Noticias generales (con la IA)", "lado": "largo o corto",
         "fuente": "medios, la Fed, la SEC y las demás fuentes", "ia": "lee antes de entrar",
         "parametros": _exits("otras"), "estudio": None},
    ]
    for m in mods:
        m["estado"] = _state(m["id"], real, sim, practice)
    return mods


def write_catalog(home: Path, now: float | None = None) -> list[dict]:
    """Lo escribe el agente (tiene las reglas cargadas); el tablero lo lee."""
    now = time.time() if now is None else now
    mods = catalog(home, now)
    paths.write_json_atomic(Path(home) / "state" / FILE, {"generado": round(now), "modulos": mods})
    return mods
