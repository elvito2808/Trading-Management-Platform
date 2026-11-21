"""Structured errors for the BSC / PancakeSwap read-only integration."""
from __future__ import annotations


class DexError(Exception):
    """Base class for every error raised by trading_platform.dex."""


class ConfigurationError(DexError):
    """Missing or invalid configuration (e.g. no RPC URL)."""


class InvalidInputError(DexError):
    """Caller supplied an invalid address, amount or direction."""


class AbiDecodeError(DexError):
    """Returned data could not be decoded as the expected ABI type."""


class RpcError(DexError):
    """Base class for JSON-RPC problems."""


class RpcConnectionError(RpcError):
    """Network-level failure talking to the RPC endpoint."""


class RpcTimeoutError(RpcConnectionError):
    """The RPC endpoint did not answer in time."""


class RpcResponseError(RpcError):
    """The node answered with a JSON-RPC error object."""

    def __init__(self, message: str, code: int | None = None, data: object = None):
        super().__init__(message)
        self.code = code
        self.data = data


class RpcMethodNotAllowedError(RpcError):
    """Raised before any request is sent: the method is not on the read-only allow-list."""


class ChainIdMismatchError(RpcError):
    """The endpoint reports a different chain than the one expected."""


class ContractCallError(DexError):
    """A read-only contract call failed or reverted."""


class PoolNotFoundError(DexError):
    """No pool/pair exists for the requested tokens (or fee tier)."""


class InsufficientLiquidityError(DexError):
    """The pool cannot fill the requested amount."""


class QuoteError(DexError):
    """A quote could not be produced or failed validation."""


class GasEstimationError(DexError):
    """Gas could not be estimated."""
