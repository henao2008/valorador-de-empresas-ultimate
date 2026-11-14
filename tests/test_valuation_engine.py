import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from valuation_engine import (DriverAssumptions, dcf_from_fcff, estimate_wacc,
                              project_fcff, validate_market_inputs)


class ValuationEngineTests(unittest.TestCase):
    def test_driver_fcff_reconciles_from_operating_drivers(self):
        a = DriverAssumptions((0.10,), (0.20,), 0.25, 0.05, 0.08, 0.10)
        row = project_fcff(1000, a)[0]
        self.assertAlmostEqual(row["revenue"], 1100)
        self.assertAlmostEqual(row["fcff"], 1100*.20*.75 + 1100*.05 - 1100*.08 - 10)

    def test_wacc_is_weighted_after_tax(self):
        w = estimate_wacc(.04, 1.0, .06, .05, .20, 800, 200)
        self.assertAlmostEqual(w["cost_of_equity"], .10)
        self.assertAlmostEqual(w["wacc"], .8*.10 + .2*.05*.8)

    def test_dcf_dilution_reduces_per_share_value(self):
        no_dilution = dcf_from_fcff([100, 110, 120], .10, .02, shares=100_000_000)
        diluted = dcf_from_fcff([100, 110, 120], .10, .02, shares=100_000_000, annual_dilution=.02)
        self.assertLess(diluted["value_per_share"], no_dilution["value_per_share"])

    def test_invalid_terminal_growth_rejected(self):
        with self.assertRaises(ValueError):
            dcf_from_fcff([10], .08, .08)

    def test_negative_terminal_fcff_rejected(self):
        with self.assertRaises(ValueError):
            dcf_from_fcff([-10, -5], .10, .02)

    def test_missing_shares_and_currency_mismatch_flagged(self):
        flags = validate_market_inputs(100, None, 10_000, "EUR", "USD")
        self.assertTrue(any("Acciones" in f for f in flags))
        self.assertTrue(any("Monedas" in f for f in flags))


if __name__ == "__main__":
    unittest.main()
