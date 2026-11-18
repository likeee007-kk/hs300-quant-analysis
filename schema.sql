-- ============================================================
-- hs300：沪深300日频行情、特征与 PMI 对齐表
-- 主键：trade_date（天然去重；重复加载走 ON DUPLICATE KEY UPDATE）
-- 说明：滚动特征前 19 个交易日允许为 NULL
-- ============================================================

CREATE TABLE IF NOT EXISTS hs300 (
    trade_date   DATE          NOT NULL COMMENT '交易日',
    open_price   DECIMAL(12,4) NULL     COMMENT '开盘价',
    high_price   DECIMAL(12,4) NULL     COMMENT '最高价',
    low_price    DECIMAL(12,4) NULL     COMMENT '最低价',
    close_price  DECIMAL(12,4) NOT NULL COMMENT '收盘价',
    volume       BIGINT        NULL     COMMENT '成交量',
    daily_return DECIMAL(12,8) NULL     COMMENT '日收益率',
    nav          DECIMAL(12,6) NULL     COMMENT '净值（起点 1.0）',
    pmi          DECIMAL(8,4)  NULL     COMMENT '制造业 PMI（月末发布，backward 对齐）',
    ma5          DECIMAL(12,4) NULL     COMMENT '5 日均线',
    ma20         DECIMAL(12,4) NULL     COMMENT '20 日均线',
    vol20        DECIMAL(12,8) NULL     COMMENT '20 日收益波动率',
    dif          DECIMAL(12,6) NULL     COMMENT 'MACD DIF',
    dea          DECIMAL(12,6) NULL     COMMENT 'MACD DEA',
    macd         DECIMAL(12,6) NULL     COMMENT 'MACD 柱 = 2 * (DIF - DEA)',
    ma_ratio     DECIMAL(12,8) NULL     COMMENT 'MA5 / MA20 - 1',
    vratio       DECIMAL(12,8) NULL     COMMENT 'volume / MA20(volume) - 1',
    updated_at   TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                  ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    PRIMARY KEY (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='沪深300日频行情、特征与PMI对齐表';
