"""MOCK/UNIT: ABI helpers and the contract registry (no network)."""
import unittest

from trading_platform.dex import abi
from trading_platform.dex.errors import AbiDecodeError, InvalidInputError
from trading_platform.dex.registry import DEFAULT_REGISTRY, VerificationStatus


class AbiTests(unittest.TestCase):
    def test_keccak_known_vectors(self):
        self.assertEqual(abi.keccak256_py(b"").hex(), "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")
        self.assertEqual(abi.keccak256(b"").hex(), abi.keccak256_py(b"").hex())

    def test_known_selectors(self):
        known = {"transfer(address,uint256)": "a9059cbb", "balanceOf(address)": "70a08231",
                 "decimals()": "313ce567", "symbol()": "95d89b41", "getReserves()": "0902f1ac",
                 "getPair(address,address)": "e6a43905", "getAmountsOut(uint256,address[])": "d06ca61f",
                 "getPool(address,address,uint24)": "1698ee82", "slot0()": "3850c7bd",
                 "quoteExactInputSingle((address,address,uint256,uint24,uint160))": "c6a5026a"}
        for sig, sel in known.items():
            self.assertEqual(abi.function_selector(sig).hex(), sel, sig)

    def test_eip55(self):
        good = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
        self.assertTrue(abi.is_checksum_address(good))
        self.assertEqual(abi.normalize_address(good.lower()), good)
        with self.assertRaises(InvalidInputError):
            abi.normalize_address("0x5AAeb6053F3E94C9b9A09f33669435E7Ef1BeAed")  # wrong casing
        with self.assertRaises(InvalidInputError):
            abi.normalize_address("0x1234")

    def test_encode_decode(self):
        data = abi.encode_call_uint_address_array("f(uint256,address[])", 5, [abi.ZERO_ADDRESS])
        self.assertEqual(len(data), 4 + 32 * 4)
        with self.assertRaises(InvalidInputError):
            abi.encode_uint(-1)
        with self.assertRaises(InvalidInputError):
            abi.encode_uint(1 << 24, 24)
        s = abi.encode_uint(32) + abi.encode_uint(4) + b"USDT".ljust(32, b"\0")
        self.assertEqual(abi.decode_string(s), "USDT")
        self.assertEqual(abi.decode_string(b"MKR".ljust(32, b"\0")), "MKR")
        with self.assertRaises(AbiDecodeError):
            abi.decode_uint(b"\x01")
        with self.assertRaises(AbiDecodeError):
            abi.word_to_address(b"\xff" * 32)


class RegistryTests(unittest.TestCase):
    def test_all_addresses_checksum_valid_and_sourced(self):
        for record in DEFAULT_REGISTRY.all():
            self.assertTrue(abi.is_checksum_address(record.address), record.key)
            self.assertTrue(record.source and record.network and record.chain_id == 56)

    def test_usdt_address_is_exactly_the_one_in_the_brief(self):
        self.assertEqual(DEFAULT_REGISTRY.address("usdt"), "0x55d398326f99059fF775485246999027B3197955")
        self.assertEqual(DEFAULT_REGISTRY.get("usdt").status, VerificationStatus.USER_SPECIFIED)

    def test_wbnb_is_not_claimed_docs_verified(self):
        self.assertEqual(DEFAULT_REGISTRY.get("wbnb").status, VerificationStatus.SECONDARY_SOURCES_ONLY)
        self.assertIn("equals PancakeSwap V2 router.WETH()", DEFAULT_REGISTRY.get("wbnb").runtime_checks)


if __name__ == "__main__":
    unittest.main()
