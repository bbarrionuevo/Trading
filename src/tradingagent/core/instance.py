"""Una sola instancia por carpeta: dos agentes (o dos supervisores) sobre el mismo estado se pisarían
las posiciones y el diario.

El candado lo suelta el sistema operativo cuando el proceso termina, aunque sea de golpe (msvcrt en
Windows, flock en el resto): nunca queda trabado después de un corte de luz o de un "Finalizar tarea".
"""
import os
from pathlib import Path


class InstanceLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._f = None

    @property
    def held(self) -> bool:
        return self._f is not None

    def acquire(self) -> bool:
        if self._f is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.path, "a+b")                     # queda abierto mientras dure el candado
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self._f = f
        return True

    def release(self) -> None:
        if self._f is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._f.seek(0)
                msvcrt.locking(self._f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self._f.close()
        self._f = None

    def held_by_other(self) -> bool:
        """¿Lo tiene otro proceso? (lo prueba y, si lo consigue, lo suelta)."""
        if self._f is not None:
            return False
        if self.acquire():
            self.release()
            return False
        return True
