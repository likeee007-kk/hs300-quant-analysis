# -*- coding: utf-8 -*-
"""回测输出一致性：仓位是否为信号滞后一日、指标是否与统计表一致。"""
from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


class BacktestOutputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bt = pd.read_csv(
            ROOT / "data" / "hs300_backtest.csv",
            encoding="utf-8-sig",
            parse_dates=["date"],
        )
        cls.stats = pd.read_csv(
            ROOT / "data" / "hs300_backtest_stats.csv", encoding="utf-8-sig"
        )
        cls.full = cls.stats.query("segment == 'FULL' and strategy == 'z-band'").iloc[0]
        cls.oos = cls.stats.query("segment == 'OOS' and strategy == 'z-band'").iloc[0]

    def test_row_count(self) -> None:
        self.assertEqual(len(self.bt), 1401)

    def test_positions_are_binary_and_start_flat(self) -> None:
        self.assertTrue(set(self.bt["position_band"].unique()).issubset({0.0, 1.0}))
        self.assertEqual(float(self.bt["position_band"].iloc[0]), 0.0)

    def test_position_is_lagged_signal(self) -> None:
        lagged = self.bt["position_band"].iloc[1:].reset_index(drop=True)
        signal = self.bt["signal_band"].iloc[:-1].reset_index(drop=True)
        pd.testing.assert_series_equal(lagged, signal, check_names=False)

    def test_net_nav_metrics_match_stats(self) -> None:
        # CSV 里的 nav_band 是毛收益；统计表是扣 5bp 单边成本后的净收益。
        turn = self.bt["position_band"].diff().abs()
        turn.iloc[0] = abs(self.bt["position_band"].iloc[0])
        net = self.bt["ret_band"] - turn * 0.0005
        nav = (1 + net).cumprod()
        self.assertTrue(bool(np.isfinite(nav).all()))
        self.assertAlmostEqual(float(nav.iloc[-1] - 1), float(self.full["total_return"]), places=6)
        drawdown = float((nav / nav.cummax() - 1).min())
        self.assertAlmostEqual(drawdown, float(self.full["max_drawdown"]), places=6)

    def test_oos_drawdown_matches_readme(self) -> None:
        self.assertAlmostEqual(float(self.oos["max_drawdown"]), -0.06918709124883682, places=8)
        self.assertAlmostEqual(float(self.full["max_drawdown"]), -0.25498364954133823, places=8)

    def test_benchmark_is_built_from_returns(self) -> None:
        self.assertAlmostEqual(
            float(self.bt["nav_benchmark"].iloc[0]),
            float(1 + self.bt["ret"].iloc[0]),
            places=8,
        )


if __name__ == "__main__":
    unittest.main()
