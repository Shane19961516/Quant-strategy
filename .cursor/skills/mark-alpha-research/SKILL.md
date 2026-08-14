---
name: mark-alpha-research
description: Buy-side single-stock and portfolio research framework for public equities, ETFs, industries, fund managers, and portfolios, combining market-implied expectations, variant perception, earnings bridge, ROE/ROIC decomposition, second-derivative alpha, valuation, falsification, catalysts, and probability-weighted position sizing. Use when the user asks to research, compare, screen, value, size, or generate a PDF report for stocks or portfolios using "my framework", "MARK", "first principles", "second derivative", "alpha", "portfolio fit", "PM", "buy-side", "FINN", "Mark Schmehl", "守望非线性", "投资宪章", or similar investment-system language.
---

# MARK Alpha Research

Use this skill to produce buy-side investment research, not company description and not short-term price prophecy. The output must help a portfolio manager decide whether to own, add, wait, reduce, or reject a security.

For full single-stock research, default to creating both:

- a Markdown source report
- a polished PDF using `scripts/build_mark_research_pdf.py`

Use the PDF skill workflow when creating PDFs: render pages to PNG, visually inspect layout, and iterate before delivery.

## Core Research Chain

Always answer this chain:

```text
Market consensus
-> my variant view
-> size of expectation gap
-> EPS / ROE / ROIC / FCF / NAV impact
-> estimate revision path
-> valuation impact
-> price impact
-> leading indicators and falsification
-> position size
```

The first-principles causal chain remains:

```text
irreversible change
-> scarcity or bottleneck
-> company moat and ROIC / ROE
-> second-derivative improvement in earnings or cash flow
-> market mispricing
-> re-rating catalyst
-> asymmetric return
-> portfolio role and position size
```

Never stop at "the company is good." Always show:

```text
Market View -> My View -> Difference -> Price Impact
```

Separate facts, consensus, inference, and variant perception. When prices, financials, holdings, company guidance, filings, regulation, or analyst data may be current, browse and prefer primary sources.

## Full Report Template

Use this structure for a complete stock report. Do not label the report as "using the framework"; make it read like a professional investment research report.

### 1. One-Sentence View

State the action view: own, add, wait, starter position, trade, watchlist, reduce, or reject. Include rough position range and the single most important reason.

### 2. Market Expectations And Variant View

Include:

- current price, market cap, major valuation metrics, source date
- current price-implied expectations for EPS, ROE/ROIC, growth, P/B, P/E, FCF, NAV, or margin
- consensus narrative
- my variant view
- expectation-gap table with 3-4 columns, not overly wide tables

Preferred table:

| Variable | Market Implied / Consensus | My View And Gap | Price Impact |
|---|---|---|---|

If there is no clear variant perception, do not call it a high-alpha idea.

### 3. Structural Change And Transmission

Explain:

- what irreversible change is happening
- how the change reaches company revenue, margin, EPS, ROE/ROIC, FCF, NAV, or multiples
- where scarcity or the bottleneck sits
- who controls the bottleneck

Keep macro concise. The report should spend more space on earnings and valuation than generic industry background.

### 4. Competitive Advantage

Answer why this company, not the closest substitutes. Compare 3-5 peers or substitute exposures where possible.

Include:

- stock-specific alpha vs. sector beta
- moat direction: widening, stable, or narrowing
- management and capital allocation
- whether a peer offers cleaner exposure

### 5. Earnings Bridge And Return Decomposition

Every full report must translate the thesis into numbers.

For most companies:

```text
units / volume / AUM / users / orders
-> price / take rate / yield
-> revenue
-> gross profit
-> operating leverage
-> EBIT / net income
-> EPS / FCF
-> estimate revision
```

For banks, brokers, insurers, asset managers, and other financials:

```text
ADT / margin financing / IPO fundraising / AUM / client assets / international activity
-> brokerage, IB, wealth, asset management, FICC, derivatives, investment income
-> revenue mix
-> cost-to-income ratio
-> net income
-> equity capital and leverage
-> ROE
-> EPS and P/B
```

For financials, never write only `net assets x ROE = profit`. Explain why ROE is 11% rather than 8%: revenue mix, fee quality, cost-income ratio, leverage, RWA/regulatory capital, investment-income quality, capital utilization, and funding cost where relevant.

### 6. Second Derivative And Estimate Revision

Focus on acceleration or deceleration:

```text
operating KPI
-> revenue
-> gross profit / fee income
-> margin / cost ratio
-> EPS / ROE / FCF
-> consensus revision
-> valuation
```

End with one classification: `Accelerating`, `Stable`, `Decelerating`, or `Not yet visible`.

### 7. Valuation And Expected Value

Use business-model-appropriate valuation:

- growth: EV/Sales, EV/EBITDA, DCF, PEG
- mature cash flow: P/E, FCF yield, EV/EBITDA, dividend yield
- banks: P/TBV, ROTCE, ROE, P/E, credit cost
- brokers/capital-market financials: P/B, ROE, P/E, earnings mix, leverage, cost-income, investment-income quality
- asset managers/alternatives: SOTP, NAV, FRE, distributable earnings, AUM, fundraising, deployment, exits

Always include bear/base/bull probabilities, upside/downside, expected return, and the valuation variable that creates most value: EPS revision, ROE improvement, margin expansion, multiple re-rating, dividend/capital return, NAV discount closure, or FCF growth.

Preferred scenario table:

| Scenario / Probability | Key Assumptions | Value Range | Return vs. Current Price |
|---|---|---:|---:|

Use this expected-return formula:

```text
expected return =
(bull probability x bull return)
+ (base probability x base return)
+ (bear probability x bear return)
```

### 8. Catalysts And Timeline

Do not merely list events. Convert catalysts into a PM timeline:

```text
Catalyst -> KPI -> EPS/ROE/FCF/NAV -> Estimate Revision -> Valuation -> Price
```

Use T+1, T+3, T+6, and T+12 months when relevant.

Preferred table:

| Time / Catalyst | Key KPI | EPS/ROE Meaning | Valuation / Price Meaning |
|---|---|---|---|

### 9. Leading Indicators And Falsification

Separate:

- data proving the thesis is working
- `Thesis broken if...`
- risks that are not yet falsification
- noise to endure if facts are unchanged

Falsification is not a generic risk list. It is observable data showing the thesis is already wrong. Include thresholds and dates where possible.

### 10. Portfolio Fit And Position Size

Classify the position:

- Core
- Structural Alpha
- High Conviction Alpha
- Trade
- Watchlist
- Reject

Then size the position using expected return, thesis confidence, catalyst visibility, liquidity, portfolio complementarity, drawdown risk, and correlation. State what would move the size up or down.

### 11. Final Rating

Use plain text ratings that render cleanly in PDF:

- Core allocation
- Alpha allocation
- Structural Alpha starter
- Watchlist
- Trade
- Reject

Avoid decorative star glyphs in PDF output. If a rating needs strength, write "四星 主动配置" or similar plain text.

## Language And Style Rules

- For Chinese reports, use clear buy-side Chinese. Avoid stale sell-side phrasing and generic background.
- Prefer "盈利" over "盈喜" unless directly quoting the title of an exchange announcement.
- Use exact dates for sources and current data.
- Use 3-4 column tables when possible; avoid cramped 5-6 column tables in the PDF.
- Keep section titles professional; do not expose internal framework names in the report title.
- Put sources at the end with links and source dates.

## PDF Output Style

For full reports, generate a PDF unless the user says text-only. Use `scripts/build_mark_research_pdf.py`.

Default visual style:

- A4 portrait
- 京华老宋体 / 京華老宋体 from the local Windows font folder when available
- fallback to SimSun or SimHei if the font is unavailable
- warm paper background `#F4EDDE`
- black ink `#1B1B18`
- muted gray `#5D5A52`
- red accent `#D9362B`
- thin horizontal header/footer rules
- small red arrow motif near the top-left
- no six-column vertical grid
- no "SIGNAL" text or slogan text
- no decorative orbs, gradients, or nested cards
- dense but readable tables with hairline borders

PDF workflow:

1. Write the report as Markdown.
2. Run:

```powershell
python scripts/build_mark_research_pdf.py --input report.md --output output/pdf/report.pdf --header "TICKER / COMPANY / INVESTMENT RESEARCH" --date YYYY-MM-DD
```

3. Render the PDF with Poppler `pdftoppm`.
4. Inspect representative PNG pages: first page, table-heavy pages, valuation/catalyst page, and final sources page.
5. Fix cramped tables, clipped text, missing glyphs, or awkward page transitions.
6. Deliver the final PDF only after visual QA passes.

## Data Rules

Prefer sources in this order:

1. SEC filings, annual reports, 10-K, 10-Q, 20-F, prospectus, fund facts
2. Company investor relations
3. Exchange, regulator, or official fund provider
4. High-quality industry data
5. Mainstream financial media
6. Analyst or third-party data

Always name source dates when using financials, holdings, fund facts, manager interviews, prices, or consensus estimates.

## Final Discipline

High-quality alpha:

```text
irreversible change
x scarcity
x high ROIC / ROE
x second-derivative improvement
x market cognition lag
x reasonable valuation
```

High-conviction alpha:

```text
first principles
x variant perception
x earnings second derivative
x re-rating catalyst
x asymmetric payoff
x falsification discipline
```

Final position:

```text
alpha quality
x payoff
x evidence strength
x capital endurance
x portfolio complementarity
```

If facts are unchanged, endure volatility. If causality changes, admit error. If payoff disappears, release capital.
