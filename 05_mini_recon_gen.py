"""05_mini_recon_gen.py — 迷你对账造数脚本（增强版）.

功能:
  1. 手写 DDL 创建 trade / position / market_data / position_adjust 四表
  2. 3 个月模拟数据: 2026-08-01 ~ 2026-10-31
  3. 5 只标的 × 3 个账户
  4. 价格模拟: 带漂移的几何布朗运动
  5. 交易模拟: 随机生成 BUY/SELL
  6. 持仓成本: 加权平均法跟踪 cost_price
  7. 公司行为: 送股/拆股/配股/现金分红，写入 position_adjust
  8. 预埋 3 颗"对账雷"供 anomaly_detection 发现

公司行为清单:
  - 2026-08-17, 600519, 10送2
  - 2026-09-01, 000858, 每股派 1.5 元
  - 2026-09-22, 601318, 10配3 @ 30 元
  - 2026-10-12, 000333, 1拆2
  - 2026-10-22, 600036, 10送1

埋雷清单:
  - 雷 1: 2026-09-15, acc_001, 600519, 漏记 100 股 (系统少记)
  - 雷 2: 2026-10-20, acc_002, 000858, 漏记 200 股 (系统少记)
  - 雷 3: 2026-10-28, acc_003, 601318, 多记 500 股 (系统多记)

用法: python 05_mini_recon_gen.py
"""

import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# ── 1. 数据库连接 ──────────────────────────────────────────────────
load_dotenv(Path(__file__).resolve().parent / ".env")
ENGINE = create_engine(os.environ.get("DATABASE_URL"))

# ── 2. 参数 ───────────────────────────────────────────────────────
np.random.seed(42)
START_DATE = datetime(2026, 8, 1)
END_DATE = datetime(2026, 10, 31)
ACCOUNTS = ["acc_001", "acc_002", "acc_003"]

# 5 只沪深300成分股 (symbol → base_price)
SYMBOLS = {
    "600519": 1500.00,  # 贵州茅台
    "000858": 140.00,   # 五粮液
    "601318": 45.00,    # 中国平安
    "000333": 55.00,    # 美的集团
    "600036": 35.00,    # 招商银行
}

# 每日漂移 (年化约 5%~15%)
ANNUAL_DRIFT = {"600519": 0.08, "000858": 0.06, "601318": 0.05, "000333": 0.12, "600036": 0.04}
ANNUAL_VOL = {"600519": 0.25, "000858": 0.30, "601318": 0.22, "000333": 0.28, "600036": 0.20}

TRADING_DAYS_PER_YEAR = 252

# ── 3. 埋雷 ───────────────────────────────────────────────────────
# (日期, 账户, 标的, 偏差量), 正=多记, 负=漏记
LANDMINES = [
    ("2026-09-15", "acc_001", "600519", -100),
    ("2026-10-20", "acc_002", "000858", -200),
    ("2026-10-28", "acc_003", "601318", +500),
]
landmine_map = {(lm[0], lm[1], lm[2]): lm[3] for lm in LANDMINES}

# ── 3b. 公司行为 ─────────────────────────────────────────────────
# (除权除息日, 标的, 类型, 比例, 每股现金分红, 配股价, 备注)
#   STOCK_DIVIDEND: 送股/转增, ratio 为每股新增股数 (10送2 → 0.2)
#   SPLIT:          拆股, ratio 为每股新增股数 (1拆2 → 1.0)
#   RIGHTS:         配股, ratio 为每股配售股数, sub_price 为配股价
#   CASH_DIVIDEND:  现金分红, cash_per_share 为每股派息
CORPORATE_ACTIONS = [
    ("2026-08-17", "600519", "STOCK_DIVIDEND", 0.20, 0.0, 0.0, "10送2"),
    ("2026-09-01", "000858", "CASH_DIVIDEND", 0.0, 1.50, 0.0, "每股派1.5元"),
    ("2026-09-22", "601318", "RIGHTS", 0.30, 0.0, 30.0, "10配3@30元"),
    ("2026-10-12", "000333", "SPLIT", 1.00, 0.0, 0.0, "1拆2"),
    ("2026-10-22", "600036", "STOCK_DIVIDEND", 0.10, 0.0, 0.0, "10送1"),
]
actions_by_date = {}
for ex_date, sym, typ, ratio, cash, sub, remark in CORPORATE_ACTIONS:
    actions_by_date.setdefault(ex_date, []).append((sym, typ, ratio, cash, sub, remark))


def ex_action_factor(typ: str, prev_close: float, ratio: float,
                     cash_per_share: float, sub_price: float) -> float:
    """除权除息参考价的折算因子（不消耗随机数）."""
    if typ in ("STOCK_DIVIDEND", "SPLIT"):
        return 1.0 / (1.0 + ratio)
    if typ == "RIGHTS":
        return (prev_close + ratio * sub_price) / (prev_close * (1.0 + ratio))
    if typ == "CASH_DIVIDEND":
        return max((prev_close - cash_per_share) / prev_close, 0.01)
    return 1.0


# ── 4. 价格生成 (几何布朗运动) ────────────────────────────────────
def generate_prices() -> dict:
    """生成每只标的每个交易日的收盘价，返回 {(date_str, symbol): (close, pre_close)}.

    pre_close 是除权除息后的参考价；公司行为不消耗随机数，
    所以 42 号种子下价格路径只会在除权日发生台阶式调整。
    """
    prices = {}
    prev_closes = {s: b for s, b in SYMBOLS.items()}

    date = START_DATE
    while date <= END_DATE:
        if date.weekday() >= 5:
            date += timedelta(days=1)
            continue
        date_str = date.strftime("%Y-%m-%d")
        for sym, base in SYMBOLS.items():
            drift = ANNUAL_DRIFT[sym] / TRADING_DAYS_PER_YEAR
            vol = ANNUAL_VOL[sym] / np.sqrt(TRADING_DAYS_PER_YEAR)
            prev = prev_closes[sym]
            factor = 1.0
            for action_sym, typ, ratio, cash, sub, _ in actions_by_date.get(date_str, []):
                if action_sym == sym:
                    factor *= ex_action_factor(typ, prev, ratio, cash, sub)
            adjusted_base = prev * factor
            close = round(adjusted_base * np.exp(drift + vol * np.random.normal(0, 1)), 2)
            close = max(close, base * 0.2)  # 防止长期阴跌后价格趋近于 0
            prices[(date_str, sym)] = (close, round(adjusted_base, 2))
            prev_closes[sym] = close
        date += timedelta(days=1)
    return prices


# ── 5. 数据生成主逻辑 ─────────────────────────────────────────────
def generate_all(prices: dict):
    trades_rows = []
    market_rows = []
    position_rows = []
    adjust_rows = []

    # 理论持仓 & 成本 (均不含埋雷偏差)
    theo_qty = {(a, s): 0 for a in ACCOUNTS for s in SYMBOLS}
    cost_price = {(a, s): 0.0 for a in ACCOUNTS for s in SYMBOLS}
    # 上日账面持仓 (含埋雷偏差)
    prev_book_qty = {(a, s): 0 for a in ACCOUNTS for s in SYMBOLS}

    date = START_DATE
    while date <= END_DATE:
        if date.weekday() >= 5:
            date += timedelta(days=1)
            continue
        date_str = date.strftime("%Y-%m-%d")

        # 5a. 行情
        for sym in SYMBOLS:
            close, pre_close = prices[(date_str, sym)]
            market_rows.append({
                "trade_date": date_str, "symbol": sym,
                "close_price": close, "pre_close": pre_close,
            })

        # 5a-2. 公司行为（盘前）：只改持仓和成本，不产生成交
        #       现金分红按除权日前持仓计提；买入发生在除权日的不享受本期分红
        for sym, typ, ratio, cash, sub, remark in actions_by_date.get(date_str, []):
            for a in ACCOUNTS:
                q = theo_qty[(a, sym)]
                if q <= 0:
                    continue

                if typ in ("STOCK_DIVIDEND", "SPLIT"):
                    delta = int(round(q * ratio))
                    if delta <= 0:
                        continue
                    new_qty = q + delta
                    # 总成本不变，每股成本按股数摊薄
                    cost_price[(a, sym)] = round(
                        cost_price[(a, sym)] * q / new_qty, 4
                    )
                    theo_qty[(a, sym)] = new_qty
                    cash_flow = 0.0
                    cash_income = 0.0
                elif typ == "RIGHTS":
                    delta = int(round(q * ratio))
                    if delta <= 0:
                        continue
                    new_qty = q + delta
                    # 配股缴款计入成本；cash_flow 作为外部资金流，不计入盈亏
                    cost_price[(a, sym)] = round(
                        (q * cost_price[(a, sym)] + delta * sub) / new_qty, 4
                    )
                    theo_qty[(a, sym)] = new_qty
                    cash_flow = round(delta * sub, 2)
                    cash_income = 0.0
                elif typ == "CASH_DIVIDEND":
                    delta = 0
                    cash_flow = 0.0
                    cash_income = round(q * cash, 2)
                else:
                    continue

                adjust_rows.append({
                    "account_id": a, "symbol": sym, "ex_date": date_str,
                    "adjust_type": typ, "qty_delta": delta,
                    "cash_flow": cash_flow, "cash_income": cash_income,
                    "remark": remark,
                })

        # 5b. 交易
        day_trades = {(a, s): {"buy": 0, "sell": 0, "buy_amt": 0.0, "sell_amt": 0.0}
                      for a in ACCOUNTS for s in SYMBOLS}

        for a in ACCOUNTS:
            for s in SYMBOLS:
                if np.random.rand() > 0.25:  # 每天每标的 25% 概率有交易
                    continue
                direction = "BUY" if np.random.rand() > 0.35 else "SELL"
                # SELL 时不能超过理论持仓
                max_sell = theo_qty[(a, s)]
                if direction == "SELL" and max_sell <= 0:
                    continue
                qty = np.random.choice([100, 200, 300, 500, 1000])
                if direction == "SELL":
                    qty = min(qty, max_sell)

                close, _ = prices[(date_str, s)]
                trade_id = f"T_{date_str.replace('-','')}_{a}_{s}_{np.random.randint(1000,9999)}"
                amount = round(qty * close, 2)

                trades_rows.append({
                    "trade_id": trade_id, "account_id": a, "symbol": s,
                    "direction": direction, "quantity": qty, "price": close,
                    "amount": amount, "trade_time": f"{date_str} 10:30:00",
                    "trade_date": date_str,
                })

                # 更新理论持仓 & 成本
                if direction == "BUY":
                    old_qty = theo_qty[(a, s)]
                    old_cost = cost_price[(a, s)]
                    new_qty = old_qty + qty
                    if new_qty > 0:
                        cost_price[(a, s)] = round(
                            (old_qty * old_cost + qty * close) / new_qty, 4
                        )
                    else:
                        cost_price[(a, s)] = 0.0
                    theo_qty[(a, s)] = new_qty
                    day_trades[(a, s)]["buy"] += qty
                    day_trades[(a, s)]["buy_amt"] += amount
                else:
                    old_qty = theo_qty[(a, s)]
                    theo_qty[(a, s)] -= qty
                    # 卖出不改变剩余持仓的 cost_price
                    if theo_qty[(a, s)] <= 0:
                        cost_price[(a, s)] = 0.0
                    day_trades[(a, s)]["sell"] += qty
                    day_trades[(a, s)]["sell_amt"] += amount

        # 5c. 持仓 (理论 + 埋雷偏差)
        for a in ACCOUNTS:
            for s in SYMBOLS:
                theo = theo_qty[(a, s)]
                delta = landmine_map.get((date_str, a, s), 0)
                book_qty = theo + delta  # 账面 = 理论 + 偏差

                # 只在有变动或非零时写入
                if book_qty != 0 or prev_book_qty[(a, s)] != 0:
                    position_rows.append({
                        "account_id": a, "symbol": s, "trade_date": date_str,
                        "pre_qty": prev_book_qty[(a, s)],
                        "qty": book_qty,
                        "cost_price": cost_price[(a, s)] if book_qty > 0 else 0.0,
                    })
                prev_book_qty[(a, s)] = book_qty

        date += timedelta(days=1)

    return trades_rows, market_rows, position_rows, adjust_rows


# ── 6. DDL ────────────────────────────────────────────────────────
DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS `trade` (
        `trade_id`    VARCHAR(64)     NOT NULL COMMENT '交易编号',
        `account_id`  VARCHAR(16)     NOT NULL COMMENT '账户ID',
        `symbol`      VARCHAR(16)     NOT NULL COMMENT '标的代码',
        `direction`   VARCHAR(4)      NOT NULL COMMENT 'BUY / SELL',
        `quantity`    INT             NOT NULL COMMENT '成交数量',
        `price`       DECIMAL(12,4)   NOT NULL COMMENT '成交价',
        `amount`      DECIMAL(20,2)   NOT NULL COMMENT '成交金额',
        `trade_time`  DATETIME        NOT NULL COMMENT '成交时间',
        `trade_date`  DATE            NOT NULL COMMENT '交易日',
        PRIMARY KEY (`trade_id`),
        INDEX `idx_trade_acc_date` (`account_id`, `trade_date`),
        INDEX `idx_trade_symbol_date` (`symbol`, `trade_date`),
        INDEX `idx_trade_acc_sym_date` (`account_id`, `symbol`, `trade_date`),
        INDEX `idx_trade_date` (`trade_date`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
      COMMENT='交易流水表'
    """,
    """
    CREATE TABLE IF NOT EXISTS `market_data` (
        `trade_date`  DATE            NOT NULL COMMENT '交易日',
        `symbol`      VARCHAR(16)     NOT NULL COMMENT '标的代码',
        `close_price` DECIMAL(12,4)   NOT NULL COMMENT '收盘价',
        `pre_close`   DECIMAL(12,4)   NULL     COMMENT '前收盘价',
        PRIMARY KEY (`trade_date`, `symbol`),
        INDEX `idx_md_symbol` (`symbol`),
        INDEX `idx_md_symbol_date` (`symbol`, `trade_date`, `close_price`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
      COMMENT='日频行情表'
    """,
    """
    CREATE TABLE IF NOT EXISTS `position` (
        `account_id`  VARCHAR(16)     NOT NULL COMMENT '账户ID',
        `symbol`      VARCHAR(16)     NOT NULL COMMENT '标的代码',
        `trade_date`  DATE            NOT NULL COMMENT '交易日',
        `pre_qty`     INT             NOT NULL DEFAULT 0 COMMENT '上日持仓',
        `qty`         INT             NOT NULL DEFAULT 0 COMMENT '本日持仓',
        `cost_price`  DECIMAL(12,4)   NOT NULL DEFAULT 0 COMMENT '持仓成本价',
        PRIMARY KEY (`account_id`, `symbol`, `trade_date`),
        INDEX `idx_pos_date` (`trade_date`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
      COMMENT='日频持仓表'
    """,
    """
    CREATE TABLE IF NOT EXISTS `position_adjust` (
        `adjust_id`   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        `account_id`  VARCHAR(16)   NOT NULL COMMENT '账户ID',
        `symbol`      VARCHAR(16)   NOT NULL COMMENT '标的代码',
        `ex_date`     DATE          NOT NULL COMMENT '除权除息日',
        `adjust_type` VARCHAR(20)   NOT NULL COMMENT 'STOCK_DIVIDEND/SPLIT/RIGHTS/CASH_DIVIDEND',
        `qty_delta`   INT           NOT NULL DEFAULT 0 COMMENT '非交易持仓变动(股)',
        `cash_flow`   DECIMAL(20,2) NOT NULL DEFAULT 0 COMMENT '外部资金流: 配股缴款为正',
        `cash_income` DECIMAL(20,2) NOT NULL DEFAULT 0 COMMENT '投资收益: 现金分红',
        `remark`      VARCHAR(128)  NULL,
        PRIMARY KEY (`adjust_id`),
        UNIQUE KEY `uk_adj` (`account_id`,`symbol`,`ex_date`,`adjust_type`),
        KEY `idx_adj_date` (`ex_date`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
      COMMENT='非交易持仓变动: 送转/拆股/配股/分红'
    """,
]

INSERT_SQL = {
    "trade": (
        "INSERT INTO `trade` (trade_id, account_id, symbol, direction, "
        "quantity, price, amount, trade_time, trade_date) "
        "VALUES (:trade_id, :account_id, :symbol, :direction, "
        ":quantity, :price, :amount, :trade_time, :trade_date)"
    ),
    "market_data": (
        "INSERT INTO `market_data` (trade_date, symbol, close_price, pre_close) "
        "VALUES (:trade_date, :symbol, :close_price, :pre_close)"
    ),
    "position": (
        "INSERT INTO `position` (account_id, symbol, trade_date, pre_qty, qty, cost_price) "
        "VALUES (:account_id, :symbol, :trade_date, :pre_qty, :qty, :cost_price)"
    ),
    "position_adjust": (
        "INSERT INTO `position_adjust` (account_id, symbol, ex_date, adjust_type, "
        "qty_delta, cash_flow, cash_income, remark) "
        "VALUES (:account_id, :symbol, :ex_date, :adjust_type, "
        ":qty_delta, :cash_flow, :cash_income, :remark)"
    ),
}


# ── 7. 入库 ───────────────────────────────────────────────────────
def main():
    print("生成模拟价格 ...")
    prices = generate_prices()
    n_dates = len({d for (d, _) in prices})
    print(f"  共 {n_dates} 个交易日 × {len(SYMBOLS)} 只标的")

    print("生成交易 & 持仓 ...")
    trades_rows, market_rows, position_rows, adjust_rows = generate_all(prices)
    print(
        f"  trade: {len(trades_rows)} | market: {len(market_rows)} | "
        f"position: {len(position_rows)} | adjust: {len(adjust_rows)}"
    )

    # DataFrame 类型清洗
    df_trade = pd.DataFrame(trades_rows)
    df_market = pd.DataFrame(market_rows)
    df_pos = pd.DataFrame(position_rows)
    df_adj = pd.DataFrame(adjust_rows)

    for df in [df_trade, df_market, df_pos, df_adj]:
        for col in df.select_dtypes(include=["datetime64[ns]"]).columns:
            df[col] = df[col].astype(str)
    for df in [df_trade, df_market, df_pos, df_adj]:
        df.replace({np.nan: None}, inplace=True)

    print("入库 MySQL ...")
    with ENGINE.begin() as conn:
        for ddl in DDL_STATEMENTS:
            conn.execute(text(ddl))
        for tbl in ["trade", "position", "market_data", "position_adjust"]:
            conn.execute(text(f"DELETE FROM `{tbl}`"))
        conn.execute(text(INSERT_SQL["trade"]), df_trade.to_dict("records"))
        conn.execute(text(INSERT_SQL["market_data"]), df_market.to_dict("records"))
        conn.execute(text(INSERT_SQL["position"]), df_pos.to_dict("records"))
        if len(df_adj) > 0:
            conn.execute(text(INSERT_SQL["position_adjust"]), df_adj.to_dict("records"))

    print(f"\n[OK] 造数完成:")
    print(f"   trade:           {len(df_trade):>5} 行")
    print(f"   market_data:     {len(df_market):>5} 行")
    print(f"   position:        {len(df_pos):>5} 行")
    print(f"   position_adjust: {len(df_adj):>5} 行")
    print(f"\n[公司行为] 已生成 {len(adjust_rows)} 条持仓调整:")
    for ex_date, sym, typ, ratio, cash, sub, remark in CORPORATE_ACTIONS:
        print(f"   {ex_date} | {sym} | {typ:<15} | {remark}")
    print(f"\n[埋雷] 已埋 {len(LANDMINES)} 颗对账雷:")
    for lm in LANDMINES:
        direction = "漏记" if lm[3] < 0 else "多记"
        print(f"   {lm[0]} | {lm[1]} | {lm[2]} | {direction} {abs(lm[3])}股")


if __name__ == "__main__":
    main()