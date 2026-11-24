"""Shared HTTP client with per-host rate limiting and source health tracking."""
from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class SourceHealth:
    """Remembers the last success / error of each data source for the dashboard."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.data: dict[str, dict] = {}

    def ok(self, name: str, note: str = "") -> None:
        with self._lock:
            d = self.data.setdefault(name, {"ok": 0, "err": 0})
            d.update(status="ok", last_ok=time.time(), note=note)
            d["ok"] += 1

    def fail(self, name: str, err: str) -> None:
        with self._lock:
            d = self.data.setdefault(name, {"ok": 0, "err": 0})
            d.update(status="error", last_err=time.time(), error=str(err)[:160])
            d["err"] += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {k: dict(v) for k, v in self.data.items()}


health = SourceHealth()


class Http:
    def __init__(self, min_interval: dict[str, float] | None = None, timeout: float = 12.0):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*"})
        self.timeout = timeout
        self.min_interval = min_interval or {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _throttle(self, host: str) -> None:
        gap = self.min_interval.get(host, 0.0)
        if not gap:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._last.get(host, 0.0) + gap - now
            self._last[host] = max(now, self._last.get(host, 0.0) + gap)
        if wait > 0:
            time.sleep(wait)

    def get(self, url: str, params: dict | None = None, headers: dict | None = None, raw: bool = False):
        self._throttle(urlparse(url).netloc)
        r = self.s.get(url, params=params, headers=headers, timeout=self.timeout)
        if r.status_code == 429:
            time.sleep(2.0)
            r = self.s.get(url, params=params, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        return r.text if raw else r.json()

    def post(self, url: str, payload: dict, headers: dict | None = None):
        self._throttle(urlparse(url).netloc)
        r = self.s.post(url, json=payload, headers=headers, timeout=self.timeout)
        if r.status_code == 429:
            time.sleep(2.0)
            r = self.s.post(url, json=payload, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json()


http = Http(min_interval={
    "api.dexscreener.com": 0.25,       # 300 req/min on pair endpoints
    "api.rugcheck.xyz": 0.6,
    "api.mainnet-beta.solana.com": 0.25,
    "frontend-api-v3.pump.fun": 1.0,
    "gmgn.ai": 2.0,
    "lite-api.jup.ag": 1.1,
    "api.coingecko.com": 3.0,
})
