"""Paper trading de market making en vivo sobre MEXC (maker 0%, taker 0.08% verificado por API) por sondeo REST.

Reutiliza el modelo de cola de live_paper_maker.Book. Sondea bookTicker y trades recientes cada ~1 s.
python -m nexus.live_paper_mexc OUT.jsonl SYM1,SYM2 minutos
"""
import json
import sys
import time

import requests

from .live_paper_maker import Book

API = "https://api.mexc.com/api/v3"


def run(syms, minutes, out):
    s = requests.Session()
    books = {x: [Book(x, maker=0.0, taker=0.08), Book(x, maker=0.1, taker=0.1)] for x in syms}  # comisión real verificada
    seen = {x: set() for x in syms}
    t0, last_dump, errors = time.time(), time.time(), 0
    while time.time() - t0 < minutes * 60:
        for x in syms:
            try:
                bt = s.get(f"{API}/ticker/bookTicker", params={"symbol": x}, timeout=5).json()
                trades = s.get(f"{API}/trades", params={"symbol": x, "limit": 100}, timeout=5).json()
            except Exception:
                errors += 1
                continue
            for bk in books[x]:
                bk.on_book(float(bt["bidPrice"]), float(bt["bidQty"]), float(bt["askPrice"]), float(bt["askQty"]))
            for t in sorted(trades, key=lambda t: t["time"]):
                key = (t["time"], t["price"], t["qty"], t["isBuyerMaker"])
                if key in seen[x]:
                    continue
                seen[x].add(key)
                for bk in books[x]:
                    bk.on_trade(float(t["price"]), float(t["qty"]), t["isBuyerMaker"])
            if len(seen[x]) > 5000:
                seen[x] = set(list(seen[x])[-2000:])
        time.sleep(0.5)
        if time.time() - last_dump >= 60:
            last_dump = time.time()
            snap = {"ts": time.time(), "exchange": "mexc", "model": "v2-floor", "elapsed_min": round((time.time() - t0) / 60, 1),
                    "errors": errors, "books": [bk.mark() for v in books.values() for bk in v]}
            with open(out, "a") as f:
                f.write(json.dumps(snap) + "\n")
            print(json.dumps(snap), flush=True)


if __name__ == "__main__":
    run(sys.argv[2].split(","), float(sys.argv[3]), sys.argv[1])
