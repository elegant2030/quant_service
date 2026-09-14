import unittest

from quant_workbench.derivatives.options import OptionType, black_scholes, implied_volatility


class OptionTests(unittest.TestCase):
    def test_known_call_price_and_iv_round_trip(self):
        result = black_scholes(100, 100, 1, 0.05, 0.2, OptionType.CALL)
        self.assertAlmostEqual(result.price, 10.4506, places=3)
        iv = implied_volatility(result.price, 100, 100, 1, 0.05, OptionType.CALL)
        self.assertAlmostEqual(iv, 0.2, places=5)


if __name__ == "__main__":
    unittest.main()

