# -*- coding: utf-8 -*-
"""Step4: 将特征数据写入 MySQL 数据库"""
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

# 读取项目根目录下的 .env（里面是数据库连接串，已被 .gitignore 忽略，不会进仓库）
load_dotenv(Path(__file__).resolve().parent / '.env')

db_url = os.environ.get('DATABASE_URL')
if not db_url:
    raise SystemExit(
        '未找到 DATABASE_URL。请把 .env.example 复制成 .env，并填入自己的数据库连接串。'
    )

engine = create_engine(db_url)

df = pd.read_csv('data/hs300_features.csv', parse_dates=['date'])

df_save = df[['date', 'close', 'ret', 'ma5', 'ma20', 'vol20']].rename(columns={
    'date': 'trade_date',
    'close': 'close_price',
    'ret': 'daily_return',
})

# MySQL 不接受 NaN（rolling 前 19 行有缺失），转成 NULL
df_save = df_save.astype(object).where(pd.notna(df_save), None)

df_save.to_sql('hs300', con=engine, if_exists='replace', index=False)

print('成功')
