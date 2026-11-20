"""MOCK/UNIT: PancakeSwap V2/V3 modules and gas against the fake chain (not live)."""
import unittest
from decimal import Decimal
from fractions import Fraction

from dex_fake_chain import NOW, ONE, USDT, V2_PAIR, V3_POOLS, WBNB, FakeChain
from trading_platform.dex import pancakeswap_v2 as v2m
from trading_platform.dex import pancakeswap_v3 as v3m
from trading_platform.dex.bsc import BscAdapter, BscAdapterConfig
from trading_platform.dex.errors import InsufficientLiquidityError, InvalidInputError, PoolNotFoundError
from trading_platform.dex.models import DataValidity, Direction, QuoteMode

TENTH = 10 ** 17


def setup(chain=None):
    chain = chain or FakeChain()
    bsc = BscAdapter(BscAdapterConfig(rpc_url="https://rpc.example.invalid/k"), transport=chain,
                     clock=lambda: NOW, sleep=lambda s: None)
    return chain, bsc, bsc.get_token_info(WBNB, "WBNB", 18), bsc.get_token_info(USDT, "USDT")


class V2MathTests(unittest.TestCase):
    def test_known_value(self):
        # 1000 in, reserves 1_000_000 / 1_000_000, fee 0.25%
        self.assertEqual(v2m.get_amount_out(1000, 10**6, 10**6), 1000 * 9975 * 10**6 // (10**6 * 10000 + 1000 * 9975))

    def test_in_out_inverse_and_rounding(self):
        for target in (10**17, 5 * 10**18, 123456789):
            need = v2m.get_amount_in(target, 30_000_000 * ONE, 50_000 * ONE)
            self.assertGreaterEqual(v2m.get_amount_out(need, 30_000_000 * ONE, 50_000 * ONE), target)
            self.assertLess(v2m.get_amount_out(need - 1, 30_000_000 * ONE, 50_000 * ONE), target)

    def test_invalid_inputs(self):
        with self.assertRaises(InvalidInputError):
            v2m.get_amount_out(0, 1, 1)
        with self.assertRaises(InsufficientLiquidityError):
            v2m.get_amount_out(1, 0, 5)
        with self.assertRaises(InsufficientLiquidityError):
            v2m.get_amount_in(5, 10, 5)

    def test_impact_is_small_for_small_trade_and_fee_excluded(self):
        r_in, r_out = 50_000 * ONE, 30_000_000 * ONE
        out = v2m.get_amount_out(TENTH, r_in, r_out)
        impact = v2m.price_impact_exact_in(TENTH, out, r_in, r_out)
        self.assertGreater(impact, 0)
        # 0.1 BNB into a 50,000 BNB reserve moves the price by ~0.1/50000 = 2e-6 (0.0002%); fee excluded
        self.assertGreater(impact, Decimal("0.0000019"))
        self.assertLess(impact, Decimal("0.0000021"))


class V2QuoteTests(unittest.TestCase):
    def test_exact_in_bnb_to_usdt(self):
        chain, bsc, wbnb, usdt = setup()
        q = v2m.PancakeSwapV2(bsc).quote(Direction.BNB_TO_USDT, QuoteMode.EXACT_IN, TENTH, wbnb, usdt)
        expected = TENTH * 9975 * chain.v2_reserve_usdt // (chain.v2_reserve_wbnb * 10000 + TENTH * 9975)
        self.assertEqual(q.amount_out.raw, expected)
        self.assertEqual(q.router_crosscheck, "MATCH")
        self.assertTrue(q.usable)
        self.assertEqual(q.spot_price_out_per_in, Decimal(600))
        self.assertLess(q.execution_price_out_per_in, q.spot_price_out_per_in)  # fee + impact, not spot
        self.assertEqual(q.protocol_fee.raw, TENTH * 25 // 10000)
        self.assertEqual(q.pool_address, V2_PAIR)

    def test_exact_out_usdt_to_bnb_derives_input(self):
        chain, bsc, wbnb, usdt = setup()
        q = v2m.PancakeSwapV2(bsc).quote(Direction.USDT_TO_BNB, QuoteMode.EXACT_OUT, TENTH, usdt, wbnb)
        self.assertEqual(q.amount_out.raw, TENTH)
        self.assertEqual(q.router_crosscheck, "MATCH")
        self.assertGreater(q.amount_in.amount, Decimal(60))  # ~60.15 USDT for 0.1 BNB at 600 + 0.25% fee
        self.assertLess(q.amount_in.amount, Decimal("61"))
        self.assertEqual(q.amount_in.symbol, "USDT")

    def test_router_mismatch_invalidates(self):
        chain, bsc, wbnb, usdt = setup()
        chain.router_skew = 1
        q = v2m.PancakeSwapV2(bsc).quote(Direction.BNB_TO_USDT, QuoteMode.EXACT_IN, TENTH, wbnb, usdt)
        self.assertEqual(q.router_crosscheck, "MISMATCH")
        self.assertEqual(q.provenance.validity, DataValidity.INVALID)
        self.assertFalse(q.usable)

    def test_router_unavailable_is_reported_not_hidden(self):
        chain, bsc, wbnb, usdt = setup()
        chain.router_available = False
        q = v2m.PancakeSwapV2(bsc).quote(Direction.BNB_TO_USDT, QuoteMode.EXACT_IN, TENTH, wbnb, usdt)
        self.assertEqual(q.router_crosscheck, "UNAVAILABLE")
        self.assertTrue(any("cross-check unavailable" in i for i in q.provenance.issues))

    def test_registry_mismatch_invalidates(self):
        chain, bsc, wbnb, usdt = setup()
        chain.router_weth = USDT
        q = v2m.PancakeSwapV2(bsc).quote(Direction.BNB_TO_USDT, QuoteMode.EXACT_IN, TENTH, wbnb, usdt)
        self.assertEqual(q.provenance.validity, DataValidity.INVALID)
        self.assertTrue(any("router.WETH()" in i for i in q.provenance.issues))

    def test_stale_block_not_usable(self):
        chain, bsc, wbnb, usdt = setup()
        chain.block_age_seconds = 300
        q = v2m.PancakeSwapV2(bsc).quote(Direction.BNB_TO_USDT, QuoteMode.EXACT_IN, TENTH, wbnb, usdt)
        self.assertEqual(q.provenance.validity, DataValidity.STALE)
        self.assertFalse(q.usable)

    def test_missing_pair_and_thin_liquidity(self):
        chain, bsc, wbnb, usdt = setup()
        chain.v2_pair_exists = False
        with self.assertRaises(PoolNotFoundError):
            v2m.PancakeSwapV2(bsc).quote(Direction.BNB_TO_USDT, QuoteMode.EXACT_IN, TENTH, wbnb, usdt)
        chain, bsc, wbnb, usdt = setup()
        chain.v2_reserve_wbnb = TENTH // 2  # pool holds less than the 0.1 BNB we want to buy
        with self.assertRaises(InsufficientLiquidityError):
            v2m.PancakeSwapV2(bsc).quote(Direction.USDT_TO_BNB, QuoteMode.EXACT_OUT, TENTH, usdt, wbnb)


class V3Tests(unittest.TestCase):
    def test_sqrt_price_orientation(self):
        from dex_fake_chain import sqrt_price_x96_for
        s = sqrt_price_x96_for(Fraction(1, 600))  # token1 (WBNB) per token0 (USDT)
        self.assertAlmostEqual(float(v3m.sqrt_price_x96_to_price_token1_per_token0(s, 18, 18)), 1 / 600, places=12)

    def test_enabled_tiers_and_best_pool_exact_in(self):
        chain, bsc, wbnb, usdt = setup()
        chain.v3_quoter_impact[2500] = Fraction(5, 1000)   # make 0.25% tier clearly worse
        qs = v3m.PancakeSwapV3(bsc).quote(Direction.BNB_TO_USDT, QuoteMode.EXACT_IN, TENTH, wbnb, usdt)
        self.assertEqual(qs.enabled_fee_tiers_ppm, (100, 500, 2500, 10000))
        self.assertEqual(len(qs.candidates), 2)
        self.assertEqual(qs.best.pool_fee_ppm, 500)
        self.assertEqual(qs.best.pool_address, V3_POOLS[500])
        self.assertTrue(any("fee tier 100: no pool" in f for f in qs.failures))
        # 0.05% fee + 0.05% simulated impact; spot 600
        self.assertAlmostEqual(float(qs.best.price_impact), 0.0005, places=7)
        self.assertEqual(qs.best.protocol_fee.raw, TENTH * 500 // 1_000_000)
        self.assertEqual(qs.best.gas_units_hint, 150_000)
        self.assertEqual(qs.best.router_crosscheck, "NOT_APPLICABLE")

    def test_exact_out_picks_lowest_input(self):
        chain, bsc, wbnb, usdt = setup()
        qs = v3m.PancakeSwapV3(bsc).quote(Direction.USDT_TO_BNB, QuoteMode.EXACT_OUT, TENTH, usdt, wbnb)
        self.assertEqual(qs.best.amount_out.raw, TENTH)
        self.assertEqual(qs.best.pool_fee_ppm, 500)
        self.assertEqual(min(c.amount_in.raw for c in qs.candidates), qs.best.amount_in.raw)
        # exact-out impact is defined as actual_in/ideal_in - 1 = 1/(1-0.0005) - 1
        self.assertAlmostEqual(float(qs.best.price_impact), 1 / (1 - 0.0005) - 1, places=9)

    def test_reverting_tier_is_recorded_others_survive(self):
        chain, bsc, wbnb, usdt = setup()
        chain.v3_quote_reverts.add(500)
        qs = v3m.PancakeSwapV3(bsc).quote(Direction.BNB_TO_USDT, QuoteMode.EXACT_IN, TENTH, wbnb, usdt)
        self.assertEqual(qs.best.pool_fee_ppm, 2500)
        self.assertTrue(any("fee tier 500" in f for f in qs.failures))

    def test_zero_liquidity_and_foreign_factory_pools_rejected(self):
        chain, bsc, wbnb, usdt = setup()
        chain.v3_liquidity[500] = 0
        chain.v3_pool_factory_override[2500] = WBNB
        with self.assertRaises(PoolNotFoundError):
            v3m.PancakeSwapV3(bsc).quote(Direction.BNB_TO_USDT, QuoteMode.EXACT_IN, TENTH, wbnb, usdt)

    def test_no_pools_at_all(self):
        chain, bsc, wbnb, usdt = setup()
        chain.v3_pools = {}
        with self.assertRaises(PoolNotFoundError):
            v3m.PancakeSwapV3(bsc).quote(Direction.BNB_TO_USDT, QuoteMode.EXACT_IN, TENTH, wbnb, usdt)


if __name__ == "__main__":
    unittest.main()
