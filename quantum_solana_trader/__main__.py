import argparse
import json
import os
import signal
import time
from pathlib import Path

from .engine import Policy, metrics
from .providers import INTERVALS, download, import_csv, rpc, token_screen
from .research import replay, synthetic, walk_forward
from .store import Store, engine_lock
from .dex import DEFAULT_POOL, download_dex, scan_dexes, source_name


def main():
    parser = argparse.ArgumentParser(description='quantum-solana-trader • local Solana research workstation')
    parser.add_argument('--db', default='data/astra.sqlite', help='SQLite journal/checkpoint path')
    sub = parser.add_subparsers(dest='command', required=True)
    server = sub.add_parser('serve', help='Open the local dashboard server')
    server.add_argument('--port', type=int, default=8765)
    sub.add_parser('demo', help='Deterministic synthetic stress replay, clearly labeled')
    fetch = sub.add_parser('download', help='Keyless DEX SOL/USDC history; Binance available as explicit legacy research source')
    fetch.add_argument('--days', type=int, default=30)
    fetch.add_argument('--interval', choices=INTERVALS, default='1m')
    fetch.add_argument('--provider', choices=['dex', 'binance'], default='dex')
    fetch.add_argument('--pool', default=DEFAULT_POOL)
    scan = sub.add_parser('scan-dex', help='Keyless cross-DEX research and round-trip quote checks; no transactions')
    scan.add_argument('--usdc', type=float, default=10.)
    load = sub.add_parser('import-csv', help='CSV columns: ts,open,high,low,close,volume; close-time UTC seconds')
    load.add_argument('path')
    load.add_argument('--source', required=True, help='Provenance name starting csv:')
    for name in ('train', 'evaluate'):
        p = sub.add_parser(name)
        p.add_argument('--source', default=source_name())
        p.add_argument('--session', default=None)
        p.add_argument('--interval', choices=INTERVALS, default='1m')
    paper = sub.add_parser('paper', help='Continuous paper worker; no keys or transactions')
    paper.add_argument('--jev', action='store_true')
    paper.add_argument('--laya', action='store_true', help='Optional local risk reviewer; requires downloaded local checkpoint')
    paper.add_argument('--feed', choices=['dex', 'binance'], default='dex', help='Binance is only for explicit legacy research')
    paper.add_argument('--pool', default=DEFAULT_POOL)
    paper.add_argument('--session', default='live-dex-paper', help='Resume this paper session, or choose a new name after a latched halt')
    paper.add_argument('--checkpoint', help='Warm-start from a real SOLUSDC 1m replay session')
    screen = sub.add_parser('screen', help='Read-only mint screening')
    screen.add_argument('mint')
    sub.add_parser('devnet-check', help='Read-only devnet RPC connectivity check, not a DEX simulation')
    sub.add_parser('devnet-exercise', help='Ephemeral devnet test-SOL self-transfer; never mainnet')
    export = sub.add_parser('export')
    export.add_argument('--session', required=True)
    export.add_argument('--output', required=True)
    backup = sub.add_parser('backup')
    backup.add_argument('destination')
    live = sub.add_parser('swap', help='Supervised, evidence-gated mainnet order; requires optional solders')
    live.add_argument('--evidence-session', default='live-dex-paper')
    live.add_argument('--side', choices=['BUY', 'SELL'], required=True)
    live.add_argument('--usdc', required=True)
    live.add_argument('--keyfile', default=os.getenv('QST_WALLET_FILE'), help='External private keyfile, or QST_WALLET_FILE environment path')
    live.add_argument('--ack', required=True)
    sub.add_parser('reconcile', help='Resolve pending mainnet signatures without resending')
    args = parser.parse_args()
    store = Store(args.db)
    if args.command == 'export':
        with open(args.output, 'x', encoding='utf-8') as file:
            file.writelines(store.export(args.session))
        print(args.output)
        return
    if args.command == 'backup':
        if Path(args.destination).exists():
            raise ValueError('Backup destination already exists; choose a new filename')
        store.backup(args.destination)
        print(args.destination)
        return
    with engine_lock(store.path):
        if args.command == 'serve':
            from .server import make_server
            server = make_server(store, args.port)
            print(f'quantum-solana-trader ready at http://127.0.0.1:{args.port} • mainnet disabled in dashboard', flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.runtime.stop_event.set()
                if server.runtime.thread:
                    server.runtime.thread.join(timeout=20)
                server.server_close()
        elif args.command == 'demo':
            source = 'synthetic:stress:v1'
            store.ingest(source, synthetic())
            engine = replay(store, source, 'demo')
            print(json.dumps(metrics(engine.state), indent=2))
        elif args.command == 'download':
            if not 1 <= args.days <= 365:
                raise ValueError('Days must be between 1 and 365')
            end = int(time.time()) // INTERVALS[args.interval] * INTERVALS[args.interval]
            progress = lambda p, n: print(f'{p:.0%} / {n:,} new candles', flush=True)
            if args.provider == 'dex':
                if args.interval != '1m':
                    raise ValueError('DEX history currently supports 1m only')
                print(download_dex(store, end - args.days * 86400, end, args.pool, progress))
            else:
                print(download(store, end - args.days * 86400, end, args.interval, progress))
        elif args.command == 'scan-dex':
            result = scan_dexes(args.usdc)
            store.log('system', 'dex_scan', result)
            print(json.dumps(result, indent=2))
        elif args.command == 'import-csv':
            print(import_csv(store, args.path, args.source))
        elif args.command in ('train', 'evaluate'):
            session = args.session or args.command + '-' + str(time.time_ns())
            policy = Policy(interval=INTERVALS[args.interval])
            if args.command == 'train':
                print(json.dumps(metrics(replay(store, args.source, session, policy).state), indent=2))
                print('Checkpoint:', session)
            else:
                result = walk_forward(store, args.source, session, policy)
                path = Path('data') / (session.replace('/', '_').replace('\\', '_') + '-evaluation.json')
                path.parent.mkdir(exist_ok=True)
                path.write_text(json.dumps(result, indent=2), encoding='utf-8')
                print(json.dumps(result, indent=2))
                print('Report:', path)
        elif args.command == 'paper':
            from .runtime import Runtime
            runtime = Runtime(store)
            signal.signal(signal.SIGINT, lambda *_: runtime.stop_event.set())
            runtime._paper({'jev': args.jev, 'laya': args.laya, 'feed': args.feed, 'pool': args.pool,
                            'checkpoint': args.checkpoint, 'session': args.session})
        elif args.command == 'screen':
            print(json.dumps(token_screen(args.mint), indent=2))
        elif args.command == 'devnet-check':
            print(json.dumps({'cluster': 'devnet', 'version': rpc('getVersion', [], devnet=True),
                'slot': rpc('getSlot', [{'commitment': 'confirmed'}], devnet=True),
                'note': 'Connectivity only; paper mode uses market observations, not devnet liquidity.'}, indent=2))
        elif args.command == 'devnet-exercise':
            from .devnet import exercise
            print(json.dumps(exercise(store), indent=2))
        elif args.command in ('swap', 'reconcile'):
            from .live import LiveExecutor
            executor = LiveExecutor(store)
            result = executor.reconcile() if args.command == 'reconcile' else executor.execute(
                args.evidence_session, args.side, args.usdc, args.keyfile, args.ack)
            print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as error:
        raise SystemExit(str(error)) from None
