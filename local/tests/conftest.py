"""Aislamiento de los tests: NINGÚN test puede leer ni escribir el estado real del PC.

El 24-oct un test leyó el update.json real del PC (que indicaba una actualización pospuesta), el
agente entró en modo vaciado dentro del test y este falló solo en Windows; el actualizador hizo
rollback correctamente. Desde ahora cada test usa su propio NEXUS_HOME temporal.
"""
import pytest


@pytest.fixture(autouse=True)
def nexus_home_temporal(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path_factory.mktemp("nexus_home")))
