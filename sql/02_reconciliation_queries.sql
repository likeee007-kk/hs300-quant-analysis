USE finance_project;
-- ============================================================
-- 02_reconciliation_queries.sql  (v2, 2026-11-21)
-- T+1 持仓与成交对账 + 统一市值/盈亏/收益率口径
-- 适用: MySQL 8.0.14+（使用 LATERAL 派生表 + 窗口函数）
-- 在 DataGrip 中逐段执行，或在 Python 中作为参数化查询调用
-- ============================================================
-- v1 → v2 主要改动:
--   1. 修复 Part B 的语法错误（孤立的 WITH / 死代码，整段无法执行）
--   2. 上日市值改用 position.pre_qty × 上一可用收盘价，
--      不再依赖 LAG 取“上一行”；停牌自动沿用最近收盘价
--   3. 新增 position_adjust 表：送转/拆股/配股/现金分红纳入对账与盈亏
--   4. 收益率拆分为三个口径：TWR / Modified Dietz / 旧资金加权近似
--   5. 新增账户级日收益率 + 链式累计 TWR / Dietz 视图
--   6. 对账差异增加“新发/持续/反转”标记，定位根因日
--   7. 补充索引与分区说明（附可重复执行的索引 DDL）
--
-- ⚠ 口径变更: v_daily_return.daily_return_pct 原来是
--   pnl / (上日市值 + 买入金额)，v2 改为 TWR = pnl / 上日市值。
--   旧口径保留为 simple_return_pct，Power BI 如仍要旧值请切列。
-- ============================================================


-- ╔══════════════════════════════════════════════════════════════╗
-- ║  运行前检查（先单独跑这一段）                                ║
-- ╚══════════════════════════════════════════════════════════════╝
-- 期望:
--   current_database = finance_project（不是 hs300_recon）
--   mysql_version    = 8.0.14+（LATERAL / CTE 需要）
--   表清单包含 trade / position / market_data
-- 若库名不对: 在 DataGrip 连接里把 Database 改成 finance_project。
-- 若表不全:   先在终端跑 python 05_mini_recon_gen.py，
--             或在当前库执行 sql/01_ddl.sql。
SELECT DATABASE() AS current_database, VERSION() AS mysql_version;

SELECT TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('trade', 'position', 'market_data', 'position_adjust')
ORDER BY TABLE_NAME;


-- ╔══════════════════════════════════════════════════════════════╗
-- ║  Part 0: 非交易持仓变动表                                    ║
-- ╚══════════════════════════════════════════════════════════════╝

-- 送转/拆股/配股/分红。没有这张表时，非交易持仓变动
-- 只能被当成“对账差异”，无法和真正的错账区分。
-- 若 01_ddl.sql 或 05 已经建过，这里会直接跳过。
CREATE TABLE IF NOT EXISTS `position_adjust` (
    `adjust_id`   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `account_id`  VARCHAR(16)   NOT NULL COMMENT '账户ID',
    `symbol`      VARCHAR(16)   NOT NULL COMMENT '标的代码',
    `ex_date`     DATE          NOT NULL COMMENT '除权除息日',
    `adjust_type` VARCHAR(20)   NOT NULL COMMENT 'STOCK_DIVIDEND/SPLIT/RIGHTS/CASH_DIVIDEND',
    `qty_delta`   INT           NOT NULL DEFAULT 0 COMMENT '非交易持仓变动(股): 送转/拆股为正, 缩股为负',
    `cash_flow`   DECIMAL(20,2) NOT NULL DEFAULT 0 COMMENT '外部资金流: 配股缴款为正, 退回为负',
    `cash_income` DECIMAL(20,2) NOT NULL DEFAULT 0 COMMENT '投资收益: 现金分红',
    `remark`      VARCHAR(128)  NULL,
    PRIMARY KEY (`adjust_id`),
    UNIQUE KEY `uk_adj` (`account_id`,`symbol`,`ex_date`,`adjust_type`),
    KEY `idx_adj_date` (`ex_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='非交易持仓变动: 送转/拆股/配股/分红';

-- 示例（默认不插入，需要时取消注释）:
-- INSERT INTO position_adjust
--   (account_id, symbol, ex_date, adjust_type, qty_delta, cash_flow, cash_income, remark)
-- VALUES
--   ('acc_001','600519','2026-09-01','STOCK_DIVIDEND', 100,  0.00,   0.00,'10送1'),
--   ('acc_001','600519','2026-09-01','CASH_DIVIDEND',    0,  0.00, 500.00,'每股派5元×100股');

-- 0.2 性能索引。
--     01_ddl.sql 和 05_mini_recon_gen.py 建表时已经包含，
--     旧库如果缺索引，手动执行下面三条（已存在会报 1061，可忽略）:
--
-- CREATE INDEX idx_md_symbol_date
--     ON market_data (symbol, trade_date, close_price);
-- CREATE INDEX idx_trade_acc_sym_date
--     ON trade (account_id, symbol, trade_date);
-- CREATE INDEX idx_trade_date
--     ON trade (trade_date);


-- ╔══════════════════════════════════════════════════════════════╗
-- ║  Part A: T+1 对账 — 持仓变动 vs 当日成交 + 非交易变动        ║
-- ╚══════════════════════════════════════════════════════════════╝

-- A1/A2. 主对账
--   预期持仓 = pre_qty + 当日净成交 + 非交易持仓变动
--   差异 >0 = 多记（系统比实际多），<0 = 漏记（系统比实际少）
--   差异模式:
--     新发 = 前一天无差异、今天出现差异（根因日，优先排查）
--     持续 = 差异方向未变（可能是同一笔错账跨日）
--     反转 = 方向翻转（通常是隔日冲正；也会暴露 05 这类“次日自愈”的埋雷）
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
),
adj_agg AS (
    SELECT
        account_id, symbol, ex_date AS trade_date,
        SUM(qty_delta)   AS qty_delta,
        SUM(cash_flow)   AS cash_flow,
        SUM(cash_income) AS cash_income
    FROM position_adjust
    GROUP BY account_id, symbol, ex_date
),
recon AS (
    SELECT
        p.account_id,
        p.symbol,
        p.trade_date,
        p.pre_qty,
        p.qty,
        COALESCE(t.buy_qty,  0) AS buy_qty,
        COALESCE(t.sell_qty, 0) AS sell_qty,
        COALESCE(t.net_qty,  0) AS net_qty,
        COALESCE(a.qty_delta, 0) AS non_trade_qty,
        p.pre_qty + COALESCE(t.net_qty, 0) + COALESCE(a.qty_delta, 0) AS expected_qty
    FROM position p
    LEFT JOIN trade_agg t
        ON p.account_id = t.account_id
       AND p.symbol     = t.symbol
       AND p.trade_date = t.trade_date
    LEFT JOIN adj_agg a
        ON p.account_id = a.account_id
       AND p.symbol     = a.symbol
       AND p.trade_date = a.trade_date
),
scored AS (
    SELECT
        r.*,
        r.qty - r.expected_qty AS diff,
        LAG(r.qty - r.expected_qty) OVER (
            PARTITION BY r.account_id, r.symbol ORDER BY r.trade_date
        ) AS prev_diff
    FROM recon r
)
SELECT
    account_id                                                     AS `账户`,
    symbol                                                         AS `标的`,
    trade_date                                                     AS `交易日`,
    pre_qty                                                        AS `上日持仓`,
    buy_qty                                                        AS `买入股数`,
    sell_qty                                                       AS `卖出股数`,
    net_qty                                                        AS `净成交`,
    non_trade_qty                                                  AS `非交易变动`,
    expected_qty                                                   AS `理论持仓`,
    qty                                                            AS `本日持仓(系统)`,
    diff                                                           AS `对账差异`,
    CASE
        WHEN diff > 0 THEN '多记(系统多)'
        WHEN diff < 0 THEN '漏记(系统少)'
        ELSE '一致'
    END                                                            AS `差异类型`,
    CASE
        WHEN diff = 0                                  THEN '一致'
        WHEN prev_diff IS NULL OR prev_diff = 0        THEN '新发'
        WHEN SIGN(diff) <> SIGN(prev_diff)             THEN '反转'
        ELSE '持续'
    END                                                            AS `差异模式`
FROM scored
WHERE diff <> 0
ORDER BY trade_date DESC, ABS(diff) DESC;


-- ╔══════════════════════════════════════════════════════════════╗
-- ║  Part B: 单标的日频市值 / 盈亏 / 收益率（视图）              ║
-- ╚══════════════════════════════════════════════════════════════╝
--
-- 关键口径:
--   上日市值 = pre_qty × 上一可用收盘价
--     · 不依赖“上一行”，缺数据/停牌不会取错日期
--     · 上一可用价格用 LATERAL 取 trade_date 之前最近一条行情
--   当日盈亏 = (本日市值 - 上日市值) - (买入 - 卖出 + 非交易资金流) + 现金分红
--   日收益率(TWR) = 当日盈亏 / 上日市值
--     · 买入/卖出是外部现金流，只进分子，不进分母
--   日收益率(Dietz) = 当日盈亏 / (上日市值 + 0.5 × 净资金流)
--     · 假设成交发生在当日中间，比 TWR 更贴近 10:30 成交的实际暴露
--   日收益率(简单) = 当日盈亏 / (上日市值 + 买入金额)   -- v1 旧口径，保留兼容
--   新开仓当日上日市值为 0，TWR 无定义 → NULL，另给 new_position_return_pct

CREATE OR REPLACE VIEW v_daily_return AS
WITH
trade_agg AS (
    SELECT
        account_id, symbol, trade_date,
        SUM(CASE WHEN direction = 'BUY'  THEN quantity ELSE 0 END) AS buy_qty,
        SUM(CASE WHEN direction = 'SELL' THEN quantity ELSE 0 END) AS sell_qty,
        SUM(CASE WHEN direction = 'BUY'  THEN quantity ELSE -quantity END) AS net_qty,
        SUM(CASE WHEN direction = 'BUY'  THEN amount ELSE 0 END) AS buy_amt,
        SUM(CASE WHEN direction = 'SELL' THEN amount ELSE 0 END) AS sell_amt
    FROM trade
    GROUP BY account_id, symbol, trade_date
),
adj_agg AS (
    SELECT
        account_id, symbol, ex_date,
        SUM(qty_delta)   AS qty_delta,
        SUM(cash_flow)   AS cash_flow,
        SUM(cash_income) AS cash_income
    FROM position_adjust
    GROUP BY account_id, symbol, ex_date
),
pos_px AS (
    SELECT
        p.account_id,
        p.symbol,
        p.trade_date,
        p.pre_qty,
        p.qty,
        p.cost_price,
        px.price_date,
        px.close_price,
        pp.prev_price_date,
        pp.prev_close_price,
        ROUND(p.qty * px.close_price, 2) AS market_value,
        CASE
            WHEN p.pre_qty = 0 THEN 0.00
            ELSE ROUND(
                p.pre_qty * COALESCE(pp.prev_close_price, px.close_price), 2
            )
        END AS prev_market_value
    FROM position p
    JOIN LATERAL (
        SELECT m.trade_date AS price_date, m.close_price
        FROM market_data m
        WHERE m.symbol = p.symbol
          AND m.trade_date <= p.trade_date
        ORDER BY m.trade_date DESC
        LIMIT 1
    ) px ON TRUE
    LEFT JOIN LATERAL (
        SELECT m.trade_date AS prev_price_date, m.close_price AS prev_close_price
        FROM market_data m
        WHERE m.symbol = p.symbol
          AND m.trade_date < p.trade_date
        ORDER BY m.trade_date DESC
        LIMIT 1
    ) pp ON TRUE
),
joined AS (
    SELECT
        x.*,
        COALESCE(t.buy_amt, 0)     AS buy_amt,
        COALESCE(t.sell_amt, 0)    AS sell_amt,
        COALESCE(a.qty_delta, 0)   AS non_trade_qty,
        COALESCE(a.cash_flow, 0)   AS adj_cash_flow,
        COALESCE(a.cash_income, 0) AS cash_income
    FROM pos_px x
    LEFT JOIN trade_agg t
        ON x.account_id = t.account_id
       AND x.symbol     = t.symbol
       AND x.trade_date = t.trade_date
    LEFT JOIN adj_agg a
        ON x.account_id = a.account_id
       AND x.symbol     = a.symbol
       AND x.trade_date = a.ex_date
),
calc AS (
    SELECT
        j.*,
        ROUND(
            (market_value - prev_market_value)
            - (buy_amt - sell_amt + adj_cash_flow)
            + cash_income,
            2
        ) AS daily_pnl,
        DATEDIFF(trade_date, price_date)       AS stale_days,
        DATEDIFF(trade_date, prev_price_date)  AS prev_price_gap_days
    FROM joined j
)
SELECT
    account_id,
    symbol,
    trade_date,
    qty,
    close_price,
    market_value,
    prev_market_value AS prev_mv,
    buy_amt,
    sell_amt,
    non_trade_qty,
    adj_cash_flow,
    cash_income,
    daily_pnl,
    CASE WHEN prev_market_value > 0
         THEN ROUND(daily_pnl / prev_market_value * 100, 4)
    END AS daily_return_pct,
    CASE WHEN prev_market_value + 0.5 * (buy_amt - sell_amt + adj_cash_flow) > 0
         THEN ROUND(
             daily_pnl / (prev_market_value + 0.5 * (buy_amt - sell_amt + adj_cash_flow)) * 100,
             4
         )
    END AS dietz_return_pct,
    CASE WHEN prev_market_value + buy_amt > 0
         THEN ROUND(daily_pnl / (prev_market_value + buy_amt) * 100, 4)
    END AS simple_return_pct,
    CASE WHEN prev_market_value = 0 AND buy_amt > 0
         THEN ROUND(daily_pnl / buy_amt * 100, 4)
    END AS new_position_return_pct,
    CASE
        WHEN prev_market_value > 0 AND qty = 0 THEN '清仓'
        WHEN prev_market_value = 0 AND qty > 0 THEN '新开仓'
        WHEN prev_market_value > 0             THEN '正常'
        ELSE '无持仓'
    END AS return_status,
    price_date,
    prev_price_date,
    stale_days,
    prev_price_gap_days
FROM calc;


-- ╔══════════════════════════════════════════════════════════════╗
-- ║  Part C: 账户级日频汇总 + 链式累计 TWR / Dietz（视图）       ║
-- ╚══════════════════════════════════════════════════════════════╝
--
-- 累计 TWR = Π(1 + r_t) - 1，用 EXP(SUM(LN(1+r))) 实现。
-- 上日市值为 0 的日期（新开仓/空仓）r 为 NULL，窗口 SUM 自动跳过，
-- 不会把 0 当成一天 0% 收益。

CREATE OR REPLACE VIEW v_account_daily_return AS
WITH
account_daily AS (
    SELECT
        account_id,
        trade_date,
        SUM(market_value)     AS market_value,
        SUM(prev_mv)          AS prev_mv,
        SUM(buy_amt)          AS buy_amt,
        SUM(sell_amt)         AS sell_amt,
        SUM(adj_cash_flow)    AS adj_cash_flow,
        SUM(cash_income)      AS cash_income,
        SUM(daily_pnl)        AS daily_pnl,
        MAX(stale_days)       AS max_stale_days,
        SUM(CASE WHEN qty > 0 THEN 1 ELSE 0 END) AS position_count
    FROM v_daily_return
    GROUP BY account_id, trade_date
),
calc AS (
    SELECT
        *,
        CASE WHEN prev_mv > 0 THEN daily_pnl / prev_mv END AS twr,
        CASE WHEN prev_mv + 0.5 * (buy_amt - sell_amt + adj_cash_flow) > 0
             THEN daily_pnl / (prev_mv + 0.5 * (buy_amt - sell_amt + adj_cash_flow))
        END AS dietz
    FROM account_daily
)
SELECT
    account_id,
    trade_date,
    market_value,
    prev_mv,
    buy_amt,
    sell_amt,
    adj_cash_flow,
    cash_income,
    daily_pnl,
    ROUND(twr * 100, 4)                 AS twr_pct,
    ROUND(dietz * 100, 4)               AS dietz_pct,
    ROUND(
        (EXP(SUM(CASE WHEN 1 + twr > 0 THEN LN(1 + twr) END)
             OVER (PARTITION BY account_id ORDER BY trade_date)) - 1) * 100,
        4
    )                                   AS cum_twr_pct,
    ROUND(
        (EXP(SUM(CASE WHEN 1 + dietz > 0 THEN LN(1 + dietz) END)
             OVER (PARTITION BY account_id ORDER BY trade_date)) - 1) * 100,
        4
    )                                   AS cum_dietz_pct,
    position_count,
    max_stale_days
FROM calc;


-- ╔══════════════════════════════════════════════════════════════╗
-- ║  Part D: 浮动盈亏（持仓成本 vs 市价）                        ║
-- ╚══════════════════════════════════════════════════════════════╝

SELECT
    p.account_id                                                          AS `账户`,
    p.symbol                                                              AS `标的`,
    p.trade_date                                                          AS `交易日`,
    p.qty                                                                 AS `持仓(股)`,
    p.cost_price                                                          AS `成本价`,
    px.close_price                                                        AS `市价`,
    DATEDIFF(p.trade_date, px.price_date)                                 AS `行情滞后天数`,
    ROUND(p.qty * p.cost_price, 2)                                        AS `成本金额`,
    ROUND(p.qty * px.close_price, 2)                                      AS `市值`,
    ROUND(p.qty * (px.close_price - p.cost_price), 2)                     AS `浮动盈亏`,
    CASE
        WHEN p.cost_price > 0
        THEN ROUND((px.close_price - p.cost_price) / p.cost_price * 100, 4)
        ELSE NULL
    END                                                                   AS `浮动收益率(%)`
FROM position p
JOIN LATERAL (
    SELECT m.trade_date AS price_date, m.close_price
    FROM market_data m
    WHERE m.symbol = p.symbol
      AND m.trade_date <= p.trade_date
    ORDER BY m.trade_date DESC
    LIMIT 1
) px ON TRUE
WHERE p.qty > 0
ORDER BY p.account_id, p.symbol, p.trade_date;


-- D2. 数据质量: 有持仓但此前完全没有行情（真正缺价，不是停牌）
SELECT
    p.account_id, p.symbol, p.trade_date, p.qty
FROM position p
WHERE NOT EXISTS (
    SELECT 1 FROM market_data m
    WHERE m.symbol = p.symbol AND m.trade_date <= p.trade_date
)
ORDER BY p.account_id, p.symbol, p.trade_date;

-- D3. 数据质量: pre_qty 与“上一行 qty”不衔接
--     上一行可能是几天前；不衔接说明漏记了持仓快照。
WITH continuity AS (
    SELECT
        p.account_id, p.symbol, p.trade_date, p.pre_qty, p.qty,
        LAG(p.qty)        OVER (PARTITION BY p.account_id, p.symbol ORDER BY p.trade_date) AS last_qty,
        LAG(p.trade_date) OVER (PARTITION BY p.account_id, p.symbol ORDER BY p.trade_date) AS last_date
    FROM position p
)
SELECT
    account_id, symbol, trade_date, last_date, last_qty, pre_qty, qty,
    pre_qty - last_qty AS continuity_gap
FROM continuity
WHERE last_qty IS NOT NULL
  AND pre_qty <> last_qty
ORDER BY account_id, symbol, trade_date;


-- ╔══════════════════════════════════════════════════════════════╗
-- ║  Part E: 对账明细视图（Power BI 消费）                       ║
-- ╚══════════════════════════════════════════════════════════════╝

CREATE OR REPLACE VIEW v_recon_diff AS
WITH
trade_agg AS (
    SELECT
        account_id, symbol, trade_date,
        SUM(CASE WHEN direction = 'BUY'  THEN quantity ELSE 0 END) AS buy_qty,
        SUM(CASE WHEN direction = 'SELL' THEN quantity ELSE 0 END) AS sell_qty,
        SUM(CASE WHEN direction = 'BUY'  THEN quantity ELSE -quantity END) AS net_qty
    FROM trade
    GROUP BY account_id, symbol, trade_date
),
adj_agg AS (
    SELECT
        account_id, symbol, ex_date AS trade_date,
        SUM(qty_delta) AS qty_delta
    FROM position_adjust
    GROUP BY account_id, symbol, ex_date
),
recon AS (
    SELECT
        p.account_id, p.symbol, p.trade_date,
        p.pre_qty, p.qty,
        COALESCE(t.buy_qty,  0) AS buy_qty,
        COALESCE(t.sell_qty, 0) AS sell_qty,
        COALESCE(t.net_qty,  0) AS net_trade,
        COALESCE(a.qty_delta, 0) AS non_trade_qty,
        p.pre_qty + COALESCE(t.net_qty, 0) + COALESCE(a.qty_delta, 0) AS expected_qty
    FROM position p
    LEFT JOIN trade_agg t
        ON p.account_id = t.account_id
       AND p.symbol     = t.symbol
       AND p.trade_date = t.trade_date
    LEFT JOIN adj_agg a
        ON p.account_id = a.account_id
       AND p.symbol     = a.symbol
       AND p.trade_date = a.trade_date
),
scored AS (
    SELECT
        r.*,
        r.qty - r.expected_qty AS diff,
        LAG(r.qty - r.expected_qty) OVER (
            PARTITION BY r.account_id, r.symbol ORDER BY r.trade_date
        ) AS prev_diff
    FROM recon r
)
SELECT
    account_id,
    symbol,
    trade_date,
    pre_qty,
    buy_qty,
    sell_qty,
    net_trade,
    non_trade_qty,
    expected_qty,
    qty,
    diff,
    CASE
        WHEN diff > 0 THEN '多记'
        WHEN diff < 0 THEN '漏记'
        ELSE '一致'
    END AS diff_type,
    CASE
        WHEN diff = 0                           THEN '一致'
        WHEN prev_diff IS NULL OR prev_diff = 0 THEN '新发'
        WHEN SIGN(diff) <> SIGN(prev_diff)      THEN '反转'
        ELSE '持续'
    END AS diff_pattern,
    CASE WHEN diff <> 0 THEN 1 ELSE 0 END AS is_break
FROM scored;


-- E2. 只看差异行（看板“异常清单”用）
CREATE OR REPLACE VIEW v_recon_breaks AS
SELECT *
FROM v_recon_diff
WHERE diff <> 0;


-- ╔══════════════════════════════════════════════════════════════╗
-- ║  Part F: 性能说明（不是分区提示，是索引 + 物化策略）         ║
-- ╚══════════════════════════════════════════════════════════════╝
--
-- 1) 视图里含窗口函数 / LATERAL 时，MySQL 无法把外层
--    WHERE trade_date = ... 下推进派生表，会先物化整段再过滤。
--    小数据量（几千行）无所谓；千万行级请二选一：
--      a. 用存储过程接收起止日期参数，把日期条件下推进 CTE；
--      b. 每日调度把 v_account_daily_return 落到
--         summary_account_daily 物理表，Power BI 只读汇总表。
--
-- 2) InnoDB 没有“分区索引提示”。真正有效的是：
--      · market_data (symbol, trade_date) → as-of 取价走索引
--      · trade (account_id, symbol, trade_date) → 对账聚合
--      · position 主键 (account_id, symbol, trade_date) → 已覆盖
--    Part 0 已按需创建。
--
-- 3) 分区（仅在单表数千万行以上考虑）。MySQL 8.0 要求
--    每个唯一键都包含分区列，trade 需要先改主键：
--      ALTER TABLE trade
--        DROP PRIMARY KEY,
--        ADD PRIMARY KEY (trade_id, trade_date);
--      ALTER TABLE trade
--        PARTITION BY RANGE (TO_DAYS(trade_date)) (
--          PARTITION p202608 VALUES LESS THAN (TO_DAYS('2026-09-01')),
--          PARTITION p202609 VALUES LESS THAN (TO_DAYS('2026-10-01')),
--          PARTITION p202610 VALUES LESS THAN (TO_DAYS('2026-11-01')),
--          PARTITION pmax    VALUES LESS THAN MAXVALUE
--        );
--    position / market_data 的主键已含 trade_date，可直接分区。
--    改了主键会与 05 的 INSERT ... ON DUPLICATE 无关，
--    但会影响依赖 trade_id 单列唯一性的写入，改前先评估。
--
-- 4) 批量灌数后执行 ANALYZE TABLE trade, position, market_data;
--
-- 5) 索引用 USE INDEX/FORCE INDEX 属于最后手段：MySQL 8.0.46
--    允许在视图定义里写索引提示，但硬编码后换数据分布就会变成
--    性能负债。先看 EXPLAIN，再决定。
