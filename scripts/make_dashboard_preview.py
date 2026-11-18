# -*- coding: utf-8 -*-
"""从回测结果生成一张静态看板预览（非 Power BI 截图）。

用法：
    python scripts/make_dashboard_preview.py

输出：
    figures/hs300_dashboard.png
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.gridspec import GridSpec
from matplotlib.patches import FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
FIGURES = ROOT / "figures"

STRATEGY_COLOR = "#1f4e79"
BENCH_COLOR = "#c46a1a"
INK = "#1d1d1b"
MUTED = "#6b6b66"
CARD_BG = "#ffffff"
PAGE_BG = "#f4f2ec"


def _kpi_card(ax, label: str, value: str, note: str, accent: str) -> None:
    ax.set_axis_off()
    ax.add_patch(
        FancyBboxPatch(
            (0.02, 0.06), 0.96, 0.88,
            boxstyle="round,pad=0.02,rounding_size=0.035",
            linewidth=0, facecolor=CARD_BG, transform=ax.transAxes,
        )
    )
    ax.plot([0.08, 0.08], [0.18, 0.82], color=accent, linewidth=3,
            transform=ax.transAxes, solid_capstyle="round")
    ax.text(0.14, 0.72, label, color=MUTED, fontsize=9, transform=ax.transAxes)
    ax.text(0.14, 0.40, value, color=INK, fontsize=19, fontweight="bold",
            transform=ax.transAxes, va="center")
    ax.text(0.14, 0.18, note, color=MUTED, fontsize=8, transform=ax.transAxes)


def main() -> int:
    FIGURES.mkdir(parents=True, exist_ok=True)
    bt = pd.read_csv(DATA / "hs300_backtest.csv", encoding="utf-8-sig", parse_dates=["date"])
    stats = pd.read_csv(DATA / "hs300_backtest_stats.csv", encoding="utf-8-sig")

    full = stats.query("segment == 'FULL' and strategy == 'z-band'").iloc[0]
    oos = stats.query("segment == 'OOS' and strategy == 'z-band'").iloc[0]
    bench = stats.query("segment == 'FULL' and strategy == 'Buy&Hold'").iloc[0]

    # CSV 的 ret_band/nav_band 是毛收益；这里按统计表口径重算扣 5bp 后的净净值
    turn = bt["position_band"].diff().abs()
    turn.iloc[0] = abs(bt["position_band"].iloc[0])
    net_ret = bt["ret_band"] - turn * 0.0005
    bt["nav_net"] = (1 + net_ret).cumprod()

    # 基准同样按 5bp 单边成本计算一次建仓成本
    bench_cost = pd.Series(0.0, index=bt.index)
    bench_cost.iloc[0] = 0.0005
    bt["nav_bench_net"] = (1 + bt["ret"] - bench_cost).cumprod()

    fig = plt.figure(figsize=(13, 8.6), facecolor=PAGE_BG)
    gs = GridSpec(
        3, 4, figure=fig,
        height_ratios=[0.72, 2.35, 1.15],
        hspace=0.42, wspace=0.28,
    )

    _kpi_card(
        fig.add_subplot(gs[0, 0]), "Full-sample return", f"{full['total_return']:+.2%}",
        f"Benchmark {bench['total_return']:+.2%}",
        STRATEGY_COLOR,
    )
    _kpi_card(
        fig.add_subplot(gs[0, 1]), "Sharpe ratio", f"{full['sharpe']:.2f}",
        "Full sample, 5bp one-way cost", MUTED,
    )
    _kpi_card(
        fig.add_subplot(gs[0, 2]), "Max drawdown", f"{full['max_drawdown']:.2%}",
        f"OOS {oos['max_drawdown']:.2%}", BENCH_COLOR,
    )
    _kpi_card(
        fig.add_subplot(gs[0, 3]), "OOS return", f"{oos['total_return']:+.2%}",
        f"Sharpe {oos['sharpe']:.2f}", STRATEGY_COLOR,
    )

    # 净值曲线
    ax_nav = fig.add_subplot(gs[1, :])
    oos_start = bt.loc[bt["date"] >= "2025-02-21", "date"].min()
    ax_nav.axvspan(oos_start, bt["date"].max(), color=STRATEGY_COLOR, alpha=0.06, linewidth=0)
    ax_nav.plot(bt["date"], bt["nav_net"], color=STRATEGY_COLOR, linewidth=1.8, label="z-band (net)")
    ax_nav.plot(bt["date"], bt["nav_bench_net"], color=BENCH_COLOR, linewidth=1.4, label="Buy & Hold (net)")
    ax_nav.axvline(oos_start, color=MUTED, linestyle="--", linewidth=0.9)
    ax_nav.text(oos_start, ax_nav.get_ylim()[1], "  OOS", color=MUTED, fontsize=9, va="top")
    ax_nav.set_title("Net value: z-band (after 5bp cost) vs Buy & Hold", loc="left", fontsize=12, color=INK)
    ax_nav.legend(loc="upper left", frameon=False, fontsize=9)
    ax_nav.grid(alpha=0.25)
    ax_nav.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    # 回撤
    ax_dd = fig.add_subplot(gs[2, :2])
    dd = bt["nav_net"] / bt["nav_net"].cummax() - 1
    ax_dd.fill_between(bt["date"], dd, 0, color=BENCH_COLOR, alpha=0.25, linewidth=0)
    ax_dd.plot(bt["date"], dd, color=BENCH_COLOR, linewidth=1.1)
    ax_dd.set_title("Strategy drawdown", loc="left", fontsize=11, color=INK)
    ax_dd.set_ylim(-0.32, 0.02)
    ax_dd.grid(alpha=0.25)
    ax_dd.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    # 仓位
    ax_pos = fig.add_subplot(gs[2, 2:])
    ax_pos.step(bt["date"], bt["position_band"], where="post", color=STRATEGY_COLOR, linewidth=1.3)
    ax_pos.fill_between(bt["date"], bt["position_band"], step="post",
                        color=STRATEGY_COLOR, alpha=0.12, linewidth=0)
    ax_pos.set_title("Position (0/1)", loc="left", fontsize=11, color=INK)
    ax_pos.set_ylim(-0.05, 1.1)
    ax_pos.grid(alpha=0.25)
    ax_pos.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    fig.suptitle(
        "HS300 z-band strategy — static dashboard preview",
        x=0.012, ha="left", fontsize=16, fontweight="bold", color=INK,
    )
    fig.text(
        0.012, 0.012,
        "Generated from data/hs300_backtest.csv · interactive version: figures/hs300_dashboard.pbix",
        fontsize=8, color=MUTED, ha="left",
    )

    out = FIGURES / "hs300_dashboard.png"
    fig.savefig(out, dpi=160, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f"[dashboard] wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
