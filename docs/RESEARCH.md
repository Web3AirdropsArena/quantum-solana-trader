# Research choices and sources

Sources inspected during implementation, September 2026. Provider contracts can change; integration validation remains necessary.

## Jev

[TypeSafe introduction](https://typesafe.ai/blog/introducing-system-one-models-and-jev), [API](https://docs.typesafe.ai/api), and [models](https://docs.typesafe.ai/models) describe a hosted System One decision model. It is not a quantum algorithm or a locally trained trading model. This adapter uses the documented `state`, `questions`, `choice` structure and pins `jev-1.13.0`. It validates answer choice, confidence and probability consistency before permitting a risk-review pass. Type-safe output is not proof that a prediction or trade is correct. Actual hosted calls remain untested without the user's credentials.

## Models and evaluation

- Online logistic regression predicts whether the forward return clears modeled trading costs. Predictions precede delayed labels. Calibration, Brier score and positive-label base rate matter alongside accuracy.
- The dashboard compares the model with a constant zero-probability (no-opportunity) forecast on the same labels. Its Brier loss equals the positive-label frequency. Skill is `1 - model_loss / baseline_loss`; zero-positive samples have undefined skill and cannot pass readiness. This is a minimum baseline, not proof of an edge.
- Momentum, mean-reversion and breakout are transparent baseline hypotheses, not copied secret strategies. [AQR's time-series momentum research](https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum) motivates testing momentum; it does not establish an intraminute crypto edge.
- Exponential expert weighting adapts to counterfactual net returns. Individual trade outcomes additionally reduce risk after losses and introduce cooldowns. These mechanisms can also underperform; adaptation alone is not improvement.
- EWMA volatility, empirical tail loss, position limits and drawdown limits constrain exposure. A 31-return tail estimate is noisy; it does not protect against arbitrary jumps, illiquidity or black swans.
- Three expanding chronological training folds use an embargo before adaptive prequential tests. No test-based parameter optimization is performed. Fold intervals and dataset hashes are recorded for reproducibility. Test samples are correlated and small samples are not reliable evidence.

No quantum speedup is claimed or implemented on this CPU machine. Quantum finance research, e.g. [Quantum algorithms for Monte Carlo and portfolio optimization](https://arxiv.org/abs/2006.14510), does not supply an established trading advantage for this deployment. Provenance and measured out-of-sample behavior take precedence over algorithm labels.

## Data and execution

[Binance public data](https://github.com/binance/binance-public-data) documents market archives. This implementation instead uses public REST klines with millisecond timestamps and keeps only completed candles. Normalized CSV input uses seconds. In particular, raw newer archive timestamp units must not be assumed to match this import format.

[Jupiter order/execute](https://developers.jup.ag/docs/swap/order-and-execute) supplies indicative quotes and prepared swaps. Price, slippage and route checks do not guarantee inclusion or realized execution price. The local adapter validates restricted transaction shapes and simulated account deltas; compatibility with actual routes remains unverified.

[Solana simulation](https://solana.com/docs/rpc/http/simulatetransaction) is a point-in-time check, not a promise about later execution. Token screening checks mint authorities, classic token ownership, concentration and roundtrip quote evidence; unknown assets remain blocked. Token-2022 assets are rejected rather than claiming comprehensive extension support. USDC and SOL still carry issuer, network and market risks.

## Deliberate limits

Single-market long/cash research only; no leverage, shorts, memecoin discovery, MEV protection guarantee, order-book reconstruction, self-modifying code, local foundation-model training or universal scam detection. CEX candle fills cannot reproduce Solana pool reserves, sandwich attacks, latency, priority fees or token-specific transfer behavior. Marked open positions at evaluation end have not paid a hypothetical final liquidation cost; compare realized and marked results separately. The buy-and-hold reference is a raw price return without execution costs and is not a risk-matched portfolio benchmark.
