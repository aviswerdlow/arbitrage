# **Cross-Venue Arbitrage, Told As A Product Story**

## **The story we are building**

Two venues look at the same world and see slightly different probabilities. That gap is not magic. It is physics. Information arrives unevenly, traders have different constraints, fee math is messy, matching engines are not identical. If you can prove two contracts mean the same thing, and you can own both sides at the same instant, the difference is profit. If you cannot prove it, or you cannot hedge it instantly, it is risk disguised as opportunity.

Our product exists to do one thing with absolute clarity. Find pairs of truly equivalent binary markets on Polymarket and Kalshi, price the real all-in edge after every fee and friction, then execute a fully hedged taker pair so exposure returns to zero within a quarter of a second. No predictions. No vibes. Just certainty about equivalence and certainty about execution.

## **First principles**

1. Prices disagree for reasons that do not require forecasting.

    Spread exists because of latency, liquidity pockets, and fee shape. If we control equivalence and execution, we do not need to guess outcomes.

2. Safety beats cleverness.

    A clever trade that leaves you unhedged is a bad product. We would rather miss a spread than take leg risk.

3. Simplicity wins under latency.

    Decide upstream with full context, then execute downstream without negotiation. The MVP hits liquidity. No maker posting while we are learning. No multi-leg art projects.

4. Interfaces should explain decisions, not impress.

    Show the edge, the cost to lock it, the confidence in equivalence, and the health of the system. Hide everything else.

## **The product in one breath**

A small, focused system that watches both venues in real time, proves contract equivalence with hard rules and an LLM check, computes a fee-aware net edge in cents, and when the edge is at least 2.5, sends a paired taker hit on both venues and is flat again within 250 milliseconds. Capital is capped at five thousand dollars per venue. Exposure per contract is capped at two hundred fifty dollars. We allow up to eight concurrent hedged pairs. If the hedge does not complete in time, we cancel and unwind. We keep trading only if feeds are fresh and fees are current.

## **A day in the life of a trade**

You open the dashboard. There are not fifty blinking panels. There is a short queue of candidate pairs with four numbers: net edge in cents, confidence in equivalence, expected slippage from top three levels, and venue health. The system has already decided whether the pair is truly the same question. It has done that with strict rules on time windows, resolution sources, and units. The LLM is not a judge. It is a witness. It proposes similarity, then the rules decide.

An edge appears. Three point one cents after fees and friction. The leader detection, built from five second bars and rolling cross-correlation, says Polymarket is moving first this hour. The system hits the price on Polymarket where depth is certain, then hits the opposite side on Kalshi. Acks return in well under a quarter second. Inventory is back to zero. The fill report lands in Discord. You see entry edge, realized slippage, and total fees. You did not click anything. You did not need to.

## **What we ship in v1**

* Binary markets only, across all categories.

* Taker-only execution.

* Hard-rule equivalence plus LLM similarity. Accept only if score is at least 0.92 and every rule passes.

* If resolution sources differ, we require a manual allowlist.

* Full fee math. Taker fees, maker fees where they apply, profit fees, gas and bridge costs, on-ramp and FX spread.

* Depth-based sizing from the top three levels.

* No legging. Hedge timeout is 250 milliseconds p95. If it is not filled, cancel and unwind.

* Capital and exposure caps baked in. Daily stop at one percent of equity, weekly pause at three percent, monthly review at five percent.

* A web dashboard that shows edges, fills, PnL after fees, exposure by venue and category, and system health.

* A Discord bot that can halt or resume a venue, list top edges, and report fills and errors.

## **How we prove sameness without fooling ourselves**

Equivalence is not a vibe. It is a checklist. Same time window after timezone normalization. Same outcome definition in the same units. Resolution sources that either match or are explicitly allowlisted. Contract text that does not contain ambiguous clauses. The LLM reads both descriptions and produces a structured comparison. We only accept when both the rules pass and the score clears the bar. When the text updates on either venue, the pair is revalidated. If anything fails, it stops trading.

## **How we decide to trade**

We price two packages. Yes on venue A with No on venue B. Then Yes on venue B with No on venue A. For each package we subtract every cost we can know. Exchange fees. Profit fees. Gas. On-ramp. FX. Modeled slippage from the live book. The higher of the two net edges is the candidate. If it is at least 2.5 cents, and our hedge completion probability is above ninety-nine percent at the intended size, and we are under caps, we trade. If any of those conditions drop, we do not.

Lead-lag does not force trades. It chooses which side to hit first to reduce the chance that the price moves away mid-hedge. It is a routing hint, nothing more.

## **What we refuse to do in v1**

We do not predict outcomes. We do not post maker orders. We do not build complicated basket trades. We do not accept fuzzy matches that are not backed by rules. We do not rely on any external signal we cannot test and measure. We do not give you a cockpit full of switches. We give you a small number of real controls.

## **The interface, designed like a tool you actually trust**

The edge list is quiet. Each row explains itself. Pair title, net edge, confidence, expected slippage, leader, and a small status dot. One click takes you to recent fills for that pair. The fills view shows what you paid, what you received, how much you lost to slippage and fees, and the realized edge in cents. System health is not a separate religion. It is one panel: feed freshness, error rates, reconnects, and hedge time distributions. Discord mirrors the essentials. You can halt a venue from your phone. You can see fills in real time.

## **The numbers that define success**

* Live median slippage at or below 0.4 cents.

* Hedge completion at or below 250 milliseconds p95.

* Alert to order at or below 200 milliseconds p50 and 350 milliseconds p95.

* False match rate below half a percent.

* Backtest Sharpe at or above two on the initial universe.

* Uptime at or above 99.5 percent during market hours.

If we miss these, we change the design. If we hit them and PnL is not real after costs, we raise the threshold or narrow the universe. The product answers to physics.

## **Rollout that respects risk**

Phase zero watches and learns. We ingest, match, compute, and backtest on twelve months of history. The dashboard is live, but execution is off. Phase one paper trades. Every decision path is exercised. Phase two goes live in small size with taker-only hedging and all caps enforced. We expand as the data supports it. Maker-then-taker, bundles, and negative-risk pairs come later behind flags. Idle cash yield waits until the core loop is robust.

## **The culture the product teaches**

This system is honest about what it knows and what it does not. It refuses trades it cannot hedge. It shows the cost of certainty every time. It prefers a small set of clean wins to a large set of noisy attempts. It treats your attention like capital. It does not waste it.

## **The north star**

Make the safest choice the default choice. If you leave the system on and walk away, it continues to do the simple thing well. It finds spreads that survive fee math. It hedges them with speed. It explains itself. It stops when the world changes. Then it starts again when it should. That is the entire product.

