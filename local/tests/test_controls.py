"""Botones del tablero: hacen lo mismo que los comandos de consola, con las mismas confirmaciones."""
import time


def test_pausar_reanudar_y_niveles():
    from nexus import paths
    from nexus.agent import controls, live
    assert not controls.run("kill", {"nivel": 7})["ok"]
    assert controls.run("kill", {"nivel": 2})["ok"] and live.kill_level() == 2
    assert controls.run("reanudar")["ok"] and not paths.kill_file().exists()
    assert controls.run("reanudar")["out"] == ["No había ninguna pausa."]
    assert not controls.run("borrar_todo")["ok"]


def test_autorizar_exige_clave_sin_retiro_y_frase_exacta():
    from nexus.agent import authorize, controls, hedge_live, live
    base = {"auto": 2, "todo": True, "dias": 7, "calibracion": True}
    r = controls.run("autorizar", base | {"clave_sin_retiro": False, "frase": authorize.PHRASE})
    assert not r["ok"] and live.authorization() is None
    r = controls.run("autorizar", base | {"clave_sin_retiro": True, "frase": "autorizo"})
    assert not r["ok"] and live.authorization() is None and any("Frase incorrecta" in x for x in r["out"])
    r = controls.run("autorizar", base | {"clave_sin_retiro": True, "frase": authorize.PHRASE})
    a = live.authorization()
    assert r["ok"] and a["auto"] == 2 and a["todo"] and a["capital_eur"] == 100
    r = controls.run("autorizar", {"pares": "zrousdt", "todo": False, "capital": 15, "clave_sin_retiro": True,
                                   "frase": authorize.PHRASE})
    assert r["ok"] and live.authorization()["symbols"] == ["ZROUSDT"] and live.authorization()["capital_eur"] == 15
    r = controls.run("cobertura", {"pares": "QUICKUSDT", "lote": 10, "clave_sin_retiro": True, "frase": authorize.PHRASE})
    assert r["ok"] and hedge_live.hedge_authorization()["symbols"] == ["QUICKUSDT"]
    assert controls.run("estado")["ok"]
    bad = controls.run("cobertura", {"pares": "QUICKUSDT", "lote": "diez", "clave_sin_retiro": True})
    assert not bad["ok"] and bad["out"]                      # argparse no tumba el tablero
    assert controls.run("revocar")["ok"] and live.authorization() is None and hedge_live.hedge_authorization() is None


def test_desbloquear_pide_revision():
    from nexus import paths
    from nexus.agent import controls, hedge_live, live
    assert not controls.run("desbloquear", {"pares": "BDXUSDT", "revisado": False})["ok"]
    assert not controls.run("desbloquear", {"pares": " ", "revisado": True})["ok"]
    assert controls.run("desbloquear", {"pares": "BDXUSDT", "revisado": True})["ok"]
    assert paths.read_json(paths.state(live.UNBLOCK_FILE), {})["symbols"] == ["BDXUSDT"]
    assert controls.run("desbloquear", {"pares": "QUICKUSDT", "revisado": True, "cobertura": True})["ok"]
    assert paths.read_json(paths.state(hedge_live.HEDGE_UNBLOCK), {})["symbols"] == ["QUICKUSDT"]


def test_aprobar_actualizacion_con_la_frase(monkeypatch):
    from nexus import paths
    from nexus.agent import controls

    class Up:
        @staticmethod
        def candidate():
            return "a" * 40, "b" * 40, ["local/nexus/agent/live.py"], "bbbbbbb Arreglo"

        @staticmethod
        def approve(ask=input, out=print):
            cur, new, prot, log = Up.candidate()
            if ask("frase").strip() != f"APRUEBO {new[:7]}":
                out("No se aprobó nada.")
                return 1
            paths.write_json_atomic(paths.state("approvals.json"), {new: {"ts": time.time(), "protected": prot}})
            out("Aprobada.")
            return 0
    monkeypatch.setattr(controls, "_update_module", lambda: Up)
    c = controls.run("actualizacion")
    assert c["candidate"] == "bbbbbbb" and "live.py" in c["out"][0]
    assert not controls.run("aprobar", {"frase": "APRUEBO"})["ok"]
    assert controls.run("aprobar", {"frase": "APRUEBO bbbbbbb"})["ok"] and paths.read_json(paths.state("approvals.json"), {})


def test_actualizar_ya_solo_en_windows(monkeypatch):
    from nexus.agent import controls
    monkeypatch.setattr(controls.sys, "platform", "linux")
    assert not controls.run("actualizar_ya")["ok"]
    seen = {}
    monkeypatch.setattr(controls.sys, "platform", "win32")
    monkeypatch.setattr(controls.subprocess, "run", lambda cmd, **kw: seen.update(cmd=cmd) or
                        type("R", (), {"returncode": 0, "stdout": "OK", "stderr": ""})())
    assert controls.run("actualizar_ya")["ok"] and seen["cmd"] == ["schtasks", "/Run", "/TN", "nexus-mantenimiento"]
