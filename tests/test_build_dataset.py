import unittest

from scripts.build_dataset import adjusted_per, final_vm_for, rating_for


class ValuationModelTests(unittest.TestCase):
    def test_applied_per_uses_history_as_base_and_partially_adjusts_for_peers(self):
        self.assertEqual(adjusted_per(10, 20), (3, 13))
        self.assertEqual(adjusted_per(11.25, 14.75), (1.05, 12.3))
        self.assertEqual(adjusted_per(20, 10), (-3, 17))

    def test_final_vm_uses_adjusted_per_and_three_year_discount(self):
        correction_per, applied_per, final_vm = final_vm_for(1_000, 10, 20, 10)

        self.assertEqual(correction_per, 3)
        self.assertEqual(applied_per, 13)
        self.assertEqual(final_vm, 9_800)

    def test_price_judgement_switches_to_watch_above_minus_ten(self):
        self.assertEqual(rating_for(80, -20), ("★★★★★", "적극 매수"))
        self.assertEqual(rating_for(80, -10), ("★★★★☆", "분할매수"))
        self.assertEqual(rating_for(80, -9.9), ("★★★☆☆", "관찰"))


if __name__ == "__main__":
    unittest.main()
