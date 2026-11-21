"""Read-only JSON-RPC client.

Safety properties (enforced in code, covered by tests):
* only methods on ``READ_ONLY_METHODS`` can be sent; anything that could sign,
  broadcast or mutate state is rejected *before* any network I/O;
* credential-bearing URLs are redacted wherever the endpoint is described;
* retries are bounded and only for transient transport failures.
"""
from __future__ import annotations

import itertools
import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional, Protocol
from urllib.parse import urlsplit

from .errors import (
    ConfigurationError,
    RpcConnectionError,
    RpcMethodNotAllowedError,
    RpcResponseError,
    RpcTimeoutError,
)

READ_ONLY_METHODS = frozenset({
    "eth_chainId", "net_version", "web3_clientVersion",
    "eth_blockNumber", "eth_getBlockByNumber", "eth_getBlockByHash",
    "eth_call", "eth_estimateGas", "eth_gasPrice", "eth_feeHistory", "eth_maxPriorityFeePerGas",
    "eth_getBalance", "eth_getCode", "eth_getStorageAt",
})

# Spelled out so reviewers (and tests) can see the intent; the allow-list alone already blocks these.
FORBIDDEN_METHOD_PREFIXES = ("eth_send", "eth_sign", "personal_", "wallet_", "eth_accounts", "eth_requestAccounts",
                             "admin_", "miner_", "debug_", "txpool_")

# Transient JSON-RPC failures worth a bounded retry (rate limiting / temporary node trouble).
_TRANSIENT_MESSAGE_HINTS = ("rate limit", "too many requests", "timeout", "temporarily", "try again")


def redact_url(url: str) -> str:
    """Drop userinfo, path and query: API keys often live there."""
    parts = urlsplit(url)
    host = parts.hostname or "unknown-host"
    port = f":{parts.port}" if parts.port else ""
    has_secret_part = bool(parts.username or parts.password or parts.query or (parts.path and parts.path not in ("", "/")))
    return f"{parts.scheme}://{host}{port}" + ("/<redacted>" if has_secret_part else "")


def validate_rpc_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ConfigurationError("RPC URL is not configured (set BSC_RPC_URL)")
    parts = urlsplit(url.strip())
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ConfigurationError("RPC URL must be an http(s) URL with a host")
    if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ConfigurationError("plain-http RPC URLs are only allowed for localhost")
    return url.strip()


class Transport(Protocol):
    def post(self, url: str, body: bytes, timeout: float) -> bytes: ...


class UrllibTransport:
    """Standard-library HTTP transport (no extra dependency)."""

    def post(self, url: str, body: bytes, timeout: float) -> bytes:
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "User-Agent": "trading-management-platform/phase-2-readonly"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - scheme validated
                return response.read()
        except urllib.error.HTTPError as exc:
            raise RpcConnectionError(f"HTTP {exc.code} from RPC endpoint") from None
        except (socket.timeout, TimeoutError) as exc:
            raise RpcTimeoutError("RPC request timed out") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise RpcTimeoutError("RPC request timed out") from exc
            raise RpcConnectionError(f"cannot reach RPC endpoint: {type(exc.reason).__name__}") from None
        except OSError as exc:
            raise RpcConnectionError(f"network error talking to RPC endpoint: {type(exc).__name__}") from None


class JsonRpcClient:
    def __init__(
        self,
        url: str,
        transport: Optional[Transport] = None,
        timeout: float = 10.0,
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._url = validate_rpc_url(url)
        self._transport = transport or UrllibTransport()
        if timeout <= 0:
            raise ConfigurationError("timeout must be positive")
        if not 0 <= max_retries <= 5:
            raise ConfigurationError("max_retries must be between 0 and 5")
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._sleep = sleep
        self._ids = itertools.count(1)
        self.request_count = 0

    @property
    def endpoint_description(self) -> str:
        return redact_url(self._url)

    @staticmethod
    def assert_method_allowed(method: str) -> None:
        if method in READ_ONLY_METHODS and not method.startswith(FORBIDDEN_METHOD_PREFIXES):
            return
        raise RpcMethodNotAllowedError(f"RPC method not allowed in read-only mode: {method}")

    def call(self, method: str, params: Optional[list[Any]] = None) -> Any:
        self.assert_method_allowed(method)
        params = params if params is not None else []
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            request_id = next(self._ids)
            body = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}).encode()
            try:
                self.request_count += 1
                raw = self._transport.post(self._url, body, self._timeout)
                return self._parse(raw, request_id)
            except RpcConnectionError as exc:  # includes timeouts
                last_error = exc
            except RpcResponseError as exc:
                if not self._is_transient(exc):
                    raise
                last_error = exc
            if attempt < self._max_retries:
                self._sleep(self._backoff * (2 ** attempt))
        assert last_error is not None
        raise last_error

    @staticmethod
    def _is_transient(exc: RpcResponseError) -> bool:
        message = str(exc).lower()
        if "revert" in message or "execution reverted" in message:
            return False
        return exc.code == -32005 or any(hint in message for hint in _TRANSIENT_MESSAGE_HINTS)

    @staticmethod
    def _parse(raw: bytes, request_id: int) -> Any:
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise RpcConnectionError("RPC endpoint returned non-JSON data") from exc
        if not isinstance(payload, dict):
            raise RpcConnectionError("unexpected RPC response shape")
        if payload.get("id") != request_id:
            raise RpcConnectionError("RPC response id mismatch")
        if "error" in payload and payload["error"] is not None:
            error = payload["error"] if isinstance(payload["error"], dict) else {"message": str(payload["error"])}
            raise RpcResponseError(str(error.get("message", "RPC error")), error.get("code"), error.get("data"))
        if "result" not in payload:
            raise RpcConnectionError("RPC response has neither result nor error")
        return payload["result"]


class FailoverJsonRpcClient:
    """Bounded read-only failover across explicitly configured endpoints."""

    def __init__(self, urls: list[str] | tuple[str, ...], transport: Optional[Transport] = None,
                 timeout: float = 10.0, max_retries: int = 2,
                 sleep: Callable[[float], None] = time.sleep):
        if not urls:
            raise ConfigurationError("at least one RPC URL is required")
        self._clients = [JsonRpcClient(url, transport=transport, timeout=timeout,
                                       max_retries=max_retries, sleep=sleep) for url in urls]
        self._active = 0
        self.failover_errors: list[str] = []

    @property
    def endpoint_description(self) -> str:
        return self._clients[self._active].endpoint_description

    @property
    def request_count(self) -> int:
        return sum(client.request_count for client in self._clients)

    def call(self, method: str, params: Optional[list[Any]] = None) -> Any:
        JsonRpcClient.assert_method_allowed(method)
        last_error: Exception | None = None
        for offset in range(len(self._clients)):
            index = (self._active + offset) % len(self._clients)
            client = self._clients[index]
            try:
                result = client.call(method, params)
                self._active = index
                return result
            except RpcConnectionError as exc:
                last_error = exc
                self.failover_errors.append(f"{client.endpoint_description} :: {method}: {exc}")
            except RpcResponseError as exc:
                if not JsonRpcClient._is_transient(exc):
                    raise
                last_error = exc
                self.failover_errors.append(f"{client.endpoint_description} :: {method}: {exc}")
        assert last_error is not None
        raise last_error
