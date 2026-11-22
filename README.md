# hs300-quant-analysis (归档)

> 本项目已拆分为两个独立仓库，推荐使用新仓库：

| 新仓库 | 说明 |
|--------|------|
| [hs300-quant-backtest](https://github.com/likeee007-kk/hs300-quant-backtest) | 沪深300量化回测：IC 分析、样本外验证、MySQL 管线 |
| [trade-recon-engine](https://github.com/likeee007-kk/trade-recon-engine) | 交易对账系统：T+1 对账、TWR/Dietz 收益率、公司行为、Power BI 看板 |

---

## 为什么拆分？

原仓库把两个独立项目（量化回测 & 对账系统）塞在一个目录里，不利于：
- 各自 README 的清晰度
- 独立的 CI/CD 和依赖管理
- GitHub 话题、搜索和星标的归属

拆分保留了原仓库的完整 commit 历史（每个文件都来自原仓库）。