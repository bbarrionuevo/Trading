"""CriptoYa (público, sin API key): precios P2P/exchanges AR. Docs: https://criptoya.com/api"""
from .base import ReadOnlyClient


class CriptoYa(ReadOnlyClient):
    PUBLIC_PREFIX = "/api/"

    def __init__(self, **kw):
        super().__init__("https://criptoya.com", **kw)

    def quote(self, exchange: str, coin: str = "USDT", fiat: str = "ARS", volume: float = 1):
        path = f"/api/{exchange}/{coin}/{fiat}/{volume}"
        self._guard(path)
        r = self.s.get(self.base_url + path, timeout=self.timeout)
        r.raise_for_status()
        return r.json()  # {ask, totalAsk, bid, totalBid, time}

    def all_quotes(self, coin="USDT", fiat="ARS", volume=1):
        path = f"/api/{coin}/{fiat}/{volume}"
        self._guard(path)
        r = self.s.get(self.base_url + path, timeout=self.timeout)
        r.raise_for_status()
        return r.json()
