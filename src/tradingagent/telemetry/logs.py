"""Logs: consola legible y archivo JSON rotado en TRADING_HOME/logs, ambos con secretos redactados."""
import json
import logging
import sys
from logging.handlers import RotatingFileHandler

from ..core.redact import redact


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": round(record.created, 3), "level": record.levelname, "logger": record.name,
             "msg": record.getMessage()}
        if record.exc_text:
            d["exc"] = record.exc_text
        return json.dumps(d, ensure_ascii=False)


def setup(log_dir, level: str = "INFO", console: bool = True, name: str = "agent.log") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    flt = RedactFilter()
    fh = RotatingFileHandler(log_dir / name, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    fh.addFilter(flt)
    root.addHandler(fh)
    if console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(logging.Formatter("%(asctime)s %(levelname).1s %(name)s: %(message)s", "%H:%M:%S"))
        ch.addFilter(flt)
        root.addHandler(ch)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
