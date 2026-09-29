# Build verification and remaining limits

Date: 2026-09-28. This is an implementation self-review, not an independent security certification or a profitability assessment.

## Verified

| Check | Result |
|---|---|
| Python regression suite | 28 tests passed after package rename in 31.139 seconds on Windows, Python 3.14.7 |
| Frontend syntax | Both JavaScript modules pass `node --check` |
| Dependency consistency | `pip check` passed |
| Known dependency vulnerabilities | `pip-audit -r requirements.lock --no-deps --disable-pip`: none reported at audit time |
| Public historical data | 10,080 completed SOL/USDC minute candles downloaded |
| Live paper browser smoke | Named session collected a current closed candle; pause/resume, kill confirmation and stop controls exercised |
| Devnet connectivity | Read-only version and slot RPC calls succeeded |
| Devnet transaction | NOT completed: faucet rejected `requestAirdrop` |
| Solana signing SDK | Offline transaction serialization/signature compatibility checked |
| Dashboard | All four views, trade filters, log filter, 3D keyboard/motion controls exercised |
| Responsive layout | No document horizontal overflow at widths 320, 768, 1024 and 1440 |
| Browser diagnostics | No console errors reported during checks |

Runnable regression command: `python -m unittest discover -s tests -v`.

The suite covers finite money/OHLC validation, conflicting duplicate ingestion rollback, uninterrupted/restarted state equivalence, no future-data leakage into past predictions, delayed labels, next-bar costs, unfavorable intrabar stop ordering, gaps, external review cancellation, persistent pause/kill, failed commit rollback, stale live rejection, synthetic readiness exclusion, backups/exports, single-writer locking, evaluation embargo/metric denominators, malformed provider responses, Jev probability consistency, mint/amount/slippage tampering, simulated wallet drains/authority mutation, unresolved transaction blocking, default-off mainnet, HTTP origin/Host/CSRF/path handling, loss-based risk reduction and named paper-session validation.

## Issues found and repaired

- Live paper initially risked filling at the open of an already completed bar. It now uses the newly observed close; historical replay alone uses the next bar's open. Regression added.
- Rejected external risk review now cancels a queued entry before any fill.
- Atomic commits now precede advancement of the in-memory account/model snapshot.
- Evaluation and warm-started paper accuracy/Brier denominators now exclude training labels.
- SQLite backup connections are closed explicitly, fixing Windows file-handle cleanup.
- CPU telemetry now compares CPU-time samples; per-request thread sampling was reporting misleading zeroes.
- Client disconnects during export are handled without noisy server tracebacks.
- Worker cancellation now reports stopped, retains committed state and can interrupt historical download between pages.
- Expanded decision and log disclosures survive periodic dashboard refreshes.
- Named paper sessions allow explicit new experiments without erasing a latched account history.
- Readiness now compares Brier loss against always predicting no opportunity. High accuracy from imbalanced labels cannot satisfy this new check by itself.
- Observation duration ends at the last observed candle, so offline waiting cannot age a session into eligibility.

## Real-data smoke evaluation

A separate validation database replayed 700 real minute candles through three chronological folds. The short report is stored locally at `data/real-smoke-evaluation.json` (ignored by Git).

| Fold | PnL on 10,000 paper USDC | Closed trades | Accuracy | Brier |
|---|---:|---:|---:|---:|
| 0 | -6.61 | 1 | 93.75% | 0.03448 |
| 1 | 0.00 | 0 | 100.00% | 0.00193 |
| 2 | 0.00 | 0 | 98.44% | 0.01713 |

**No profitable edge demonstrated.** High accuracy in these folds reflects a heavily imbalanced cost-hurdle label, not profitable trading. A seven-day download and a 700-bar smoke test are infrastructure checks, not adequate strategy validation. Synthetic demo gains must not be used in performance claims. The final loss-memory adjustment is covered by regression tests; the recorded real-data report is an earlier smoke run, not a fresh final-model benchmark.

## Not verified or not implemented

### Follow-up: full downloaded week

The final model was evaluated on 10,080 real minute candles, with three independent 1,507-bar test folds and 1,471 matured predictions in each fold. Full machine-readable results are in `docs/full-week-evaluation.json`; training/test checkpoints remain in `data/full-week-validation.sqlite`.

| Fold | Paper PnL | Closed trades | Brier skill vs no opportunity |
|---|---:|---:|---:|
| 0 | 0.00 USDC | 0 | +0.0878% |
| 1 | +1.6843 USDC | 1 | +3.3127% |
| 2 | 0.00 USDC | 0 | -1.2918% |

One profitable trade is insufficient evidence. Two folds did not trade, and one model was worse than the trivial baseline despite 99.46% prediction accuracy. No strategy thresholds were tuned on these results. Readiness remains unestablished. WSL is available on this Windows host but has no installed Linux distribution, so Ubuntu verification remains outstanding.

### Remaining work

- The full system has not been executed on the dedicated Ubuntu hardware. Installation is documented; Ubuntu checks must be run there.
- Actual Jev and Jupiter authenticated requests await operator credentials. Their contracts are implemented and mocked at boundaries, not certified live.
- No real wallet was loaded, no real funds were signed or sent, and no autonomous mainnet loop exists. The restricted supervised adapter rejects lookup tables and many ordinary Jupiter transaction shapes. A working production swap path remains unverified.
- The readiness screen's duration, sample-size and PnL checks are minimum research gates. It does not prove statistical significance, robust out-of-sample alpha, future safety or execution fidelity. Passing them never automatically enables mainnet.
- Token screening is heuristic. Unknown tokens remain blocked; Token-2022 transfer hooks and arbitrary programs are not comprehensively supported. There is no universal rug/honeypot detector.
- CEX OHLC observations omit DEX liquidity, MEV, latency and actual slippage. Paper stop execution is an assumption and cannot guarantee exits during outages or gaps.
- Logs and snapshots persist but are not encrypted. There is no log-retention automation, user authentication, public hosting, multi-account support, robust state migration framework or protection against a compromised local OS.
- Numerical online learning and trade lessons persist. This is not local Jev training, a foundation-model reasoning engine, autonomous source-code repair or quantum computation.

Before any mainnet use: collect much longer independent market evidence, validate the specific Jupiter route and RPC behavior, independently review signing/account-delta controls, and resolve devnet transaction tests. Do not infer readiness from a passing unit-test suite.

## Free local / DEX follow-up

37 regression tests passed on Windows/Python 3.14.7 (30.450 seconds in the final run). New boundaries cover DEX mint metadata, completed timestamps, pagination, spoofed token symbols, pool age/liquidity, quote tampering, friction checks, hosted-AI blocking despite a configured key, local Laya response validation, external wallet paths and exact paper restart state. JavaScript syntax validation passed.

Live keyless checks retrieved 1,440 one-minute Orca SOL/USDC candles and compared three DEX venues. At the observed snapshot the raw gap was about 0.114%, while the conservative Raydium round-trip loss was about 1.011% on 10 USDC; these are transient indicative observations, not arbitrage evidence. The DEX replay made no trades and had negative prediction skill. Three chronological evaluation folds also made no trades; the two folds with positive baseline loss had Brier skill of about -12.60% and -10.27%. The first fold's always-no-opportunity baseline had zero error. No profitable edge was established. The report is in `docs/dex-smoke-evaluation.json` with source and dataset hash; its original generic CEX limitation text is superseded by this DEX provenance note.

Defaults now use DEX observations. CEX history remains available only as explicit legacy research and cannot satisfy the DEX readiness gate. Paid hosted AI requires an additional explicit environment switch. The optional local Laya adapter cannot override failed checks, loads only local assets in offline mode, and fails closed; actual checkpoint loading, runtime memory use and trading benefit remain unverified. Laya fine-tuning is not implemented. Windows mainnet key loading is rejected; the Ubuntu loader requires an external regular, owner-only keyfile. No secrets were loaded and no transactions were signed for these checks.

DEX price/liquidity checks are heuristics over cached public data. They do not prove pool safety, quote freshness, MEV protection or atomic arbitrage feasibility. The new provider path does not change the remaining real-funds, independent strategy-validation and Ubuntu-validation limitations above.

Browser verification exercised the DEX scan and a named live paper session. Two completed DEX bars were persisted with HOLD decisions. A missing acceptable two-way quote blocked entries as intended. The worker was then stopped with its state retained; hosted reviewers remained disabled.
