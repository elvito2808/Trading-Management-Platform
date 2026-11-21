"""LIVE: real BNB Smart Chain RPC and real PancakeSwap contracts. READ-ONLY.

Skipped unless RUN_LIVE_BSC_TESTS=1 and BSC_RPC_URL is set. These are the ONLY tests
whose success counts as evidence of real connectivity; mock tests do not.
Optional: BSC_GAS_SIMULATION_FROM (public address holding BNB) for node gas simulation.
"""
import os
import unittest
from decimal import Decimal

from trading_platform.dex.bsc import BscAdapter, BscAdapterConfig
from trading_platform.dex.dry_run import FIXED_BNB_AMOUNT_WEI, DryRunPreparer
from trading_platform.dex.errors import ChainIdMismatchError
from trading_platform.dex.gas import GasEstimator
from trading_platform.dex.models import DataValidity, Direction, QuoteMode
from trading_platform.dex.pancakeswap_v2 import PancakeSwapV2
from trading_platform.dex.pancakeswap_v3 import PancakeSwapV3
from trading_platform.dex.registry import DEFAULT_REGISTRY as R

LIVE = os.environ.get("RUN_LIVE_BSC_TESTS") == "1" and bool(os.environ.get("BSC_RPC_URL"))


@unittest.skipUnless(LIVE, "set RUN_LIVE_BSC_TESTS=1 and BSC_RPC_URL to run live read-only checks")
class LiveBscTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bsc = BscAdapter(BscAdapterConfig.from_env())
        cls.block = cls.bsc.snapshot_block()
        cls.wbnb = cls.bsc.get_token_info(R.address("wbnb"), "WBNB", 18)
        cls.usdt = cls.bsc.get_token_info(R.address("usdt"), "USDT")

    def test_01_chain_and_health(self):
        self.assertEqual(self.bsc.ensure_expected_chain(), 56)
        self.assertTrue(self.bsc.health_check().ok)

    def test_02_wrong_chain_detected(self):
        wrong = BscAdapter(BscAdapterConfig(rpc_url=os.environ["BSC_RPC_URL"], expected_chain_id=1))
        with self.assertRaises(ChainIdMismatchError):
            wrong.get_block_number()

    def test_03_usdt_token_info(self):
        self.assertEqual(self.usdt.provenance.validity, DataValidity.VALID)
        print("USDT on-chain:", self.usdt.symbol, self.usdt.decimals, self.usdt.name)

    def test_04_v2_deployment_and_pair(self):
        v2 = PancakeSwapV2(self.bsc)
        self.assertEqual(v2.verify_deployment(self.block.number), ())
        self.assertTrue(v2.resolve_pair(self.wbnb.address, self.usdt.address, self.block.number))

    def test_05_v3_deployment_and_pools(self):
        v3 = PancakeSwapV3(self.bsc)
        self.assertEqual(v3.verify_deployment(self.block.number), ())
        tiers, _ = v3.enabled_fee_tiers(self.block.number)
        print("enabled V3 fee tiers (ppm):", tiers)
        self.assertTrue(tiers)

    def test_06_quotes_both_dexes_both_directions(self):
        v2, v3 = PancakeSwapV2(self.bsc), PancakeSwapV3(self.bsc)
        q = v2.quote(Direction.BNB_TO_USDT, QuoteMode.EXACT_IN, FIXED_BNB_AMOUNT_WEI, self.wbnb, self.usdt, self.block)
        self.assertEqual(q.router_crosscheck, "MATCH", q.provenance.issues)
        q = v2.quote(Direction.USDT_TO_BNB, QuoteMode.EXACT_OUT, FIXED_BNB_AMOUNT_WEI, self.usdt, self.wbnb, self.block)
        self.assertEqual(q.router_crosscheck, "MATCH", q.provenance.issues)
        a = v3.quote(Direction.BNB_TO_USDT, QuoteMode.EXACT_IN, FIXED_BNB_AMOUNT_WEI, self.wbnb, self.usdt, self.block)
        b = v3.quote(Direction.USDT_TO_BNB, QuoteMode.EXACT_OUT, FIXED_BNB_AMOUNT_WEI, self.usdt, self.wbnb, self.block)
        self.assertIsNotNone(a.best)
        self.assertIsNotNone(b.best)

    def test_07_full_dry_run(self):
        sender = os.environ.get("BSC_GAS_SIMULATION_FROM") or None
        report = DryRunPreparer(self.bsc, gas=GasEstimator(self.bsc, sender)).prepare_all()
        print(report.to_json())
        self.assertEqual(len(report.directions), 2)
        for d in report.directions:
            self.assertNotEqual(d.status, "NOT_READY", d.validation_errors)


if __name__ == "__main__":
    unittest.main()
