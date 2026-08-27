# Stock Analysis V3

一个适合个人研究、对初学者友好的本地股票分析应用。界面使用 Streamlit，行情与公司资料通过免费的 `yfinance` 读取 Yahoo Finance 公开数据。无需 API Key、付费数据源或券商账户。

## 功能

### 技术分析（V1/V2 保留）

- 股票代码查询、当前价格、日涨跌和成交量
- 交互式 K 线与成交量图，可选 3 个月、6 个月或 1 年
- MA20、MA50、MA200 与均线排列
- RSI(14)、MACD(12, 26, 9) 和动能解释
- 近期价格结构、52 周高低点、支撑与阻力估算
- 成交量异常与价格走势确认
- 可解释的 0–100 技术评分

### 基本面分析（V3）

- 公司名称、板块、行业、国家/地区、市值和业务简介
- Trailing P/E、Forward P/E、P/S、P/B、PEG、EV 和 EV/EBITDA
- 营收、盈利、EPS 及季度增长（数据可用时）
- 毛利率、营业利润率、净利率、ROE 和 ROA
- 现金、债务、负债权益比、流动比率、速动比率和现金流
- 股息率、年度股息、派息率和除息日；非派息公司会明确标示
- 52 周价格背景；不虚构历史估值区间
- 独立、透明、缺失数据友好的 0–100 基本面评分
- 技术评分与基本面评分并列展示，但不生成综合投资分数

## 项目结构

```text
stock-analysis/
|-- .gitignore
|-- app.py
|-- requirements.txt
|-- stock_analysis/
|   |-- __init__.py
|   |-- analysis.py
|   `-- fundamentals.py
`-- README.md
```

- `app.py`：Streamlit 页面、中文界面与图表
- `stock_analysis/analysis.py`：V2 技术指标、趋势、支撑阻力和技术评分
- `stock_analysis/fundamentals.py`：公司资料、基本面指标和基本面评分

## Windows 本地运行

建议使用 Python 3.10 或更新版本。先在 PowerShell 检查：

```powershell
py --version
```

如果命令失败，请从 [python.org](https://www.python.org/downloads/windows/) 安装 Python，安装时启用将 Python 添加到 `PATH` 的选项，然后重新打开 PowerShell。

首次运行：

```powershell
cd "C:\Users\Lenovo\Codex Projects\stock-analysis"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py -m streamlit run app.py
```

如果已经为 V1/V2 创建虚拟环境：

```powershell
cd "C:\Users\Lenovo\Codex Projects\stock-analysis"
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py -m streamlit run app.py
```

如果 PowerShell 阻止虚拟环境激活，可在当前窗口临时允许脚本：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Streamlit 通常会自动打开 `http://localhost:8501`。按 `Ctrl+C` 停止应用。

## 基本面评分

基本面评分和技术评分完全分开。六个基本面类别的标准权重合计 100：

| 类别 | 权重 | 参考指标 |
|---|---:|---|
| 估值 | 20 | P/E、P/S、P/B、PEG、EV/EBITDA |
| 增长 | 20 | 营收、盈利、EPS 与季度增长 |
| 盈利能力 | 20 | 毛利率、营业利润率、净利率、ROE、ROA |
| 财务健康 | 20 | 负债权益比、流动/速动比率、现金与债务 |
| 现金流 | 15 | 自由现金流、经营现金流是否为正 |
| 股东回报/股息 | 5 | 股息率与派息率（仅适用于派息公司） |

每个类别只对 Yahoo Finance 实际提供的指标进行等权计算。缺失指标不记零分；完全缺失的类别或不派息公司的股息类别会从适用权重中排除。最终得分按适用权重归一化为 0–100，并显示指标覆盖率。

- 80–100：强
- 60–79：良好
- 40–59：一般
- 20–39：偏弱
- 0–19：很弱

评分规则是简化的研究框架，不适用于所有行业。例如银行、保险、房地产和早期成长公司的合理估值与资产负债结构可能明显不同。

## 技术评分

V2 的独立技术评分保持不变：趋势 20、移动平均线 20、RSI 15、MACD 20、价格结构 15、成交量 10。界面会逐项显示所得分数和解释。

## 数据来源与限制

- 行情与公司数据来自 Yahoo Finance，并通过非官方的免费 `yfinance` 库访问。
- 数据可能延迟、暂时缺失、口径不同或随 Yahoo 页面变化而不可用。
- 不同市场和行业可能缺少部分字段；应用会显示 `N/A`，并降低覆盖率，而不是虚构数据。
- 金额通常按 Yahoo Finance 返回的交易币种或报表币种显示；当前版本在界面中使用 `$` 作为简化前缀，尚未进行完整币种本地化。
- Yahoo Finance 通常不提供完整的历史估值序列，因此 V3 不推断历史估值高低。
- 免费数据不适合作为交易执行或实时风控依据。

基本面查询缓存 1 小时，价格查询缓存 15 分钟，以减少重复请求。

## 未来版本（尚未实现）

- 新闻与催化剂分析
- AI 生成的综合分析或投资结论
- 持仓与成本基础分析
- 自选股与研究笔记
- 更完整的 A 股和港股字段、币种及市场支持

## 隐私与免责声明

项目不使用或存储 API Key、券商凭证或其他秘密信息。技术面与基本面分析仅供个人研究和学习，不构成财务建议。
