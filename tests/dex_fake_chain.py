"""A fake BNB Smart Chain JSON-RPC node for MOCK / UNIT tests only.

Nothing here talks to a network. Passing tests built on it prove the code's logic
against a simulated chain; they are NOT evidence of live BSC / PancakeSwap
connectivity (see tests/test_dex_live_rpc.py for the opt-in live checks).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Any, Callable, Optional

from trading_platform.dex import abi
from trading_platform.dex.errors import RpcConnectionError
from trading_platform.dex.registry import DEFAULT_REGISTRY as R

NOW = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
WBNB, USDT = R.address("wbnb"), R.address("usdt")
V2_FACTORY, V2_ROUTER = R.address("v2_factory"), R.address("v2_router")
V3_FACTORY, V3_QUOTER = R.address("v3_factory"), R.address("v3_quoter_v2")
# Arbitrary FIXTURE addresses (not real pools); checksummed programmatically.
V2_PAIR = abi.to_checksum_address("0x" + "11" * 20)
V3_POOLS = {500: abi.to_checksum_address("0x" + "22" * 20), 2500: abi.to_checksum_address("0x" + "33" * 20)}
ONE = 10 ** 18


class RevertError(Exception):
    pass


def enc_string(text: str) -> bytes:
    raw = text.encode()
    return abi.encode_uint(32) + abi.encode_uint(len(raw)) + raw.ljust(((len(raw) + 31) // 32) * 32, b"\0")


def enc_words(*values: int) -> bytes:
    return b"".join(abi.encode_uint(v) for v in values)


def sqrt_price_x96_for(token1_per_token0: Fraction) -> int:
    with localcontext() as ctx:
        ctx.prec = 80
        value = (Decimal(token1_per_token0.numerator) / Decimal(token1_per_token0.denominator)).sqrt()
        return int(value * Decimal(2 ** 96))


class FakeChain:
    """Implements the Transport protocol (post(url, body, timeout) -> bytes)."""

    def __init__(self) -> None:
        self.chain_id = 56
        self.block_number = 45_000_000
        self.block_age_seconds = 3
        self.reachable = True
        self.methods_called: list[str] = []
        self.gas_price_wei: Optional[int] = 3 * 10 ** 9
        self.estimate_gas_result: Optional[int] = None          # None => eth_estimateGas reverts
        # tokens
        self.usdt_symbol = "USDT"
        self.has_code = {a.lower() for a in (WBNB, USDT, V2_FACTORY, V2_ROUTER, V3_FACTORY, V3_QUOTER, V2_PAIR,
                                              *V3_POOLS.values())}
        # V2 world: 50,000 WBNB vs 30,000,000 USDT  => 600 USDT/BNB
        self.v2_pair_exists = True
        self.v2_reserve_wbnb = 50_000 * ONE
        self.v2_reserve_usdt = 30_000_000 * ONE
        self.router_skew = 0                                     # added to router answers to force a MISMATCH
        self.router_weth = WBNB
        self.router_factory = V2_FACTORY
        self.router_available = True
        # V3 world
        self.usdt_per_bnb = Fraction(600)
        self.v3_enabled_tiers = {100: 1, 500: 10, 2500: 50, 10000: 200}
        self.v3_pools = dict(V3_POOLS)                           # fee -> address
        self.v3_liquidity = {500: 10 ** 24, 2500: 10 ** 24}
        self.v3_quoter_impact = {500: Fraction(5, 10_000), 2500: Fraction(5, 10_000)}
        self.v3_quote_reverts: set[int] = set()
        self.v3_pool_factory_override: dict[int, str] = {}
        self.v3_gas_hint = 150_000

    # ------------------------------------------------------------------ transport
    def post(self, url: str, body: bytes, timeout: float) -> bytes:
        if not self.reachable:
            raise RpcConnectionError("fake chain unreachable")
        request = json.loads(body)
        method, params = request["method"], request.get("params", [])
        self.methods_called.append(method)
        try:
            result = self._handle(method, params)
            return json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
        except RevertError as exc:
            return json.dumps({"jsonrpc": "2.0", "id": request["id"],
                               "error": {"code": 3, "message": f"execution reverted: {exc}"}}).encode()

    # ------------------------------------------------------------------ rpc methods
    def _handle(self, method: str, params: list[Any]) -> Any:
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "eth_blockNumber":
            return hex(self.block_number)
        if method == "eth_getBlockByNumber":
            ts = int((NOW - timedelta(seconds=self.block_age_seconds)).timestamp())
            return {"number": hex(self.block_number), "timestamp": hex(ts), "hash": "0x" + "ab" * 32,
                    "gasUsed": hex(20_000_000), "gasLimit": hex(140_000_000)}
        if method == "eth_getCode":
            return "0x6080604052" if params[0].lower() in self.has_code else "0x"
        if method == "eth_gasPrice":
            if self.gas_price_wei is None:
                raise RevertError("gas price unavailable")
            return hex(self.gas_price_wei)
        if method == "eth_estimateGas":
            if self.estimate_gas_result is None:
                raise RevertError("insufficient allowance / balance")
            return hex(self.estimate_gas_result)
        if method == "eth_getBalance":
            return hex(7 * ONE)
        if method == "eth_call":
            call = params[0]
            data = abi.hex_to_bytes(call["data"])
            return abi.to_hex(self._dispatch(call["to"].lower(), data))
        raise RevertError(f"unsupported fake method {method}")

    # ------------------------------------------------------------------ contracts
    def _dispatch(self, to: str, data: bytes) -> bytes:
        sel, args = data[:4], data[4:]
        sig = lambda s: abi.function_selector(s)  # noqa: E731
        if to in (WBNB.lower(), USDT.lower()):
            symbol, name = ("WBNB", "Wrapped BNB") if to == WBNB.lower() else (self.usdt_symbol, "Tether USD")
            table = {sig("decimals()"): lambda: enc_words(18), sig("symbol()"): lambda: enc_string(symbol),
                     sig("name()"): lambda: enc_string(name), sig("totalSupply()"): lambda: enc_words(10 ** 27)}
            return self._lookup(table, sel)
        if to == V2_FACTORY.lower():
            return self._lookup({sig("getPair(address,address)"): lambda: self._get_pair(args)}, sel)
        if to == V2_ROUTER.lower():
            if not self.router_available and sel in (sig("getAmountsOut(uint256,address[])"), sig("getAmountsIn(uint256,address[])")):
                raise RevertError("router unavailable")
            return self._lookup({
                sig("factory()"): lambda: abi.encode_address(self.router_factory),
                sig("WETH()"): lambda: abi.encode_address(self.router_weth),
                sig("getAmountsOut(uint256,address[])"): lambda: self._router_out(args),
                sig("getAmountsIn(uint256,address[])"): lambda: self._router_in(args)}, sel)
        if to == V2_PAIR.lower():
            t0, t1 = sorted((WBNB, USDT), key=lambda a: int(a, 16))
            r = {WBNB: self.v2_reserve_wbnb, USDT: self.v2_reserve_usdt}
            return self._lookup({
                sig("factory()"): lambda: abi.encode_address(V2_FACTORY),
                sig("token0()"): lambda: abi.encode_address(t0), sig("token1()"): lambda: abi.encode_address(t1),
                sig("getReserves()"): lambda: enc_words(r[t0], r[t1], int(NOW.timestamp()) - 10)}, sel)
        if to == V3_FACTORY.lower():
            return self._lookup({sig("feeAmountTickSpacing(uint24)"): lambda: self._tick_spacing(args),
                                 sig("getPool(address,address,uint24)"): lambda: self._get_pool(args)}, sel)
        for fee, pool in self.v3_pools.items():
            if to == pool.lower():
                return self._v3_pool(fee, sel)
        if to == V3_QUOTER.lower():
            return self._lookup({sig("factory()"): lambda: abi.encode_address(V3_FACTORY),
                                 sig("quoteExactInputSingle((address,address,uint256,uint24,uint160))"): lambda: self._quote(args, True),
                                 sig("quoteExactOutputSingle((address,address,uint256,uint24,uint160))"): lambda: self._quote(args, False)}, sel)
        return b""  # an EOA / unknown contract: eth_call returns "0x"

    @staticmethod
    def _lookup(table: dict[bytes, Callable[[], bytes]], sel: bytes) -> bytes:
        if sel not in table:
            raise RevertError("unknown selector")
        return table[sel]()

    # --- V2
    def _get_pair(self, args: bytes) -> bytes:
        a, b = abi.word_to_address(args, 0), abi.word_to_address(args, 1)
        if self.v2_pair_exists and {a, b} == {WBNB, USDT}:
            return abi.encode_address(V2_PAIR)
        return abi.encode_address(abi.ZERO_ADDRESS)

    def _v2_out(self, amount_in: int, r_in: int, r_out: int) -> int:  # independent re-statement of the formula
        fee_adj = amount_in * 9975
        return fee_adj * r_out // (r_in * 10000 + fee_adj)

    def _v2_in(self, amount_out: int, r_in: int, r_out: int) -> int:
        return (r_in * amount_out * 10000) // ((r_out - amount_out) * 9975) + 1

    def _path_reserves(self, path: list[str]) -> tuple[int, int]:
        r = {WBNB: self.v2_reserve_wbnb, USDT: self.v2_reserve_usdt}
        return r[path[0]], r[path[1]]

    def _decode_path(self, args: bytes) -> tuple[int, list[str]]:
        amount = abi.word_to_int(args, 0)
        offset = abi.word_to_int(args, 1)
        n = int.from_bytes(args[offset:offset + 32], "big")
        path = [abi.word_to_address(args[offset + 32:], i) for i in range(n)]
        return amount, path

    def _router_out(self, args: bytes) -> bytes:
        amount, path = self._decode_path(args)
        r_in, r_out = self._path_reserves(path)
        out = self._v2_out(amount, r_in, r_out) + self.router_skew
        return abi.encode_uint(32) + enc_words(2, amount, out)

    def _router_in(self, args: bytes) -> bytes:
        amount, path = self._decode_path(args)
        r_in, r_out = self._path_reserves(path)
        need = self._v2_in(amount, r_in, r_out) + self.router_skew
        return abi.encode_uint(32) + enc_words(2, need, amount)

    # --- V3
    def _tick_spacing(self, args: bytes) -> bytes:
        return enc_words(self.v3_enabled_tiers.get(abi.word_to_int(args, 0), 0))

    def _get_pool(self, args: bytes) -> bytes:
        fee = abi.word_to_int(args, 2)
        pool = self.v3_pools.get(fee)
        return abi.encode_address(pool if pool else abi.ZERO_ADDRESS)

    def _v3_pool(self, fee: int, sel: bytes) -> bytes:
        sig = lambda s: abi.function_selector(s)  # noqa: E731
        t0, t1 = sorted((WBNB, USDT), key=lambda a: int(a, 16))
        # price of token1 per token0 in raw units (both 18 decimals): WBNB per USDT if token0 is USDT
        usdt_is_token0 = abi.same_address(t0, USDT)
        p = (1 / self.usdt_per_bnb) if usdt_is_token0 else self.usdt_per_bnb
        slot0 = enc_words(sqrt_price_x96_for(p), 0, 1, 100, 100, 0, 1)
        tick = abi.encode_uint((-(10 ** 5)) % (1 << 256))  # tick word value is irrelevant to the code under test
        slot0 = slot0[:32] + tick + slot0[64:]
        return self._lookup({
            sig("factory()"): lambda: abi.encode_address(self.v3_pool_factory_override.get(fee, V3_FACTORY)),
            sig("fee()"): lambda: enc_words(fee),
            sig("token0()"): lambda: abi.encode_address(t0), sig("token1()"): lambda: abi.encode_address(t1),
            sig("slot0()"): lambda: slot0,
            sig("liquidity()"): lambda: enc_words(self.v3_liquidity.get(fee, 0))}, sel)

    def _quote(self, args: bytes, exact_in: bool) -> bytes:
        token_in, token_out = abi.word_to_address(args, 0), abi.word_to_address(args, 1)
        amount, fee = abi.word_to_int(args, 2), abi.word_to_int(args, 3)
        if fee in self.v3_quote_reverts or fee not in self.v3_pools:
            raise RevertError("SPL")
        spot = self.usdt_per_bnb if token_in == WBNB else 1 / self.usdt_per_bnb      # out per in
        fee_f, imp = Fraction(fee, 1_000_000), self.v3_quoter_impact.get(fee, Fraction(0))
        if exact_in:
            result = int(Fraction(amount) * (1 - fee_f) * spot * (1 - imp))
        else:
            ideal = Fraction(amount) / spot / (1 - fee_f) / (1 - imp)
            result = int(ideal) + (1 if ideal != int(ideal) else 0)
        return enc_words(result, sqrt_price_x96_for(Fraction(1)), 2, self.v3_gas_hint)
