# quantum-solana-trader / local Solana research and trading workstation

## Capability map and implementation order
| Module | Responsibility | Depends on |
|---|---|---|
| data | Validated candles, SQLite events, snapshots, exports | - |
| learning | Online logistic model, adaptive experts, chronological evaluation | data |
| execution | Paper accounting, risk policy, Solana screening, guarded mainnet adapter | learning |
| interface | Portfolio, decisions/stages, interactive 3D, logs and telemetry | execution |

## Objective and assumptions
Single-user, local-first application for a CPU-only Ubuntu machine (Python 3.11+), developed and checked on Windows. Spot long/cash only; default market SOL/USDC. No leverage. No keys or real funds required for development. The user authorized implementation and review in this request; routine local construction proceeds without repeated approvals.

Build an inspectable research system, not a claim of profitable or perfect AI. The same deterministic safety policy constrains learned decisions. Unknown token safety, stale prices, invalid data, and unresolved transactions block new exposure. Learning changes numerical model parameters, never source code or risk limits. Training every possible future failure is impossible.

## Design contract
Graphite trading workstation with mint highlights, compact typography, persistent resource bar, clear PAPER/LIVE provenance, four navigation tabs, actionable empty/error states. Portfolio prioritizes equity and fills. Decisions show observable features, policy reasons, prediction outcomes and stage evidence, not invented hidden reasoning. A draggable, zoomable 3D decision network visualizes actual signals with an equivalent accessible text view. At 320px tables scroll and controls wrap. Reduced motion supported. No fabricated performance or countdowns.

## Commands and structure
`python -m quantum_solana_trader --help`, `python -m unittest discover -s tests -v`, `python -m quantum_solana_trader demo`, `python -m quantum_solana_trader serve`.
`quantum_solana_trader/` backend, `web/` browser application, `tests/` behavioral checks, `docs/` operation/research/audit, `tasks/` plan and verification checklist. SQLite data stays in ignored `data/`.

## Acceptance criteria
- Download/import validated chronological candles; reject malformed, duplicate-conflicting, future or incomplete data. Keep provenance.
- Predict before observing labels; persist model, pending labels, capital, risk state and histories atomically. Restart reproduces uninterrupted results.
- Chronological holdout evaluation includes costs, drawdown, closed-trade statistics and prediction calibration. Synthetic runs cannot qualify mainnet.
- Paper orders include adverse slippage, fee and gas assumptions; no same-bar hindsight fills. Live observations and historical replays have separate accounts.
- Explicit risk limits, pause/kill, stop/target exits, data gap detection, cost-aware abstention, model drift monitoring and deterministic incident logs.
- Read-only on-chain token checks reject untrusted authorities/extensions and missing liquidity/concentration evidence. They are heuristics, not scam guarantees.
- Mainnet signing is opt-in and isolated from web controls. Require real observation evidence and explicit operator configuration; journal intent before submission and reconcile uncertain outcomes before any retry.
- Four working dashboard tabs, filters, exports, stage evidence, real telemetry, resource-conscious animation.
- Optional documented Jev API integration. No invented quantum advantage or local Jev training claims.
- Behavioral tests, security/failure review, Ubuntu instructions, and refreshed Graphify output.

## Style and boundaries
Prefer Python standard library and native browser APIs. Decimal/integer amounts at money boundaries; bounded finite values at external inputs. Example: `if age > max_age: return Decision('HOLD', ['stale market data'])`.
Always preserve secrets, validate inputs, use parameterized SQL, and test failure paths. Never auto-enable mainnet, claim perfect safety, auto-edit code, or mix synthetic metrics with real performance. External API credentials and extended real-market observation are runtime prerequisites, not fabricated build results.

## Verification
Standard-library unittest with offline provider fixtures; live read-only smoke checks when available; browser checks for all tabs and controls; persistence restart and transaction uncertainty tests. Record limitations in docs/AUDIT.md.
