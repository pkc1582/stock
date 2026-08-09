from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from scripts import fetch_dart


def statement_rows(
    candidate: fetch_dart.ReportCandidate,
    *,
    receipt: str = "20260809000001",
    eps: str | None = None,
    include_operating: bool = True,
) -> list[dict[str, str]]:
    meta = {
        "rcept_no": receipt,
        "reprt_code": candidate.report_code,
        "bsns_year": str(candidate.report_year),
    }
    interim = not candidate.is_annual
    revenue_current = "3,000,000,000" if interim else "12,000,000,000"
    revenue_cumulative = "6,000,000,000" if interim else ""
    operating_current = "300,000,000" if interim else "1,200,000,000"
    operating_cumulative = "600,000,000" if interim else ""
    profit_current = "200,000,000" if interim else "1,000,000,000"
    profit_cumulative = "400,000,000" if interim else ""
    eps_current = eps if eps is not None else ("300" if interim else "1,000")
    eps_cumulative = eps if eps is not None else ("600" if interim else "")

    rows = [
        {
            **meta,
            "sj_div": "IS",
            "account_id": "ifrs-full_Revenue",
            "account_nm": "매출액",
            "thstrm_amount": revenue_current,
            "thstrm_add_amount": revenue_cumulative,
        },
        {
            **meta,
            "sj_div": "IS",
            "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
            "account_nm": "지배기업 소유주지분 당기순이익",
            "thstrm_amount": profit_current,
            "thstrm_add_amount": profit_cumulative,
        },
        {
            **meta,
            "sj_div": "BS",
            "account_id": "ifrs-full_Liabilities",
            "account_nm": "부채총계",
            "thstrm_amount": "5,000,000,000",
            "frmtrm_amount": "4,000,000,000",
        },
        {
            **meta,
            "sj_div": "BS",
            "account_id": "ifrs-full_EquityAttributableToOwnersOfParent",
            "account_nm": "자본총계",
            "thstrm_amount": "10,000,000,000",
            "frmtrm_amount": "8,000,000,000",
        },
        {
            **meta,
            "sj_div": "IS",
            "account_id": "ifrs-full_BasicEarningsLossPerShare",
            "account_nm": "기본주당이익",
            "thstrm_amount": eps_current,
            "thstrm_add_amount": eps_cumulative,
        },
    ]
    if include_operating:
        rows.insert(
            1,
            {
                **meta,
                "sj_div": "IS",
                "account_id": "dart_OperatingIncomeLoss",
                "account_nm": "영업이익",
                "thstrm_amount": operating_current,
                "thstrm_add_amount": operating_cumulative,
            },
        )
    return rows


def report(
    candidate: fetch_dart.ReportCandidate,
    *,
    fs_div: str = "CFS",
    include_operating: bool = True,
) -> fetch_dart.StatementReport:
    rows = statement_rows(candidate, include_operating=include_operating)
    return fetch_dart.StatementReport(candidate, fs_div, rows[0]["rcept_no"], rows)


def companies(count: int) -> dict:
    return {
        "financialDataStatus": "existing",
        "companies": [
            {
                "code": f"{index:06d}",
                "financials": {
                    "revenue2025": 1,
                    "operatingMargin2025": 1,
                    "roe2025": 1,
                    "debtRatio2025": 1,
                    "eps2025": 1,
                },
            }
            for index in range(1, count + 1)
        ],
    }


class CandidateTests(unittest.TestCase):
    def test_candidates_on_2026_08_09_start_with_half_q1_and_prior_annual(self) -> None:
        candidates = fetch_dart.report_candidates(date(2026, 8, 9))
        self.assertEqual(
            [(item.report_year, item.report_code) for item in candidates[:3]],
            [(2026, "11012"), (2026, "11013"), (2025, "11011")],
        )

    def test_candidates_generalize_to_future_year(self) -> None:
        candidates = fetch_dart.report_candidates(date(2027, 5, 20))
        self.assertEqual(
            [(item.report_year, item.report_code) for item in candidates[:3]],
            [(2027, "11013"), (2026, "11011"), (2026, "11014")],
        )


class FetchTests(unittest.TestCase):
    def test_cfs_no_data_uses_ofs(self) -> None:
        candidate = fetch_dart.report_candidates(date(2026, 8, 9))[0]
        requested_divisions: list[str] = []

        def fake_request(url: str) -> bytes:
            division = parse_qs(urlparse(url).query)["fs_div"][0]
            requested_divisions.append(division)
            if division == "CFS":
                return json.dumps({"status": "013", "message": "no data"}).encode()
            return json.dumps({"status": "000", "list": statement_rows(candidate)}).encode()

        with patch.object(fetch_dart, "request_bytes", side_effect=fake_request):
            selected = fetch_dart.fetch_report("key", "00000001", candidate)

        self.assertIsNotNone(selected)
        self.assertEqual(selected.fs_div, "OFS")
        self.assertEqual(requested_divisions, ["CFS", "OFS"])

    def test_fatal_status_does_not_try_ofs(self) -> None:
        candidate = fetch_dart.report_candidates(date(2026, 8, 9))[0]
        payload = json.dumps({"status": "020", "message": "limit exceeded"}).encode()
        with patch.object(fetch_dart, "request_bytes", return_value=payload) as request:
            with self.assertRaises(fetch_dart.DartApiError):
                fetch_dart.fetch_report("key", "00000001", candidate)
        self.assertEqual(request.call_count, 1)


class MetricTests(unittest.TestCase):
    def test_interim_flows_use_accumulated_amount_and_bs_uses_current_amount(self) -> None:
        candidate = fetch_dart.report_candidates(date(2026, 8, 9))[0]
        metrics = fetch_dart.report_metrics(report(candidate))
        self.assertEqual(metrics["revenueEok"], 60)
        self.assertEqual(metrics["operatingMargin"], 10.0)
        self.assertEqual(metrics["roeAnnualized"], 8.89)
        self.assertEqual(metrics["debtRatio"], 50.0)
        self.assertEqual(metrics["epsCumulative"], 600)

    def test_annual_compatibility_fields_use_annual_amount_and_accept_negative_eps(self) -> None:
        candidate = fetch_dart.annual_2025_candidate()
        rows = statement_rows(candidate, eps="(1,234)")
        annual = fetch_dart.StatementReport(candidate, "CFS", rows[0]["rcept_no"], rows)
        financials = {"eps2025": 999}

        changed = fetch_dart.update_annual_2025_fields(financials, annual)

        self.assertTrue(changed)
        self.assertEqual(financials["revenue2025"], 120)
        self.assertEqual(financials["operatingMargin2025"], 10.0)
        self.assertEqual(financials["eps2025"], -1234)

    def test_latest_payload_does_not_fill_missing_metric_from_another_report(self) -> None:
        candidate = fetch_dart.report_candidates(date(2026, 8, 9))[0]
        payload = fetch_dart.latest_report_payload(
            report(candidate, include_operating=False),
            datetime(2026, 8, 9, 12, 0, tzinfo=fetch_dart.KST),
        )
        self.assertIsNone(payload["operatingMargin"])
        self.assertEqual(payload["dataQuality"], "partial")
        self.assertIn("operatingMargin", payload["missingMetrics"])


class RefreshSafetyTests(unittest.TestCase):
    def test_below_minimum_coverage_raises_without_mutating_input(self) -> None:
        source = companies(5)
        original = copy.deepcopy(source)
        mapping = {item["code"]: f"corp-{item['code']}" for item in source["companies"]}
        half = fetch_dart.report_candidates(date(2026, 8, 9))[0]

        def fake_fetch(_key: str, corp_code: str, candidate: fetch_dart.ReportCandidate):
            if corp_code in {"corp-000001", "corp-000002", "corp-000003"} and candidate == half:
                return report(candidate)
            return None

        with (
            patch.object(fetch_dart, "corp_code_map", return_value=mapping),
            patch.object(fetch_dart, "fetch_report", side_effect=fake_fetch),
        ):
            with self.assertRaises(fetch_dart.CoverageError):
                fetch_dart.refresh_data(
                    source,
                    "key",
                    as_of=date(2026, 8, 9),
                    fetched_at=datetime(2026, 8, 9, 12, 0, tzinfo=fetch_dart.KST),
                    sleep_seconds=0,
                )

        self.assertEqual(source, original)

    def test_threshold_coverage_is_written_as_partial_status(self) -> None:
        source = companies(5)
        mapping = {item["code"]: f"corp-{item['code']}" for item in source["companies"]}
        half = fetch_dart.report_candidates(date(2026, 8, 9))[0]
        annual = fetch_dart.annual_2025_candidate()
        successful = {"corp-000001", "corp-000002", "corp-000003", "corp-000004"}

        def fake_fetch(_key: str, corp_code: str, candidate: fetch_dart.ReportCandidate):
            if corp_code not in successful:
                return None
            if candidate == half:
                return report(candidate)
            if candidate == annual:
                return report(candidate)
            return None

        with (
            patch.object(fetch_dart, "corp_code_map", return_value=mapping),
            patch.object(fetch_dart, "fetch_report", side_effect=fake_fetch),
        ):
            refreshed, summary = fetch_dart.refresh_data(
                source,
                "key",
                as_of=date(2026, 8, 9),
                fetched_at=datetime(2026, 8, 9, 12, 0, tzinfo=fetch_dart.KST),
                sleep_seconds=0,
            )

        self.assertEqual(refreshed["financialDataStatus"], "partially_updated_from_opendart_latest_reports")
        self.assertEqual(summary["latestReportSuccessCount"], 4)
        self.assertEqual(summary["latestReportFailedCount"], 1)
        self.assertEqual(summary["latestReportSuccessRate"], 0.8)
        self.assertEqual(refreshed["companies"][0]["financials"]["latestReport"]["reportCode"], "11012")

    def test_fatal_error_preserves_file_byte_for_byte(self) -> None:
        source = companies(1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "companies.json"
            path.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            before = path.read_bytes()

            with (
                patch.object(fetch_dart, "corp_code_map", return_value={"000001": "corp-000001"}),
                patch.object(
                    fetch_dart,
                    "fetch_report",
                    side_effect=fetch_dart.DartApiError("020", "limit exceeded"),
                ),
            ):
                with self.assertRaises(fetch_dart.DartApiError):
                    fetch_dart.refresh_file(
                        "key",
                        path=path,
                        as_of=date(2026, 8, 9),
                        fetched_at=datetime(2026, 8, 9, 12, 0, tzinfo=fetch_dart.KST),
                        sleep_seconds=0,
                    )

            self.assertEqual(path.read_bytes(), before)

    def test_main_returns_nonzero_for_fatal_api_error(self) -> None:
        with (
            patch.dict(os.environ, {"DART_API_KEY": "configured-key"}),
            patch.object(
                fetch_dart,
                "refresh_file",
                side_effect=fetch_dart.DartApiError("800", "maintenance"),
            ),
        ):
            self.assertEqual(fetch_dart.main(), 1)


if __name__ == "__main__":
    unittest.main()
