import unittest

from scripts.build_dataset import (
    adjusted_per,
    bank_pbr_vm_for,
    final_vm_for,
    financial_hybrid_vm_for,
    insurance_sotp_vm_for,
    normalized_memory_vm_for,
    rating_for,
    valuation_scenarios_for,
    vm_confidence_for,
    change_log_for,
    apply_official_final_vm,
)


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

    def test_memory_vm_uses_normalized_eps_and_two_year_discount(self):
        self.assertEqual(normalized_memory_vm_for(72_398, 6.5, 10), (471_000, 389_000))
        self.assertEqual(normalized_memory_vm_for(512_706, 7, 11), (3_589_000, 2_913_000))

    def test_bank_vm_uses_pbr_as_primary_and_per_as_cross_check(self):
        primary, cross_check, final_vm = bank_pbr_vm_for(169_091, 1.10, 19_579, 9.5, 1_000)

        self.assertEqual(primary, 186_000)
        self.assertEqual(cross_check, 186_000)
        self.assertEqual(final_vm, primary)

    def test_mixed_financial_vm_blends_primary_and_cross_check(self):
        self.assertEqual(
            financial_hybrid_vm_for(60_921, 1.30, 16_626, 7.5, 0.60),
            (79_200, 124_700, 97_400),
        )

    def test_insurance_vm_keeps_csm_sotp_adjustment_separate(self):
        self.assertEqual(
            insurance_sotp_vm_for(433_589, 1.0, 65_000, 51_220, 8),
            (498_600, 409_800, 498_600),
        )

    def test_memory_scenarios_keep_base_and_bound_it_with_downside_and_upside(self):
        valuation = {
            "valuationModel": "memory_normalized",
            "valuationHorizonYears": 2,
            "normalizedEps": 72_398,
            "normalizedPer": 6.5,
            "discountRate": 10,
            "finalVm": 389_000,
        }
        scenarios = valuation_scenarios_for(valuation, {"roundingUnit": 1_000})

        self.assertLess(scenarios["conservative"]["finalVm"], 389_000)
        self.assertEqual(scenarios["base"]["finalVm"], 389_000)
        self.assertGreater(scenarios["optimistic"]["finalVm"], 389_000)

    def test_official_master_vm_preserves_model_calculation_for_audit(self):
        valuation = {"valuationModel": "standard_per", "finalVm": 280_000}

        result = apply_official_final_vm(
            "192820",
            valuation,
            {"officialFinalVm": 288_000, "officialVmSource": "공식 마스터"},
        )

        self.assertEqual(result["finalVm"], 288_000)
        self.assertEqual(result["modelCalculatedVm"], 280_000)
        self.assertTrue(result["officialVmOverride"])
        self.assertEqual(result["officialVmSource"], "공식 마스터")

        scenario_input = {
            **result,
            "valuationHorizonYears": 3,
            "forwardEps3y": 40_000,
            "appliedPer": 10,
            "discountRate": 11,
        }
        scenarios = valuation_scenarios_for(scenario_input, {"roundingUnit": 100})
        self.assertLess(scenarios["conservative"]["finalVm"], 288_000)
        self.assertEqual(scenarios["base"]["finalVm"], 288_000)
        self.assertGreater(scenarios["optimistic"]["finalVm"], 288_000)

    def test_draft_vm_with_complete_report_is_confidence_c(self):
        company = {
            "financials": {"latestReport": {"dataQuality": "complete"}},
            "sources": [{"url": "a"}, {"url": "b"}],
        }
        assumption = {
            "status": "draft",
            "forwardEps3y": 100,
            "historicalPer5y": 10,
        }

        grade, _, reasons = vm_confidence_for(company, assumption)

        self.assertEqual(grade, "C")
        self.assertIn("VM 가정 최종 승인 전", reasons)

    def test_change_log_detects_vm_gap_cavm_and_report_changes(self):
        previous = {
            "generatedAt": "2026-08-12T10:00:00+09:00",
            "companies": [{
                "code": "005930",
                "name": "삼성전자",
                "finalVm": 500_000,
                "gapRate": -40,
                "cavm": 90,
                "financials": {"latestReport": {"rceptNo": "old", "periodEnd": "2025-12-31"}},
            }],
        }
        current = [{
            "code": "005930",
            "name": "삼성전자",
            "rank": 1,
            "finalVm": 389_000,
            "gapRate": -41,
            "cavm": 91,
            "financials": {"latestReport": {"rceptNo": "new", "periodEnd": "2026-03-31", "periodLabel": "1분기"}},
        }]

        changes = change_log_for(previous, current, "2026-08-13T10:00:00+09:00")

        self.assertTrue(changes["hasMaterialChanges"])
        self.assertEqual(len(changes["vm"]), 1)
        self.assertEqual(len(changes["gap"]), 1)
        self.assertEqual(len(changes["cavm"]), 1)
        self.assertEqual(len(changes["reports"]), 1)


if __name__ == "__main__":
    unittest.main()
