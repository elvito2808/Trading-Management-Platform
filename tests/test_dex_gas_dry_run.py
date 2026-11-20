"""MOCK/UNIT: gas estimation and dry-run orchestration against the fake chain (not live)."""
import json
import unittest
from decimal import Decimal

from dex_fake_chain import NOW, ONE, USDT, V2_ROUTER, WBNB, FakeChain
from trading_platform.dex import abi
from trading_platform.dex.bsc import BscAdapter, BscAdapterConfig
from trading_platform.dex.dry_run import FIXED_BNB_AMOUNT_WEI, DryRunPreparer
from trading_platform.dex.errors import InvalidInputError
from trading_platform.dex.gas import GasEstimator, build_v2_swap_exact_eth_for_tokens_call, convert_bnb_cost_to_usdt
from trading_platform.dex.models import Direction, GasStatus, NonSendablePlan
from trading_platform.dex.rpc import READ_ONLY_METHODS

SENDER = "0x" + "ab" * 20  # public placeholder; never holds keys here


def make(chain=None, sender=None):
    chain = chain or FakeChain()
    bsc = BscAdapter(BscAdapterConfig(rpc_url="https://rpc.example.invalid/k"), transport=chain,
                     clock=lambda: NOW, sleep=lambda s: None)
    return chain, bsc, DryRunPreparer(bsc, gas=GasEstimator(bsc, simulation_sender=sender))


class GasTests(unittest.TestCase):
    def test_conversion_rounds_up_and_rejects_bad_price(self):
        chain, bsc, _ = make()
        usdt = bsc.get_token_info(USDT, "USDT")
        c = convert_bnb_cost_to_usdt(1, Decimal("600"), usdt)  # 1 wei of BNB -> 600 raw units exactly
        self.assertEqual(c.raw, 600)
        c = convert_bnb_cost_to_usdt(1, Decimal("0.0000000000000000001"), usdt)
        self.assertEqual(c.raw, 1)  # rounded UP, never down to zero
        with self.assertRaises(InvalidInputError):
            convert_bnb_cost_to_usdt(1, Decimal(0), usdt)

    def test_simulation_calldata_shape(self):
        _, bsc, _ = make()
        call = build_v2_swap_exact_eth_for_tokens_call(V2_ROUTER, WBNB, USDT, FIXED_BNB_AMOUNT_WEI, SENDER, bsc.get_block())
        data = abi.hex_to_bytes(call["data"])
        self.assertEqual(data[:4].hex(), "7ff36ab5")
        self.assertEqual(abi.word_to_int(data[4:], 1), 128)               # offset of path
        self.assertEqual(abi.word_to_address(data[4:], 2), abi.normalize_address(SENDER))
        self.assertEqual(abi.word_to_int(data[4:], 4), 2)                 # path length
        self.assertEqual(abi.word_to_address(data[4:], 5), WBNB)
        self.assertEqual(abi.word_to_address(data[4:], 6), USDT)
        self.assertEqual(int(call["value"], 16), FIXED_BNB_AMOUNT_WEI)
        self.assertNotIn("nonce", call)
        self.assertNotIn("signature", call)

    def test_node_estimate_partial_hint_and_unknown(self):
        chain, bsc, _ = make()
        usdt, block = bsc.get_token_info(USDT, "USDT"), bsc.get_block()
        est = GasEstimator(bsc, SENDER)
        call = build_v2_swap_exact_eth_for_tokens_call(V2_ROUTER, WBNB, USDT, FIXED_BNB_AMOUNT_WEI, SENDER, block)
        chain.estimate_gas_result = 130_000
        g = est.estimate(Decimal(600), "test price", usdt, block, node_call=call, quoter_gas_hint=150_000)
        self.assertEqual((g.status, g.gas_units), (GasStatus.ESTIMATED_NODE, 130_000))
        self.assertEqual(g.cost.raw, 130_000 * 3 * 10 ** 9)
        self.assertEqual(g.cost_usdt.raw, 130_000 * 3 * 10 ** 9 * 600)
        self.assertTrue(g.is_estimate_not_payment)
        chain.estimate_gas_result = None  # node simulation reverts -> fall back, flagged partial
        g = est.estimate(Decimal(600), "test price", usdt, block, node_call=call, quoter_gas_hint=150_000)
        self.assertEqual(g.status, GasStatus.PARTIAL_POOL_ONLY)
        self.assertTrue(any("understates" in n for n in g.notes))
        g = est.estimate(Decimal(600), "test price", usdt, block, node_call=call, quoter_gas_hint=None)
        self.assertEqual(g.status, GasStatus.UNKNOWN)
        self.assertIsNone(g.cost)
        self.assertIsNone(g.gas_units)  # no invented number

    def test_gas_price_failure_is_unknown(self):
        chain, bsc, _ = make()
        usdt, block = bsc.get_token_info(USDT, "USDT"), bsc.get_block()
        chain.gas_price_wei = None
        g = GasEstimator(bsc).estimate(Decimal(600), "p", usdt, block, quoter_gas_hint=1)
        self.assertEqual(g.status, GasStatus.UNKNOWN)


class DryRunTests(unittest.TestCase):
    def test_both_directions_fixed_size_and_selection(self):
        chain, bsc, prep = make()
        report = prep.prepare_all()
        self.assertEqual(len(report.directions), 2)
        a, b = report.directions
        self.assertEqual(a.direction, Direction.BNB_TO_USDT)
        self.assertEqual(a.selected.amount_in.raw, FIXED_BNB_AMOUNT_WEI)          # sells exactly 0.1 BNB
        self.assertEqual(b.selected.amount_out.raw, FIXED_BNB_AMOUNT_WEI)         # buys exactly 0.1 BNB
        self.assertEqual(b.selected.amount_in.symbol, "USDT")
        # V3 0.05% tier beats V2 0.25% in the fixture
        self.assertEqual(a.selected.dex, "PancakeSwap V3")
        self.assertEqual(max(q.amount_out.raw for q in (a.v2_quote, a.v3_best)), a.selected.amount_out.raw)
        self.assertEqual(min(q.amount_in.raw for q in (b.v2_quote, b.v3_best)), b.selected.amount_in.raw)
        self.assertEqual(a.status, "READY_GAS_PARTIAL")  # pool-only gas cannot claim READY
        self.assertEqual(a.gas.status, GasStatus.PARTIAL_POOL_ONLY)
        self.assertIsNone(a.net_output)
        self.assertIsNone(b.net_output)
        self.assertIn("withheld", a.net_basis)

    def test_v2_only_node_gas_estimate(self):
        chain, bsc, prep = make(sender=SENDER)
        chain.v3_pools = {}                      # force V2 as the only venue
        chain.estimate_gas_result = 140_000
        a = prep.prepare_direction(Direction.BNB_TO_USDT)
        self.assertEqual(a.selected.dex, "PancakeSwap V2")
        self.assertEqual(a.gas.status, GasStatus.ESTIMATED_NODE)
        self.assertEqual(a.status, "READY")
        self.assertTrue(a.v3_failures)
        b = prep.prepare_direction(Direction.USDT_TO_BNB)   # no node simulation path for direction B on V2
        self.assertEqual(b.gas.status, GasStatus.UNKNOWN)
        self.assertEqual(b.status, "READY_GAS_UNKNOWN")
        self.assertIsNone(b.net_output)
        self.assertIn("withheld", b.net_basis)

    def test_no_usable_quote_is_not_ready(self):
        chain, bsc, prep = make()
        chain.v3_pools = {}
        chain.router_skew = 1                    # V2 mismatch -> INVALID
        a = prep.prepare_direction(Direction.BNB_TO_USDT)
        self.assertIsNone(a.selected)
        self.assertEqual(a.status, "NOT_READY")
        self.assertIsNone(a.plan)

    def test_dynamic_sizing_rejected(self):
        _, _, prep = make()
        with self.assertRaises(InvalidInputError):
            prep.prepare_direction(Direction.BNB_TO_USDT, amount_wei=2 * ONE)
        prep.prepare_direction(Direction.BNB_TO_USDT, amount_wei=FIXED_BNB_AMOUNT_WEI)

    def test_unhealthy_rpc_yields_no_fake_results(self):
        chain, bsc, prep = make()
        chain.chain_id = 1
        r = prep.prepare_all()
        self.assertEqual(r.directions, ())
        self.assertFalse(r.rpc_responses_received)
        self.assertTrue(r.token_issues)
        chain2, _, prep2 = make()
        chain2.usdt_symbol = "FAKE"              # token validation failure is reported, not papered over
        r2 = prep2.prepare_all()
        self.assertEqual(r2.directions, ())
        self.assertTrue(any("failed validation" in i for i in r2.token_issues))

    def test_nothing_is_signed_or_sent(self):
        chain, bsc, prep = make(sender=SENDER)
        chain.estimate_gas_result = 140_000
        report = prep.prepare_all()
        self.assertTrue(set(chain.methods_called) <= READ_ONLY_METHODS)
        self.assertFalse([m for m in chain.methods_called if m.startswith(("eth_send", "eth_sign", "personal_"))])
        self.assertEqual(report.safety["transactions_signed"], 0)
        self.assertEqual(report.safety["transactions_broadcast"], 0)
        self.assertFalse(report.safety["wallet_required"])
        plan = report.directions[0].plan
        self.assertIsInstance(plan, NonSendablePlan)
        self.assertFalse(plan.sendable)
        for attr in ("sign", "send", "broadcast", "nonce", "private_key", "calldata", "data"):
            self.assertFalse(hasattr(plan, attr), attr)

    def test_report_is_json_with_units(self):
        _, _, prep = make()
        doc = json.loads(prep.prepare_all().to_json())
        self.assertEqual(doc["network"]["chain_id"], 56)
        d = doc["directions"][0]
        self.assertEqual(d["selected"]["amount_in"]["symbol"], "WBNB")
        self.assertEqual(d["selected"]["amount_in"]["amount"], "0.1")
        self.assertIn("raw", d["selected"]["amount_out"])
        self.assertTrue(all("verification_status" in c for c in doc["contract_registry"]))
        self.assertNotIn("KEY", json.dumps(doc))  # no RPC credentials leak into the report


if __name__ == "__main__":
    unittest.main()
