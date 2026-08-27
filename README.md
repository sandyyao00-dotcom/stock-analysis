# Stock Analysis V4.1

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

### 规则式解释层（V4）

- 技术评分逐项显示原有得分、满分、实际指标值与动态解释
- 基本面评分逐类显示原有得分、权重、覆盖率、可用指标与动态解释
- 按现有分数贡献比例列出技术面和基本面的 Top 3 优势与风险
- 说明技术面和基本面当前是共振、分歧还是混合状态
- 页面顶部提供技术面一句话、基本面一句话、主要优势与主要风险
- 不创建新的综合投资评分，也不生成买卖建议

所有 V4 解释均由本地确定性规则生成，以现有评分组件和实际可用指标为输入，不调用外部 AI 模型，也不创造新信号。

### 多市场支持（V4.1）

- 美股：直接输入 `AAPL`、`NVDA`、`TSLA` 等普通 ticker
- A 股：输入 6 位代码，自动识别沪市/深市并添加 `.SS` 或 `.SZ`
- 港股：输入 1–5 位代码，常见代码自动补足 4 位并添加 `.HK`
- 页面同时显示用户输入代码与实际 Yahoo Finance 数据代码
- 优先使用 Yahoo Finance 返回的币种；缺失时美股回退 USD、A 股回退 CNY、港股回退 HKD
- 所有价格、均线、支撑阻力、市值、企业价值、现金、债务和现金流均使用市场感知的币种格式

## 项目结构

```text
stock-analysis/
|-- .gitignore
|-- app.py
|-- requirements.txt
|-- stock_analysis/
|   |-- __init__.py
|   |-- analysis.py
|   |-- explanations.py
|   |-- markets.py
|   `-- fundamentals.py
`-- README.md
```

- `app.py`：Streamlit 页面、中文界面与图表
- `stock_analysis/analysis.py`：V2 技术指标、趋势、支撑阻力和技术评分
- `stock_analysis/fundamentals.py`：公司资料、基本面指标和基本面评分
- `stock_analysis/explanations.py`：技术面/基本面规则式解释、优势风险排序与共振判断
- `stock_analysis/markets.py`：市场选择、代码校验、Yahoo symbol 规范化与币种格式

## 支持市场与代码规范化

| 市场 | 用户输入 | Yahoo Finance 数据代码 | 默认币种 |
|---|---|---|---|
| 美股 | `AAPL` | `AAPL` | USD（`$`） |
| A 股（沪市） | `600519` | `600519.SS` | CNY（`¥`） |
| A 股（深市） | `300750` | `300750.SZ` | CNY（`¥`） |
| 港股 | `700` 或 `0700` | `0700.HK` | HKD（`HK$`） |
| 港股 | `9988` | `9988.HK` | HKD（`HK$`） |

A 股必须是 6 位数字。常见 `5/6/9` 开头代码按沪市处理，`0/1/2/3` 开头代码按深市处理。当前不自动支持北交所代码。港股接受 1–5 位数字；1–4 位代码补足为 4 位，合法 5 位代码保持原长度。

## V4 解释方法

- 技术解释直接读取 V2 技术评分中的六个组件得分，并展示计算时使用的价格、均线、RSI、MACD、价格结构和成交量数据。
- 基本面解释直接读取 V3 各类别的原有得分和 coverage，只列出 Yahoo Finance 实际返回的指标。
- 优势按“已得分/类别满分”从高到低排列；风险按同一比例从低到高排列。缺失类别不会被当作风险。
- 共振/分歧说明只比较技术评分与基本面评分所在方向，不产生第三个分数。
- 解释不会改变任何技术或基本面评分阈值、权重、数据源或缺失值处理规则。

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
- A 股和港股在 Yahoo Finance 上的基本面字段通常少于美股；低 coverage 时应用会明确提示，缺失值仍不会自动计为负面。
- V4.1 优先使用 Yahoo 返回的 `currency` 字段并按 USD/CNY/HKD 显示；若提供方币种缺失，则使用所选市场默认币种。报表币种与交易币种偶尔仍可能不一致。
- Yahoo Finance 通常不提供完整的历史估值序列，因此 V3 不推断历史估值高低。
- 免费数据不适合作为交易执行或实时风控依据。

基本面查询缓存 1 小时，价格查询缓存 15 分钟，以减少重复请求。解释层使用已经获取的数据，不发起额外网络请求。

## 未来版本（尚未实现）

- 新闻与催化剂分析
- AI 生成的综合分析或投资结论
- 持仓与成本基础分析
- 自选股与研究笔记
- 更完整的 A 股和港股字段、币种及市场支持

## 隐私与免责声明

项目不使用或存储 API Key、券商凭证或其他秘密信息。技术面与基本面分析仅供个人研究和学习，不构成财务建议。

规则式解释只能描述当前模型为何得到某个分数，不能预测未来价格，也不能替代行业研究、财报核验或专业投资判断。
