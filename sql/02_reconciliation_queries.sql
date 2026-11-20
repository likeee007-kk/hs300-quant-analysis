-- ============================================================
-- 02_reconciliation_queries.sql
-- T+1 持仓与成交对账 + 统一市值/盈亏/收益率口径
-- 在 DataGrip 中逐段执行，或在 Python 中作为参数化查询调用
-- ============================================================

-- ╔══════════════════════════════════════════════════════════════╗
-- ║  Part A: T+1 对账 — 持仓变动 vs 当日成交                      ║
-- ╚══════════════════════════════════════════════════════════════╝

-- A1. 交易汇总（每账户-标的-日期 的净成交）
--     用于和 position 表比对
--     命名: tmp_ 前缀 = CTE / 中间结果，方便复用
WITH
trade_agg AS (
    SELECT
        account_id,
        symbol,
        trade_date,
        SUM(CASE WHEN direction = 'BUY'  THEN quantity ELSE 0 END) AS buy_qty,
        SUM(CASE WHEN direction = 'SELL' THEN quantity ELSE 0 END) AS sell_qty,
        SUM(CASE WHEN direction = 'BUY'  THEN quantity ELSE -quantity END) AS net_qty,
        SUM(CASE WHEN direction = 'BUY'  THEN amount   ELSE 0 END) AS buy_amt,
        SUM(CASE WHEN direction = 'SELL' THEN amount   ELSE 0 END) AS sell_amt
    FROM trade
    GROUP BY account_id, symbol, trade_date
)
-- A2. 主对账: 理论 qty = pre_qty + net_trade_qty
--     差异 >0 = 多记（系统比实际多），<0 = 漏记（系统比实际少）
SELECT
    p.account_id,
    p.symbol,
    p.trade_date,
    p.pre_qty                                                      AS `上日持仓`,
    p.qty                                                          AS `本日持仓(系统)`,
    COALESCE(t.buy_qty,  0)                                        AS `买入股数`,
    COALESCE(t.sell_qty, 0)                                        AS `卖出股数`,
    COALESCE(t.net_qty,  0)                                        AS `净成交`,
    p.pre_qty + COALESCE(t.net_qty, 0)                             AS `理论持仓`,
    p.qty - (p.pre_qty + COALESCE(t.net_qty, 0))                   AS `对账差异`,
    CASE
        WHEN p.qty - (p.pre_qty + COALESCE(t.net_qty, 0)) > 0 THEN '多记(系统多)'
        WHEN p.qty - (p.pre_qty + COALESCE(t.net_qty, 0)) < 0 THEN '漏记(系统少)'
        ELSE '一致'
    END                                                             AS `差异类型`
FROM position p
LEFT JOIN trade_agg t
    ON p.account_id = t.account_id
   AND p.symbol     = t.symbol
   AND p.trade_date = t.trade_date
WHERE p.pre_qty + COALESCE(t.net_qty, 0) <> p.qty   -- 只返回差异行
ORDER BY p.trade_date DESC, ABS(p.qty - (p.pre_qty + COALESCE(t.net_qty, 0))) DESC;


-- ╔══════════════════════════════════════════════════════════════╗
-- ║  Part B: 统一市值 / 盈亏 / 收益率口径                        ║
-- ╚══════════════════════════════════════════════════════════════╝

-- B1. 日频持仓市值、盈亏、收益率（每账户-标的-日期一行）
WITH
trade_agg AS (
    SELECT
        account_id, symbol, trade_date,
        SUM(CASE WHEN direction = 'BUY'  THEN quantity ELSE 0 END) AS buy_qty,
        SUM(CASE WHEN direction = 'SELL' THEN quantity ELSE 0 END) AS sell_qty,
        SUM(CASE WHEN direction = 'BUY'  THEN quantity ELSE -quantity END) AS net_qty,
        SUM(CASE WHEN direction = 'BUY'  THEN amount   ELSE 0 END) AS buy_amt,
        SUM(CASE WHEN direction = 'SELL' THEN amount   ELSE 0 END) AS sell_amt
    FROM trade
    GROUP BY account_id, symbol, trade_date
),
pos_with_mv AS (
    SELECT
        p.account_id,
        p.symbol,
        p.trade_date,
        p.pre_qty,
        p.qty,
        p.cost_price,
        m.close_price,
        -- ── 市值 ──
        ROUND(p.qty * m.close_price, 2)                                 AS market_value,
        -- 上日市值（用 pre_qty × 当日收盘价近似，或 LAG 取上日精确市值）
        ROUND(p.pre_qty * m.close_price, 2)                              AS pre_market_value
    FROM position p
    JOIN market_data m
        ON p.trade_date = m.trade_date
       AND p.symbol     = m.symbol
),
pos_lagged AS (
    -- 取上日收盘价计算上日真实市值
    SELECT
        pwmv.*,
        LAG(m.close_price) OVER (
            PARTITION BY pwmv.account_id, pwmv.symbol
            ORDER BY pwmv.trade_date
        ) AS prev_close_price
    FROM pos_with_mv pwmv
    JOIN market_data m
        ON m.trade_date = DATE_SUB(pwmv.trade_date, INTERVAL 1 DAY)  -- 可能跨周末，实际用 LAG 更安全
       AND m.symbol     = pwmv.symbol
)
-- B3. Actually use a cleaner approach with LAG
-- 最终: 每日盈亏 + 收益率
WITH
daily_snapshot AS (
    SELECT
        p.account_id,
        p.symbol,
        p.trade_date,
        p.qty,
        m.close_price,
        ROUND(p.qty * m.close_price, 2) AS market_value
    FROM position p
    JOIN market_data m
        ON p.trade_date = m.trade_date AND p.symbol = m.symbol
),
snapshot_with_prev AS (
    SELECT
        ds.*,
        LAG(ds.market_value) OVER (
            PARTITION BY ds.account_id, ds.symbol ORDER BY ds.trade_date
        ) AS prev_market_value,
        LAG(ds.qty) OVER (
            PARTITION BY ds.account_id, ds.symbol ORDER BY ds.trade_date
        ) AS prev_qty
    FROM daily_snapshot ds
),
trade_agg AS (
    SELECT
        account_id, symbol, trade_date,
        SUM(CASE WHEN direction = 'BUY' THEN amount ELSE 0 END) AS buy_amt,
        SUM(CASE WHEN direction = 'SELL' THEN amount ELSE 0 END) AS sell_amt
    FROM trade
    GROUP BY account_id, symbol, trade_date
)
SELECT
    s.account_id                                                         AS `账户`,
    s.symbol                                                             AS `标的`,
    s.trade_date                                                         AS `交易日`,
    s.qty                                                                AS `持仓(股)`,
    s.close_price                                                        AS `收盘价`,
    s.market_value                                                       AS `市值`,
    COALESCE(s.prev_market_value, 0)                                     AS `上日市值`,
    COALESCE(t.buy_amt, 0)                                               AS `买入金额`,
    COALESCE(t.sell_amt, 0)                                              AS `卖出金额`,
    -- 净现金流: 卖出收入 - 买入支出 (>0=净流入, <0=净流出)
    COALESCE(t.sell_amt, 0) - COALESCE(t.buy_amt, 0)                     AS `净现金流`,
    -- 当日盈亏 = 市值变动 + 净现金流（卖出收入视为已实现，买入支出视为新增投入）
    -- ΔMV = MV_today - MV_yesterday
    -- P&L = ΔMV - buy_amt + sell_amt = (MV_t - MV_{t-1}) + (sell - buy)
    ROUND(
        (s.market_value - COALESCE(s.prev_market_value, 0))
        + (COALESCE(t.sell_amt, 0) - COALESCE(t.buy_amt, 0)),
        2
    )                                                                    AS `当日盈亏`,
    -- 收益率 = 盈亏 / (上日市值 + 买入金额)
    -- 分母=上日已投入资本 + 当日新增投入
    CASE
        WHEN COALESCE(s.prev_market_value, 0) + COALESCE(t.buy_amt, 0) > 0
        THEN ROUND(
            ((s.market_value - COALESCE(s.prev_market_value, 0))
             + (COALESCE(t.sell_amt, 0) - COALESCE(t.buy_amt, 0)))
            / (COALESCE(s.prev_market_value, 0) + COALESCE(t.buy_amt, 0)) * 100,
            4
        )
        ELSE NULL
    END                                                                  AS `日收益率(%)`
FROM snapshot_with_prev s
LEFT JOIN trade_agg t
    ON s.account_id = t.account_id
   AND s.symbol     = t.symbol
   AND s.trade_date = t.trade_date
ORDER BY s.account_id, s.symbol, s.trade_date;


-- ╔══════════════════════════════════════════════════════════════╗
-- ║  Part C: 账户级汇总（聚合所有标的）                          ║
-- ╚══════════════════════════════════════════════════════════════╝

WITH
daily_snapshot AS (
    SELECT
        p.account_id, p.symbol, p.trade_date,
        ROUND(p.qty * m.close_price, 2) AS market_value
    FROM position p
    JOIN market_data m
        ON p.trade_date = m.trade_date AND p.symbol = m.symbol
),
snapshot_with_prev AS (
    SELECT
        ds.*,
        LAG(ds.market_value) OVER (
            PARTITION BY ds.account_id, ds.symbol ORDER BY ds.trade_date
        ) AS prev_market_value
    FROM daily_snapshot ds
),
trade_agg AS (
    SELECT account_id, symbol, trade_date,
        SUM(CASE WHEN direction = 'BUY' THEN amount ELSE 0 END) AS buy_amt,
        SUM(CASE WHEN direction = 'SELL' THEN amount ELSE 0 END) AS sell_amt
    FROM trade
    GROUP BY account_id, symbol, trade_date
),
detail AS (
    SELECT
        s.account_id, s.trade_date,
        s.market_value,
        COALESCE(s.prev_market_value, 0) AS prev_mv,
        COALESCE(t.buy_amt, 0) AS buy_amt,
        COALESCE(t.sell_amt, 0) AS sell_amt
    FROM snapshot_with_prev s
    LEFT JOIN trade_agg t
        ON s.account_id = t.account_id AND s.symbol = t.symbol AND s.trade_date = t.trade_date
)
SELECT
    account_id                                                            AS `账户`,
    trade_date                                                            AS `交易日`,
    SUM(market_value)                                                     AS `总市值`,
    SUM(prev_mv)                                                          AS `上日总市值`,
    SUM(buy_amt)                                                          AS `总买入`,
    SUM(sell_amt)                                                         AS `总卖出`,
    SUM(sell_amt) - SUM(buy_amt)                                          AS `净现金流`,
    ROUND(
        SUM(market_value - prev_mv) + SUM(sell_amt) - SUM(buy_amt), 2
    )                                                                     AS `总盈亏`,
    CASE
        WHEN SUM(prev_mv) + SUM(buy_amt) > 0
        THEN ROUND(
            (SUM(market_value - prev_mv) + SUM(sell_amt) - SUM(buy_amt))
            / (SUM(prev_mv) + SUM(buy_amt)) * 100, 4
        )
        ELSE NULL
    END                                                                   AS `总收益率(%)`
FROM detail
GROUP BY account_id, trade_date
ORDER BY account_id, trade_date;


-- ╔══════════════════════════════════════════════════════════════╗
-- ║  Part D: 浮动盈亏（持仓成本 vs 市价）                        ║
-- ╚══════════════════════════════════════════════════════════════╝

SELECT
    p.account_id                                                          AS `账户`,
    p.symbol                                                              AS `标的`,
    p.trade_date                                                          AS `交易日`,
    p.qty                                                                 AS `持仓(股)`,
    p.cost_price                                                          AS `成本价`,
    m.close_price                                                         AS `市价`,
    ROUND(p.qty * p.cost_price, 2)                                        AS `成本金额`,
    ROUND(p.qty * m.close_price, 2)                                       AS `市值`,
    ROUND(p.qty * (m.close_price - p.cost_price), 2)                      AS `浮动盈亏`,
    CASE
        WHEN p.cost_price > 0
        THEN ROUND((m.close_price - p.cost_price) / p.cost_price * 100, 4)
        ELSE NULL
    END                                                                   AS `浮动收益率(%)`
FROM position p
JOIN market_data m
    ON p.trade_date = m.trade_date
   AND p.symbol     = m.symbol
WHERE p.qty > 0
ORDER BY p.account_id, p.symbol, p.trade_date;


-- ╔══════════════════════════════════════════════════════════════╗
-- ║  Part E: 对账差异清单（Power BI 直接消费此视图）             ║
-- ╚══════════════════════════════════════════════════════════════╝

CREATE OR REPLACE VIEW v_recon_diff AS
WITH
trade_agg AS (
    SELECT account_id, symbol, trade_date,
        SUM(CASE WHEN direction = 'BUY' THEN quantity ELSE -quantity END) AS net_qty
    FROM trade
    GROUP BY account_id, symbol, trade_date
)
SELECT
    p.account_id,
    p.symbol,
    p.trade_date,
    p.pre_qty,
    p.qty,
    COALESCE(t.net_qty, 0)                                      AS net_trade,
    p.pre_qty + COALESCE(t.net_qty, 0)                          AS expected_qty,
    p.qty - (p.pre_qty + COALESCE(t.net_qty, 0))                AS diff,
    CASE
        WHEN p.qty - (p.pre_qty + COALESCE(t.net_qty, 0)) > 0 THEN '多记'
        WHEN p.qty - (p.pre_qty + COALESCE(t.net_qty, 0)) < 0 THEN '漏记'
        ELSE '一致'
    END                                                          AS diff_type
FROM position p
LEFT JOIN trade_agg t
    ON p.account_id = t.account_id AND p.symbol = t.symbol AND p.trade_date = t.trade_date;


-- ╔══════════════════════════════════════════════════════════════╗
-- ║  Part F: 日收益率视图（Power BI 画时序）                    ║
-- ╚══════════════════════════════════════════════════════════════╝

CREATE OR REPLACE VIEW v_daily_return AS
WITH
daily_snapshot AS (
    SELECT p.account_id, p.symbol, p.trade_date,
        ROUND(p.qty * m.close_price, 2) AS market_value
    FROM position p
    JOIN market_data m ON p.trade_date = m.trade_date AND p.symbol = m.symbol
),
snapshot_with_prev AS (
    SELECT ds.*,
        LAG(ds.market_value) OVER (
            PARTITION BY ds.account_id, ds.symbol ORDER BY ds.trade_date
        ) AS prev_market_value
    FROM daily_snapshot ds
),
trade_agg AS (
    SELECT account_id, symbol, trade_date,
        SUM(CASE WHEN direction = 'BUY' THEN amount ELSE 0 END) AS buy_amt,
        SUM(CASE WHEN direction = 'SELL' THEN amount ELSE 0 END) AS sell_amt
    FROM trade GROUP BY account_id, symbol, trade_date
)
SELECT
    s.account_id,
    s.symbol,
    s.trade_date,
    s.market_value,
    COALESCE(s.prev_market_value, 0) AS prev_mv,
    COALESCE(t.buy_amt, 0) AS buy_amt,
    COALESCE(t.sell_amt, 0) AS sell_amt,
    ROUND(
        (s.market_value - COALESCE(s.prev_market_value, 0))
        + (COALESCE(t.sell_amt, 0) - COALESCE(t.buy_amt, 0)), 2
    ) AS daily_pnl,
    CASE
        WHEN COALESCE(s.prev_market_value, 0) + COALESCE(t.buy_amt, 0) > 0
        THEN ROUND(
            ((s.market_value - COALESCE(s.prev_market_value, 0))
             + (COALESCE(t.sell_amt, 0) - COALESCE(t.buy_amt, 0)))
            / (COALESCE(s.prev_market_value, 0) + COALESCE(t.buy_amt, 0)) * 100, 4
        )
        ELSE NULL
    END AS daily_return_pct
FROM snapshot_with_prev s
LEFT JOIN trade_agg t
    ON s.account_id = t.account_id AND s.symbol = t.symbol AND s.trade_date = t.trade_date;