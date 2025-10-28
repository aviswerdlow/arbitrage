# **Cross-venue Prediction Market Arbitrage**

## **Technical Design Document \+ Test-Driven Development Spec**

## **TL;DR**

We will build a small, reliable system that monitors Polymarket and Kalshi, matches equivalent binary markets, computes fee-adjusted edges, and executes fully hedged, taker-only pairs when the net edge ≥ 2.5¢ with strict leg-risk controls. MVP scope is binary markets only, $5k per venue, $250 max exposure per contract, up to 8 concurrent pairs, no deterministic replay logs in MVP, web dashboard \+ Discord bot, AWS managed services. Lead-lag signals use rolling cross-correlation on 5-second bars to prioritize which side to hit first. All frictions and fees are included in edge math.

---

## **1\) Design principles**

1. Safety first: no legging, taker-only in MVP, cancel-all if hedge not filled within 250 ms (p95 target ≤ 250 ms).

2. Deterministic decisions: explicit thresholds in config; all fee and friction models are versioned and logged.

3. Simplicity: binary vs binary only in P0–P1; bundles and negative-risk later.

4. Observability: every decision produces a structured event; latency and slippage tracked live.

5. Pluggable intelligence: LLM matching is advisory and gated by rules, not authoritative.

---

## **2\) System overview**

**Data plane**

* Ingestion services: Polymarket CLOB \+ Gamma read endpoints and websockets; Kalshi public market data and websockets.

* Normalizer: canonical schema for events, markets, books, trades.

* Matcher: rules \+ LLM ranker produce candidate cross-venue pairs with similarity scores.

* Signal engine: fee-aware mispricing \+ 5-sec lead-lag x-corr; produces trade intents.

* Execution engine: taker-taker paired orders with atomic commit logic and strict timeouts.

* Risk manager: position caps, exposure, daily loss stop, edge decay and unwind policy.

**Control plane**

* Config service (feature flags, thresholds).

* Monitoring (Prometheus, Grafana) and Discord alerts.

* Web dashboard (SvelteKit) for live edges, fills, PnL, health.

* Postgres for state; Redis for queues and rate-limit tokens.

* Secrets in AWS Secrets Manager.

**Stack**

* Python 3.11, FastAPI services.

* Postgres (RDS), Redis (ElastiCache), ECS Fargate, Docker.

* UI in SvelteKit; Discord bot for commands and alerts.

* LLM: DeepSeek primary, GPT-4o fallback via provider gateway.

---

## **3\) External interfaces (high-level contracts)**

### **3.1 Exchange adapters**

* polymarket\_adapter:

  * stream\_books(markets) \-\> orderbook snapshots

  * get\_markets() \-\> \[Market\]

  * get\_events() \-\> \[Event\]

  * place\_order(side, price, size, token\_id) \-\> OrderId

  * cancel(order\_id)

* kalshi\_adapter:

  * stream\_books(markets) \-\> orderbook snapshots

  * get\_markets(query) \-\> \[Market\]

  * get\_event(event\_ticker) \-\> Event

  * place\_order(side, price\_cents, qty, ticker) \-\> OrderId

  * cancel(order\_id)

All adapters expose a uniform TopOfBook and Depth3 structure, timestamps in UTC, and include fee metadata from venue fee schedules.

### **3.2 Matching API**

* match\_candidates(venue\_a\_markets, venue\_b\_markets) \-\> \[PairCandidate{a,b,similarity,hard\_rule\_passes}\]

* confirm\_pair(pair\_id) \-\> activate  (MVP uses auto-confirm when score ≥ 0.92 and rules pass)

### **3.3 Signal API**

* compute\_edge(pair, books, fees, frictions) \-\> EdgeQuote{net\_edge\_cents, confidence, best\_side\_to\_hit}

* compute\_leadlag(pair, recent\_bars) \-\> LeadLag{leader, score}

### **3.4 Execution API**

* execute\_hedged(pair, side\_primary, qty, price\_limits, timeout\_ms) \-\> FillReport

* unwind(pair\_position, reason) \-\> FillReport

---

## **4\) Data model (core tables)**

Use short tables for keywords and numbers only.

**markets**

* id, venue, ticker\_or\_token, title, resolution\_source, close\_time, category, binary\_flag

**events**

* id, venue, slug\_or\_ticker, title, start\_time, end\_time

**market\_pairs**

* id, market\_a\_id, market\_b\_id, llm\_score, rules\_passed, active\_flag

**orderbooks**

* id, market\_id, ts, bid\_px, bid\_sz, ask\_px, ask\_sz, lvl2\_json

**edges**

* id, pair\_id, ts, net\_edge\_cents, leader, signal\_conf, fee\_rev\_hash

**orders**

* id, venue, market\_id, side, px, qty, ts\_sent, ts\_ack, status

**positions**

* id, venue, market\_id, qty\_yes, qty\_no, avg\_px\_yes, avg\_px\_no

**fills**

* id, order\_id, px, qty, ts\_fill, fee, slippage\_cents

**configs**

* key, val, version, ts

---

## **5\) Matching design**

### **5.1 Hard rules (must pass)**

* Both sides must be binary.

* Same time window and timezone normalization.

* Title semantic units include identical entities and relation (e.g., “CPI YoY ≥ 3.0% September 2025”).

* Resolution source compatible or allowlisted.

* Unit and threshold alignment (%, basis points, counts).

### **5.2 LLM ranker**

* Prompt builds a structured comparison with extracted fields.

* Output: similarity in \[0,1\], field-by-field agreement booleans, and an explanation.

* Accept if score ≥ 0.92 and all hard rules pass.

* If resolution sources differ, require manual allowlist in MVP.

### **5.3 Candidate generation**

* Lexical blocking: normalized n-grams, symbols, dates.

* Tag blocking: category, series.

* Jaccard on entities.

* Only then call LLM on survivors.

---

## **6\) Signals**

### **6.1 Fee-aware mispricing**

We compute the theoretical risk-free package: long YES on cheaper venue and long NO on the other, or the reverse. Net edge includes:

* Exchange taker fees and maker fees if any.

* Profit fees if applicable.

* Gas, bridge, on-ramp, FX spreads.

* Expected slippage from depth up to requested size.

Trade if:

* net\_edge\_cents ≥ 2.5

* Estimated hedge completion probability ≥ 99 percent.

* Projected post-trade inventory within limits.

### **6.2 Lead-lag**

* Build 5-sec bars of mid probability for each venue.

* Rolling 10-min window cross-correlation.

* Leader \= argmax positive lag correlation.

* Require stability filter (same leader in 3 of last 4 windows).

* Use leader to decide which side to hit first when both edges qualify.

---

## **7\) Execution engine**

### **7.1 State machine (taker-only MVP)**

1. **Ready**: edge ≥ threshold, risk checks pass.

2. **Place A**: hit side A at or through ask/bid with price cap from depth model.

3. **Hedge B**: immediately hit B opposite side.

4. **Confirm**: both acks within 250 ms p95.

5. **Settle**: record fills, fees, slippage; update positions.

6. **Failure**: if B not filled within timeout or size mismatch \> 1 contract, cancel A remainder and unwind filled exposure.

### **7.2 Controls**

* No legging.

* Partial fills policy: cancel all unhedged remainder.

* Re-attempt up to 3 times with capped exponential backoff.

* Auto-unwind when adverse move ≥ 1.5¢ for 5 seconds or liquidity collapses.

---

## **8\) Risk management**

* Capital: $5k per venue.

* Position cap: $250 per contract per venue, max 8 concurrent pairs.

* Daily stop: 1 percent of equity. Weekly 3 percent pause. Monthly 5 percent review.

* Exposure caps by category to avoid correlated blowups.

* Venue health gates: halt trading on stale data or status down.

---

## **9\) Fees and frictions**

* Load venue fee schedules programmatically and cache with version hash.

* Round-up behavior handled exactly per venue rules.

* Maintain friction pack: gas estimator, on-ramp fees, FX quote.

* Edge math uses the current friction pack version; stored on each edge and fill.

---

## **10\) Configuration (key thresholds)**

| key | value |
| ----- | ----- |
| min\_net\_edge\_cents | 2.5 |
| hedge\_timeout\_ms | 250 |
| backoff\_max\_ms | 800 |
| llm\_accept\_score | 0.92 |
| pairs\_max | 8 |
| per\_contract\_exposure\_usd | 250 |

---

## **11\) Web app and Discord**

**Web dashboard pages**

* Live edges: pair, net edge, best side, lead-lag leader, confidence.

* Recent fills: entry edge vs realized, slippage, fees.

* Exposure: by venue, by category.

* Health: feed latency p50/p95, error rates.

**Discord bot**

* /edges top N

* /halt venue

* /resume venue

* Alerts: fills, errors, threshold breaches.

---

## **12\) Deployment**

* AWS ECS Fargate services: ingest, matcher, signals, execution, api, ui.

* RDS Postgres for state; ElastiCache Redis for queues and rate limiting.

* Secrets in AWS Secrets Manager; IAM roles for tasks.

* GitHub Actions: lint, unit, integration with simulators, image build, deploy to staging then prod with manual approval.

---

## **13\) Security**

* Keys and API tokens only in Secrets Manager.

* Polymarket signing keys encrypted at rest and in memory via kms-decrypted envelope.

* No PII.

* Single user with long-lived session token for the web app stored in secure cookies.

---

## **14\) Test-Driven Development plan**

We will write tests first, then code to satisfy them. Each test references explicit acceptance criteria.

### **14.1 Unit tests (selected)**

* Fee calculators: exact rounding on example tables.

* Friction pack: correct edge impact for gas, FX.

* Depth model: expected slippage within tolerance.

* LLM ranker wrapper: rejects low score; accepts known equivalents.

* Rule checker: mismatched time windows rejected.

### **14.2 Property-based tests**

* Edge invariants: for identical prices across venues, net edge ≤ 0 after fees.

* Round-trip hedging: if both fills at quotes, exposure always neutral.

### **14.3 Integration tests**

* Matching pipeline: given real titles and metadata, produce a confirmed pair with score ≥ 0.92 and rules pass.

* Signal engine: inject synthetic books to create 3.0¢ net edge; expect trade intent.

* Lead-lag: synthetic bar series with known lead; detect correct leader.

### **14.4 Execution tests**

* Hedged taker flow: both acks within 200 ms, status Complete, no residual exposure.

* Timeout path: second leg stalls, cancel and unwind, net residual zero.

* Partial fill path: cancel remainder, emit alert, no position drift.

### **14.5 Backtest harness**

* Load 12 months of snapshots or reconstructed TOB.

* Produce metrics: Sharpe, hit rate, average edge at entry, realized slippage, PnL net of fees.

* Acceptance: Sharpe ≥ 2.0 on chosen universe.

### **14.6 Performance tests**

* Ingestion throughput: 1k book updates per second sustained on staging.

* Latency budgets: alert-to-order p50 ≤ 200 ms, p95 ≤ 350 ms; hedge completion p95 ≤ 250 ms.

### **14.7 Chaos tests**

* Drop one venue feed for 10 seconds. Expect halt for that venue, no trades.

* Inject stale timestamps. Expect rejection and alert.

---

## **15\) Core algorithms (pseudo)**

### **15.1 Edge calculation**

def net\_edge(pair, bookA, bookB, fees, frictions, qty):

    \# consider both packages: YES\_A \+ NO\_B and YES\_B \+ NO\_A

    cands \= \[\]

    for pkg in packages(pair, bookA, bookB, qty):

        gross\_edge \= 100 \- (pkg.price\_yes \+ pkg.price\_no)

        fees\_c \= fee\_model(pkg, fees, qty)           \# venue taker calc, profit fees if any

        fric\_c \= friction\_model(pkg, frictions, qty) \# gas, FX, ramps

        slip\_c \= slippage\_estimate(pkg.depth, qty)

        cands.append(gross\_edge \- fees\_c \- fric\_c \- slip\_c)

    return max(cands)

### **15.2 Execution state machine**

if edge \>= min\_edge and risk\_ok():

    oidA \= place\_taker(A, side=best\_side\_to\_hit, qty, price\_cap)

    if not ack\_in(hedge\_timeout): cancel(A); return fail

    oidB \= place\_taker(B, side=opposite, qty, price\_cap)

    if not ack\_in(hedge\_timeout):

        cancel(B); unwind(A\_filled)

        return fail

    record\_fills(); return success

### **15.3 Lead-lag cross-correlation**

leader \= argmax\_lag( xcorr(mid\_poly, mid\_kalshi, window=10min, step=5s) )

---

## **16\) Backtest and live acceptance criteria**

* Backtest Sharpe ≥ 2.0 on the initial universe.

* Live median slippage ≤ 0.4¢.

* False-match rate ≤ 0.5 percent in production pairs.

* Alert-to-order latency p50 ≤ 200 ms, p95 ≤ 350 ms.

* Hedge completion p95 ≤ 250 ms.

* Uptime ≥ 99.5 percent during market hours.

---

## **17\) Roadmap**

**P0 Monitor \+ backtest**

* Ingest, normalize, store.

* Matcher rules \+ LLM advisory.

* Mispricing signal only.

* Backtest harness and dashboard.

**P1 Paper trade**

* Full execution simulator.

* Discord bot and alerts.

* Fee integration and friction pack.

**P2 Small-size live**

* Taker-only hedging in production.

* Lead-lag routing.

* Maker-then-taker experiment behind flag.

**P3 Scale**

* Bundles and negative-risk pairs.

* Deterministic replay logs.

* Advanced cash management and yield.

---

## **18\) Runbook (MVP)**

* To halt trading: /halt venue=kalshi in Discord.

* To resume: /resume venue=kalshi.

* If false pair detected: deactivate pair in dashboard, add to blocklist.

* If feed latency \> 2 seconds: auto-halt venue and alert.

---

## **19\) Open risks and mitigations**

* **Regulatory**: Kalshi U.S. only. Ensure account and trading remain compliant.

* **Spec changes**: contract clarifications can change. Keep ruleset strict and allow manual allowlist only.

* **Liquidity shocks**: depth collapse can raise slippage. We cap quantity and use hedge timeouts.

* **LLM hallucination**: LLM is advisory, never overrides hard rules.

---

## **20\) What you will see first**

* A working P0 stack on AWS staging.

* Web dashboard with live edges and health.

* Discord bot responding to /edges top 5\.

* Backtest report for the initial universe with Sharpe, slippage, and hit rate.

