"""MOCK/UNIT: static guards over the dex package source."""
import pathlib
import re
import unittest

PKG = pathlib.Path(__file__).resolve().parent.parent / "trading_platform" / "dex"


class StaticSafetyTests(unittest.TestCase):
    def sources(self):
        return {p.name: p.read_text() for p in PKG.glob("*.py")}

    def test_no_float_in_financial_code(self):
        for name, text in self.sources().items():
            for line in text.splitlines():
                if "float(" in line:
                    # only the non-financial RPC timeout setting may parse a float
                    self.assertEqual(name, "bsc.py", line)
                    self.assertIn("timeout", line.lower(), line)

    def test_no_signing_sending_or_key_handling_code(self):
        banned = re.compile(r"(sendRawTransaction|eth_sendTransaction|signTransaction|sign_transaction|"
                            r"private[_ ]?key|seed[_ ]?phrase|mnemonic|Account\.from_key|\.transfer\(|approve\()", re.I)
        for name, text in self.sources().items():
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(("#", '"""')) or "never" in stripped.lower() or "no private" in stripped.lower():
                    continue
                if banned.search(line):
                    self.assertIn(name, {"rpc.py", "dry_run.py"}, f"{name}: {line}")

    def test_no_secrets_committed(self):
        secret = re.compile(r"(api[_-]?key\s*=\s*['\"][^'\"]+['\"]|0x[0-9a-fA-F]{64})")
        for name, text in self.sources().items():
            self.assertIsNone(secret.search(text), name)


if __name__ == "__main__":
    unittest.main()
