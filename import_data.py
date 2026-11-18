# -*- coding: utf-8 -*-
"""Step 5（可选）：将特征表写入 MySQL，含数据质量校验与增量 upsert。

用法：
    python import_data.py --dry-run   # 只做质量校验，不连接数据库
    python import_data.py             # 建表并 upsert 到 MySQL

连接串从 .env 的 DATABASE_URL 读取；.env 已被 .gitignore 忽略。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy.dialects.mysql import insert

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "data" / "hs300_features.csv"
SCHEMA_PATH = ROOT / "schema.sql"

# CSV 列 -> MySQL 列
COLUMN_MAP = {
    "date": "trade_date",
    "open": "open_price",
    "high": "high_price",
    "low": "low_price",
    "close": "close_price",
    "volume": "volume",
    "ret": "daily_return",
    "nav": "nav",
    "pmi": "pmi",
    "ma5": "ma5",
    "ma20": "ma20",
    "vol20": "vol20",
    "dif": "dif",
    "dea": "dea",
    "macd": "macd",
    "ma_ratio": "ma_ratio",
    "vratio": "vratio",
}
REQUIRED_COLUMNS = list(COLUMN_MAP)
NON_NULL_COLUMNS = ("date", "close", "ret", "pmi")
ROLLING_COLUMNS = ("ma5", "ma20", "vol20", "ma_ratio", "vratio")
MYSQL_COLUMNS = list(COLUMN_MAP.values())


def _force_utf8() -> None:
    """Windows 控制台默认 GBK，统一切到 UTF-8，避免中文报告乱码。"""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def load_features(path: Path = CSV_PATH) -> pd.DataFrame:
    """读取特征表；utf-8-sig 兼容带 BOM 的 CSV。"""
    return pd.read_csv(path, encoding="utf-8-sig", parse_dates=["date"])


def validate_features(df: pd.DataFrame) -> dict[str, Any]:
    """校验字段、主键、空值与日期范围，返回可打印的质量报告。"""
    report: dict[str, Any] = {
        "rows": int(len(df)),
        "errors": [],
        "warnings": [],
        "null_counts": {},
    }

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        report["errors"].append(f"缺少列：{missing}")
        return report

    if df["date"].isna().any():
        report["errors"].append("date 存在空值")

    dup = int(df["date"].duplicated().sum())
    if dup:
        report["warnings"].append(
            f"date 存在 {dup} 个重复值，加载时将按主键 trade_date 保留最后一条"
        )
    if not df["date"].is_monotonic_increasing:
        report["warnings"].append("date 未按升序排列，加载时将重新排序")

    for col in NON_NULL_COLUMNS:
        n = int(df[col].isna().sum())
        if n:
            report["errors"].append(f"{col} 存在 {n} 个空值")

    # 滚动特征允许前 20 行空，但不允许后段空
    for col in ROLLING_COLUMNS:
        nulls = df[col].isna()
        if nulls.any() and bool(nulls.iloc[20:].any()):
            report["errors"].append(f"{col} 在滚动窗口之后仍有空值")

    report["null_counts"] = {
        c: int(df[c].isna().sum()) for c in df.columns if df[c].isna().any()
    }
    report["duplicate_dates"] = dup
    if len(df):
        report["date_min"] = str(df["date"].min().date())
        report["date_max"] = str(df["date"].max().date())
        report["months"] = int(df["date"].dt.to_period("M").nunique())
    return report


def prepare_for_load(df: pd.DataFrame) -> pd.DataFrame:
    """按 trade_date 去重、排序、改列名，并把 NaN 转成 MySQL 可接受的 NULL。"""
    out = (
        df.drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .rename(columns=COLUMN_MAP)
        .copy()
    )
    out["trade_date"] = out["trade_date"].dt.date
    return out.astype(object).where(pd.notna(out), None)


def ensure_schema(engine) -> None:
    ddl = SCHEMA_PATH.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(ddl))


def upsert_rows(
    engine,
    rows: pd.DataFrame,
    table_name: str = "hs300",
    chunk_size: int = 500,
) -> int:
    """按主键 trade_date 执行 INSERT ... ON DUPLICATE KEY UPDATE。"""
    if rows.empty:
        return 0

    metadata = MetaData()
    table = Table(table_name, metadata, autoload_with=engine)
    cols = [c for c in MYSQL_COLUMNS if c in table.c]
    records = rows[cols].to_dict(orient="records")

    written = 0
    with engine.begin() as conn:
        for start in range(0, len(records), chunk_size):
            chunk = records[start : start + chunk_size]
            stmt = insert(table).values(chunk)
            update_cols = {c: stmt.inserted[c] for c in cols if c != "trade_date"}
            conn.execute(stmt.on_duplicate_key_update(**update_cols))
            written += len(chunk)
    return written


def print_report(report: dict[str, Any]) -> None:
    summary_keys = ["rows", "date_min", "date_max", "months", "duplicate_dates"]
    print("[validate] " + ", ".join(f"{k}={report[k]}" for k in summary_keys if k in report))
    if report["null_counts"]:
        print(f"[validate] 滚动窗口内允许的空值：{report['null_counts']}")
    for msg in report["warnings"]:
        print(f"[warn] {msg}")
    for msg in report["errors"]:
        print(f"[error] {msg}")


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    parser = argparse.ArgumentParser(description="将 hs300 特征表写入 MySQL（增量 upsert）")
    parser.add_argument("--dry-run", action="store_true", help="只做质量校验，不连接数据库")
    parser.add_argument("--database-url", default=None, help="覆盖 .env 中的 DATABASE_URL")
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env")
    db_url = args.database_url or os.environ.get("DATABASE_URL")

    df = load_features()
    report = validate_features(df)
    print_report(report)

    if report["errors"]:
        print("[abort] 数据质量校验未通过，未写入数据库。", file=sys.stderr)
        return 1
    if args.dry_run:
        print("[dry-run] 校验通过；未连接数据库。")
        return 0
    if not db_url:
        print(
            "[abort] 未找到 DATABASE_URL。请复制 .env.example 为 .env 并填写。",
            file=sys.stderr,
        )
        return 2

    rows = prepare_for_load(df)
    engine = create_engine(db_url, pool_pre_ping=True)
    ensure_schema(engine)
    written = upsert_rows(engine, rows)
    print(f"[mysql] 已 upsert {written} 行到 hs300（主键 trade_date，重复日期自动更新）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
