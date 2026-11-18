# -*- coding: utf-8 -*-
"""MySQL 加载前的质量校验与主键去重逻辑，不连接数据库。"""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import import_data  # noqa: E402


class LoaderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.df = import_data.load_features()

    def test_validation_passes_on_committed_data(self) -> None:
        report = import_data.validate_features(self.df)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["rows"], 1420)
        self.assertEqual(report["months"], 71)
        self.assertEqual(report["duplicate_dates"], 0)

    def test_prepare_maps_columns_and_dates(self) -> None:
        prepared = import_data.prepare_for_load(self.df)
        self.assertEqual(len(prepared), 1420)
        self.assertIn("trade_date", prepared.columns)
        self.assertIn("close_price", prepared.columns)
        self.assertNotIn("date", prepared.columns)
        self.assertIsInstance(prepared["trade_date"].iloc[0], dt.date)

    def test_duplicate_dates_are_deduped(self) -> None:
        duplicated = pd.concat([self.df, self.df.iloc[[0]]], ignore_index=True)
        report = import_data.validate_features(duplicated)
        self.assertTrue(any("重复" in msg for msg in report["warnings"]))
        prepared = import_data.prepare_for_load(duplicated)
        self.assertEqual(len(prepared), 1420)
        self.assertEqual(int(prepared["trade_date"].duplicated().sum()), 0)

    def test_null_key_column_is_rejected(self) -> None:
        bad = self.df.copy()
        bad.loc[0, "close"] = np.nan
        report = import_data.validate_features(bad)
        self.assertTrue(any("close" in msg for msg in report["errors"]))

    def test_rolling_null_after_window_is_rejected(self) -> None:
        bad = self.df.copy()
        bad.loc[100, "ma20"] = np.nan
        report = import_data.validate_features(bad)
        self.assertTrue(
            any("ma20" in msg and "滚动窗口" in msg for msg in report["errors"])
        )

    def test_dry_run_does_not_need_database(self) -> None:
        self.assertEqual(import_data.main(["--dry-run"]), 0)


if __name__ == "__main__":
    unittest.main()
