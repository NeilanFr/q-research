"""Synthetic provider payloads only: no network and no real cached data."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from quantlab import data


def temporary_workspace():
    test_root = Path(__file__).resolve().parents[1] / ".cache/tests"
    test_root.mkdir(parents=True, exist_ok=True)
    temporary = TemporaryDirectory(dir=test_root)
    if not Path(temporary.name).resolve().is_relative_to(test_root.resolve()):
        raise RuntimeError("Temporary test data escaped its intended directory")
    return temporary


def chart_payload(dates=("2026-09-16", "2026-09-17", "2026-09-18")):
    n = len(dates)
    return {"chart": {"error": None, "result": [{
        "meta": {"symbol": "AAA", "currency": "USD", "instrumentType": "ETF"},
        "timestamp": [int(pd.Timestamp(d + "T13:30:00Z").timestamp()) for d in dates],
        "indicators": {
            "quote": [{"open": [100.0] * n, "high": [105.0] * n,
                       "low": [95.0] * n, "close": [102.0] * n, "volume": [100000] * n}],
            "adjclose": [{"adjclose": [51.0] * n}],
        },
    }]}}


class DataTests(unittest.TestCase):
    def test_latest_session_respects_weekends_publication_lag_and_early_close(self):
        cases = {
            "2026-09-19T12:00:00Z": "2026-09-18",
            "2026-09-21T13:00:00Z": "2026-09-18",
            "2026-09-21T20:59:59Z": "2026-09-18",
            "2026-09-21T21:00:00Z": "2026-09-21",
            # Friday after Thanksgiving closes at 18:00 UTC; availability 19:00.
            "2026-11-27T18:59:59Z": "2026-11-25",
            "2026-11-27T19:00:00Z": "2026-11-27",
        }
        for now, session in cases.items():
            with self.subTest(now=now):
                self.assertEqual(data.latest_completed_session(now), pd.Timestamp(session))
        with self.assertRaises(ValueError):
            data.latest_completed_session("2026-09-19T12:00:00")

    def test_parser_retains_raw_prices_and_explicit_adjusted_open_proxy(self):
        bars = data.parse_chart(chart_payload(), "AAA", "2026-09-16", "2026-09-18")
        self.assertEqual(len(bars), 3)
        np.testing.assert_allclose(bars["open"], 100)
        np.testing.assert_allclose(bars["close"], 102)
        np.testing.assert_allclose(bars["adj_open"], 50)
        self.assertEqual(bars.date.iloc[-1], pd.Timestamp("2026-09-18"))

    def test_parser_rejects_missing_duplicate_and_non_session_dates(self):
        for dates in (("2026-09-16", "2026-09-18"),
                      ("2026-09-16", "2026-09-17", "2026-09-17", "2026-09-18"),
                      ("2026-09-16", "2026-09-17", "2026-09-18", "2026-09-19")):
            with self.subTest(dates=dates), self.assertRaises(ValueError):
                data.parse_chart(chart_payload(dates), "AAA", "2026-09-16", "2026-09-19")

    def test_parser_rejects_missing_nonpositive_and_inconsistent_ohlcv(self):
        for field, value in (("open", None), ("close", -1), ("volume", 0),
                             ("high", 99), ("low", 103), ("close", float("inf"))):
            payload = chart_payload()
            payload["chart"]["result"][0]["indicators"]["quote"][0][field][1] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                data.parse_chart(payload, "AAA", "2026-09-16", "2026-09-18")

    def test_parser_rejects_provider_errors_and_wrong_instrument_metadata(self):
        for payload in ({}, {"chart": {"error": {"code": "Not Found"}, "result": None}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                data.parse_chart(payload, "AAA", "2026-09-16", "2026-09-18")
        for field, value in (("symbol", "BBB"), ("currency", "EUR"), ("instrumentType", "EQUITY")):
            payload = chart_payload()
            payload["chart"]["result"][0]["meta"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                data.parse_chart(payload, "AAA", "2026-09-16", "2026-09-18")

    def test_http_200_html_cannot_be_cached_as_market_data(self):
        response = Mock(status_code=200)
        response.json.side_effect = json.JSONDecodeError("Not JSON", "<html>blocked</html>", 0)
        session = Mock()
        session.headers = {}
        session.get.return_value = response
        with temporary_workspace() as directory, patch.object(data, "ROOT", Path(directory)), \
             patch.object(data.requests, "Session", return_value=session), \
             patch.object(data, "latest_completed_session", return_value=pd.Timestamp("2026-09-18")):
            with self.assertRaises(ValueError):
                data.fetch({"data_start": "2026-09-16", "symbols": ["AAA"], "benchmark": "SPY"}, "2026-09-18")
            self.assertFalse((Path(directory) / "data/LATEST").exists())
            self.assertEqual(list((Path(directory) / "data/raw").iterdir()), [])

    def test_cached_bars_and_raw_inputs_are_checked_for_mutation(self):
        with temporary_workspace() as directory, patch.object(data, "ROOT", Path(directory)):
            root = Path(directory)
            destination = root / "data/snapshots/testid"
            destination.mkdir(parents=True)
            raw_dir = root / "data/raw"
            raw_dir.mkdir()
            raw = json.dumps(chart_payload()).encode()
            bars = b"date,symbol,close\n2026-09-18,AAA,102.0\n"
            (raw_dir / "provider.json").write_bytes(raw)
            (destination / "bars.csv").write_bytes(bars)
            manifest = {"bars_sha256": data.digest(bars), "raw_sources": [
                {"file": "data/raw/provider.json", "sha256": data.digest(raw)}]}
            (destination / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            loaded, _, _ = data.load_snapshot("testid")
            self.assertEqual(len(loaded), 1)
            (destination / "bars.csv").write_bytes(bars.replace(b"102.0", b"999.0"))
            with self.assertRaisesRegex(ValueError, "integrity"):
                data.load_snapshot("testid")
            (destination / "bars.csv").write_bytes(bars)
            (raw_dir / "provider.json").write_bytes(b"{}")
            with self.assertRaisesRegex(ValueError, "integrity"):
                data.load_snapshot("testid")


if __name__ == "__main__":
    unittest.main()
