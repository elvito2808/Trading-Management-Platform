"""Minimal, dependency-light ABI helpers for read-only EVM calls.

Only what the BSC / PancakeSwap read-only integration needs: function selectors,
EIP-55 checksums, and encoding/decoding of static words, ``address[]`` and strings.
All integers stay Python ``int``; nothing here ever touches ``float``.
"""
from __future__ import annotations

import re
from typing import Sequence

from .errors import AbiDecodeError, InvalidInputError

WORD = 32
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
ZERO_ADDRESS = "0x" + "00" * 20

# --------------------------------------------------------------------------
# keccak-256 (NOT sha3-256). Prefer eth_hash (a declared project dependency);
# fall back to a small pure-Python implementation so this module stays usable,
# and unit-testable, in environments where eth_hash is not installed.
# --------------------------------------------------------------------------
_MASK = (1 << 64) - 1
_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
_ROT = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)


def _rol(value: int, shift: int) -> int:
    shift %= 64
    return ((value << shift) | (value >> (64 - shift))) & _MASK if shift else value


def _keccak_f(state: list[int]) -> None:
    for rc in _RC:
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for i in range(25):
            state[i] ^= d[i % 5]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(state[x + 5 * y], _ROT[x][y])
        for y in range(5):
            for x in range(5):
                state[x + 5 * y] = b[x + 5 * y] ^ ((~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y]) & _MASK
        state[0] ^= rc


def keccak256_py(data: bytes) -> bytes:
    """Pure-Python Keccak-256 (original Keccak padding, as used by Ethereum)."""
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate:
        padded.append(0x00)
    padded[-1] |= 0x80
    state = [0] * 25
    for offset in range(0, len(padded), rate):
        block = padded[offset:offset + rate]
        for i in range(rate // 8):
            state[i] ^= int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        _keccak_f(state)
    return b"".join(state[i].to_bytes(8, "little") for i in range(4))


try:  # pragma: no cover - exercised only where eth_hash is installed
    from eth_hash.auto import keccak as _keccak_impl

    def keccak256(data: bytes) -> bytes:
        return bytes(_keccak_impl(data))

    KECCAK_BACKEND = "eth_hash"
except Exception:  # ImportError or backend-selection errors
    def keccak256(data: bytes) -> bytes:
        return keccak256_py(data)

    KECCAK_BACKEND = "pure-python"


def function_selector(signature: str) -> bytes:
    """First four bytes of keccak256 of a canonical signature, e.g. ``balanceOf(address)``."""
    return keccak256(signature.encode("ascii"))[:4]


# Canonical signatures used by the Phase 2 integration.  Keeping these in one
# inventory makes every selector auditable from the Solidity ABI signature;
# callers must not rely on undocumented numeric selector constants.
ABI_SIGNATURES = {
    "erc20_name": "name()",
    "erc20_symbol": "symbol()",
    "erc20_decimals": "decimals()",
    "erc20_total_supply": "totalSupply()",
    "erc20_balance_of": "balanceOf(address)",
    "erc20_allowance": "allowance(address,address)",
    "v2_factory_get_pair": "getPair(address,address)",
    "v2_pair_get_reserves": "getReserves()",
    "v2_router_get_amounts_out": "getAmountsOut(uint256,address[])",
    "v2_router_get_amounts_in": "getAmountsIn(uint256,address[])",
    "v2_router_swap_exact_eth_for_tokens": "swapExactETHForTokens(uint256,address[],address,uint256)",
    "v2_router_swap_tokens_for_exact_eth": "swapTokensForExactETH(uint256,uint256,address[],address,uint256)",
    "v3_factory_get_pool": "getPool(address,address,uint24)",
    "v3_factory_fee_spacing": "feeAmountTickSpacing(uint24)",
    "v3_pool_slot0": "slot0()",
    "v3_quoter_exact_input_single": "quoteExactInputSingle((address,address,uint256,uint24,uint160))",
    "v3_quoter_exact_output_single": "quoteExactOutputSingle((address,address,uint256,uint24,uint160))",
    # PancakeSwap V3 SwapRouter calldata inventory; builders are local-only.
    "v3_router_exact_input_single": "exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))",
    "v3_router_exact_output_single": "exactOutputSingle((address,address,uint24,address,uint256,uint256,uint160))",
}


def selector_for(name: str) -> bytes:
    """Return the selector for a named, documented ABI signature."""
    try:
        return function_selector(ABI_SIGNATURES[name])
    except KeyError as exc:
        raise InvalidInputError(f"unknown ABI inventory entry: {name}") from exc


# --------------------------------------------------------------------------
# Addresses
# --------------------------------------------------------------------------
def is_address(value: object) -> bool:
    return isinstance(value, str) and bool(_ADDRESS_RE.match(value))


def to_checksum_address(address: str) -> str:
    if not is_address(address):
        raise InvalidInputError(f"not a 20-byte hex address: {address!r}")
    lower = address[2:].lower()
    digest = keccak256(lower.encode("ascii")).hex()
    return "0x" + "".join(ch.upper() if int(digest[i], 16) >= 8 else ch for i, ch in enumerate(lower))


def is_checksum_address(address: str) -> bool:
    """True only for a mixed-case address whose EIP-55 checksum is correct."""
    return is_address(address) and address == to_checksum_address(address)


def normalize_address(address: str) -> str:
    """Return the checksummed form. Mixed-case input must carry a valid checksum."""
    if not is_address(address):
        raise InvalidInputError(f"not a 20-byte hex address: {address!r}")
    body = address[2:]
    if body != body.lower() and body != body.upper() and not is_checksum_address(address):
        raise InvalidInputError(f"address fails EIP-55 checksum: {address}")
    return to_checksum_address(address)


def same_address(a: str, b: str) -> bool:
    return is_address(a) and is_address(b) and a.lower() == b.lower()


# --------------------------------------------------------------------------
# Encoding
# --------------------------------------------------------------------------
def encode_uint(value: int, bits: int = 256) -> bytes:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidInputError("uint must be an int")
    if value < 0 or value >= (1 << bits):
        raise InvalidInputError(f"value out of range for uint{bits}")
    return value.to_bytes(WORD, "big")


def encode_address(address: str) -> bytes:
    if not is_address(address):
        raise InvalidInputError(f"not a 20-byte hex address: {address!r}")
    return bytes(12) + bytes.fromhex(address[2:])


def encode_address_array(addresses: Sequence[str]) -> bytes:
    return encode_uint(len(addresses)) + b"".join(encode_address(a) for a in addresses)


def encode_call_address(signature: str, address: str) -> bytes:
    return encode_call(signature, encode_address(address))


def encode_call_address_address(signature: str, first: str, second: str) -> bytes:
    return encode_call(signature, encode_address(first), encode_address(second))


def encode_call(signature: str, *static_words: bytes) -> bytes:
    """selector + already-encoded 32-byte static words."""
    for word in static_words:
        if len(word) != WORD:
            raise InvalidInputError("static ABI words must be 32 bytes")
    return function_selector(signature) + b"".join(static_words)


def encode_call_uint_address_array(signature: str, amount: int, path: Sequence[str]) -> bytes:
    """Encode ``f(uint256, address[])`` (e.g. ``getAmountsOut``)."""
    head = encode_uint(amount) + encode_uint(2 * WORD)
    return function_selector(signature) + head + encode_address_array(path)


def to_hex(data: bytes) -> str:
    return "0x" + data.hex()


# --------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------
def hex_to_bytes(value: str) -> bytes:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise AbiDecodeError("expected 0x-prefixed hex data")
    body = value[2:]
    if len(body) % 2:
        raise AbiDecodeError("odd-length hex data")
    try:
        return bytes.fromhex(body)
    except ValueError as exc:
        raise AbiDecodeError("invalid hex data") from exc


def decode_words(data: bytes, expected_words: int) -> list[int]:
    if len(data) < expected_words * WORD:
        raise AbiDecodeError(f"expected at least {expected_words} words, got {len(data)} bytes")
    return [int.from_bytes(data[i * WORD:(i + 1) * WORD], "big") for i in range(expected_words)]


def decode_uint(data: bytes) -> int:
    return decode_words(data, 1)[0]


def decode_int(data: bytes, bits: int = 256) -> int:
    raw = decode_words(data, 1)[0]
    return raw - (1 << bits) if raw >= (1 << (bits - 1)) else raw


def word_to_int(data: bytes, index: int, signed_bits: int | None = None) -> int:
    start = index * WORD
    if len(data) < start + WORD:
        raise AbiDecodeError(f"missing word {index}")
    raw = int.from_bytes(data[start:start + WORD], "big")
    if signed_bits is not None:
        low = raw & ((1 << signed_bits) - 1)
        return low - (1 << signed_bits) if low >= (1 << (signed_bits - 1)) else low
    return raw


def decode_bool(data: bytes) -> bool:
    value = decode_uint(data)
    if value not in (0, 1):
        raise AbiDecodeError("invalid bool encoding")
    return bool(value)


def decode_address(data: bytes) -> str:
    raw = decode_words(data, 1)[0]
    if raw >> 160:
        raise AbiDecodeError("address word has non-zero upper bytes")
    return to_checksum_address("0x" + raw.to_bytes(20, "big").hex())


def word_to_address(data: bytes, index: int = 0) -> str:
    start = index * WORD
    if len(data) < start + WORD:
        raise AbiDecodeError(f"missing word {index}")
    raw = int.from_bytes(data[start:start + WORD], "big")
    if raw >> 160:
        raise AbiDecodeError("address word has non-zero upper bytes")
    return to_checksum_address("0x" + raw.to_bytes(20, "big").hex())


def decode_uint_array(data: bytes) -> list[int]:
    """Decode a single returned ``uint256[]`` (head offset, length, items)."""
    offset = decode_words(data, 1)[0]
    if offset + WORD > len(data):
        raise AbiDecodeError("array offset out of range")
    length = int.from_bytes(data[offset:offset + WORD], "big")
    start = offset + WORD
    if length > 1024 or start + length * WORD > len(data):
        raise AbiDecodeError("array length out of range")
    return [int.from_bytes(data[start + i * WORD:start + (i + 1) * WORD], "big") for i in range(length)]


def decode_string(data: bytes) -> str:
    """Decode an ABI ``string``; also accepts legacy ``bytes32`` symbols (e.g. MKR)."""
    if len(data) == WORD:
        return data.rstrip(b"\x00").decode("utf-8", errors="strict")
    offset = decode_words(data, 1)[0]
    if offset + WORD > len(data):
        raise AbiDecodeError("string offset out of range")
    length = int.from_bytes(data[offset:offset + WORD], "big")
    start = offset + WORD
    if length > 4096 or start + length > len(data):
        raise AbiDecodeError("string length out of range")
    try:
        return data[start:start + length].decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise AbiDecodeError("string is not valid UTF-8") from exc
