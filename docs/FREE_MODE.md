# Free local training and DEX workflow

The default engine needs no hosted AI subscription or API key. It uses CPU logistic learning and adaptive strategy rules, not a remote language model. Live observations still require internet and public HTTP APIs. API availability and free limits are controlled by their providers. Electricity, internet, storage, mainnet gas and exchange fees are not free.

| Component | Default cost/dependency |
|---|---|
| Core training, inference, SQLite memory, dashboard | Local; no inference bill |
| Synthetic demo / imported CSV replay | Works offline |
| DEX candles | Keyless GeckoTerminal public API; history/rate limits apply |
| Pool research | Keyless DexScreener public API |
| Round-trip quote research | Keyless Raydium compute API; no transaction submission |
| Mint screening | Solana public RPC, or an operator-configured endpoint |
| Laya reviewer | Optional local weights and CPU PyTorch; download/storage required |
| Jev reviewer | Hosted, potentially paid; disabled by default even with a key |
| Real swaps | Network/DEX fees; supervised Jupiter adapter also requires its API key |

## Train, evaluate and resume

Activate the project's virtual environment. Stop the dashboard first because only one writer can own a database. Alternatively choose another `--db` before each subcommand.

```bash
python -m quantum_solana_trader download --days 7
python -m quantum_solana_trader train --session dex-training-001
python -m quantum_solana_trader evaluate --session dex-evaluation-001
python -m quantum_solana_trader scan-dex --usdc 10
python -m quantum_solana_trader paper --session live-dex-paper --checkpoint dex-training-001
# Ctrl+C, then resume later without resetting the account or learned parameters:
python -m quantum_solana_trader paper --session live-dex-paper
```

The default source is the established Orca SOL/USDC pool `Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE`. Candle metadata must identify canonical SOL and USDC mint addresses. Downloads convert opening timestamps to completed-bar timestamps and reject invalid/conflicting data. Requests may return less history than requested; inspect dataset counts. Use `--pool` for another canonical SOL-base/USDC-quote pool and supply its exact `dex:geckoterminal:POOL:1m` source to train/evaluate. Warm starts cannot mix pools or CEX data.

Paper decisions compare established, liquid Orca/Raydium/Meteora pools, screen canonical mints and request buy/sell quotes for the planned size. Missing evidence, price discrepancies, observed liquidity shocks or excessive round-trip friction block new entries. These are conservative heuristics, not proof of absence of manipulation. Unknown tokens remain blocked. Cached pool prices lack execution timestamps; a displayed price gap is not an executable arbitrage opportunity. Atomic cross-DEX arbitrage is not implemented. Paper fills remain assumptions and do not reproduce actual MEV or liquidity.

Parameters, pending learning labels, account/risk state and events persist atomically in SQLite. Restart the same session to continue. Back up before upgrades; retain the corresponding code. The app does not rewrite itself, guarantee improvement on each trade, or prevent every repeated loss.

## Optional Laya: local, separate and unverified with actual weights

The supplied community article led to the [official Laya repository](https://github.com/NandhaKishorM/laya). This integration uses typed choice probabilities to review sanitized numerical evidence. It can block/defer an entry; it cannot lift deterministic limits. It does not provide a trading-trained model or retrain Laya during paper trading.

On Ubuntu, install optional dependencies only if you want this extra reviewer:

```bash
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements-laya.txt
```

Download an official checkpoint using the publisher's instructions into a location outside this checkout. Keep its revision and file hashes with your experiment records. The [English checkpoint](https://huggingface.co/convaiinnovations/laya) requires its config, `model.safetensors`, tokenizer directory and any encoder assets required by that revision. Follow the publisher's offline-cache instructions if dependencies reference additional pretrained assets. No checkpoint has been installed or inference-benchmarked in this project yet.

```bash
export QST_LAYA_MODEL_DIR="$HOME/models/laya"
python -m quantum_solana_trader paper --session laya-paper --laya
```

The adapter sets Hugging Face/Transformers offline mode before loading and limits CPU threads to four. Missing weights/dependencies or malformed/uncertain answers block entries; it does not download during trading. Run a separate experiment and compare held-out results before treating this reviewer as useful. A reported confidence of 0.85 is not an 85% chance of profitable trading. Core online learning works without Laya.

Leave `QST_ALLOW_HOSTED_AI` unset (or `0`) to block Jev calls. Merely setting `TYPESAFE_API_KEY` does not enable billing. Hosted use requires `QST_ALLOW_HOSTED_AI=1` plus an explicit `--jev` run; it is outside the zero-paid default.

## Wallet and operator approval

The dashboard never accepts private keys and never enables real trading automatically. Passing the minimum evidence checks displays a request for operator review. The only current real-order path is the separately configured, acknowledged, supervised `swap` CLI. There is no unattended mainnet trading loop, and no real-funds validation has been performed.

For a future operator-created wallet, store a Solana CLI 64-byte JSON keypair outside the checkout on the dedicated Ubuntu machine:

```bash
mkdir -p "$HOME/.config/quantum-solana-trader"
chmod 700 "$HOME/.config/quantum-solana-trader"
# Place the operator-created wallet.json there privately; never paste its contents here.
chmod 600 "$HOME/.config/quantum-solana-trader/wallet.json"
export QST_WALLET_FILE="$HOME/.config/quantum-solana-trader/wallet.json"
```

This environment variable contains a path, not a secret. The program does not auto-load `.env` files. Key loading rejects checkout paths, symbolic-link files, nonregular files, wrong ownership and group/other permissions. Windows mainnet key loading is blocked because this implementation does not validate Windows ACLs. Local files are not encrypted by the app and cannot protect against a compromised OS. Keep mainnet unset while training; do not fund a wallet merely because tests pass.

## Primary provider references

- [DexScreener API](https://docs.dexscreener.com/api/reference)
- [GeckoTerminal public API](https://www.geckoterminal.com/dex-api)
- [Raydium official swap API example](https://github.com/raydium-io/raydium-sdk-V2-demo/blob/master/src/api/swap.ts)
- [Laya installation and inference documentation](https://github.com/NandhaKishorM/laya)
