# Ubuntu operation and recovery

## Install and move data

Use a supported Ubuntu release with Python 3.11 or newer. If venv is unavailable, install the distribution's `python3-venv` package. From the copied project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m quantum_solana_trader serve
```

The server binds only to 127.0.0.1. Keep it private. To view a dedicated Ubuntu machine remotely, use an SSH tunnel, e.g. `ssh -L 8765:127.0.0.1:8765 user@ubuntu-host`, then visit localhost:8765. There is no multi-user authentication or public deployment configuration.

For an existing Windows dataset, create a SQLite backup, stop its worker, then transfer the backup into Ubuntu's `data/astra.sqlite`. Do not copy a live database file without its WAL. Never copy Windows `.venv` to Linux.

## CLI examples

Run one writer at a time. The dashboard and CLI share a process lock, so stop the server before these commands, or use a separate `--db` path. Global `--db` comes before the command.

```bash
# Offline end-to-end demo in a separate database
python -m quantum_solana_trader --db data/demo.sqlite demo

# Public network requests; no API key needed
python -m quantum_solana_trader download --days 30 --interval 1m
python -m quantum_solana_trader train --session initial-training
python -m quantum_solana_trader evaluate --session evaluation-001

# Live observations, simulated money; Ctrl+C stops cleanly
python -m quantum_solana_trader paper --session live-paper --checkpoint initial-training
# Resume that exact saved model/account later
python -m quantum_solana_trader paper --session live-paper

python -m quantum_solana_trader export --session live-paper --output data/live-paper.jsonl
python -m quantum_solana_trader backup data/checkpoint-001.sqlite
```

Train resumes an existing session against later candles. Evaluation requires a fresh prefix; interrupted evaluation leaves partial checkpoints but issues no completed report. Dataset downloads deduplicate exact candles and reject conflicting replacements. CSV header is `ts,open,high,low,close,volume`, with **close-time UTC Unix seconds**, finite positive prices and nonnegative volume. Use `import-csv file.csv --source csv:your-provenance`. Raw Binance archive timestamps require normalization; do not pass raw archive files directly.

## Worker controls and failure handling

Pause blocks new entries while allowing risk exits. Stop worker ends observation and retains state; it does not liquidate exposure. Kill is latched and queues a paper exit at the next valid observation; no fill is possible while the feed is down. A stopped paper account can remain exposed in simulation. Never interpret these controls as managing an external real wallet.

Provider failures block entries and retry with bounded backoff. Missing or stale candles reset the feature/label context; historical gap data is not invented. Review the journal before resuming. Never delete a database to clear an uncertain mainnet transaction. Keep the OS clock synchronized and monitor available disk space. Logs currently have no retention policy: archive/back up old sessions before storage fills.

The model/account checkpoint survives restart; running threads do not. Restart the server, then explicitly start the same paper session. The CLI paper loop is appropriate for a persistent terminal session. No unattended background service is installed.

## Optional external integrations

Set environment variables in your own terminal before launching. Do not paste secrets into the dashboard, source files, logs or chat. The app does not automatically read a `.env` file.

| Variable | Purpose |
|---|---|
| `QST_ALLOW_HOSTED_AI` | Leave unset/0 in free mode; hosted Jev additionally requires 1 |
| `TYPESAFE_API_KEY` | Hosted Jev reviewer; opt in using `--jev` or the checkbox |
| `QST_LAYA_MODEL_DIR` | Optional local Laya checkpoint; see FREE_MODE.md |
| `QST_WALLET_FILE` | External owner-only Ubuntu keyfile path |
| `JUPITER_API_KEY` | Supervised mainnet adapter only |
| `SOLANA_RPC_URL` | Optional HTTPS Solana mainnet RPC endpoint |

Jev can cost money and is blocked by default even with a key. Default paper mode uses keyless DEX data, Raydium quote checks and public RPC mint screening. Missing evidence blocks entries. Prices and paper fills are indicative, not proof of executable swaps. See [free mode and local Laya](FREE_MODE.md) for exact dependencies and limits.

## Devnet and mainnet boundary

`python -m quantum_solana_trader devnet-check` checks devnet RPC connectivity. `python -m quantum_solana_trader devnet-exercise` needs the optional signing dependencies, requests faucet test SOL into an ephemeral in-memory wallet and attempts a simulated/self-transfer transaction. It does not validate a DEX strategy, and faucet rate limits may prevent completion.

Do not fund this system based on its build completion. The supervised `swap` CLI requires explicit mainnet configuration, a separate keyfile, acknowledgment, qualifying fresh paper evidence, transaction simulation and durable intent recording. It limits orders to 1-25 USDC and gross daily volume to 100 USDC. It rejects address lookup tables and many normal routes. It has not been tested against real funds and is not an autonomous executor.

`python -m quantum_solana_trader reconcile` checks submitted signatures without resending. An unknown result remains blocked for manual investigation; absence from a single RPC is not evidence that a swap failed. Windows mainnet key loading is blocked; Ubuntu owner-only permissions are required. Keep any future wallet outside this project and use a new isolated wallet. Independent transaction/security review and realistic execution evidence remain prerequisites before considering real use.

## Upgrades

1. Stop observation, export events, and use the backup command to a new filename.
2. Keep the old code and backup together. Run tests against a copy of the database.
3. Verify schema/model compatibility before opening the only copy with new code.
4. Resume the same session only after its state and risk latches are confirmed.

The JSONL event schema records session, event kind, timestamp and structured data for future supervised training. These are observed decisions/outcomes, not ground-truth profitable actions. Remove sensitive additions before using third-party training services.
