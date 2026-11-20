"""06_anomaly_detection.py — Python 异常检测：对账差异 + 收益率跳变.

扫描 MySQL 中的对账数据和收益率数据，输出异常清单。
可作为 Power BI 的数据源（csv/json）或独立脚本运行。

检测维度:
  1. 对账差异检测 — 基于 v_recon_diff 视图
  2. 收益率跳变检测 — 基于 v_daily_return 视图
  3. 持仓为负检测 — qty < 0 的异常记录

用法: python 06_anomaly_detection.py [--output reports/]
"""

import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# ── 配置 ──────────────────────────────────────────────────────────
load_dotenv(Path(__file__).resolve().parent / ".env")
ENGINE = create_engine(os.environ.get("DATABASE_URL"))
OUTPUT_DIR = Path(__file__).resolve().parent / "reports"
OUTPUT_DIR.mkdir(exist_ok=True)

TODAY_STR = datetime.now().strftime("%Y%m%d_%H%M%S")
ZSCORE_THRESHOLD = 2.5  # 收益率 z-score 跳变阈值


# ── 检测 1: 对账差异 ──────────────────────────────────────────────
def detect_recon_diff() -> pd.DataFrame:
    """从 v_recon_diff 视图读取所有差异行."""
    sql = """
        SELECT account_id, symbol, trade_date, pre_qty, qty,
               net_trade, expected_qty, diff, diff_type
        FROM v_recon_diff
        WHERE diff != 0
        ORDER BY trade_date DESC, ABS(diff) DESC
    """
    df = pd.read_sql(text(sql), ENGINE)
    return df


# ── 检测 2: 收益率跳变 ────────────────────────────────────────────
def detect_return_jumps() -> pd.DataFrame:
    """检测日收益率超过 z-score 阈值的异常跳变."""
    sql = """
        SELECT account_id, symbol, trade_date,
               market_value, prev_mv, buy_amt, sell_amt,
               daily_pnl, daily_return_pct
        FROM v_daily_return
        WHERE daily_return_pct IS NOT NULL
        ORDER BY account_id, symbol, trade_date
    """
    df = pd.read_sql(text(sql), ENGINE)
    if df.empty:
        return df

    # 按 (account, symbol) 分组计算滚动 z-score
    df["zscore"] = (
        df.groupby(["account_id", "symbol"])["daily_return_pct"]
        .transform(lambda x: (x - x.mean()) / (x.std() if pd.notna(x.std()) and x.std() != 0 else np.nan))
    )

    jumps = df[df["zscore"].abs() > ZSCORE_THRESHOLD].copy()
    jumps["jump_direction"] = jumps["zscore"].apply(
        lambda z: "暴涨" if z > 0 else "暴跌"
    )
    jumps["anomaly_reason"] = jumps.apply(
        lambda row: (
            f"{row['symbol']} 在 {row['trade_date']} 日收益率 "
            f"{row['daily_return_pct']:.4f}%，z-score={row['zscore']:.2f}，"
            f"触发{'上' if row['zscore'] > 0 else '下'}穿阈值 ±{ZSCORE_THRESHOLD}，"
            f"市值={row['market_value']:,.2f}，当日盈亏={row['daily_pnl']:,.2f}"
        ),
        axis=1,
    )
    return jumps.sort_values("zscore", key=abs, ascending=False)


# ── 检测 3: 持仓为负 ──────────────────────────────────────────────
def detect_negative_positions() -> pd.DataFrame:
    """检测持仓数量和市值异常."""
    sql = """
        SELECT p.account_id, p.symbol, p.trade_date,
               p.qty, m.close_price,
               ROUND(p.qty * m.close_price, 2) AS market_value
        FROM position p
        JOIN market_data m
            ON p.trade_date = m.trade_date AND p.symbol = m.symbol
        WHERE p.qty < 0
        ORDER BY p.trade_date DESC
    """
    df = pd.read_sql(text(sql), ENGINE)
    return df


# ── 报告生成 ──────────────────────────────────────────────────────
def generate_report():
    """运行全部检测，输出 CSV + 控制台摘要."""
    print("=" * 64)
    print("  异常检测报告")
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 64)

    # 1. 对账差异
    print("\n[1/3] 对账差异检测 ...")
    diff_df = detect_recon_diff()
    n_diff = len(diff_df)
    print(f"  → 发现 {n_diff} 条对账差异")
    if n_diff > 0:
        diff_df.to_csv(OUTPUT_DIR / f"anomaly_recon_diff_{TODAY_STR}.csv", index=False)
        for _, row in diff_df.iterrows():
            direction = "系统多记" if row["diff"] > 0 else "系统漏记"
            print(
                f"    [!] {row['trade_date']} | {row['account_id']} | {row['symbol']} | "
                f"diff={row['diff']:+.0f}股 | {direction} | "
                f"pre={row['pre_qty']} -> qty={row['qty']} | net_trade={row['net_trade']}"
            )

    # 2. 收益率跳变
    print("\n[2/3] 收益率跳变检测 (z-score > ±{:.1f}) ...".format(ZSCORE_THRESHOLD))
    jump_df = detect_return_jumps()
    n_jump = len(jump_df)
    print(f"  → 发现 {n_jump} 条收益率跳变")
    if n_jump > 0:
        jump_df.to_csv(OUTPUT_DIR / f"anomaly_return_jumps_{TODAY_STR}.csv", index=False)
        for _, row in jump_df.iterrows():
            print(
                f"    [z] {row['trade_date']} | {row['account_id']} | {row['symbol']} | "
                f"ret={row['daily_return_pct']:.4f}% | z={row['zscore']:.2f} | "
                f"{row['jump_direction']}"
            )

    # 3. 持仓异常
    print("\n[3/3] 持仓为负检测 ...")
    neg_df = detect_negative_positions()
    n_neg = len(neg_df)
    print(f"  → 发现 {n_neg} 条持仓为负")
    if n_neg > 0:
        neg_df.to_csv(OUTPUT_DIR / f"anomaly_neg_position_{TODAY_STR}.csv", index=False)
        for _, row in neg_df.iterrows():
            print(
                f"    [X] {row['trade_date']} | {row['account_id']} | {row['symbol']} | "
                f"qty={row['qty']} | mv={row['market_value']:,.2f}"
            )

    # ── 汇总 ──
    print(f"\n{'─' * 48}")
    total = n_diff + n_jump + n_neg
    print(f"  合计异常: {total} 条 (对账差异:{n_diff} | 跳变:{n_jump} | 负持仓:{n_neg})")
    print(f"  报告目录: {OUTPUT_DIR.resolve()}")
    print(f"{'─' * 48}")

    return {
        "recon_diff": diff_df,
        "return_jumps": jump_df,
        "neg_position": neg_df,
        "summary": {"diff": n_diff, "jump": n_jump, "neg": n_neg, "total": total},
    }


# ── 入口 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    generate_report()