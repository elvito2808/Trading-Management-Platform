"""MOCK/UNIT: JSON-RPC safety, BSC adapter (fake chain; not live connectivity)."""
import json
import unittest

from dex_fake_chain import NOW, USDT, WBNB, FakeChain
from trading_platform.dex.bsc import BscAdapter, BscAdapterConfig
from trading_platform.dex.errors import (
    ChainIdMismatchError, ConfigurationError, ContractCallError, RpcConnectionError,
    RpcMethodNotAllowedError, RpcResponseError, RpcTimeoutError)
from trading_platform.dex.models import DataValidity
from trading_platform.dex.rpc import JsonRpcClient, READ_ONLY_METHODS, redact_url, validate_rpc_url

URL = "https://rpc.example.invalid/KEY123"


def adapter(chain, **kw):
    return BscAdapter(BscAdapterConfig(rpc_url=URL, **kw), transport=chain, clock=lambda: NOW, sleep=lambda s: None)


class SafetyTests(unittest.TestCase):
    def test_signing_and_broadcast_methods_blocked_before_any_io(self):
        chain = FakeChain()
        client = JsonRpcClient(URL, transport=chain)
        for method in ("eth_sendRawTransaction", "eth_sendTransaction", "eth_sign", "personal_sign",
                       "eth_signTransaction", "eth_accounts", "wallet_addEthereumChain", "debug_traceCall"):
            with self.assertRaises(RpcMethodNotAllowedError, msg=method):
                client.call(method, [])
        self.assertEqual(chain.methods_called, [])
        self.assertTrue(all(not m.startswith(("eth_send", "eth_sign", "personal_")) for m in READ_ONLY_METHODS))

    def test_url_redaction_and_validation(self):
        self.assertNotIn("KEY123", redact_url(URL))
        self.assertNotIn("secret", redact_url("https://user:secret@host.example/v1?apikey=abc"))
        with self.assertRaises(ConfigurationError):
            validate_rpc_url("http://remote.example/")
        with self.assertRaises(ConfigurationError):
            validate_rpc_url("")
        self.assertEqual(validate_rpc_url("http://localhost:8545"), "http://localhost:8545")

    def test_missing_rpc_url_is_a_configuration_error(self):
        with self.assertRaises(ConfigurationError):
            BscAdapterConfig.from_env({})
        self.assertEqual(BscAdapterConfig.from_env({"BSC_RPC_URL": URL}).expected_chain_id, 56)

    def test_bounded_retry_then_success_and_exhaustion(self):
        calls = []

        class Flaky:
            def __init__(self, failures, exc):
                self.failures, self.exc = failures, exc

            def post(self, url, body, timeout):
                calls.append(1)
                if self.failures:
                    self.failures -= 1
                    raise self.exc
                return json.dumps({"jsonrpc": "2.0", "id": json.loads(body)["id"], "result": "0x38"}).encode()

        client = JsonRpcClient(URL, transport=Flaky(2, RpcConnectionError("x")), max_retries=2, sleep=lambda s: None)
        self.assertEqual(client.call("eth_chainId"), "0x38")
        self.assertEqual(len(calls), 3)
        calls.clear()
        client = JsonRpcClient(URL, transport=Flaky(99, RpcTimeoutError("t")), max_retries=2, sleep=lambda s: None)
        with self.assertRaises(RpcTimeoutError):
            client.call("eth_chainId")
        self.assertEqual(len(calls), 3)

    def test_revert_is_not_retried(self):
        chain = FakeChain()
        a = adapter(chain)
        with self.assertRaises(ContractCallError):
            a.eth_call(WBNB, b"\x12\x34\x56\x78")
        self.assertEqual(chain.methods_called.count("eth_call"), 1)


class AdapterTests(unittest.TestCase):
    def test_chain_mismatch_blocks_everything(self):
        chain = FakeChain(); chain.chain_id = 1
        a = adapter(chain)
        with self.assertRaises(ChainIdMismatchError):
            a.get_block_number()
        with self.assertRaises(ChainIdMismatchError):
            a.eth_call(WBNB, b"\x00\x00\x00\x00")
        self.assertNotIn("eth_call", chain.methods_called)
        health = a.health_check()
        self.assertFalse(health.ok)
        self.assertEqual(health.chain_id, None)

    def test_health_ok_unreachable_and_stale(self):
        chain = FakeChain()
        h = adapter(chain).health_check()
        self.assertTrue(h.ok and h.chain_id == 56 and h.block_number == 45_000_000)
        chain.reachable = False
        h = adapter(chain, max_retries=0).health_check()
        self.assertFalse(h.ok)
        self.assertEqual(h.provenance.validity, DataValidity.INVALID)
        chain = FakeChain(); chain.block_age_seconds = 600
        h = adapter(chain).health_check()
        self.assertFalse(h.ok)
        self.assertEqual(h.provenance.validity, DataValidity.STALE)

    def test_token_info_reads_chain_values(self):
        a = adapter(FakeChain())
        info = a.get_token_info(USDT, expected_symbol="USDT")
        self.assertEqual((info.symbol, info.decimals, info.name), ("USDT", 18, "Tether USD"))
        self.assertEqual(info.provenance.validity, DataValidity.VALID)
        self.assertEqual(str(info.amount(10 ** 17)), "0.1 USDT")

    def test_token_symbol_mismatch_and_no_code(self):
        chain = FakeChain(); chain.usdt_symbol = "FAKE"
        info = adapter(chain).get_token_info(USDT, expected_symbol="USDT")
        self.assertEqual(info.provenance.validity, DataValidity.INVALID)
        chain.has_code.discard(USDT.lower())
        with self.assertRaises(ContractCallError):
            adapter(chain).get_token_info(USDT)

    def test_native_balance_units(self):
        bal = adapter(FakeChain()).get_native_balance(USDT)
        self.assertEqual((bal.symbol, bal.raw), ("BNB", 7 * 10 ** 18))


if __name__ == "__main__":
    unittest.main()
