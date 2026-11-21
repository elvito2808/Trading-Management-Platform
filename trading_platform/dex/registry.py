"""Registry of contract addresses used by the BSC / PancakeSwap integration.

Addresses are never guessed. Every record names its source and an honest
verification status. ``DOCS_VERIFIED`` means the address was read from the
official PancakeSwap developer documentation during Phase 2 (2026-11-20); it does
NOT mean it was checked on-chain from this repository's CI. On-chain cross-checks
(router.factory(), router.WETH(), pool.factory(), token symbol/decimals) are
performed at runtime by the adapters and reported separately.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional

from .abi import normalize_address, same_address
from .errors import ConfigurationError

BSC_CHAIN_ID = 56
BSC_NETWORK = "BNB Smart Chain Mainnet"
REGISTRY_REVIEWED_ON = "2026-11-20"


class VerificationStatus(str, Enum):
    DOCS_VERIFIED = "DOCS_VERIFIED"                      # official PancakeSwap docs
    USER_SPECIFIED = "USER_SPECIFIED"                    # given by the project owner in the mission brief
    SECONDARY_SOURCES_ONLY = "SECONDARY_SOURCES_ONLY"    # third-party sources agree; must be cross-checked on-chain
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class ContractRecord:
    key: str
    name: str
    protocol_version: str
    address: str
    network: str
    chain_id: int
    source: str
    status: VerificationStatus
    runtime_checks: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        normalize_address(self.address)  # raises on malformed / bad-checksum addresses

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key, "name": self.name, "protocol_version": self.protocol_version,
            "address": self.address, "network": self.network, "chain_id": self.chain_id,
            "source": self.source, "verification_status": self.status.value,
            "runtime_checks": list(self.runtime_checks), "notes": self.notes,
            "reviewed_on": REGISTRY_REVIEWED_ON,
        }


_V3_DOC = "https://developer.pancakeswap.finance/contracts/v3/addresses (mirror: developer.pancake.xyz)"
_V2_DOC = "https://developer.pancakeswap.finance/contracts/v2/addresses"

# Fee tiers PancakeSwap V3 may enable (ppm). Which of them are ACTUALLY enabled is
# read from the factory at runtime (feeAmountTickSpacing); this is only the probe list.
V3_FEE_TIER_CANDIDATES_PPM: tuple[int, ...] = (100, 500, 2500, 10000)

BSC_CONTRACTS: tuple[ContractRecord, ...] = (
    ContractRecord(
        key="usdt", name="USDT (BSC-USD / Tether USD)", protocol_version="BEP-20",
        address="0x55d398326f99059fF775485246999027B3197955", network=BSC_NETWORK, chain_id=BSC_CHAIN_ID,
        source="Project owner mission brief (PHASE-2-BSC-PANCAKESWAP-INTEGRATION, section 2)",
        status=VerificationStatus.USER_SPECIFIED,
        runtime_checks=("eth_getCode non-empty", "symbol() == 'USDT'", "decimals() read from chain"),
        notes="Address used exactly as supplied; symbol/decimals are read from the contract, never hard-coded.",
    ),
    ContractRecord(
        key="wbnb", name="WBNB (Wrapped BNB)", protocol_version="BEP-20",
        address="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", network=BSC_NETWORK, chain_id=BSC_CHAIN_ID,
        source="Secondary sources only (multiple third-party repos/SDKs agree); PancakeSwap docs state Router.WETH() returns WBNB",
        status=VerificationStatus.SECONDARY_SOURCES_ONLY,
        runtime_checks=("equals PancakeSwap V2 router.WETH()", "symbol() == 'WBNB'", "decimals() == 18"),
        notes="Primary official page for this address was not retrieved; runtime cross-check against router.WETH() is mandatory.",
    ),
    ContractRecord(
        key="v2_factory", name="PancakeFactory", protocol_version="PancakeSwap V2",
        address="0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73", network=BSC_NETWORK, chain_id=BSC_CHAIN_ID,
        source=_V2_DOC + "; https://docs.pancakeswap.finance/developers/smart-contracts/pancakeswap-exchange/v2-contracts/factory-v2",
        status=VerificationStatus.DOCS_VERIFIED,
        runtime_checks=("eth_getCode non-empty", "equals router.factory()"),
    ),
    ContractRecord(
        key="v2_router", name="PancakeRouter (V2 Router02)", protocol_version="PancakeSwap V2",
        address="0x10ED43C718714eb63d5aA57B78B54704E256024E", network=BSC_NETWORK, chain_id=BSC_CHAIN_ID,
        source=_V2_DOC,
        status=VerificationStatus.DOCS_VERIFIED,
        runtime_checks=("eth_getCode non-empty", "factory() equals registry v2_factory", "WETH() equals registry wbnb"),
        notes="Used read-only for getAmountsOut/getAmountsIn cross-checks only.",
    ),
    ContractRecord(
        key="v3_factory", name="PancakeV3Factory", protocol_version="PancakeSwap V3",
        address="0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865", network=BSC_NETWORK, chain_id=BSC_CHAIN_ID,
        source=_V3_DOC, status=VerificationStatus.DOCS_VERIFIED,
        runtime_checks=("eth_getCode non-empty", "feeAmountTickSpacing(fee) probes", "pool.factory() equals this address"),
    ),
    ContractRecord(
        key="v3_quoter_v2", name="QuoterV2", protocol_version="PancakeSwap V3",
        address="0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997", network=BSC_NETWORK, chain_id=BSC_CHAIN_ID,
        source=_V3_DOC, status=VerificationStatus.DOCS_VERIFIED,
        runtime_checks=("eth_getCode non-empty", "factory() equals registry v3_factory"),
        notes="Called with eth_call only; the quote functions are non-view but simulate the swap and revert internally.",
    ),
    ContractRecord(
        key="v3_swap_router", name="SwapRouter (V3)", protocol_version="PancakeSwap V3",
        address="0x1b81D678ffb9C0263b24A97847620C99d213eB14", network=BSC_NETWORK, chain_id=BSC_CHAIN_ID,
        source=_V3_DOC, status=VerificationStatus.DOCS_VERIFIED,
        notes="Used only as the target of local eth_estimateGas simulations; no transaction is signed or submitted.",
    ),
)

_BY_KEY = {record.key: record for record in BSC_CONTRACTS}


class ContractRegistry:
    def __init__(self, records: Iterable[ContractRecord] = BSC_CONTRACTS):
        items = tuple(records)
        self._records = {r.key: r for r in items}
        if len(self._records) != len(items):
            raise ConfigurationError("duplicate contract keys in registry")

    def get(self, key: str) -> ContractRecord:
        try:
            return self._records[key]
        except KeyError as exc:
            raise ConfigurationError(f"unknown contract key: {key}") from exc

    def address(self, key: str) -> str:
        return self.get(key).address

    def all(self) -> tuple[ContractRecord, ...]:
        return tuple(self._records.values())

    def find_by_address(self, address: str) -> Optional[ContractRecord]:
        for record in self._records.values():
            if same_address(record.address, address):
                return record
        return None

    def to_dicts(self) -> list[dict[str, object]]:
        return [r.to_dict() for r in self._records.values()]


DEFAULT_REGISTRY = ContractRegistry()
