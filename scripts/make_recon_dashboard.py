# -*- coding: utf-8 -*-
"""生成对账看板 HTML —— 浏览器直接打开，不需要服务器。"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
ENGINE = create_engine(os.environ.get("DATABASE_URL"))


def _fmt(n, decimals=0):
    """格式化金额。"""
    if abs(n) >= 1e6:
        return f"{n/1e6:,.{decimals}f}M"
    if abs(n) >= 1e4:
        return f"{n/1e4:,.{decimals}f}万"
    return f"{n:,.{decimals}f}"


def _choose_class(name):
    """上市公司 → 中文简称。"""
    m = {
        "600519": "贵州茅台", "000858": "五粮液",
        "601318": "中国平安", "000333": "美的集团",
        "600036": "招商银行",
    }
    return m.get(name, name)


def extract():
    """从 MySQL 提取看板所需数据，返回 JSON。"""
    with ENGINE.connect() as c:
        acct = pd.read_sql(text("SELECT * FROM v_account_daily_return ORDER BY account_id, trade_date"), c)
        breaks = pd.read_sql(text("SELECT * FROM v_recon_breaks ORDER BY trade_date DESC, account_id, symbol"), c)

    for df in [acct, breaks]:
        for col in df.select_dtypes(include=["datetime64[ns]", "object"]).columns:
            df[col] = df[col].astype(str)

    latest_date = acct["trade_date"].max()
    latest = acct[acct["trade_date"] == latest_date]

    # ── KPI ──
    total_mv = float(latest["market_value"].sum())
    total_pnl = float(latest["daily_pnl"].sum())
    total_pos_count = int(latest["position_count"].sum())

    # 每个账户的 TWR（卡片文本）
    twr_parts = []
    for _, row in latest.iterrows():
        aid = row["account_id"]
        twr_parts.append({"account_id": aid, "twr": round(float(row["cum_twr_pct"]), 2)})

    # TWR 时序（给画线图）
    twr_series = {}
    for aid in sorted(acct["account_id"].unique()):
        d = acct[acct["account_id"] == aid]
        twr_series[aid] = {
            "dates": d["trade_date"].tolist(),
            "values": d["cum_twr_pct"].round(2).tolist(),
        }

    # 对账差异
    breaks_rows = []
    for _, r in breaks.iterrows():
        diff = int(r["diff"])
        pre = r["pre_qty"]
        qty = r["qty"]
        net = r["net_trade"]
        nq = r["non_trade_qty"]
        dp = r["diff_pattern"]
        sym = _choose_class(r["symbol"])

        guide = {
            "新发": "首次出现差异——优先排查当天发生的原始错账。",
            "反转": "昨日差异方向相反——通常是被冲正或「次日自愈」类差异。",
            "持续": "差异方向未变——可能是同一笔错账跨了多个交易日。",
        }.get(dp, "")

        breaks_rows.append({
            "trade_date": r["trade_date"],
            "account_id": r["account_id"],
            "symbol": sym,
            "code": r["symbol"],
            "diff": diff,
            "diff_type": r["diff_type"],
            "diff_pattern": dp,
            "pre_qty": pre,
            "qty": qty,
            "net_trade": net,
            "non_trade_qty": nq,
            "summary": f"{r['diff_type']} {abs(diff)}股 | {dp}",
            "detail": (
                f"理论持仓 = {pre} + ({net}) + ({nq})"
                + (" == " if pre + net + nq == qty + r.get('diff', 0) else " ... ")
                + f"系统记录 = {qty}\n"
                + f"差异 = {diff}股 → {r['diff_type']}\n"
                + f"说明：{guide}"
            ),
        })

    return {
        "latest_date": latest_date,
        "total_mv": round(total_mv, 2),
        "total_mv_text": _fmt(total_mv, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_text": ("+" if total_pnl >= 0 else "") + _fmt(total_pnl, 2),
        "total_pos_count": total_pos_count,
        "twr_parts": twr_parts,
        "twr_series": twr_series,
        "breaks": breaks_rows,
    }


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mini Recon · 交易对账看板</title>
<script src="https://cdn.plot.ly/plotly-3.0.0.min.js"></script>
<style>
:root {
  --surface-page: #f9f9f7;
  --card-bg: #ffffff;
  --ink-primary: #0b0b0b;
  --ink-secondary: #52514e;
  --ink-muted: #898781;
  --border: rgba(11,11,11,0.10);
  --gridline: #e1e0d9;
}

[data-theme="dark"] {
  --surface-page: #0d0d0d;
  --card-bg: #1e1e1d;
  --ink-primary: #f0efec;
  --ink-secondary: #c3c2b7;
  --ink-muted: #898781;
  --border: rgba(255,255,255,0.10);
  --gridline: #2c2c2a;
}

*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
  background:var(--surface-page);
  color:var(--ink-primary);
  padding:32px 40px;line-height:1.5;
}
.page{max-width:1200px;margin:0 auto}

.header{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:28px}
.header h1{font-size:22px;font-weight:600}
.header .date{font-size:13px;color:var(--ink-muted)}

.stat-row{display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:14px;margin-bottom:24px}
.stat-tile{background:var(--card-bg);border:1px solid var(--border);border-radius:8px;padding:20px 24px}
.stat-tile.hero .label{font-size:12px;font-weight:500;color:var(--ink-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px}
.stat-tile.hero .value{font-size:40px;font-weight:600}
.stat-tile .label{font-size:12px;font-weight:500;color:var(--ink-muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px}
.stat-tile .value{font-size:28px;font-weight:600;font-variant-numeric:tabular-nums}
.stat-tile .sub{font-size:12px;color:var(--ink-secondary);margin-top:2px}

.chart-card{background:var(--card-bg);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:24px}
.chart-card h2{font-size:15px;font-weight:600;margin-bottom:2px}
.chart-card .subtitle{font-size:12px;color:var(--ink-muted);margin-bottom:12px}

.anomaly-card{background:var(--card-bg);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:24px}
.anomaly-card h2{font-size:15px;font-weight:600;margin-bottom:12px}

.anomaly-table{width:100%;border-collapse:collapse;font-size:13px}
.anomaly-table th{text-align:left;padding:8px 12px;color:var(--ink-muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.3px;border-bottom:1px solid var(--gridline)}
.anomaly-table td{padding:8px 12px;border-bottom:1px solid var(--gridline);cursor:pointer;transition:background .15s}
.anomaly-table tr:hover td{background:rgba(57,135,229,.08)}
.anomaly-table tr.selected td{background:rgba(57,135,229,.14)}

.c1{color:#e66767;font-weight:600}
.c2{color:#3987e5;font-weight:600}

.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.badge-new{background:rgba(236,131,90,.15);color:#ec835a}
.badge-reverse{background:rgba(250,178,25,.15);color:#fab219}
.badge-continue{background:rgba(57,135,229,.12);color:#3987e5}

.detail-card{background:var(--card-bg);border:1px solid var(--border);border-radius:8px;padding:20px 24px;margin-top:16px;display:none}
.detail-card.active{display:block}
.detail-card h3{font-size:14px;font-weight:600;margin-bottom:6px}
.detail-card .formula{font-family:'Consolas','Courier New',monospace;font-size:13px;background:var(--surface-page);padding:12px 16px;border-radius:6px;margin:8px 0;color:var(--ink-primary);line-height:1.8;white-space:pre-wrap}
.detail-card .guide{font-size:13px;color:var(--ink-secondary)}

@media(max-width:900px){.stat-row{grid-template-columns:1fr 1fr}.stat-tile.hero{grid-column:span 2}body{padding:16px}}
</style>
</head>
<body>
<div class="page">

<div class="header">
  <h1>交易对账看板</h1>
  <span class="date">截至 {{latest_date}}</span>
</div>

<div class="stat-row">
  <div class="stat-tile hero">
    <div class="label">账户累计 TWR</div>
    <div class="value">{{twr_line}}</div>
    <div class="sub">{{twr_sub}}</div>
  </div>
  <div class="stat-tile">
    <div class="label">总市值</div>
    <div class="value">{{total_mv_text}}</div>
    <div class="sub">{{latest_date}}</div>
  </div>
  <div class="stat-tile">
    <div class="label">当日盈亏</div>
    <div class="value" style="{{pnl_color}}">{{total_pnl_text}}</div>
    <div class="sub">{{latest_date}} 三个账户合计</div>
  </div>
  <div class="stat-tile">
    <div class="label">持仓标的数</div>
    <div class="value">{{total_pos_count}}</div>
    <div class="sub">持有 × 账户数</div>
  </div>
</div>

<div class="chart-card">
  <h2>累计收益率 (TWR)</h2>
  <div class="subtitle">时间加权收益率 · 链式累计</div>
  <div id="chart-twr" style="height:360px"></div>
</div>

<div class="anomaly-card">
  <h2>对账差异清单 · {{break_count}} 条</h2>
  <table class="anomaly-table" id="anomaly-table">
    <thead>
      <tr>
        <th>交易日</th><th>账户</th><th>标的</th><th>差异(股)</th>
        <th>类型</th><th>模式</th><th>上日→本日</th><th>净成交</th><th>非交易</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>
</div>

<div class="detail-card" id="detail-card">
  <h3 id="detail-title"></h3>
  <div class="formula" id="detail-formula"></div>
</div>

</div>

<script>
var DATA = {{data_json}};

var fmtNum = new Intl.NumberFormat("zh-CN",{style:"currency",currency:"CNY",minimumFractionDigits:0,maximumFractionDigits:0}).format;

// 折线图
var traces = [];
var cols = ["#3987e5","#d95926","#199e70"];
var ci = 0;
for(var k in DATA.twr_series){
  var s = DATA.twr_series[k];
  traces.push({
    x:s.dates, y:s.values,
    name:k,
    type:"scatter", mode:"lines+markers",
    line:{width:2, color:cols[ci]},
    marker:{size:5, color:cols[ci], line:{width:2, color:"#1e1e1d"}},
    hovertemplate:"%{x}<br>"+k+": %{y:.2f}%<extra></extra>",
  });
  ci++;
}

Plotly.newPlot("chart-twr", traces, {
  margin:{l:52, r:20, t:12, b:40},
  paper_bgcolor:"transparent",
  plot_bgcolor:"transparent",
  xaxis:{
    gridcolor:"rgba(255,255,255,.06)",
    zeroline:false,
    tickfont:{size:11, color:"#898781"},
    showline:true, linewidth:1, linecolor:"#2c2c2a",
  },
  yaxis:{
    gridcolor:"rgba(255,255,255,.06)",
    zeroline:true, zerolinecolor:"#383835", zerolinewidth:1,
    tickfont:{size:11, color:"#898781"},
    ticksuffix:"%",
    showline:true, linewidth:1, linecolor:"#2c2c2a",
  },
  legend:{orientation:"h", y:1.1, font:{size:12, color:"#c3c2b7"}},
  hovermode:"x unified",
}, {responsive:true, displayModeBar:false});

// 异常表
var tbody = document.querySelector("#anomaly-table tbody");
DATA.breaks.forEach(function(row, i){
  var tr = document.createElement("tr");
  tr.dataset.idx = i;
  tr.dataset.title = row.trade_date + " " + row.account_id + " " + row.symbol;
  tr.dataset.detail = row.detail;
  var dc = row.diff > 0 ? "c1" : "c2";
  var bc = row.diff_pattern === "新发" ? "badge badge-new"
        : row.diff_pattern === "反转" ? "badge badge-reverse"
        : "badge badge-continue";
  tr.innerHTML =
    "<td>" + row.trade_date + "</td>"
    + "<td>" + row.account_id + "</td>"
    + "<td>" + row.symbol + "</td>"
    + "<td class=\"" + dc + "\">" + (row.diff>0?"+":"") + row.diff + "</td>"
    + "<td>" + row.diff_type + "</td>"
    + "<td><span class=\"" + bc + "\">" + row.diff_pattern + "</span></td>"
    + "<td>" + row.pre_qty + " → " + row.qty + "</td>"
    + "<td>" + row.net_trade + "</td>"
    + "<td>" + row.non_trade_qty + "</td>";

  tr.addEventListener("click", function(){
    document.querySelectorAll("#anomaly-table tr").forEach(function(r){r.classList.remove("selected")});
    this.classList.add("selected");
    var card = document.getElementById("detail-card");
    card.classList.add("active");
    document.getElementById("detail-title").textContent = this.dataset.title;
    document.getElementById("detail-formula").textContent = this.dataset.detail;
    card.scrollIntoView({behavior:"smooth", block:"nearest"});
  });
  tbody.appendChild(tr);
});
// 默认选中最后一条（最新的）
var rows = tbody.querySelectorAll("tr");
if(rows.length) rows[0].click();
</script>
</body>
</html>"""


def render(data: dict) -> str:
    """把数据塞进 HTML 模板。"""
    twr_line = " / ".join(f"{p['twr']:+.2f}%" for p in data["twr_parts"])
    twr_sub = " · ".join(p["account_id"] for p in data["twr_parts"])

    pnl_color = "color:#0ca30c" if data["total_pnl"] >= 0 else "color:#e66767"

    out = HTML
    out = out.replace("{{latest_date}}", data["latest_date"])
    out = out.replace("{{twr_line}}", twr_line)
    out = out.replace("{{twr_sub}}", twr_sub)
    out = out.replace("{{total_mv_text}}", data["total_mv_text"])
    out = out.replace("{{total_pnl_text}}", data["total_pnl_text"])
    out = out.replace("{{pnl_color}}", pnl_color)
    out = out.replace("{{total_pos_count}}", str(data["total_pos_count"]))
    out = out.replace("{{break_count}}", str(len(data["breaks"])))
    out = out.replace("{{data_json}}", json.dumps({
        "twr_series": data["twr_series"],
        "breaks": data["breaks"],
    }, ensure_ascii=False))
    return out


def main() -> int:
    print("提取 MySQL 数据 ...")
    data = extract()
    print(f"  日期: {data['latest_date']}")
    print(f"  市值: {data['total_mv_text']}")
    print(f"  盈亏: {data['total_pnl_text']}")
    print(f"  对账差异: {len(data['breaks'])} 条")

    html = render(data)

    out = ROOT / "figures" / "recon_dashboard.html"
    out.write_text(html, encoding="utf-8")
    print(f"\n→ 看板已生成: {out}")
    print(f"  用浏览器打开即可（双击 {out.name} 或用 Ctrl+O）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())