"""BNB Smart Chain read-only adapter.

Independent of PancakeSwap V2/V3 and of the KuCoin exchange code. Everything it
does is a public, read-only JSON-RPC query. It never signs, never broadcasts,
and has no wallet or private-key concept.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Mapping, Optional

from . import abi
from .errors import (
    ChainIdMismatchError,
    ConfigurationError,
    ContractCallError,
    DexError,
    InvalidInputError,
    RpcError,
    RpcResponseError,
)
from .models import (
    BlockInfo,
    DataProvenance,
    DataValidity,
    RpcHealth,
    TokenAmount,
    TokenInfo,
)
from .registry import BSC_CHAIN_ID
from .rpc import JsonRpcClient, Transport

ENV_RPC_URL = "BSC_RPC_URL"
ENV_RPC_TIMEOUT = "BSC_RPC_TIMEOUT_SECONDS"
ENV_RPC_RETRIES = "BSC_RPC_MAX_RETRIES"
ENV_MAX_BLOCK_AGE = "BSC_MAX_BLOCK_AGE_SECONDS"

BNB_DECIMALS = 18


@dataclass(frozen=True)
class BscAdapterConfig:
    rpc_url: str
    expected_chain_id: int = BSC_CHAIN_ID
    timeout_seconds: float = 10.0
    max_retries: int = 2
    max_block_age_seconds: int = 30

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "BscAdapterConfig":
        env = os.environ if environ is None else environ
        url = env.get(ENV_RPC_URL, "").strip()
        if not url:
            raise ConfigurationError(
                f"{ENV_RPC_URL} is not set. Configure any BSC RPC endpoint (public or your own node); "
                "no paid endpoint is required and none is used by default."
            )
        try:
            timeout = float(env.get(ENV_RPC_TIMEOUT, "10"))
            retries = int(env.get(ENV_RPC_RETRIES, "2"))
            max_age = int(env.get(ENV_MAX_BLOCK_AGE, "30"))
        except ValueError as exc:
            raise ConfigurationError("invalid numeric BSC_* setting") from exc
        return cls(rpc_url=url, timeout_seconds=timeout, max_retries=retries, max_block_age_seconds=max_age)


def _hex_to_int(value: Any, what: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise RpcError(f"unexpected {what} format from RPC")
    try:
        return int(value, 16)
    except ValueError as exc:
        raise RpcError(f"unexpected {what} format from RPC") from exc


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BscAdapter:
    """Read-only access to BNB Smart Chain mainnet."""

    def __init__(
        self,
        config: BscAdapterConfig,
        transport: Optional[Transport] = None,
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self._clock = clock
        self._rpc = JsonRpcClient(
            config.rpc_url, transport=transport, timeout=config.timeout_seconds,
            max_retries=config.max_retries, sleep=sleep,
        )
        self._chain_verified = False

    # ---- plumbing -------------------------------------------------------
    @property
    def endpoint(self) -> str:
        return self._rpc.endpoint_description

    @property
    def rpc_request_count(self) -> int:
        return self._rpc.request_count

    def now(self) -> datetime:
        return self._clock()

    def _provenance(self, what: str, validity: DataValidity, block: Optional[BlockInfo] = None,
                    issues: tuple[str, ...] = (), block_number: Optional[int] = None) -> DataProvenance:
        return DataProvenance(
            source=f"{self.endpoint} :: {what}", fetched_at=self.now(),
            block_number=block.number if block else block_number,
            block_timestamp=block.timestamp if block else None,
            validity=validity, issues=issues,
        )

    # ---- chain identity -------------------------------------------------
    def get_chain_id(self) -> int:
        return _hex_to_int(self._rpc.call("eth_chainId"), "chain id")

    def ensure_expected_chain(self) -> int:
        """Verify once per adapter that the endpoint serves the expected chain."""
        chain_id = self.get_chain_id()
        if chain_id != self.config.expected_chain_id:
            self._chain_verified = False
            raise ChainIdMismatchError(
                f"RPC endpoint {self.endpoint} reports chain id {chain_id}, expected {self.config.expected_chain_id}"
            )
        self._chain_verified = True
        return chain_id

    def _require_chain(self) -> None:
        if not self._chain_verified:
            self.ensure_expected_chain()

    # ---- blocks ---------------------------------------------------------
    def get_block_number(self) -> int:
        self._require_chain()
        return _hex_to_int(self._rpc.call("eth_blockNumber"), "block number")

    def get_block(self, number: Optional[int] = None) -> BlockInfo:
        self._require_chain()
        tag = "latest" if number is None else hex(number)
        raw = self._rpc.call("eth_getBlockByNumber", [tag, False])
        if not isinstance(raw, dict):
            raise RpcError("block not found")
        base_fee = raw.get("baseFeePerGas")
        return BlockInfo(
            number=_hex_to_int(raw.get("number"), "block number"),
            timestamp=datetime.fromtimestamp(_hex_to_int(raw.get("timestamp"), "block timestamp"), tz=timezone.utc),
            block_hash=raw.get("hash"),
            gas_used=_hex_to_int(raw["gasUsed"], "gasUsed") if raw.get("gasUsed") else None,
            gas_limit=_hex_to_int(raw["gasLimit"], "gasLimit") if raw.get("gasLimit") else None,
            base_fee_per_gas_wei=_hex_to_int(base_fee, "baseFeePerGas") if base_fee else None,
        )

    def block_age_seconds(self, block: BlockInfo) -> Decimal:
        return Decimal((self.now() - block.timestamp).total_seconds()).quantize(Decimal("0.001"))

    def block_is_fresh(self, block: BlockInfo) -> bool:
        return self.block_age_seconds(block) <= Decimal(self.config.max_block_age_seconds)

    # ---- health ---------------------------------------------------------
    def health_check(self) -> RpcHealth:
        started = time.perf_counter()
        errors: list[str] = []
        chain_id: Optional[int] = None
        block: Optional[BlockInfo] = None
        try:
            chain_id = self.ensure_expected_chain()
            block = self.get_block()
        except DexError as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        latency = Decimal((time.perf_counter() - started) * 1000).quantize(Decimal("0.1"))
        age: Optional[Decimal] = None
        if block is not None:
            age = self.block_age_seconds(block)
            if age > Decimal(self.config.max_block_age_seconds):
                errors.append(f"latest block is {age}s old (limit {self.config.max_block_age_seconds}s): RPC may be lagging")
            if age < Decimal(-5):
                errors.append("latest block timestamp is in the future: local clock skew?")
        ok = not errors and chain_id == self.config.expected_chain_id
        validity = DataValidity.VALID if ok else (DataValidity.STALE if block is not None and chain_id is not None else DataValidity.INVALID)
        return RpcHealth(ok=ok, chain_id=chain_id, block_number=block.number if block else None,
                         block_age_seconds=age, latency_ms=latency, errors=tuple(errors),
                         provenance=self._provenance("health_check", validity, block, tuple(errors)))

    # ---- balances (public address only) ----------------------------------
    def get_native_balance(self, address: str) -> TokenAmount:
        """Native BNB balance of a *public* address. Not needed by the swap flow."""
        self._require_chain()
        checked = abi.normalize_address(address)
        raw = _hex_to_int(self._rpc.call("eth_getBalance", [checked, "latest"]), "balance")
        return TokenAmount(raw=raw, decimals=BNB_DECIMALS, symbol="BNB")

    # ---- contract reads ---------------------------------------------------
    def get_code_size(self, address: str, block: Optional[int] = None) -> int:
        self._require_chain()
        code = self._rpc.call("eth_getCode", [abi.normalize_address(address), hex(block) if block is not None else "latest"])
        return len(abi.hex_to_bytes(code))

    def eth_call(self, to: str, data: bytes, block: Optional[int] = None) -> bytes:
        """Read-only contract call. Reverts become ContractCallError."""
        self._require_chain()
        call = {"to": abi.normalize_address(to), "data": abi.to_hex(data)}
        try:
            result = self._rpc.call("eth_call", [call, hex(block) if block is not None else "latest"])
        except RpcResponseError as exc:
            raise ContractCallError(f"call to {to} reverted or failed: {exc}") from exc
        out = abi.hex_to_bytes(result)
        if not out:
            raise ContractCallError(f"call to {to} returned no data (not a contract, or wrong function)")
        return out

    def get_gas_price_wei(self) -> int:
        self._require_chain()
        return _hex_to_int(self._rpc.call("eth_gasPrice"), "gas price")

    def estimate_gas(self, call: Mapping[str, Any], block: Optional[int] = None,
                     state_override: Optional[Mapping[str, Any]] = None) -> int:
        """Gas estimation by the node (local simulation; nothing is submitted)."""
        self._require_chain()
        params: list[Any] = [dict(call), hex(block) if block is not None else "latest"]
        if state_override:
            params.append(dict(state_override))
        return _hex_to_int(self._rpc.call("eth_estimateGas", params), "gas estimate")

    # ---- tokens -----------------------------------------------------------
    def get_token_info(self, address: str, expected_symbol: Optional[str] = None,
                       expected_decimals: Optional[int] = None, block: Optional[int] = None) -> TokenInfo:
        checked = abi.normalize_address(address)
        issues: list[str] = []
        has_code = self.get_code_size(checked, block) > 0
        if not has_code:
            raise ContractCallError(f"no contract code at {checked}")
        decimals = abi.decode_uint(self.eth_call(checked, abi.function_selector("decimals()"), block))
        if decimals > 36:
            raise ContractCallError(f"implausible decimals ({decimals}) at {checked}")
        symbol = abi.decode_string(self.eth_call(checked, abi.function_selector("symbol()"), block))
        name: Optional[str] = None
        total_supply: Optional[int] = None
        try:
            name = abi.decode_string(self.eth_call(checked, abi.function_selector("name()"), block))
        except DexError as exc:
            issues.append(f"name() unavailable: {exc}")
        try:
            total_supply = abi.decode_uint(self.eth_call(checked, abi.function_selector("totalSupply()"), block))
        except DexError as exc:
            issues.append(f"totalSupply() unavailable: {exc}")
        if expected_symbol is not None and symbol != expected_symbol:
            issues.append(f"symbol mismatch: on-chain {symbol!r}, expected {expected_symbol!r}")
        if expected_decimals is not None and decimals != expected_decimals:
            issues.append(f"decimals mismatch: on-chain {decimals}, expected {expected_decimals}")
        mismatch = any("mismatch" in i for i in issues)
        validity = DataValidity.INVALID if mismatch else DataValidity.VALID
        return TokenInfo(address=checked, symbol=symbol, name=name, decimals=decimals,
                         total_supply_raw=total_supply, has_code=has_code,
                         provenance=self._provenance(f"token {checked}", validity, block_number=block, issues=tuple(issues)))

    def snapshot_block(self) -> BlockInfo:
        """Latest block used to pin every read of one evaluation to the same state."""
        return self.get_block()


def require_valid(info: TokenInfo) -> TokenInfo:
    if info.provenance.validity is not DataValidity.VALID:
        raise InvalidInputError(f"token {info.address} failed validation: {'; '.join(info.provenance.issues)}")
    return info
