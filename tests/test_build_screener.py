from __future__ import annotations

import unittest
from datetime import datetime

from scripts import build_screener


def metric(current: int | None, previous: int | None, status: str | None = None) -> dict:
    change = None
    if current is not None and previous not in (None, 0):
        change = round((current - previous) / abs(previous) * 100, 2)
    return {
        "current": current,
        "previous": previous,
        "changePct": change,
        "status": status or ("available" if current is not None else "missing"),
    }


def financial(code: str, *, debt_ratio: float = 80, include_operating: bool = True) -> dict:
    equity = 150_000_000_000
    metrics = {
        "revenue": metric(250_000_000_000, 200_000_000_000),
        "operatingProfit": metric(30_000_000_000, 20_000_000_000),
        "netIncome": metric(18_000_000_000, 14_000_000_000),
        "assets": metric(500_000_000_000, 450_000_000_000),
        "liabilities": metric(int(equity * debt_ratio / 100), 100_000_000_000),
        "equity": metric(equity, 130_000_000_000),
        "operatingCashFlow": metric(
            None,
            None,
            "pending_not_available_from_multi_account_api",
        ),
    }
    if not include_operating:
        metrics["revenue"] = metric(None, None)
        metrics["operatingProfit"] = metric(None, None)
    return {
        "code": code,
        "status": "partial",
        "reportYear": 2025,
        "reportCode": "11011",
        "metrics": metrics,
    }


def market(code: str, *, cap: int = 1_000_000_000_000, value: int = 2_000_000_000) -> dict:
    return {
        "srtnCd": f"A{code}",
        "itmsNm": f"회사 {code}",
        "mrktCtg": "KOSPI",
        "clpr": 50_000,
        "mrktTotAmt": cap,
        "accTrdval": value,
        "basDt": "20260807",
    }


class ScreeningPolicyTests(unittest.TestCase):
    def test_new_alphanumeric_equity_code_is_supported(self) -> None:
        self.assertEqual(build_screener.normalize_stock_code("A0001A0"), "0001A0")

    def test_financial_company_is_exempt_from_general_debt_and_revenue_gates(self) -> None:
        universe = {
            "securities": [
                {"code": "000001", "name": "일반기업", "sector": "제조업"},
                {"code": "000002", "name": "금융기업", "sector": "은행"},
                {"code": "000003", "name": "고부채기업", "sector": "제조업"},
            ]
        }
        markets = {"items": [market("000001"), market("000002"), market("000003")]}
        financials = {
            "companies": [
                financial("000001"),
                financial("000002", debt_ratio=1_200, include_operating=False),
                financial("000003", debt_ratio=300),
            ]
        }

        output = build_screener.build_screener(
            universe,
            markets,
            financials,
            generated_at=datetime(2026, 8, 9, 12, 0, tzinfo=build_screener.KST),
        )

        candidate_codes = {item["code"] for item in output["candidates"]}
        self.assertEqual(candidate_codes, {"000001", "000002"})
        financial_candidate = next(item for item in output["candidates"] if item["code"] == "000002")
        self.assertEqual(financial_candidate["companyType"], "financial")
        self.assertGreater(financial_candidate["metrics"]["debtRatioPct"], 250)
        self.assertIsNone(financial_candidate["caqm"])
        self.assertEqual(financial_candidate["caqmStatus"], "not_reviewed")
        self.assertEqual(financial_candidate["screenStatus"], "review_pending")
        self.assertEqual(financial_candidate["metrics"]["fcfStatus"], "pending_capex_not_collected")

        rejected = next(item for item in output["rejected"] if item["code"] == "000003")
        reason_codes = {reason["code"] for reason in rejected["rejectionReasons"]}
        self.assertIn("debt_ratio_above_maximum", reason_codes)
        self.assertIn("부채비율", rejected["exclusionReason"])

    def test_public_aliases_and_summary_cover_every_company(self) -> None:
        universe = {"items": [{"srtnCd": "000001", "itmsNm": "테스트", "mrktCtg": "KOSDAQ"}]}
        output = build_screener.build_screener(
            universe,
            {"companies": [market("000001")]},
            {"companies": [financial("000001")]},
        )
        self.assertEqual(output["summary"]["total"], 1)
        self.assertEqual(output["summary"]["pending"], 1)
        self.assertEqual(len(output["companies"]), 1)
        item = output["companies"][0]
        self.assertEqual(item["screenScore"], item["quantScore"])
        self.assertEqual(item["currentPrice"], 50_000)
        self.assertEqual(item["marketCapEok"], 10_000)
        self.assertEqual(item["reportPeriod"], "2025-12-31")
        self.assertIsNotNone(item["roe"])
        self.assertTrue(item["quantPassed"])
        self.assertTrue(item["reviewPending"])
        self.assertEqual(output["basisDate"], "2026-08-07")

    def test_universe_exclusion_is_preserved_with_source_reason(self) -> None:
        universe = {
            "companies": [
                {
                    "code": "000001",
                    "name": "제외기업",
                    "eligible": False,
                    "exclusionReasons": ["우선주"],
                }
            ]
        }
        output = build_screener.build_screener(
            universe,
            {"items": [market("000001")]},
            {"companies": [financial("000001")]},
        )
        self.assertEqual(output["summary"]["candidateCount"], 0)
        reason = output["rejected"][0]["rejectionReasons"][0]
        self.assertEqual(reason["code"], "universe_exclusion")
        self.assertEqual(reason["message"], "우선주")

    def test_pre_request_skip_keeps_primary_reason_without_financial_noise(self) -> None:
        output = build_screener.build_screener(
            {"companies": [{"code": "000001", "name": "소형기업"}]},
            {"items": [market("000001", cap=50_000_000_000)]},
            {
                "companies": [
                    {
                        "code": "000001",
                        "status": "skipped_market_gate",
                        "skipReasons": [
                            {
                                "code": "market_cap_below_minimum",
                                "source": "market",
                                "message": "시가총액이 1,000억원 미만입니다.",
                            }
                        ],
                        "metrics": {},
                    }
                ]
            },
        )

        reason_codes = {
            reason["code"] for reason in output["rejected"][0]["rejectionReasons"]
        }
        self.assertEqual(reason_codes, {"market_cap_below_minimum"})
        self.assertEqual(len(output["rejected"][0]["rejectionReasons"]), 1)
        self.assertNotIn("missing_net_income", reason_codes)
        self.assertNotIn("missing_revenue", reason_codes)

    def test_synced_universe_exclusion_and_limitations_are_propagated(self) -> None:
        universe = {
            "managed_item_checked": False,
            "dataLimitations": ["관리종목 자동 확인 미구현"],
            "securities": [
                {
                    "code": "000001",
                    "corporationName": "테스트우",
                    "eligibleForPreliminaryScreen": False,
                    "exclusionReason": "우선주",
                }
            ],
        }
        output = build_screener.build_screener(
            universe,
            {"basisDate": "20260807", "items": [market("000001")]},
            {"companies": [financial("000001")]},
        )
        self.assertFalse(output["managedItemChecked"])
        self.assertIn("관리종목 자동 확인 미구현", output["dataLimitations"])
        self.assertTrue(any("FCF" in item for item in output["dataLimitations"]))
        self.assertEqual(output["rejected"][0]["exclusionReason"], "우선주")

    def test_financial_name_and_trading_volume_aliases_are_recognized(self) -> None:
        universe = {"items": [{"code": "000001", "corporationName": "카카오뱅크"}]}
        market_record = market("000001")
        market_record.pop("accTrdval")
        market_record["tradingVolume"] = 100_000
        output = build_screener.build_screener(
            universe,
            {"items": [market_record]},
            {"companies": [financial("000001", debt_ratio=1_200, include_operating=False)]},
        )
        self.assertEqual(output["candidateCount"] if "candidateCount" in output else output["summary"]["candidateCount"], 1)
        candidate = output["candidates"][0]
        self.assertEqual(candidate["companyType"], "financial")
        self.assertEqual(candidate["metrics"]["liquidityBasis"], "price_times_volume")

    def test_non_krw_financials_wait_for_currency_conversion(self) -> None:
        record = financial("000001")
        for value in record["metrics"].values():
            value["currency"] = "USD"
        output = build_screener.build_screener(
            {"items": [{"code": "000001", "name": "외화공시기업"}]},
            {"items": [market("000001")]},
            {"companies": [record]},
        )
        reasons = {
            reason["code"] for reason in output["rejected"][0]["rejectionReasons"]
        }
        self.assertIn("currency_conversion_pending", reasons)

    def test_low_market_code_coverage_is_rejected(self) -> None:
        codes = [f"{index:06d}" for index in range(1, 11)]
        with self.assertRaises(build_screener.ScreenerError):
            build_screener.build_screener(
                {"items": [{"code": code} for code in codes]},
                {"items": [market(code) for code in codes[:8]]},
                {"companies": [financial(code) for code in codes]},
            )

    def test_mixed_universe_and_market_dates_are_rejected(self) -> None:
        with self.assertRaises(build_screener.ScreenerError):
            build_screener.build_screener(
                {"basisDate": "2026-08-06", "items": [{"code": "000001"}]},
                {"basisDate": "2026-08-07", "items": [market("000001")]},
                {"companies": [financial("000001")]},
            )


class RankingTests(unittest.TestCase):
    def test_top_limit_records_every_outside_company_as_rejected(self) -> None:
        codes = [f"{index:06d}" for index in range(1, 6)]
        universe = {"securities": [{"code": code, "sector": "제조업"} for code in codes]}
        markets = {
            "items": [
                market(code, cap=1_000_000_000_000 + index * 100_000_000_000)
                for index, code in enumerate(codes)
            ]
        }
        financials = {"companies": [financial(code) for code in codes]}

        output = build_screener.build_screener(
            universe,
            markets,
            financials,
            candidate_limit=2,
        )

        self.assertEqual(len(output["candidates"]), 2)
        self.assertEqual(len(output["rejected"]), 3)
        self.assertEqual(len(output["companies"]), 5)
        for rejected in output["rejected"]:
            self.assertIn(
                "outside_top_quant_cutoff",
                {reason["code"] for reason in rejected["rejectionReasons"]},
            )
        self.assertEqual(output["summary"]["candidateCount"] + output["summary"]["rejectedCount"], 5)

        compact = build_screener.compact_public_output(output)
        self.assertNotIn("candidates", compact)
        self.assertNotIn("rejected", compact)
        self.assertEqual(len(compact["companies"]), 5)
        self.assertEqual(len(compact["candidateCodes"]), 2)


if __name__ == "__main__":
    unittest.main()
