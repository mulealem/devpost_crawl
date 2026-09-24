"""Polite HTTP fetcher.

Devpost sits behind AWS WAF: plain bot-shaped requests get a 202 challenge
page, and bursts of requests get throttled. This fetcher uses browser-shaped
headers, keeps cookies in a session (aws-waf-token sticks once issued),
rate-limits per host with jitter, and backs off exponentially when challenged.
"""
import random
import threading
import time

import requests

from .config import BASE_HEADERS, MAX_RETRIES, REQUEST_DELAY, REQUEST_TIMEOUT


class FetchError(Exception):
    """Raised when a URL could not be fetched after retries."""


def _looks_like_challenge(resp: requests.Response) -> bool:
    if resp.status_code == 202:
        return True
    if resp.status_code in (403, 503) and "awsWaf" in resp.text[:20000]:
        return True
    return False


class Fetcher:
    def __init__(self, delay: float = REQUEST_DELAY):
        self.delay = delay
        self._last_hit: dict[str, float] = {}
        self._lock = threading.Lock()
        self.session = requests.Session()
        self.session.headers.update(BASE_HEADERS)
        self.stats = {"ok": 0, "challenge": 0, "throttled": 0, "notfound": 0, "failed": 0}

    def _throttle(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            last = self._last_hit.get(host, 0.0)
            wait = self.delay - (now - last) + random.uniform(0, self.delay * 0.4)
            self._last_hit[host] = now + max(0.0, wait)
        if wait > 0:
            time.sleep(wait)

    def get(self, url: str, *, accept_json: bool = False) -> requests.Response | None:
        """GET a URL, returning None for 404s. Raises FetchError after retries."""
        headers = {"Accept": "application/json"} if accept_json else {}
        backoff = 20.0
        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle(requests.utils.urlparse(url).hostname or "")
            try:
                resp = self.session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                self.stats["failed"] += 1
                if attempt == MAX_RETRIES:
                    raise FetchError(f"{url}: {exc}") from exc
                time.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 404:
                self.stats["notfound"] += 1
                return None

            if _looks_like_challenge(resp):
                self.stats["challenge"] += 1
                if attempt == MAX_RETRIES:
                    raise FetchError(f"{url}: WAF challenge not cleared after {MAX_RETRIES} tries")
                print(f"    [waf] challenged, backing off {backoff:.0f}s ({attempt}/{MAX_RETRIES})")
                time.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                self.stats["throttled"] += 1
                if attempt == MAX_RETRIES:
                    raise FetchError(f"{url}: HTTP {resp.status_code} after retries")
                retry_after = float(resp.headers.get("Retry-After", backoff))
                time.sleep(min(retry_after, 120.0))
                backoff *= 2
                continue

            if resp.status_code == 403:
                # 403 without WAF marker: skip, retrying rarely helps.
                self.stats["failed"] += 1
                return None

            resp.raise_for_status()
            self.stats["ok"] += 1
            return resp
        raise FetchError(f"{url}: exhausted retries")  # pragma: no cover
