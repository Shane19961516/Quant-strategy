---
name: nonlinear-alpha-research
description: Buy-side opportunity-discovery, single-stock, fund-manager, and portfolio-allocation framework for public equities and ETFs, combining irreversible-change analysis, market-implied expectations, variant perception, customer-behavior and supply-constraint signals, earnings/FCF second derivatives, ROE/ROIC decomposition, valuation, falsification, evidence-expectation gaps, capital replacement, and probability-weighted position sizing. Use when the user asks to discover, screen, research, compare, value, size, replace, or generate a PDF report using "my framework", "MARK", "first principles", "second derivative", "nonlinear alpha", "portfolio fit", "PM", "buy-side", "FINN", "Mark Schmehl", "守望非线性", "投资宪章", or similar investment-system language.
---

# 守望非线性 / Nonlinear Alpha Research

> Current visual standard: Modern Investment Editorial — 京华老宋体 × modern institutional typography, stronger PM hierarchy, restrained red accents, de-gridded tables, deliberate pagination, and clean separation between final rating and sources.


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

## Opportunity Discovery Engine

This skill is not only a deep-research template. It must also help discover companies whose economic state is changing before market expectations fully adjust.

The discovery objective is:

```text
fundamental change
x expectation lag
x economic quality
= nonlinear alpha opportunity
```

Do not search for "the next ten-bagger" directly. Search for observable state transitions:

```text
old identity / old earnings model
-> customer behavior changes
-> supply constraint or bottleneck appears
-> revenue mix shifts
-> margin and incremental ROIC inflect
-> EPS / FCF estimates accelerate
-> market identity changes
-> re-rating
```

The best discovery window is normally not the lowest share price. It is the period in which evidence is becoming sufficient while expectations remain behind reality.

### Opportunity State Machine

Every candidate should have a current state:

| State | Definition | Typical Evidence | Default Action |
|---|---|---|---|
| `S0 Normal` | no meaningful structural anomaly | ordinary growth, no new bottleneck | ignore |
| `S1 Discovery` | slow variables begin to change | new customer, qualification, design win, unusual orders, lead-time change | create Alpha Card / watch |
| `S2 Confirmation` | financials begin to confirm | revenue acceleration, margin inflection, guidance or estimate revision | deep research / starter candidate |
| `S3 Acceleration` | causal chain accelerates broadly | orders + revenue + margin + EPS/FCF + ROIC improving | portfolio candidate / size up if payoff remains attractive |
| `S4 Recognition` | market has largely recognized the new state | broad narrative adoption, large estimate revisions, multiple expansion, crowded positioning | manage winner; stop aggressive adding |

The preferred alpha window is usually `S1 -> S2 -> early S3`, not late `S4`.

### Slow-Variable Radar

Before financial statements fully change, search company filings, earnings calls, customer commentary, competitor commentary, and industry data for changes in customer behavior and supply conditions.

High-value customer-behavior signals include:

- qualification / approved-vendor status
- design win
- multi-year or long-term agreement
- capacity reservation
- take-or-pay
- prepayment
- minimum purchase commitment
- strategic-supplier status
- sold-out / allocation language
- lead-time extension
- unusually strong backlog or book-to-bill
- customer willingness to sacrifice flexibility to secure future supply

Treat these as evidence, not automatic bullish signals. Their importance comes from what they imply about future scarcity, revenue visibility, pricing power, and customer switching costs.

### Supply Constraint Radar

Demand growth matters most when supply cannot respond quickly.

Score evidence such as:

- demand acceleration
- declining inventory
- utilization approaching practical limits
- lead times extending
- capacity expansion requiring long lead times
- certification or qualification bottlenecks
- competitor exit or capex discipline
- customer prepayments or long-duration supply commitments

The research question is:

```text
Does one additional unit of demand require much more than one unit of time/capital to create supply?
```

If supply responds faster than demand, downgrade the structural-alpha thesis even if the industry narrative is attractive.

### Triple Acceleration

Do not rely on EPS acceleration alone. Look for simultaneous improvement in three layers:

```text
Revenue acceleration
+ margin / unit-economics inflection
+ EPS / FCF acceleration
```

The strongest pattern is:

```text
volume / units / customers up
+ price / mix / take rate up
+ margin up
=> operating leverage and estimate revisions
```

If revenue rises while gross margin, incremental ROIC, or FCF quality deteriorates, treat it as lower-quality growth.

### Mix Inflection And State Transition

Pay special attention when a previously small high-growth business becomes large enough to change the economics of the whole company.

Typical pattern:

```text
new business = 2%-5% of revenue
-> 10%-15%
-> 20%-30%+
```

Then test whether the mix shift changes:

- consolidated growth
- gross margin
- operating leverage
- capital intensity
- incremental ROIC
- earnings duration
- valuation identity

A powerful opportunity often appears when the market still values the company according to the legacy business while the new business is becoming economically dominant.

## Evidence-Expectation Gap

The central timing concept is not evidence alone and not valuation alone. It is the gap between how fast reality is changing and how fast expectations are adapting.

Define conceptually:

```text
Evidence-Expectation Gap
= strength and persistence of operating evidence
- degree to which consensus, valuation, positioning, and narrative already reflect it
```

Interpretation:

| Evidence | Expectation | Interpretation |
|---|---|---|
| low | low | early discovery / speculation |
| high | low | best alpha zone |
| high | high | recognized winner / hold or selective add |
| low | high | dangerous expectations / avoid or reduce |

### Expectation Lag Index (ELI)

Use `ELI` as a disciplined qualitative or semi-quantitative measure of:

```text
real-world change velocity
- consensus-change velocity
```

Inputs may include:

- order/backlog growth vs. consensus revenue growth
- management guidance vs. analyst estimates
- customer commitments vs. modeled demand
- realized margin vs. forecast margin
- estimate-revision breadth and magnitude
- multiple expansion
- share-price performance
- narrative crowding
- institutional positioning where available

High `ELI` means reality is moving faster than expectations. Low or negative `ELI` means the market may already be pricing more improvement than the evidence supports.

## Nonlinear Alpha Score

For discovery and ranking, score candidates out of 100. Do not treat the score as false precision; it is a forcing function for consistent comparison.

| Module | Weight | Core Question |
|---|---:|---|
| Irreversible Change | 10 | Is the world structurally moving this way? |
| Customer Behavior | 15 | Are customers committing real money or changing procurement behavior? |
| Supply Constraint | 15 | Can supply respond quickly enough? |
| Revenue Acceleration | 8 | Is topline growth accelerating? |
| Margin Inflection | 12 | Is pricing/mix/utilization improving unit economics? |
| EPS / FCF Acceleration | 10 | Is operating leverage reaching earnings and cash flow? |
| Incremental ROIC / ROE | 10 | Does new growth create economic value? |
| Variant Perception | 10 | What does the market still misunderstand? |
| Valuation / Asymmetry | 7 | Is the payoff attractive after current expectations? |
| Capital Flow | 3 | Is capital beginning to confirm the thesis without being fully crowded? |
| **Total** | **100** | |

Interpretation:

- `<55`: Noise / reject from active research
- `55-69`: Discovery
- `70-79`: Confirmation; deep-research priority
- `80-89`: Acceleration; portfolio candidate
- `90+`: Exceptional evidence set; still subject to hard gates and valuation

### Hard Gates

A high score alone cannot justify a core alpha position. Three hard gates apply:

1. **Economic-value gate** — incremental ROIC/ROE and FCF quality must support the growth. If capex merely creates excess supply and lower returns, downgrade or reject.
2. **Variant-perception gate** — state consensus in one or two sentences, then state why it is wrong. If this cannot be done clearly, the idea is not high alpha.
3. **Asymmetry gate** — expected upside must materially exceed credible downside. As a rough discipline, base-case upside/downside should normally exceed ~2x, with stronger opportunities offering materially better bull/bear asymmetry.

## Capital Replacement Engine

A new idea is not compared only with zero. It competes with cash, existing positions, and additional allocation to current winners.

For every actionable candidate, compare at least:

```text
Candidate
vs. Cash
vs. lowest-marginal-IRR existing position
vs. adding to the best current position
```

Ask:

- What is the candidate's expected return?
- What is the confidence level?
- What is the credible downside?
- What portfolio factor exposure does it add?
- Does it diversify or duplicate existing causal chains?
- What existing position would fund it?
- Why is the new use of capital clearly superior?

### Capital Replacement Score (CRS)

Use `CRS` as a comparative tool, not a mechanically precise formula.

Conceptually:

```text
CRS
= expected return
x thesis confidence
x diversification / portfolio benefit
/ downside and thesis fragility
```

A candidate should replace an existing holding only when the improvement in marginal expected value is meaningful, not merely because the new idea feels more interesting.

Cash is a valid competitor. If no candidate clears the replacement hurdle, keep the cash.

## Alpha Card

For each serious candidate, maintain a compact living record:

| Field | Required Content |
|---|---|
| Ticker / Company | identifier |
| Current State | S0 / S1 / S2 / S3 / S4 |
| Old Identity | how the market currently defines it |
| Emerging Identity | what it may be becoming |
| Irreversible Change | structural driver |
| Customer Change | observable behavior change |
| Supply Constraint | bottleneck / response time |
| Revenue Acceleration | evidence |
| Margin Inflection | evidence |
| EPS / FCF Revision | evidence |
| Incremental ROIC / ROE | evidence |
| Consensus | current market view |
| Variant Perception | differentiated view |
| Re-rating Catalyst | what forces recognition |
| Bear / Base / Bull | payoff |
| Kill Switches | at least three observable falsifiers |
| Nonlinear Alpha Score | /100 |
| ELI | evidence vs. expectation |
| CRS | relative capital attractiveness |
| Next Evidence Date | next earnings, investor day, qualification, capacity event, contract milestone, etc. |

Always update the card when the state changes. The point is to track evidence accumulation, not merely preserve the original thesis.

## Discovery Funnel

When screening broadly, use a funnel rather than deep-researching everything:

```text
Global investable universe
-> quantitative anomaly screen
-> slow-variable / customer-behavior screen
-> Nonlinear Alpha Score
-> deep fundamental research
-> portfolio challenge
-> investment / watchlist / reject
```

A practical funnel may look like:

```text
thousands
-> 200-300 quantitative anomalies
-> ~50 slow-variable candidates
-> 15-25 score >70
-> 5-10 deep research names
-> 2-4 portfolio challengers
-> 1-3 actual positions
```

The exact counts are not targets. The principle is extreme selectivity.

## Monitoring Cadence

Use different cadences for different evidence:

- **Daily:** detect abnormal events such as orders, long-term contracts, qualification, guidance changes, competitor exits, major regulatory or customer developments.
- **Weekly:** update state changes, Alpha Cards, and the Discovery/Confirmation/Acceleration queue.
- **Earnings season:** refresh revenue, margin, EPS, FCF, ROIC/ROE, backlog/book-to-bill, guidance, and consensus revisions.
- **Monthly:** run a Capital Replacement Review and identify the portfolio position with the lowest marginal expected return.

Do not create turnover for its own sake. Monitoring should change a position only when evidence, expectations, or relative opportunity cost changes materially.

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

Also state, when relevant:

- current opportunity state: `S0 / S1 / S2 / S3 / S4`
- Nonlinear Alpha Score /100
- Evidence-Expectation Gap / ELI: High, Medium, Low, or Negative
- whether the company is undergoing a genuine identity/state transition

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

For actionable new ideas, also include a Capital Replacement comparison against cash, the lowest-marginal-IRR existing position, and adding to the strongest current holding. State the likely funding source and whether the candidate clears the replacement hurdle.

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

### Visual Identity: Modern Investment Editorial

The report should feel like an independent buy-side publication, not a sell-side template and not an AI dashboard.

Target visual character:

```text
Calm
Intellectual
Sparse
Conviction-driven
```

Design language:

```text
old-school Chinese financial editorial
x
modern institutional research
```

Default visual style:

- A4 portrait
- 京华老宋体 / 京華老宋体 as the primary Chinese display and body typeface when available
- restrained modern sans-serif for English metadata, section numbers, table headers, KPI labels, dates, tickers, and navigation text
- fallback Chinese serif: SimSun; fallback sans-serif: SimHei or another clean system sans
- warm paper background `#F4EDDE`
- black ink `#1B1B18`
- muted gray `#5D5A52`
- red accent `#D9362B`
- thin horizontal header/footer rules
- small red arrow motif near the top-left
- no six-column vertical grid
- no "SIGNAL" text or slogan text
- no decorative orbs, gradients, nested cards, dashboard tiles, generic stock imagery, or AI-style infographics
- dense but readable tables
- add `Written by 林凯 (Ricardo Lin)` as a restrained watermark on every page
- place the watermark in the bottom-right footer margin at small size and low opacity; keep it horizontal, away from body text, and visually subordinate to the page number and report content
- do not use a large, diagonal, centered, repeated, or high-contrast watermark

### Information Hierarchy

The report must support three reading speeds:

```text
10 seconds -> know the action and position
1 minute   -> understand the thesis, expectation gap, and payoff
10 minutes -> verify the earnings bridge, catalysts, falsification, and sources
```

Do not give every paragraph equal visual weight.

The first page should prioritize:

```text
Company / Ticker
-> Investment Classification
-> Position Size
-> One-Sentence Thesis
-> Core Risk / Reward
-> supporting explanation
```

`Structural Alpha starter`, `High Conviction Alpha`, `Core`, or another classification should never be buried in body copy. Treat classification and initial position size as a primary visual signal.

Example:

```text
STRUCTURAL ALPHA STARTER
2.0%–3.0%
```

The Chinese report title may come before or after this block depending on balance. The requirement is immediate discoverability of action, classification, and size.

### Color Discipline

Red is an editorial navigation color, not a default paint bucket.

Use red mainly for:

- section numbers
- the report title or a limited number of first-level titles
- short editorial dividers
- selected table-header accents
- critical numerical anchors where emphasis is genuinely useful

Avoid simultaneously making every section title, table header, border, label, and callout red.

If a page already contains a strong red table header, prefer black section titles with a red section number. Red should retain signaling power.

### Typography Discipline

Use 京华老宋体 to preserve the report's distinctive Chinese editorial character, especially for:

- report titles
- first-level Chinese headings
- body text
- important conclusion sentences

Use the modern sans-serif layer for:

- `BABA / ALIBABA GROUP / INVESTMENT RESEARCH`
- `STRUCTURAL ALPHA STARTER`
- dates and page navigation
- `FY2028`, `EBITA`, `ROIC`, `CapEx`, `FCF`, `P/E`, `SOTP`
- KPI labels, scenario labels, and small table metadata

Pay attention to Chinese-English-number rhythm:

- avoid awkward spacing around `%`, `/`, `+`, `-`, and financial acronyms
- keep numerals aligned and visually stable
- avoid obvious baseline mismatch between Chinese text and Latin abbreviations
- prefer consistent forms such as `FY2028`, `+34%`, `11.5x`, `2.0%–3.0%`

The aim is to remove the mechanical feeling of programmatically generated mixed-language reports.

### Table Design

Tables should communicate judgment, not merely display a database.

Default rules:

- prefer 3–4 columns
- minimize vertical grid lines
- use hairline horizontal rules
- allow generous internal cell padding
- use background fills sparingly
- emphasize the decision-relevant row or column, not every cell
- in Bear / Base / Bull tables, Base may receive a very light neutral fill
- keep numeric columns aligned
- avoid saturated full-table color blocks unless the table is very small

For scenario analysis, the reader's eye should reach the Base case first, then understand Bear and Bull asymmetry.

### Editorial Components

Use shaded logic boxes only for true causal chains, bridges, or decision rules, for example:

```text
Cloud revenue acceleration
-> cloud EBITA
-> group EBITA / FCF
-> EPS revision
-> multiple re-rating
```

A logic box should not become a generic container for ordinary prose.

For second-derivative sections, key acceleration / deceleration numbers may be pulled out as visual anchors when useful, e.g.:

```text
+38%   +40%   +57%   -56%
```

Do this only when the numbers tell the causal story. Do not turn the report into a KPI dashboard.

For `Leading Indicators And Falsification`, prefer a clear visual separation between:

```text
Evidence thesis is working
vs.
Thesis broken if...
```

A two-column or paired-section treatment is preferred when page width permits.

### Page Rhythm And Pagination

Do not compress the report merely to reduce page count.

A strong page is allowed to contain whitespace. Preserve editorial breathing room when it helps hierarchy.

Prefer natural page breaks:

- do not force a new major section into the bottom of a crowded page
- do not place the final rating immediately above a long source list
- allow the report to become 7 pages instead of 6 if the extra page materially improves rhythm
- table-heavy pages and thesis-heavy pages may have different density

The visual structure should mirror the analytical structure.

### Final Page And Sources

The investment conclusion should feel like an ending.

Prefer:

```text
Portfolio Role / Position Size
-> Final Rating
-> final thesis sentence
```

Then end the research body cleanly.

If sources are long, move them to a separate:

```text
APPENDIX / SOURCES
```

page.

Do not let raw URLs visually overwhelm the final investment conclusion.

### Design Priority

When choosing between adding more visual elements and improving hierarchy, improve hierarchy.

Do not add icons, gradients, ornamental charts, rating stars, decorative cards, or generic stock imagery merely to make the PDF look richer.

The design objective is:

```text
less decoration
+ stronger hierarchy
+ clearer conviction
+ more recognizable authorship
```

A successful report should be identifiable as `MARK / INVESTMENT RESEARCH` before the reader notices any individual graphic element.

### PDF Workflow

1. Write the report as Markdown.
2. Run:

```powershell
python scripts/build_mark_research_pdf.py --input report.md --output output/pdf/report.pdf --header "TICKER / COMPANY / INVESTMENT RESEARCH" --date YYYY-MM-DD
```

3. Render the PDF with Poppler `pdftoppm`.
4. Inspect representative PNG pages: first page, table-heavy pages, valuation/catalyst page, portfolio/final-rating page, and sources page.
5. Fix cramped tables, clipped text, missing glyphs, awkward page transitions, overused red, or weak information hierarchy.
6. Verify that `Written by 林凯 (Ricardo Lin)` appears subtly and legibly on every page without competing with content.
7. Verify that the first page exposes classification and position size immediately.
8. Verify that the final investment conclusion is visually separated from long source lists.
9. Deliver the final PDF only after visual QA passes.

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

Discovery discipline:

```text
Do not chase the most obvious winner.
Find where customer behavior, scarcity, economics, and earnings are changing faster than expectations.
Evidence high + expectation low = preferred alpha zone.
```

Capital discipline:

```text
Every dollar has an opportunity cost.
A new position must beat cash and the weakest existing use of capital.
The portfolio should evolve through evidence and relative expected value, not idea accumulation.
```
