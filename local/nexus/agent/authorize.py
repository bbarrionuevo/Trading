"""Autorización LOCAL para operar con dinero real (R-02). Solo la puede dar una persona en esta PC.

  python -m nexus.agent.authorize --todo --dias 7 --pares BONKUSDT [--calibracion]
      (--todo: siempre TODO el capital de la subcuenta, hasta 100 €; las ganancias se reinvierten.
       --capital N fija un monto menor)
  python -m nexus.agent.authorize --todo --dias 7 --auto 2 --calibracion
      (modo automático: el agente opera hasta N pares PROMOVIDOS por el laboratorio, los de mejor cota
      inferior fuera de muestra; cambia de par solo sin posición; el capital se reparte entre ellos)
  python -m nexus.agent.authorize --cobertura QUICKUSDT,DCRUSDT --lote 10 --dias 7 --calibracion
      (cobertura MEXC→Binance: maker en MEXC y cobertura al instante en Binance; hace falta la clave de
       Binance en .env, SOLO spot, sin retiro y con la IP de esta PC, y USDT y moneda en los dos exchanges)
  python -m nexus.agent.authorize --desbloquear-cobertura QUICKUSDT   (tras revisar saldos de un par bloqueado)
  python -m nexus.agent.authorize --estado
  python -m nexus.agent.authorize --revocar        (deja de abrir posiciones; las abiertas salen normal)
  python -m nexus.agent.authorize --desbloquear BONKUSDT   (tras revisar en MEXC un par bloqueado: lo que
      haya de esa moneda queda como residuo no gestionado y el par arranca de cero; si querés que no
      quede nada, vendelo a mano antes)

Topes: hasta 100 € (el capital del dueño) y 7 días por autorización. Sin --calibracion exige que la evidencia en
paper cumpla todos los criterios de R-15. Con --calibracion se permite antes, como medición de los
llenados reales (excepción de REGLAS.md): mismo gestor de riesgo y mismos límites.
Claude nunca ejecuta este comando: la frase la escribe el dueño.
"""
import argparse
import time

from .. import paths
from .hedge_live import HEDGE_AUTH, HEDGE_UNBLOCK, MAX_HEDGE_PAIRS, MAX_LOT_USDT, MIN_LOT_USDT, hedge_authorization
from .live import AUTH_FILE, MAX_AUTO_PAIRS, MAX_CAPITAL_EUR, MAX_DAYS, UNBLOCK_FILE, authorization

PHRASE = "AUTORIZO OPERAR CON DINERO REAL"


def validate(capital, days, symbols, calibration, status, auto=0):
    """Lista de motivos por los que no se puede autorizar (vacía = se puede)."""
    errs = []
    if auto and not 1 <= auto <= MAX_AUTO_PAIRS:
        errs.append(f"--auto admite de 1 a {MAX_AUTO_PAIRS} pares")
    if auto and symbols:
        errs.append("usar --pares o --auto, no los dos")
    if not 0 < capital <= MAX_CAPITAL_EUR:
        errs.append(f"capital {capital} fuera de rango (máximo {MAX_CAPITAL_EUR:g} €)")
    if not 0 < days <= MAX_DAYS:
        errs.append(f"vigencia {days} días fuera de rango (máximo {MAX_DAYS})")
    if (not symbols and not auto) or any(not s.endswith("USDT") or not s.isalnum() or s != s.upper() for s in symbols):
        errs.append("pares inválidos: usar símbolos de MEXC como BONKUSDT")
    ev = (status or {}).get("evidence") or {}
    met, total = ev.get("criteria_met", 0), ev.get("criteria_total", 0)
    if not calibration and not (total and met == total):
        errs.append(f"la evidencia en paper cumple {met}/{total} criterios de R-15: sin --calibracion no se autoriza")
    if (status or {}).get("risk", {}).get("sticky_halt"):
        errs.append("hay un halt permanente activo en el paper")
    return errs


def validate_hedge(symbols, lot, days, calibration):
    errs = []
    if not symbols or len(symbols) > MAX_HEDGE_PAIRS or any(
            not s.endswith("USDT") or not s.isalnum() or s != s.upper() for s in symbols):
        errs.append(f"--cobertura: de 1 a {MAX_HEDGE_PAIRS} pares como QUICKUSDT")
    if not MIN_LOT_USDT <= lot <= MAX_LOT_USDT:
        errs.append(f"--lote {lot:g} fuera de rango ({MIN_LOT_USDT:g} a {MAX_LOT_USDT:g} USDT por orden)")
    if not 0 < days <= MAX_DAYS:
        errs.append(f"vigencia {days} días fuera de rango (máximo {MAX_DAYS})")
    if not calibration:
        errs.append("la cobertura todavía no tiene criterios de evidencia propios: autorizar con --calibracion")
    return errs


def authorize_hedge(a, ask, out, now):
    symbols = [s.strip().upper() for s in a.cobertura.split(",") if s.strip()]
    errs = validate_hedge(symbols, a.lote, a.dias, a.calibracion)
    if errs:
        out("No se puede autorizar la cobertura:\n- " + "\n- ".join(errs))
        return 2
    out(f"Vas a autorizar DINERO REAL en la cobertura MEXC→Binance: {', '.join(symbols)}, órdenes de "
        f"{a.lote:g} USDT, durante {a.dias:g} días (calibración). Usa el USDT y la moneda que haya en los dos "
        "exchanges; los límites de pérdida y el KILL siguen valiendo.")
    if ask("¿La clave de BINANCE es solo spot, tiene el RETIRO DESACTIVADO y está limitada a la IP de esta PC? "
           "(escribí SI): ").strip() != "SI":
        out("Sin confirmar la clave no se autoriza (R-01). No se cambió nada.")
        return 2
    if ask(f'Escribí exactamente "{PHRASE}": ').strip() != PHRASE:
        out("Frase incorrecta. No se cambió nada.")
        return 2
    paths.write_json_atomic(paths.state(HEDGE_AUTH), {"ts": now, "expires": now + a.dias * 86400, "symbols": symbols,
                                                      "lot_usdt": a.lote, "calibracion": True,
                                                      "sin_retiro_confirmado": True})
    out(f"Cobertura autorizada hasta {time.strftime('%Y-%m-%d %H:%M', time.localtime(now + a.dias * 86400))}. "
        "El agente la toma solo en menos de un minuto.")
    return 0


def main(argv=None, ask=input, out=print, now=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capital", type=float)
    ap.add_argument("--todo", action="store_true")
    ap.add_argument("--dias", type=float, default=7)
    ap.add_argument("--pares", default="")
    ap.add_argument("--auto", type=int, default=0)
    ap.add_argument("--calibracion", action="store_true")
    ap.add_argument("--estado", action="store_true")
    ap.add_argument("--revocar", action="store_true")
    ap.add_argument("--desbloquear", default="")
    ap.add_argument("--cobertura", default="")
    ap.add_argument("--desbloquear-cobertura", default="")
    ap.add_argument("--lote", type=float, default=10.0)
    a = ap.parse_args(argv)
    now = now or time.time()
    path = paths.state(AUTH_FILE)
    if a.cobertura:
        return authorize_hedge(a, ask, out, now)
    if a.desbloquear_cobertura:
        syms = [s.strip().upper() for s in a.desbloquear_cobertura.split(",") if s.strip()]
        if ask(f"¿Revisaste los saldos de {', '.join(syms)} en MEXC y Binance y cubriste a mano lo que haya quedado "
               "sin cubrir? (escribí SI): ").strip() != "SI":
            out("No se cambió nada.")
            return 2
        paths.write_json_atomic(paths.state(HEDGE_UNBLOCK), {"ts": now, "symbols": syms})
        out("Pedido registrado: la cobertura se desbloquea en el próximo ciclo.")
        return 0
    if a.desbloquear:
        syms = [s.strip().upper() for s in a.desbloquear.split(",") if s.strip()]
        if ask(f"¿Revisaste en MEXC {', '.join(syms)} (órdenes y saldo)? Lo que haya quedará como residuo (escribí SI): "
               ).strip() != "SI":
            out("No se cambió nada.")
            return 2
        paths.write_json_atomic(paths.state(UNBLOCK_FILE), {"ts": now, "symbols": syms})
        out("Pedido registrado: el agente desbloquea en su próximo ciclo.")
        return 0
    if a.todo and a.capital is None:
        a.capital = MAX_CAPITAL_EUR                       # todo lo de la subcuenta, hasta el tope
    if a.estado or (a.capital is None and not a.revocar):
        cur, hedge = authorization(now), hedge_authorization(now)
        out(f"Autorización vigente: {cur}" if cur else "No hay autorización vigente: el agente opera solo en paper.")
        out(f"Cobertura vigente: {hedge}" if hedge else "Sin cobertura autorizada.")
        return 0
    if a.revocar:                                        # revoca TODO el dinero real: market making y cobertura
        for p in (path, paths.state(HEDGE_AUTH)):
            cur = paths.read_json(p, None)
            if cur:
                paths.write_json_atomic(p, cur | {"expires": now, "revoked": now})
        out("Revocada: no se abren posiciones reales nuevas ni se cotiza la cobertura; las abiertas siguen su "
            "salida normal y lo ya llenado se cubre. Para cerrar todo ya: escribir 3 en el archivo KILL.")
        return 0
    symbols = [s.strip().upper() for s in a.pares.split(",") if s.strip()]
    errs = validate(a.capital, a.dias, symbols, a.calibracion, paths.read_json(paths.state("status.json"), {}), a.auto)
    if errs:
        out("No se puede autorizar:\n- " + "\n- ".join(errs))
        return 2
    where = ', '.join(symbols) if symbols else f"los {a.auto} mejores pares promovidos (modo automático)"
    amount = f"TODO el capital de la subcuenta (hasta {a.capital:g} €)" if a.todo else f"hasta {a.capital:g} €"
    out(f"Vas a autorizar DINERO REAL: {amount} en {where} durante {a.dias:g} días"
        f"{' (calibración, antes de cumplir R-15)' if a.calibracion else ''}.")
    out("Límites que siguen valiendo: pérdida diaria 2 €, semanal 4 €, total 8 € (halt), KILL en cualquier momento.")
    if ask("¿La clave de MEXC tiene el RETIRO DESACTIVADO y está limitada a la IP de esta PC? (escribí SI): ").strip() != "SI":
        out("Sin confirmar la clave no se autoriza (R-01). No se cambió nada.")
        return 2
    if ask(f'Escribí exactamente "{PHRASE}": ').strip() != PHRASE:
        out("Frase incorrecta. No se cambió nada.")
        return 2
    paths.write_json_atomic(path, {"ts": now, "expires": now + a.dias * 86400, "capital_eur": a.capital,
                                   "symbols": symbols, "auto": a.auto, "todo": a.todo, "calibracion": a.calibracion,
                                   "sin_retiro_confirmado": True})
    out(f"Autorizado hasta {time.strftime('%Y-%m-%d %H:%M', time.localtime(now + a.dias * 86400))}. "
        "El agente lo toma solo en menos de un minuto: no hace falta reiniciarlo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
