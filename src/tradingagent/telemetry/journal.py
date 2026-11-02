"""Diario de decisiones con hash encadenado (REGLAS F-1), escrito por un hilo propio.

Cada línea JSONL: {"seq", "ts", "kind", "data", "prev", "hash"}, con hash = sha256(prev + JSON canónico
del registro sin el hash). Alterar, borrar o reordenar una línea rompe la cadena y `verify` lo detecta.
La cadena sigue entre archivos diarios y entre reinicios (al arrancar se lee el último hash).
`record()` no hace E/S: encola y vuelve; si la cola se llena, cuenta la pérdida y nunca bloquea.
"""
import hashlib
import json
import logging
import queue
import threading
import time
from pathlib import Path

from ..core.redact import redact

log = logging.getLogger("tradingagent.journal")
GENESIS = "0" * 64


def _canon(rec: dict) -> str:
    return json.dumps(rec, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def _hash(prev: str, rec: dict) -> str:
    return hashlib.sha256((prev + _canon(rec)).encode("utf-8")).hexdigest()


def _files(directory: Path) -> list[Path]:
    return sorted(Path(directory).glob("journal-*.jsonl"))


def _tail(directory: Path) -> tuple[int, str]:
    files = _files(directory)
    for f in reversed(files):
        lines = [x for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
        if lines:
            last = json.loads(lines[-1])
            return int(last["seq"]), last["hash"]
    return 0, GENESIS


class Journal:
    def __init__(self, directory, clock=None, *, max_queue: int = 20000):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.q: queue.Queue = queue.Queue(max_queue)
        self.seq, self.prev = _tail(self.dir)
        self.dropped = self.written = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def record(self, kind: str, data=None, **kw) -> bool:
        ts = self.clock.time() if self.clock else time.time()
        try:
            self.q.put_nowait((ts, kind, data if data is not None else kw))
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def _write(self, batch) -> None:
        by_file: dict[Path, list[str]] = {}
        for ts, kind, data in batch:
            self.seq += 1
            rec = {"seq": self.seq, "ts": round(ts, 6), "kind": kind, "data": data, "prev": self.prev}
            h = _hash(self.prev, rec)
            rec["hash"] = h
            self.prev = h
            day = time.strftime("%Y%m%d", time.gmtime(ts))
            by_file.setdefault(self.dir / f"journal-{day}.jsonl", []).append(_canon(rec))
        for path, lines in by_file.items():
            with path.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        self.written += len(batch)

    def _run(self) -> None:
        while not (self._stop.is_set() and self.q.empty()):
            try:
                first = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            batch = [first]
            while len(batch) < 500:
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            try:
                self._write(batch)
            except Exception as e:                                # noqa: BLE001 (disco lleno, permisos...)
                self.dropped += len(batch)
                log.error("diario: no se pudo escribir %d registros: %s", len(batch), redact(e))

    def start(self) -> "Journal":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="diario", daemon=True)
            self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def flush(self, timeout: float = 5.0) -> None:
        """Espera a que lo encolado quede escrito (tests y cierre ordenado)."""
        end = time.monotonic() + timeout
        while not self.q.empty() and time.monotonic() < end:
            time.sleep(0.01)
        time.sleep(0.05)

    def snapshot(self) -> dict:
        return {"written": self.written, "queued": self.q.qsize(), "dropped": self.dropped, "seq": self.seq}


def verify(directory) -> tuple[bool, int, str]:
    """(íntegra, registros verificados, problema). Recorre todos los archivos en orden."""
    prev, n, last_seq = GENESIS, 0, 0
    for f in _files(directory):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                h = rec.pop("hash")
            except (ValueError, KeyError):
                return False, n, f"{f.name}:{i}: línea ilegible"
            if rec.get("prev") != prev or _hash(prev, rec) != h:
                return False, n, f"{f.name}:{i}: la cadena se rompe (línea alterada, borrada o reordenada)"
            if rec.get("seq") != last_seq + 1:
                return False, n, f"{f.name}:{i}: falta un registro (seq {last_seq + 1} → {rec.get('seq')})"
            prev, last_seq, n = h, rec["seq"], n + 1
    return True, n, ""
