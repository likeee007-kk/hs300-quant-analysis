# -*- coding: utf-8 -*-
"""特征表的数据质量检查：行数、主键、空值模式、PMI 覆盖。"""
from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


class DataQualityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.df = pd.read_csv(
            ROOT / "data" / "hs300_features.csv",
            encoding="utf-8-sig",
            parse_dates=["date"],
        )

    def test_rows_and_date_range(self) -> None:
        # 回归测试针对当前快照；01_fetch_data 追加新数据后仍应通过
        self.assertGreaterEqual(len(self.df), 1420)
        self.assertEqual(self.df["date"].min(), pd.Timestamp("2021-01-04"))
        self.assertGreaterEqual(self.df["date"].max(), pd.Timestamp("2026-11-13"))

    def test_no_duplicate_dates(self) -> None:
        self.assertEqual(int(self.df["date"].duplicated().sum()), 0)

    def test_required_columns(self) -> None:
        expected = {
            "date", "open", "high", "low", "close", "volume", "ret", "nav",
            "pmi", "ma5", "ma20", "vol20", "dif", "dea", "macd",
            "ma_ratio", "vratio",
        }
        self.assertTrue(expected.issubset(set(self.df.columns)))
        self.assertNotIn("fwd_ret", self.df.columns)

    def test_key_columns_have_no_nulls(self) -> None:
        for col in ["date", "close", "ret", "pmi"]:
            self.assertEqual(int(self.df[col].isna().sum()), 0, col)

    def test_rolling_null_only_at_start(self) -> None:
        # 滚动窗口前 20 行允许为空，之后不允许
        for col in ["ma5", "ma20", "vol20", "ma_ratio", "vratio"]:
            nulls = self.df[col].isna()
            self.assertFalse(bool(nulls.iloc[20:].any()), col)

    def test_pmi_coverage(self) -> None:
        self.assertGreaterEqual(int(self.df["date"].dt.to_period("M").nunique()), 71)
        self.assertEqual(int(self.df["pmi"].isna().sum()), 0)
        self.assertTrue(bool(self.df["pmi"].between(30, 60).all()))

    def test_pmi_changes_only_on_month_last_trading_day(self) -> None:
        # PMI 月末发布：一个自然月内最多两个值，且切换只发生在该月最后一个交易日
        for period, g in self.df.groupby(self.df["date"].dt.to_period("M")):
            vals = g["pmi"].reset_index(drop=True)
            changed = vals.ne(vals.shift())
            changed.iloc[0] = False
            idx = changed[changed].index.tolist()
            if idx:
                self.assertEqual(
                    idx, [len(vals) - 1],
                    msg=f"PMI 在 {period} 月中发生了切换：{idx}",
                )


if __name__ == "__main__":
    unittest.main()
