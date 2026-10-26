"""Supervisión determinista (sin IA): vigilante horario e informe diario, publicados como issues.

GitHub envía mail al dueño del repo por cada comentario del bot en un issue asignado a él.
  python -m nexus.monitor watchdog PC_STATUS.json      -> issue "nexus: alertas"
  python -m nexus.monitor digest PC_STATUS.json DATA_DIR -> issue "nexus: informe diario"
Variables: GITHUB_TOKEN, GITHUB_REPOSITORY, NEXUS_OWNER (usuario a asignar).
"""
import json
import os
import sys
import time
from pathlib import Path

ALERT_TITLE = "nexus: alertas"
DIGEST_TITLE = "nexus: informe diario"


# ------------------------------------------------------------------ lógica pura (testeable)
def auth_where(auth):
    """Dónde opera el dinero real según la autorización: pares fijos o modo automático."""
    syms = ", ".join(auth.get("symbols") or [])
    auto = f"modo automático ({auth['auto']} pares promovidos)" if auth.get("auto") else ""
    return " + ".join(x for x in (syms, auto) if x) or "ningún par"


def auth_amount(auth):
    """Cuánto capital real está autorizado: un monto fijo o todo lo de la subcuenta (--todo)."""
    if auth.get("todo"):
        return f"todo el capital de la subcuenta (hasta {auth.get('capital_eur'):g} €)"
    return f"hasta {auth.get('capital_eur')} €"


def alerts(pc, now=None, ci_conclusion=None):
    """Lista de alertas a partir del latido del PC. pc=None significa que nunca reportó."""
    now = now or time.time()
    a = []
    if ci_conclusion and ci_conclusion != "success":
        a.append(f"El CI de main está en '{ci_conclusion}': el PC no instalará versiones nuevas.")
    if not pc:
        return a + ["El PC todavía no publicó ningún latido (¿instalación pendiente o sin credenciales de git?)."]
    age_h = (now - pc.get("ts", 0)) / 3600
    if age_h > 2:
        a.append(f"El PC no reporta hace {age_h:.1f} h (¿apagado, sin internet o tarea detenida?).")
    st = pc.get("status") or {}
    if st and pc.get("ts", 0) - st.get("ts", 0) > 600:
        a.append("El agente no actualiza su estado hace más de 10 min (¿detenido o colgado?).")
    if not st:
        a.append("El agente nunca escribió su estado.")
    risk = st.get("risk") or {}
    if risk.get("sticky_halt"):
        a.append(f"HALT PERMANENTE: {risk['sticky_halt']}. Requiere tu revisión y rearme manual.")
    if st.get("errors_last_hour", 0) > 120:
        a.append(f"{st['errors_last_hour']} errores de datos en la última hora.")
    up = pc.get("update") or {}
    if str(up.get("result", "")).startswith("rollback"):
        a.append(f"Actualización revertida: {up['result']} (candidata {up.get('candidate')}).")
    for r in up.get("reasons", []):
        if "rutas protegidas" in r:
            a.append(f"Versión {up.get('candidate')} espera TU aprobación en el PC: {r}")
    if pc.get("kill_switch"):
        a.append("Interruptor de emergencia ACTIVO (no se abren posiciones).")
    lv = st.get("live") or {}
    for b in lv.get("books") or []:
        if b.get("blocked"):
            a.append(f"DINERO REAL: {b['sym']} BLOQUEADO ({b['blocked']}). Revisá en MEXC y desbloqueá con "
                     "`python -m nexus.agent.authorize --desbloquear` (ver OPERACION_WINDOWS.md).")
    for x in (st.get("hedge") or {}).get("pairs") or []:
        if x.get("blocked"):
            a.append(f"COBERTURA: {x['sym']} BLOQUEADA ({x['blocked']}). Revisá saldos en MEXC y Binance y desbloqueá "
                     "con `python -m nexus.agent.authorize --desbloquear-cobertura` (ver OPERACION_WINDOWS.md).")
    pf = (pc.get("real") or {}).get("preflight") or {}
    if pf.get("auth") and not pf.get("ready"):
        a.append("DINERO REAL autorizado pero el chequeo previo falla: "
                 + "; ".join((pf.get("problems") or []) + [f"{x['sym']}: {', '.join(x['problems'])}"
                                                          for x in pf.get("symbols") or [] if not x.get("ok")])[:400])
    if (lv.get("risk") or {}).get("sticky_halt"):
        a.append(f"DINERO REAL en HALT PERMANENTE: {lv['risk']['sticky_halt']} (rearme: risk --rearmar --real).")
    return a


def digest(pc, research_md=None, now=None):
    now = now or time.time()
    L = [f"## Informe {time.strftime('%Y-%m-%d', time.gmtime(now))} (UTC)", ""]
    st = (pc or {}).get("status") or {}
    if not st:
        L.append("**PC:** sin datos del agente todavía.")
    else:
        risk = st.get("risk", {})
        L += [f"**Agente** versión `{st.get('version')}` · modo **{st.get('mode')}** · comisiones maker/taker "
              f"{st.get('fees')} · errores/h {st.get('errors_last_hour')}",
              f"**Riesgo:** PnL hoy {risk.get('pnl_day', 0):+.2f} € · semana {risk.get('pnl_week', 0):+.2f} € · "
              f"acumulado {risk.get('pnl_total', 0):+.2f} € · stops seguidos {risk.get('stops_row', 0)} · "
              f"halt: {risk.get('sticky_halt') or 'no'} · bloqueo actual: {st.get('risk_block') or 'ninguno'}", "",
              "| par | estado | PnL € (incl. abierta) | realizado € | vueltas | ganadoras | stops | timeouts |",
              "|---|---|---|---|---|---|---|---|"]
        for b in st.get("books", []):
            L.append(f"| {b['sym']} | {b['state']} | {b['pnl_eur']:+.4f} | {b.get('realized_eur', 0):+.4f} | "
                     f"{b['roundtrips']} | {b['wins']} | {b['stops']} | {b.get('timeouts', 0)} |")
        ev = st.get("evidence") or {}
        if ev.get("criteria"):
            L += ["", f"### Criterios para pasar a dinero real: {ev['criteria_met']}/{ev['criteria_total']}",
                  "| criterio | estado | detalle |", "|---|---|---|"]
            L += [f"| {c['name']} | {'✅' if c['ok'] else '⏳'} | {c['detail']} |" for c in ev["criteria"]]
        kinds = ev.get("by_kind") or {}
        if kinds:
            names = {"maker": "venta con ganancia (maker)", "stop": "stop", "timeout": "tiempo máximo"}
            L += ["", "**De dónde sale el resultado:** " + " · ".join(
                f"{names.get(k, k)} {v['n']} vueltas {v['total']:+.2f} €" for k, v in kinds.items())]
            if ev.get("excluded_old_model"):
                L.append(f"(Excluidas {ev['excluded_old_model']} vueltas del modelo de llenado anterior, con sesgo.)")
            prev = ev.get("previous") or {}
            if ev.get("strategy"):
                L.append(f"Estrategia vigente: **{ev['strategy']}** (los criterios se miden solo con ella). "
                         f"Versiones anteriores: {prev.get('n', 0)} vueltas, {prev.get('total', 0):+.2f} €.")
        reg = ev.get("regimes") or {}
        if reg.get("vigente") or reg.get("control"):
            L += ["", "### Resultado por clima de mercado (vigente vs. control sin filtro, mismos datos)",
                  "| dimensión | clima | vigente: vueltas · total € | control: vueltas · total € |", "|---|---|---|---|"]
            for dim in ("volatilidad", "tendencia", "mercado"):
                cats = sorted(set((reg.get("vigente") or {}).get(dim, {})) | set((reg.get("control") or {}).get(dim, {})))
                for c in cats:
                    a = (reg.get("vigente") or {}).get(dim, {}).get(c, {})
                    b = (reg.get("control") or {}).get(dim, {}).get(c, {})
                    L.append(f"| {dim} | {c} | {a.get('n', 0)} · {a.get('total', 0):+.2f} | {b.get('n', 0)} · {b.get('total', 0):+.2f} |")
        hg = st.get("hedge")
        if hg:
            L += ["", "### Cobertura MEXC→Binance (dinero real)",
                  f"Resultado {hg.get('pnl', 0):+.4f} USDT · " + " · ".join(
                      f"{x['sym']}: {x['fills']} cubiertas, {x['pnl']:+.4f}"
                      + (f", BLOQUEADO: {x['blocked']}" if x.get("blocked") else "") for x in hg.get("pairs") or [])]
        lv = st.get("live")
        if lv:
            rr, rk, auth = lv.get("real") or {}, lv.get("risk") or {}, lv.get("auth") or {}
            L += ["", "### Dinero real (ejecuciones del exchange)",
                  f"Autorización: {(auth_amount(auth) + ' en ' + auth_where(auth)) if auth else 'vencida o revocada (solo cierra lo abierto)'}"
                  f"{' · calibración' if auth.get('calibracion') else ''}. "
                  f"Vueltas reales {rr.get('n', 0)} · total {rr.get('total', 0) or 0:+.2f} · {rr.get('days', 0)} días · "
                  f"riesgo real hoy {rk.get('pnl_day', 0):+.2f} / semana {rk.get('pnl_week', 0):+.2f} · "
                  f"bloqueo: {lv.get('risk_block') or 'ninguno'} · KILL nivel {lv.get('kill_level', 0)}"]
            sel = pc.get("selector") or {}
            if sel:
                ch = ", ".join(f"{s} ({c['strategy']})" for s, c in (sel.get("choices") or {}).items()) or "ninguna con ventaja hoy"
                L.append(f"Selector: opera {ch}; descartadas: {', '.join(sel.get('banned') or {}) or 'ninguna'}.")
            for b in lv.get("books") or []:
                L.append(f"- {b['sym']}: {b['state']}, posición {b.get('pos_qty')}, residuo {b.get('dust')}, "
                         f"PnL {b['pnl_eur']:+.4f}{' · BLOQUEADO: ' + b['blocked'] if b.get('blocked') else ''}")
        rec = st.get("recommended") or []
        L += ["", "**Par recomendado para el dinero real** (promovido con mejor cota inferior, fuera de muestra): "
              + (", ".join(f"{r['sym']} (cota {r['score']}, {r['n']} vueltas, media {r['mean']})" for r in rec)
                 or "ninguno todavía: no hay pares con evidencia suficiente.")]
        cal = ev.get("calibration") or {}
        if cal:
            L += ["", "### Calibración: mismo par, paper contra real (media en % del monto de la vuelta)",
                  "| par | horas | paper: vueltas · por hora · media % | real: vueltas · por hora · media % |",
                  "|---|---|---|---|"]
            for s, c in cal.items():
                p, r = c["paper"], c["real"]
                L.append(f"| {s} | {c['hours']} | {p.get('n', 0)} · {p.get('per_hour')} · {p.get('mean')} | "
                         f"{r.get('n', 0)} · {r.get('per_hour')} · {r.get('mean')} |")
        var = ev.get("variants") or {}
        if var:
            L += ["", "### Variantes en el laboratorio (últimas 24 h, mismos pares y datos)",
                  "| variante | vueltas | total € | media € | IC95 desde |", "|---|---|---|---|---|"]
            for k, x in var.items():
                mark = " (vigente)" if k == ev.get("strategy") else ""
                L.append(f"| {k}{mark} | {x.get('n', 0)} | {x.get('total', 0):+.2f} | {x.get('mean')} | {x.get('ci95_low')} |")
        sel = ev.get("selection") or {}
        if sel:
            L += ["", "### Selección de pares por evidencia (laboratorio, fuera de muestra)",
                  "Promovido = par elegido por su cota inferior con vueltas ANTERIORES; si no supera a "
                  "explorando, elegir por evidencia no sirve.",
                  "| selección | vueltas | total € | media € | IC95 desde |", "|---|---|---|---|---|"]
            for k, x in sel.items():
                L.append(f"| {k} | {x.get('n', 0)} | {x.get('total', 0):+.2f} | {x.get('mean')} | {x.get('ci95_low')} |")
        per = ev.get("per_symbol") or {}
        if per:
            rows = sorted(per.items(), key=lambda kv: (kv[1].get("n", 0) >= 30, kv[1].get("mean") or -9),
                          reverse=True)[:12]
            L += ["", "### Pares (operados y laboratorio) — vueltas cerradas en paper",
                  "| par | tipo | vueltas | total € | media € | IC95 desde | ganadoras |", "|---|---|---|---|---|---|---|"]
            for sym, x in rows:
                L.append(f"| {sym} | {'lab' if x.get('lab') else 'operado'} | {x.get('n')} | {x.get('total')} | "
                         f"{x.get('mean')} | {x.get('ci95_low')} | {x.get('win_rate')} |")
            best_lab = [k for k, x in rows if x.get("lab") and x.get("n", 0) >= 30 and (x.get("ci95_low") or -1) > 0]
            if best_lab:
                L.append(f"\n**Candidatos del laboratorio con evidencia positiva:** {', '.join(best_lab)}")
        lab = st.get("lab") or []
        if lab:
            L.append(f"\nLaboratorio activo: {', '.join(b['sym'] for b in lab)}")
        L += ["", "Nivel de evidencia: **paper** (simulado con datos reales). Criterios aprobados el 24-oct: "
                  "≥7 días, ≥300 vueltas, IC95% > 0, ≥5/7 días positivos, robustez a +0,02% de comisión y "
                  "+200 ms; luego 20 € reales una semana antes de los 100 €."]
    up = (pc or {}).get("update") or {}
    if up:
        L += ["", f"**Actualización:** {up.get('result')} {up.get('version') or up.get('candidate') or ''} "
                  f"{'· ' + '; '.join(up.get('reasons', [])) if up.get('reasons') else ''}"]
    if research_md:
        L += ["", "<details><summary>Radar de oportunidades e investigación automática</summary>", "", research_md,
              "</details>"]
    return "\n".join(L)


# ------------------------------------------------------------------ GitHub (efectos)
class Issues:
    def __init__(self):
        import requests
        self.r = requests.Session()
        self.r.headers.update({"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                               "Accept": "application/vnd.github+json"})
        self.base = f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}"
        self.owner = os.environ.get("NEXUS_OWNER")

    def find(self, title):
        for state in ("open", "closed"):
            for i in self.r.get(f"{self.base}/issues", params={"state": state, "per_page": 100}).json():
                if i.get("title") == title and "pull_request" not in i:
                    return i
        return None

    def ensure(self, title, body):
        i = self.find(title)
        if not i:
            data = {"title": title, "body": body}
            if self.owner:
                data["assignees"] = [self.owner]
            i = self.r.post(f"{self.base}/issues", json=data).json()
        return i

    def last_comment(self, number):
        c = self.r.get(f"{self.base}/issues/{number}/comments", params={"per_page": 100}).json()
        return c[-1]["body"] if c else ""

    def commented_on(self, number, day):
        """True si el issue ya tiene un comentario del día `day` (AAAA-MM-DD, UTC)."""
        c = self.r.get(f"{self.base}/issues/{number}/comments", params={"per_page": 100}).json()
        return any(str(x.get("created_at", "")).startswith(day) for x in c)

    def comment(self, number, body):
        self.r.post(f"{self.base}/issues/{number}/comments", json={"body": body})

    def set_state(self, number, state):
        self.r.patch(f"{self.base}/issues/{number}", json={"state": state})


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def run_watchdog(pc_path, ci_conclusion=None):
    found = alerts(_load(pc_path), ci_conclusion=ci_conclusion)
    gh = Issues()
    if not found and not gh.find(ALERT_TITLE):
        print("Todo en orden (sin issue de alertas).")
        return
    issue = gh.ensure(ALERT_TITLE, "Alertas automáticas del vigilante de nexus. Se cierra solo cuando todo está bien.")
    body = "### Alertas activas\n" + "\n".join(f"- {x}" for x in found) if found else "Todo en orden: alertas resueltas."
    if body != gh.last_comment(issue["number"]):
        if found or issue["state"] == "open":
            gh.comment(issue["number"], body)
    gh.set_state(issue["number"], "open" if found else "closed")
    print(body)


def run_digest(pc_path, research_path=None, once_per_day=False):
    """Publica el informe. once_per_day (corridas programadas): si ya hay uno de hoy, no repite; así
    puede haber una hora de respaldo por si GitHub saltea la primera (pasó el 25-oct)."""
    research = Path(research_path).read_text(encoding="utf-8") if research_path and Path(research_path).exists() else None
    text = digest(_load(pc_path), research)
    gh = Issues()
    issue = gh.ensure(DIGEST_TITLE, "Informe diario automático de nexus (un comentario por día).")
    import time as _t
    if once_per_day and gh.commented_on(issue["number"], _t.strftime("%Y-%m-%d", _t.gmtime())):
        print("Ya hay informe de hoy: no se repite.")
        return
    gh.comment(issue["number"], text)
    print(text)


if __name__ == "__main__":
    if sys.argv[1] == "watchdog":
        run_watchdog(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    elif sys.argv[1] == "digest":
        run_digest(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None,
                   once_per_day=os.environ.get("NEXUS_DIGEST_ONCE") == "1")
