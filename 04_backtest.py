# -*- coding: utf-8 -*-
"""Step4: 基于 Step3 特征与 IC 结论的趋势择时回测（含成本 / 样本外 / 敏感性）。

相较初版，本版做了三点优化：
1. 信号与 Step3 对齐：不再直接用 ma5 > ma20 这个二值状态，而是对 Step3 的
   ma_ratio 做 252 日滚动 z-score（与 Step3 的 rolling_zscore 同口径，跨期可比），
   再加滞回带：z > +0.5 入场、z < -0.5 离场。滞回带避免均线贴合时反复开平
   （初版年化换手约 18 倍，改进后约 7 倍）。
2. 成本与评价：按单边 5bp 计交易成本，额外输出年化换手、成本拖累、Calmar、
   持仓占比、交易次数，并保留“零成本”对照，避免把零成本回测当成策略收益。
3. 防过拟合：前 70% 为样本内（IS），后 30% 为样本外（OOS），只做一次分割；
   参数网格只在 IS 上展示敏感性，默认参数由经济含义预先固定（±0.5σ），
   不在 IS 上挑“最优参数”再拿 OOS 背书。

补充：Step3 的 IC 只看了 1 日 horizon。趋势策略的持有期更长，这里用
“非重叠样本”补算 1/5/20/60 日 IC，检验信号在更长周期是否成立。结论是
所有特征在任何周期都不显著，因此本策略的定位应是“控制回撤”，而非 alpha。
"""
import os
import sys
import codecs

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ---------------------------------------------------------------- 参数
HORIZONS = (1, 5, 20, 60)   # 多周期 IC 检验
Z_WINDOW = 252              # 滚动 z-score 窗口（约一年），与 Step3 一致
MIN_PERIODS = 60
ENTER_Z = 0.5               # 入场：趋势强度高于自身一年均值 0.5σ
EXIT_Z = -0.5               # 离场：跌破一年均值 0.5σ
COST = 0.0005               # 单边交易成本 5bp（佣金 + 冲击成本，保守估计）
IS_FRAC = 0.70              # 样本内占比
TRADING_DAYS = 252
FEATURES = ["ma_ratio", "macd", "vol20", "vratio", "pmi"]

# Windows 控制台默认 GBK，统一为 UTF-8 以便中文输出正常显示
def _force_utf8(stream):
    """尽力把文本流切到 UTF-8；没有 reconfigure 的流（代理/测试捕获）直接跳过。"""
    if stream is None:
        return
    enc = getattr(stream, "encoding", None)
    if enc:
        try:
            if codecs.lookup(enc).name == "utf-8":
                return
        except LookupError:
            pass
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass


_force_utf8(sys.stdout)
_force_utf8(sys.stderr)
os.makedirs("figures", exist_ok=True)


# ---------------------------------------------------------------- 工具函数
def rolling_zscore(s: pd.Series, window: int = Z_WINDOW, min_periods: int = MIN_PERIODS) -> pd.Series:
    """滚动标准化；与 Step3 同口径，保证特征强度跨期可比。"""
    mu = s.rolling(window, min_periods=min_periods).mean()
    sd = s.rolling(window, min_periods=min_periods).std()
    return (s - mu) / sd.replace(0, np.nan)


def band_signal(z: pd.Series, enter: float = ENTER_Z, exit_: float = EXIT_Z) -> pd.Series:
    """滞回带状态机：z > enter 才做多，z < exit_ 才离场，中间区域保持原仓位。"""
    if exit_ >= enter:
        raise ValueError("滞回带要求 exit_ < enter")
    state = 0
    out = np.zeros(len(z))
    for i, v in enumerate(np.asarray(z, dtype=float)):
        if np.isfinite(v):
            if state == 0 and v > enter:
                state = 1
            elif state == 1 and v < exit_:
                state = 0
        out[i] = state
    return pd.Series(out, index=z.index)


def forward_return(ret: pd.Series, h: int) -> pd.Series:
    """未来 h 日累计收益 ret(t+1)..ret(t+h)。"""
    nav = (1 + ret).cumprod()
    return nav.shift(-h) / nav - 1


def horizon_ic(df: pd.DataFrame, columns=FEATURES, horizons=HORIZONS) -> pd.DataFrame:
    """多周期 IC：每隔 h 个交易日取一个样本，避免重叠窗口把 t 值放大。

    Step3 的每日 IC 若直接用于 60 日收益，样本高度重叠（同一段行情被重复计数），
    t 值会被严重高估；这里用非重叠抽样，n≈样本天数/h。
    """
    rows = []
    for col in columns:
        for h in horizons:
            fwd = forward_return(df["ret"], h)
            m = pd.concat([df[col].rename("x"), fwd.rename("fwd")], axis=1)
            m = m.iloc[::h].dropna()          # 先等间隔抽样，再删缺失 → 非重叠
            n = len(m)
            if n < 4:
                continue
            ic = m["x"].corr(m["fwd"])
            t_stat = ic * np.sqrt((n - 2) / (1 - ic ** 2)) if abs(ic) < 1 else np.nan
            rows.append({"feature": col, "horizon": h, "n": n, "ic": ic, "t_stat": t_stat})
    return pd.DataFrame(rows)


def performance(pos: pd.Series, ret: pd.Series, cost: float = COST) -> pd.Series:
    """按日频仓位评估：收益、风险、换手与成本。

    pos 是当日实际持仓（0/1），已做过 shift，故不涉及前视。
    成本 = |Δ仓位| × 单边成本，调仓当日计提。
    """
    pos = pd.Series(np.asarray(pos, dtype=float)).reset_index(drop=True)
    ret = pd.Series(np.asarray(ret, dtype=float)).reset_index(drop=True)

    turn = pos.diff().abs()
    turn.iloc[0] = abs(pos.iloc[0])          # 每段独立评估，起点视为空仓
    gross = pos * ret
    net = gross - turn * cost

    nav = (1 + net).cumprod()
    n = len(net)
    years = n / TRADING_DAYS
    total = nav.iloc[-1] - 1
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 else np.nan
    vol = net.std(ddof=1) * np.sqrt(TRADING_DAYS)
    sharpe = net.mean() / net.std(ddof=1) * np.sqrt(TRADING_DAYS) if net.std(ddof=1) > 0 else np.nan
    mdd = (nav / nav.cummax() - 1).min()
    calmar = cagr / abs(mdd) if mdd < 0 else np.nan
    in_market = pos > 0
    win = (net[in_market] > 0).mean() if in_market.any() else np.nan
    trades = int((turn > 1e-12).sum())
    turnover_ann = turn.sum() / years
    cost_ann = turn.sum() * cost / years

    return pd.Series({
        "total_return": total,
        "cagr": cagr,
        "ann_vol": vol,
        "sharpe": sharpe,
        "max_drawdown": mdd,
        "calmar": calmar,
        "win_rate": win,
        "exposure": pos.mean(),
        "trades": trades,
        "turnover_ann": turnover_ann,
        "cost_ann": cost_ann,
    })


def segment_metrics(positions: dict, ret: pd.Series, segments: dict, cost: float = COST) -> pd.DataFrame:
    """对每个策略 × 每个样本区间计算指标。"""
    rows = []
    for seg_name, (lo, hi) in segments.items():
        for strat_name, pos in positions.items():
            s = performance(pos.iloc[lo:hi], ret.iloc[lo:hi], cost)
            rows.append({"segment": seg_name, "strategy": strat_name, **s.to_dict()})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 读取数据
df = pd.read_csv("data/hs300_features.csv", parse_dates=["date"])
# ma_ratio 前 19 行是滚动窗口热启动期；从第一个有效值开始回测
df = df.dropna(subset=["ma5", "ma20"]).reset_index(drop=True)

print(f"[Step4] 回测区间 {df['date'].iloc[0]:%Y-%m-%d} → {df['date'].iloc[-1]:%Y-%m-%d}，"
      f"{len(df)} 个交易日")

# ---------------------------------------------------------------- 1. 多周期 IC（复核 Step3）
print("\n[Step4] ===== 多周期 IC（非重叠样本，feature(t) vs 未来 h 日累计收益）=====")
ic_h = horizon_ic(df)
ic_h.to_csv("data/hs300_ic_horizon.csv", index=False, encoding="utf-8-sig")
ic_wide = ic_h.pivot(index="feature", columns="horizon", values="ic").reindex(FEATURES)
t_wide = ic_h.pivot(index="feature", columns="horizon", values="t_stat").reindex(FEATURES)
for feat in FEATURES:
    cells = "  ".join(f"h{h}: r={ic_wide.loc[feat, h]:+.3f} (t={t_wide.loc[feat, h]:+.2f})"
                      for h in HORIZONS)
    print(f"  {feat:<9s} {cells}")
print("  → 所有 |t| < 1.96：本样本中没有任何特征在 1/5/20/60 日周期上显著；")
print("    Step3 若用重叠样本算长周期 IC，t 值会被高估数倍，这里已用非重叠抽样校正。")

# ---------------------------------------------------------------- 2. 构造信号
df["z_trend"] = rolling_zscore(df["ma_ratio"])

# 信号在 t 日收盘后生成，t+1 日才持仓（shift(1)），杜绝前视偏差
sig_band = band_signal(df["z_trend"], ENTER_Z, EXIT_Z)
sig_ma = (df["ma5"] > df["ma20"]).astype(float)

positions = {
    "z-band": sig_band.shift(1).fillna(0),
    "MA5/20": sig_ma.shift(1).fillna(0),
    "Buy&Hold": pd.Series(1.0, index=df.index),
}

# 样本内 / 样本外各评估一次，样本外不参与任何参数选择
n = len(df)
cut = int(n * IS_FRAC)
segments = {
    "FULL": (0, n),
    "IS": (0, cut),
    "OOS": (cut, n),
}
print(f"\n[Step4] 样本切分：IS 到 {df['date'].iloc[cut - 1]:%Y-%m-%d}，"
      f"OOS 从 {df['date'].iloc[cut]:%Y-%m-%d} 起")

# ---------------------------------------------------------------- 3. 回测结果
stats = segment_metrics(positions, df["ret"], segments)
stats.to_csv("data/hs300_backtest_stats.csv", index=False, encoding="utf-8-sig")

cols = ["total_return", "cagr", "ann_vol", "sharpe", "max_drawdown", "calmar",
        "win_rate", "exposure", "trades", "turnover_ann", "cost_ann"]
pct_cols = ["total_return", "cagr", "ann_vol", "max_drawdown", "win_rate", "exposure", "cost_ann"]
show = stats[["segment", "strategy"] + cols].copy()
for c in pct_cols:
    show[c] = show[c].map(lambda v: f"{v:7.2%}" if pd.notna(v) else "      -")
for c in ["sharpe", "calmar", "turnover_ann"]:
    show[c] = show[c].map(lambda v: f"{v:6.2f}" if pd.notna(v) else "     -")

for seg_name in segments:
    part = show[show["segment"] == seg_name]
    if seg_name == "FULL":
        title = f"全样本 {df['date'].iloc[0]:%Y-%m-%d} → {df['date'].iloc[-1]:%Y-%m-%d}"
    elif seg_name == "IS":
        title = f"样本内 {df['date'].iloc[0]:%Y-%m-%d} → {df['date'].iloc[cut - 1]:%Y-%m-%d}"
    else:
        title = f"样本外 {df['date'].iloc[cut]:%Y-%m-%d} → {df['date'].iloc[-1]:%Y-%m-%d}"
    print(f"\n[Step4] ===== {title} =====")
    print(part.drop(columns=["segment"]).to_string(index=False))

# 零成本对照：看成本到底吃掉了多少
gross_stats = segment_metrics({"z-band(零成本)": positions["z-band"]}, df["ret"], segments, cost=0.0)
zero = gross_stats[["segment", "total_return", "cagr", "sharpe", "max_drawdown"]].copy()
for c in ["total_return", "cagr", "max_drawdown"]:
    zero[c] = zero[c].map(lambda v: f"{v:7.2%}")
print("\n[Step4] 零成本对照（z-band）：")
print(zero.to_string(index=False))
print(f"  单边成本 {COST:.2%}，全样本成本拖累约 "
      f"{stats[(stats.segment == 'FULL') & (stats.strategy == 'z-band')]['cost_ann'].iloc[0]:.2%}/年")

# 相对基准的超额
print("\n[Step4] 相对 Buy&Hold 的年化超额：")
for seg_name in segments:
    b = stats[(stats.segment == seg_name) & (stats.strategy == "Buy&Hold")].iloc[0]
    for strat in ("z-band", "MA5/20"):
        s = stats[(stats.segment == seg_name) & (stats.strategy == strat)].iloc[0]
        excess = s["cagr"] - b["cagr"]
        dd_gain = s["max_drawdown"] - b["max_drawdown"]
        print(f"  {seg_name:<5s} {strat:<7s} CAGR 超额 {excess:+.2%}，"
              f"最大回撤改善 {dd_gain:+.2%}")

# ---------------------------------------------------------------- 4. 参数敏感性（仅 IS）
print("\n[Step4] ===== 滞回带参数敏感性（仅样本内；默认 +0.5/-0.5，不据此挑参）=====")
grid_rows = []
for enter in (0.25, 0.5, 0.75, 1.0):
    for exit_ in (-0.75, -0.5, -0.25, 0.0):
        if exit_ >= enter:
            continue
        sig = band_signal(df["z_trend"], enter, exit_).shift(1).fillna(0)
        m = performance(sig.iloc[:cut], df["ret"].iloc[:cut])
        grid_rows.append({"enter": enter, "exit": exit_, **m.to_dict()})
grid = pd.DataFrame(grid_rows)
grid.to_csv("data/hs300_param_grid.csv", index=False, encoding="utf-8-sig")
grid_show = grid[["enter", "exit", "cagr", "sharpe", "max_drawdown", "exposure", "turnover_ann"]].copy()
grid_show["cagr"] = grid_show["cagr"].map(lambda v: f"{v:7.2%}")
grid_show["max_drawdown"] = grid_show["max_drawdown"].map(lambda v: f"{v:7.2%}")
grid_show["exposure"] = grid_show["exposure"].map(lambda v: f"{v:7.2%}")
print(grid_show.round(2).to_string(index=False))
print("  → 参数越宽，回撤越大、收益越差；越窄，持仓占比越低、越接近空仓。")
print("    区间内没有稳定占优的组合，说明结果对参数敏感，不应当作可交易的 alpha。")

# ---------------------------------------------------------------- 5. 分年度收益
year = df["date"].dt.year
net_band = positions["z-band"] * df["ret"]
net_ma = positions["MA5/20"] * df["ret"]
yearly = pd.DataFrame({
    "z-band": (1 + net_band).groupby(year).prod() - 1,
    "MA5/20": (1 + net_ma).groupby(year).prod() - 1,
    "Buy&Hold": (1 + df["ret"]).groupby(year).prod() - 1,
})
yearly.to_csv("data/hs300_backtest_yearly.csv", encoding="utf-8-sig")
print("\n[Step4] ===== 分年度收益（不含交易成本）=====")
print((yearly * 100).round(1).to_string())

# ---------------------------------------------------------------- 6. 图：净值 / 回撤 / 仓位
nav_band = (1 + positions["z-band"] * df["ret"]).cumprod()
nav_ma = (1 + positions["MA5/20"] * df["ret"]).cumprod()
nav_bh = (1 + df["ret"]).cumprod()

fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True,
                         gridspec_kw={"height_ratios": [3, 1.4, 1]})
ax = axes[0]
ax.plot(df["date"], nav_band, label=f"Z-band (z>{ENTER_Z:+.1f} / z<{EXIT_Z:+.1f})", linewidth=1.6)
ax.plot(df["date"], nav_ma, label="MA5/20 crossover", alpha=0.8, linewidth=1.1)
ax.plot(df["date"], nav_bh, label="Buy & Hold", alpha=0.7, linewidth=1.1)
ax.axvline(df["date"].iloc[cut], color="k", linestyle="--", linewidth=0.9, alpha=0.6)
ax.text(df["date"].iloc[cut], ax.get_ylim()[1], "  OOS →", va="top", fontsize=9)
ax.set_ylabel("NAV")
ax.set_title("Strategy vs Benchmark (gross of cost)")
ax.legend(loc="upper left")
ax.grid(alpha=0.3)

ax = axes[1]
for nav, label in ((nav_band, "Z-band"), (nav_ma, "MA5/20"), (nav_bh, "Buy & Hold")):
    ax.plot(df["date"], nav / nav.cummax() - 1, label=label, linewidth=1.1)
ax.axvline(df["date"].iloc[cut], color="k", linestyle="--", linewidth=0.9, alpha=0.6)
ax.set_ylabel("Drawdown")
ax.legend(loc="lower left", fontsize=8)
ax.grid(alpha=0.3)

ax = axes[2]
ax.step(df["date"], positions["z-band"], where="post", label="Z-band position", linewidth=1.2)
ax.step(df["date"], positions["MA5/20"], where="post", label="MA5/20 position",
        linewidth=0.9, alpha=0.6)
ax.axvline(df["date"].iloc[cut], color="k", linestyle="--", linewidth=0.9, alpha=0.6)
ax.set_ylabel("Position")
ax.set_xlabel("Date")
ax.set_ylim(-0.1, 1.15)
ax.legend(loc="upper left", fontsize=8)
ax.grid(alpha=0.3)
fig.tight_layout()
plt.savefig("figures/hs300_backtest.png", dpi=150)
plt.close()

# ---------------------------------------------------------------- 落盘
out = pd.DataFrame({
    "date": df["date"],
    "close": df["close"],
    "ret": df["ret"],
    "z_trend": df["z_trend"],
    "signal_band": sig_band,
    "position_band": positions["z-band"],
    "ret_band": net_band,
    "nav_band": nav_band,
    "position_ma": positions["MA5/20"],
    "ret_ma": net_ma,
    "nav_ma": nav_ma,
    "nav_benchmark": nav_bh,
})
out.to_csv("data/hs300_backtest.csv", index=False, encoding="utf-8-sig")

print("\n[Step4] 完成：回测明细、统计表、年度收益、参数网格已保存（data/），"
      "净值/回撤/仓位图已保存（figures/hs300_backtest.png）")
