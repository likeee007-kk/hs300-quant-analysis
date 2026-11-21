-- ============================================================
-- 01_ddl.sql — 手写 DDL：trade / position / market_data / position_adjust 四表
-- 数据库: finance_project（与 .env / 05 / 06 一致，不要用 hs300_recon）
-- 用途: T+1 持仓与成交对账 + 统一市值/盈亏/收益率计算
-- ============================================================

-- 库不存在时创建（DataGrip 中手动执行；hs300 账号可能没有 CREATE DATABASE 权限）
-- CREATE DATABASE IF NOT EXISTS finance_project
--   DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- USE finance_project;

-- ── 1. 交易流水表 ──────────────────────────────────────────────
DROP TABLE IF EXISTS `trade`;
CREATE TABLE `trade` (
    `trade_id`    VARCHAR(64)     NOT NULL COMMENT '交易编号（唯一）',
    `account_id`  VARCHAR(16)     NOT NULL COMMENT '账户ID',
    `symbol`      VARCHAR(16)     NOT NULL COMMENT '标的代码',
    `direction`   VARCHAR(4)      NOT NULL COMMENT 'BUY / SELL',
    `quantity`    INT             NOT NULL COMMENT '成交数量（股）',
    `price`       DECIMAL(12,4)   NOT NULL COMMENT '成交价',
    `amount`      DECIMAL(20,2)   NOT NULL COMMENT '成交金额 = quantity × price',
    `trade_time`  DATETIME        NOT NULL COMMENT '成交时间',
    `trade_date`  DATE            NOT NULL COMMENT '交易日（派生列，加速 JOIN）',
    PRIMARY KEY (`trade_id`),
    INDEX `idx_trade_acc_date` (`account_id`, `trade_date`),
    INDEX `idx_trade_symbol_date` (`symbol`, `trade_date`),
    INDEX `idx_trade_acc_sym_date` (`account_id`, `symbol`, `trade_date`),
    INDEX `idx_trade_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='交易流水表 — 每一笔成交一条记录';

-- ── 2. 日频行情表 ──────────────────────────────────────────────
DROP TABLE IF EXISTS `market_data`;
CREATE TABLE `market_data` (
    `trade_date`  DATE            NOT NULL COMMENT '交易日',
    `symbol`      VARCHAR(16)     NOT NULL COMMENT '标的代码',
    `close_price` DECIMAL(12,4)   NOT NULL COMMENT '日收盘价',
    `pre_close`   DECIMAL(12,4)   NULL     COMMENT '前收盘价（用于涨跌幅）',
    PRIMARY KEY (`trade_date`, `symbol`),
    INDEX `idx_md_symbol` (`symbol`),
    INDEX `idx_md_symbol_date` (`symbol`, `trade_date`, `close_price`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='日频行情表 — 每个交易日每个标的收盘价';

-- ── 3. 日频持仓表 ──────────────────────────────────────────────
-- 注意: position 是 MySQL 保留字，必须加反引号
DROP TABLE IF EXISTS `position`;
CREATE TABLE `position` (
    `account_id`  VARCHAR(16)     NOT NULL COMMENT '账户ID',
    `symbol`      VARCHAR(16)     NOT NULL COMMENT '标的代码',
    `trade_date`  DATE            NOT NULL COMMENT '交易日',
    `pre_qty`     INT             NOT NULL DEFAULT 0 COMMENT '上日持仓（股）',
    `qty`         INT             NOT NULL DEFAULT 0 COMMENT '本日持仓（股）',
    `cost_price`  DECIMAL(12,4)   NOT NULL DEFAULT 0 COMMENT '持仓成本价（加权平均）',
    PRIMARY KEY (`account_id`, `symbol`, `trade_date`),
    INDEX `idx_pos_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='日频持仓表 — 每个交易日盘后持仓快照';

-- ── 4. 非交易持仓变动表 ────────────────────────────────────────
-- 送股/转增/拆股/配股/现金分红。对账公式:
--   理论持仓 = pre_qty + net_trade + qty_delta
DROP TABLE IF EXISTS `position_adjust`;
CREATE TABLE `position_adjust` (
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