from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import URLError

from scripts import fetch_screen_financials as screen_financials


def corp_zip(entries: list[tuple[str, str, str]]) -> bytes:
    lists = "".join(
        "<list>"
        f"<corp_code>{corp_code}</corp_code>"
        f"<corp_name>{name}</corp_name>"
        f"<stock_code>{stock_code}</stock_code>"
        "<modify_date>20260801</modify_date>"
        "</list>"
        for stock_code, corp_code, name in entries
    )
    payload = f"<?xml version='1.0' encoding='UTF-8'?><result>{lists}</result>".encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("CORPCODE.xml", payload)
    return buffer.getvalue()


def row(
    stock_code: str,
    metric: str,
    current: str,
    previous: str,
    *,
    fs_div: str = "CFS",
) -> dict[str, str]:
    mapping = {
        "revenue": ("IS", "ifrs-full_Revenue", "매출액"),
        "operatingProfit": ("IS", "dart_OperatingIncomeLoss", "영업이익"),
        "netIncome": (
            "IS",
            "ifrs-full_ProfitLossAttributableToOwnersOfParent",
            "지배기업 소유주 귀속 당기순이익",
        ),
        "assets": ("BS", "ifrs-full_Assets", "자산총계"),
        "liabilities": ("BS", "ifrs-full_Liabilities", "부채총계"),
        "equity": ("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "자본총계"),
        "operatingCashFlow": (
            "CF",
            "ifrs-full_CashFlowsFromUsedInOperatingActivities",
            "영업활동현금흐름",
        ),
    }
    statement, account_id, account_name = mapping[metric]
    return {
        "stock_code": stock_code,
        "bsns_year": "2025",
        "reprt_code": "11011",
        "rcept_no": "20260331000001",
        "fs_div": fs_div,
        "sj_div": statement,
        "account_id": account_id,
        "account_nm": account_name,
        "thstrm_amount": current,
        "frmtrm_amount": previous,
        "currency": "KRW",
    }


def complete_rows(stock_code: str, *, fs_div: str = "CFS") -> list[dict[str, str]]:
    values = {
        "revenue": ("200,000,000,000", "100,000,000,000"),
        "operatingProfit": ("20,000,000,000", "10,000,000,000"),
        "netIncome": ("15,000,000,000", "10,000,000,000"),
        "assets": ("300,000,000,000", "250,000,000,000"),
        "liabilities": ("120,000,000,000", "110,000,000,000"),
        "equity": ("180,000,000,000", "140,000,000,000"),
    }
    return [
        row(stock_code, metric, current, previous, fs_div=fs_div)
        for metric, (current, previous) in values.items()
    ]


class CorpDirectoryTests(unittest.TestCase):
    def test_corp_zip_maps_only_valid_listed_stock_codes(self) -> None:
        payload = corp_zip(
            [
                ("005930", "00126380", "삼성전자"),
                ("", "00000001", "비상장"),
            ]
        )
        mapping = screen_financials.parse_corp_code_zip(payload)
        self.assertEqual(set(mapping), {"005930"})
        self.assertEqual(mapping["005930"].corp_code, "00126380")
        self.assertEqual(mapping["005930"].corp_name, "삼성전자")

    def test_universe_aliases_are_supported(self) -> None:
        parsed = screen_financials.parse_universe(
            {"securities": [{"srtnCd": "A005930", "itmsNm": "삼성전자", "mrktCtg": "KOSPI"}]}
        )
        self.assertEqual(parsed[0].code, "005930")
        self.assertEqual(parsed[0].market, "KOSPI")

    def test_new_alphanumeric_equity_code_is_supported(self) -> None:
        self.assertEqual(screen_financials.normalize_stock_code("A0001A0"), "0001A0")

    def test_dynamic_annual_year_waits_until_april(self) -> None:
        self.assertEqual(screen_financials.latest_expected_annual_year(date(2026, 3, 31)), 2024)
        self.assertEqual(screen_financials.latest_expected_annual_year(date(2026, 4, 1)), 2025)

    def test_transport_retry_is_small_and_bounded(self) -> None:
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"ok"
        with (
            patch.object(
                screen_financials,
                "urlopen",
                side_effect=[URLError("temporary"), response],
            ) as request,
            patch.object(screen_financials.time, "sleep") as sleep,
        ):
            self.assertEqual(screen_financials.request_bytes("https://example.test"), b"ok")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(sleep.call_count, 1)


class BatchAndMetricTests(unittest.TestCase):
    def test_205_companies_use_small_operational_batches_below_official_limit(self) -> None:
        universe = [
            screen_financials.UniverseSecurity(f"{index:06d}", f"회사 {index}", "KOSPI")
            for index in range(1, 206)
        ]
        directory = {
            item.code: screen_financials.CorpEntry(
                f"{index:08d}", item.name or "", item.code, "20260801"
            )
            for index, item in enumerate(universe, start=1)
        }
        batch_sizes: list[int] = []
        progress_messages: list[str] = []

        def fake_batch(_key: str, corp_codes: list[str], **_kwargs: object) -> list[dict[str, str]]:
            batch_sizes.append(len(corp_codes))
            reverse = {entry.corp_code: entry.stock_code for entry in directory.values()}
            return [entry for corp_code in corp_codes for entry in complete_rows(reverse[corp_code])]

        with (
            patch.object(screen_financials, "fetch_corp_directory", return_value=directory),
            patch.object(screen_financials, "fetch_major_accounts_batch", side_effect=fake_batch),
        ):
            snapshot = screen_financials.build_snapshot(
                universe,
                "secret",
                fetched_at=datetime(2026, 8, 9, 12, 0, tzinfo=screen_financials.KST),
                progress=progress_messages.append,
            )

        self.assertEqual(batch_sizes, [25] * 8 + [5])
        self.assertEqual(snapshot["summary"]["batchCount"], 9)
        self.assertEqual(snapshot["summary"]["reportAvailableCount"], 205)
        self.assertEqual(len(progress_messages), 21)
        self.assertIn("selected 205/205", progress_messages[0])
        self.assertIn("1/9", progress_messages[3])
        self.assertIn("9/9", progress_messages[-1])
        self.assertNotIn("secret", " ".join(progress_messages))

    def test_cfs_is_preferred_and_previous_comparison_is_structured(self) -> None:
        security = screen_financials.UniverseSecurity("005930", "삼성전자", "KOSPI")
        corp = screen_financials.CorpEntry("00126380", "삼성전자", "005930", None)
        rows = complete_rows("005930", fs_div="OFS") + complete_rows("005930", fs_div="CFS")
        for item in rows:
            if item["fs_div"] == "OFS" and item["account_id"] == "ifrs-full_Revenue":
                item["thstrm_amount"] = "999,000,000,000"

        record = screen_financials.company_financial_record(
            security,
            corp,
            rows,
            report_year=2025,
            report_code="11011",
        )

        self.assertEqual(record["fsDiv"], "CFS")
        revenue = record["metrics"]["revenue"]
        self.assertEqual(revenue["current"], 200_000_000_000)
        self.assertEqual(revenue["previous"], 100_000_000_000)
        self.assertEqual(revenue["changePct"], 100.0)
        self.assertEqual(
            record["metrics"]["operatingCashFlow"]["status"],
            "pending_not_available_from_multi_account_api",
        )
        self.assertIn("operatingCashFlow", record["dataQuality"]["pendingMetrics"])
        self.assertEqual(record["dataQuality"]["currencies"], ["KRW"])

    def test_ofs_is_used_when_no_cfs_rows_exist(self) -> None:
        security = screen_financials.UniverseSecurity("005930", "삼성전자", "KOSPI")
        corp = screen_financials.CorpEntry("00126380", "삼성전자", "005930", None)
        record = screen_financials.company_financial_record(
            security,
            corp,
            complete_rows("005930", fs_div="OFS"),
            report_year=2025,
            report_code="11011",
        )
        self.assertEqual(record["fsDiv"], "OFS")
        self.assertEqual(record["status"], "available")


class SafetyTests(unittest.TestCase):
    def test_low_report_coverage_is_rejected(self) -> None:
        universe = [
            screen_financials.UniverseSecurity("000001", "회사 1", "KOSPI"),
            screen_financials.UniverseSecurity("000002", "회사 2", "KOSDAQ"),
        ]
        directory = {
            item.code: screen_financials.CorpEntry(
                f"{index:08d}", item.name or "", item.code, "20260801"
            )
            for index, item in enumerate(universe, start=1)
        }

        with (
            patch.object(screen_financials, "fetch_corp_directory", return_value=directory),
            patch.object(
                screen_financials,
                "fetch_major_accounts_batch",
                return_value=complete_rows("000001"),
            ),
        ):
            with self.assertRaises(screen_financials.DartDataError):
                screen_financials.build_snapshot(universe, "secret")

    def test_fatal_failure_preserves_existing_output_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            universe_path = root / "universe.json"
            output_path = root / "screen-financials.json"
            universe_path.write_text(
                json.dumps({"items": [{"code": "005930"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            output_path.write_bytes(b'{"verified":true}\n')
            before = output_path.read_bytes()

            with patch.object(
                screen_financials,
                "build_snapshot",
                side_effect=screen_financials.DartApiError("020", "limit exceeded"),
            ):
                with self.assertRaises(screen_financials.DartApiError):
                    screen_financials.refresh_file(
                        "secret",
                        universe_path=universe_path,
                        output_path=output_path,
                    )

            self.assertEqual(output_path.read_bytes(), before)
            self.assertFalse(output_path.with_suffix(".json.tmp").exists())


class PreRequestGateTests(unittest.TestCase):
    def test_only_preliminary_eligible_liquid_large_caps_reach_dart(self) -> None:
        universe = [
            screen_financials.UniverseSecurity("000001", "통과 기업", "KOSPI"),
            screen_financials.UniverseSecurity(
                "000002",
                "우선주 후보",
                "KOSPI",
                False,
                ("name_indicates_preferred",),
                "name_likely_preferred",
            ),
            screen_financials.UniverseSecurity(
                "000003",
                "원천 제외",
                "KOSDAQ",
                False,
                ("upstream_policy_exclusion",),
            ),
            screen_financials.UniverseSecurity("000004", "소형주", "KOSDAQ"),
            screen_financials.UniverseSecurity("000005", "저유동성", "KOSDAQ"),
            screen_financials.UniverseSecurity("000006", "시세 없음", "KOSPI"),
        ]
        market = {
            "000001": screen_financials.MarketSecurity(
                "000001",
                screen_financials.MIN_MARKET_CAP,
                screen_financials.MIN_TRADING_VALUE,
            ),
            "000002": screen_financials.MarketSecurity(
                "000002",
                screen_financials.MIN_MARKET_CAP * 2,
                screen_financials.MIN_TRADING_VALUE * 2,
            ),
            "000003": screen_financials.MarketSecurity(
                "000003",
                screen_financials.MIN_MARKET_CAP * 2,
                screen_financials.MIN_TRADING_VALUE * 2,
            ),
            "000004": screen_financials.MarketSecurity(
                "000004",
                screen_financials.MIN_MARKET_CAP - 1,
                screen_financials.MIN_TRADING_VALUE * 2,
            ),
            "000005": screen_financials.MarketSecurity(
                "000005",
                screen_financials.MIN_MARKET_CAP * 2,
                screen_financials.MIN_TRADING_VALUE - 1,
            ),
        }
        directory = {
            "000001": screen_financials.CorpEntry(
                "00000001", "통과 기업", "000001", "20260801"
            )
        }
        requested_batches: list[list[str]] = []

        def fake_batch(
            _key: str, corp_codes: list[str], **_kwargs: object
        ) -> list[dict[str, str]]:
            requested_batches.append(list(corp_codes))
            return complete_rows("000001")

        with (
            patch.object(screen_financials, "fetch_corp_directory", return_value=directory),
            patch.object(
                screen_financials,
                "fetch_major_accounts_batch",
                side_effect=fake_batch,
            ),
        ):
            snapshot = screen_financials.build_snapshot(
                universe,
                "secret",
                market=market,
                market_basis_date="2026-08-07",
            )

        self.assertEqual(requested_batches, [["00000001"]])
        self.assertEqual(len(snapshot["companies"]), len(universe))
        self.assertEqual(snapshot["summary"]["dartTargetCount"], 1)
        self.assertEqual(snapshot["summary"]["dartRequestedCount"], 1)
        self.assertEqual(snapshot["summary"]["preRequestSkippedCount"], 5)
        self.assertEqual(snapshot["summary"]["skippedNameGateCount"], 1)
        self.assertEqual(snapshot["summary"]["skippedUpstreamGateCount"], 2)
        self.assertEqual(snapshot["summary"]["skippedMarketGateCount"], 3)
        self.assertEqual(snapshot["summary"]["corpCodeCoveragePct"], 100.0)
        self.assertEqual(snapshot["summary"]["reportCoveragePct"], 100.0)
        self.assertEqual(snapshot["source"]["marketBasisDate"], "2026-08-07")

        companies = {company["code"]: company for company in snapshot["companies"]}
        self.assertEqual(companies["000001"]["status"], "available")
        self.assertTrue(companies["000001"]["dartRequest"]["requested"])
        self.assertEqual(companies["000002"]["status"], "skipped_name_gate")
        self.assertEqual(companies["000003"]["status"], "skipped_upstream_gate")
        self.assertEqual(companies["000004"]["status"], "skipped_market_gate")
        self.assertEqual(companies["000005"]["status"], "skipped_market_gate")
        self.assertEqual(companies["000006"]["status"], "skipped_market_gate")
        self.assertEqual(
            companies["000004"]["skipReasons"][0]["code"],
            "market_cap_below_minimum",
        )
        self.assertEqual(
            companies["000005"]["skipReasons"][0]["code"],
            "trading_value_below_minimum",
        )
        self.assertEqual(
            companies["000006"]["skipReasons"][0]["code"],
            "missing_market_record",
        )
        self.assertEqual(
            companies["000004"]["metrics"]["revenue"]["status"],
            "not_requested_due_to_pre_screen_gate",
        )

    def test_market_parser_preserves_alphanumeric_codes_and_cli_has_default(self) -> None:
        parsed = screen_financials.parse_market(
            {
                "securities": [
                    {
                        "code": "0001A0",
                        "marketCap": "100,000,000,000",
                        "tradingValue": "100,000,000",
                    }
                ]
            }
        )
        self.assertEqual(parsed["0001A0"].market_cap, 100_000_000_000)
        self.assertEqual(parsed["0001A0"].trading_value, 100_000_000)
        self.assertEqual(
            screen_financials._parser().parse_args([]).market,
            screen_financials.MARKET_PATH,
        )

    def test_name_classification_alone_is_a_conservative_upstream_gate(self) -> None:
        security = screen_financials.parse_universe(
            {
                "companies": [
                    {
                        "code": "0001A0",
                        "name": "테스트우",
                        "market": "KOSPI",
                        "classification": "name_likely_preferred",
                    }
                ]
            }
        )[0]
        decision = screen_financials.dart_target_decision(
            security,
            screen_financials.MarketSecurity(
                "0001A0",
                screen_financials.MIN_MARKET_CAP,
                screen_financials.MIN_TRADING_VALUE,
            ),
        )
        self.assertFalse(decision.target)
        self.assertEqual(decision.status, "skipped_name_gate")
        self.assertEqual(decision.reasons[0]["code"], "name_indicates_preferred")


if __name__ == "__main__":
    unittest.main()
