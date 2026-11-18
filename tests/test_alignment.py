# -*- coding: utf-8 -*-
"""PMI 时间对齐检查：行数、空值、日期唯一性、切换时点。"""
from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


class AlignmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        read = lambda name: pd.read_csv(
            ROOT / "data" / name, encoding="utf-8-sig", parse_dates=["date"]
        )
        cls.aligned = read("hs300_with_pmi.csv")
        cls.features = read("hs300_features.csv")

    def test_row_count_and_dates(self) -> None:
        self.assertEqual(len(self.aligned), 1420)
        self.assertEqual(int(self.aligned["date"].duplicated().sum()), 0)
        self.assertTrue(bool(self.aligned["date"].is_monotonic_increasing))

    def test_no_missing_pmi(self) -> None:
        self.assertEqual(int(self.aligned["pmi"].isna().sum()), 0)

    def test_pmi_matches_feature_table(self) -> None:
        merged = self.aligned[["date", "pmi"]].merge(
            self.features[["date", "pmi"]], on="date", suffixes=("_aligned", "_features")
        )
        self.assertEqual(len(merged), len(self.aligned))
        self.assertTrue(
            bool((merged["pmi_aligned"] == merged["pmi_features"]).all())
        )

    def test_pmi_switch_timing(self) -> None:
        # 一个自然月内最多两个 PMI 值，且切换只发生在最后一个交易日
        for period, g in self.aligned.groupby(self.aligned["date"].dt.to_period("M")):
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
