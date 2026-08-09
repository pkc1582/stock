import unittest

from scripts.build_dataset import adjusted_per, final_vm_for, rating_for


class ValuationModelTests(unittest.TestCase):
    def test_applied_per_averages_history_and_overseas_peers(self):
        self.assertEqual(adjusted_per(10, 20), 15)
        self.assertEqual(adjusted_per(11.25, 14.75), 13)

    def test_final_vm_uses_adjusted_per_and_three_year_discount(self):
        applied_per, final_vm = final_vm_for(1_000, 10, 20, 10)

        self.assertEqual(applied_per, 15)
        self.assertEqual(final_vm, 11_300)

    def test_price_judgement_switches_to_watch_above_minus_ten(self):
        self.assertEqual(rating_for(80, -20), ("★★★★★", "적극 매수"))
        self.assertEqual(rating_for(80, -10), ("★★★★☆", "분할매수"))
        self.assertEqual(rating_for(80, -9.9), ("★★★☆☆", "관찰"))


if __name__ == "__main__":
    unittest.main()
