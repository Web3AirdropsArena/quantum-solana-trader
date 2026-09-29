import base64
import copy
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from quantum_solana_trader.engine import Engine, Policy, metrics, readiness
from quantum_solana_trader.live import LiveExecutor, base58_bytes, validate_quote, validate_simulation
from quantum_solana_trader.model import Candle, features
from quantum_solana_trader.providers import SOL, USDC, TOKEN_PROGRAM, ProviderError, binance_candles, jev_review, token_screen
from quantum_solana_trader.research import synthetic, walk_forward
from quantum_solana_trader.server import make_server
from quantum_solana_trader.store import Store, engine_lock


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'test.sqlite')
        self.candles = list(synthetic(450))

    def tearDown(self):
        self.temp.cleanup()

    def engine(self, name='test'):
        return Engine(self.store, name, 'demo', source='synthetic:test')

    def test_invalid_money_and_ohlc_rejected(self):
        for changes in ({'close': float('nan')}, {'volume': -1}, {'low': 9999}, {'ts': 1.5}, {'open': True}):
            with self.assertRaises(ValueError):
                Candle.parse(self.candles[0] | changes)
        for changes in ({'allocation': .9}, {'fee_bps': -1}, {'capital': float('inf')}, {'horizon': 1.2}):
            with self.assertRaises(ValueError):
                Policy(**changes)

    def test_duplicate_ingestion_and_conflict_atomicity(self):
        self.assertEqual(self.store.ingest('a', self.candles[:3]), 3)
        self.assertEqual(self.store.ingest('a', self.candles[:3]), 0)
        with self.assertRaises(ValueError):
            self.store.ingest('a', [self.candles[3], self.candles[1] | {'volume': 123}])
        self.assertEqual(len(list(self.store.candles('a'))), 3)

    def test_restart_matches_uninterrupted_engine(self):
        full, interrupted = self.engine('full'), self.engine('resume')
        for candle in self.candles:
            full.step(candle)
        for candle in self.candles[:211]:
            interrupted.step(candle)
        resumed = self.engine('resume')
        for candle in self.candles[211:]:
            resumed.step(candle)
        a, b = copy.deepcopy(full.state), copy.deepcopy(resumed.state)
        a.pop('started'); b.pop('started')
        self.assertEqual(a, b)

    def test_future_data_cannot_change_prior_predictions(self):
        a, b = self.engine('a'), self.engine('b')
        for candle in self.candles[:150]:
            a.step(candle); b.step(candle)
        predictions = self.store.events('a', 'decision', 1000)
        for candle in self.candles[150:]:
            a.step(candle)
        shifted = [dict(c, open=c['open']*2, high=c['high']*2, low=c['low']*2, close=c['close']*2) for c in self.candles[150:]]
        for candle in shifted:
            b.step(candle)
        original = [e['data'] for e in predictions]
        historical = [e['data'] for e in self.store.events('b', 'decision', 1000) if e['ts'] <= self.candles[149]['ts']]
        self.assertEqual(original, historical)

    def test_delayed_labels_and_duplicate_bar(self):
        e = self.engine()
        for c in self.candles[:32]:
            e.step(c)
        self.assertEqual(e.state['model']['n'], 0)
        for c in self.candles[32:37]:
            e.step(c)
        self.assertEqual(e.state['model']['n'], 1)
        old = copy.deepcopy(e.state)
        self.assertFalse(e.step(self.candles[36]))
        self.assertEqual(old, e.state)

    def test_cash_costs_and_next_bar_execution(self):
        e = self.engine()
        c = dict(ts=1000, open=100, high=100, low=100, close=100, volume=1)
        e.state['pending_order'] = {'side': 'BUY', 'reason': 'test'}
        e.step(c)
        self.assertIsNotNone(e.state['position'])
        self.assertLess(e.state['equity'], e.policy.capital)
        self.assertLessEqual(float(e.state['position']['cost']), 1000)
        e.state['pending_order'] = {'side': 'SELL', 'reason': 'test'}
        e.step(c | {'ts': 1060})
        self.assertIsNone(e.state['position'])
        self.assertLess(e.state['realized'], 0)
        self.assertEqual(e.state['closed'], 1)
        self.assertAlmostEqual(e.state['equity'] - 10000, e.state['realized'], places=6)

    def test_stop_wins_ambiguous_stop_target_bar(self):
        e = self.engine()
        e.state['pending_order'] = {'side': 'BUY', 'reason': 'test'}
        e.step(dict(ts=1000, open=100, high=110, low=90, close=100, volume=1))
        fill = self.store.events('test', 'fill')[0]['data']
        self.assertIn('stop', fill['reason'])
        self.assertLess(fill['pnl'], 0)

    def test_gap_cancels_entry_and_resets_features(self):
        e = self.engine()
        for c in self.candles[:40]:
            e.step(c)
        e.state['pending_order'] = {'side': 'BUY', 'reason': 'test'}
        e.step(self.candles[45])
        self.assertIsNone(e.state['position'])
        self.assertEqual(len(e.state['history']), 1)
        self.assertEqual(e.state['pending_labels'], [])

    def test_external_rejection_cancels_queued_entry(self):
        e = self.engine()
        e.state['pending_order'] = {'side': 'BUY', 'reason': 'test'}
        e.step(self.candles[0], risk_review={'allow': False})
        self.assertIsNone(e.state['position'])

    def test_trade_losses_reduce_risk_and_preserve_lessons(self):
        e = self.engine()
        for i in range(3):
            e.state['pending_order'] = {'side': 'BUY', 'reason': 'test'}
            e.step(dict(ts=1000+i*120, open=100, low=100, high=100, close=100, volume=1))
            e.state['pending_order'] = {'side': 'SELL', 'reason': 'test loss'}
            e.step(dict(ts=1060+i*120, open=99, low=99, high=99, close=99, volume=1))
        memory = self.engine().state['trade_memory']
        self.assertEqual(memory['loss_streak'], 3)
        self.assertLess(memory['risk_scale'], 1)
        self.assertGreater(memory['cooldown_until'], e.state['bars'])
        self.assertEqual(memory['by_exit']['test loss']['count'], 3)

    def test_pause_and_kill_persist_and_block_resume(self):
        e = self.engine()
        e.state['pending_order'] = {'side': 'BUY', 'reason': 'test'}
        e.control('pause')
        self.assertIsNone(e.state['pending_order'])
        e.control('resume')
        e.control('kill')
        with self.assertRaises(ValueError):
            self.engine().control('resume')

    def test_failed_commit_does_not_advance_memory(self):
        e = self.engine()
        previous = copy.deepcopy(e.state)
        with patch.object(self.store, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                e.step(self.candles[0])
        self.assertEqual(previous, e.state)
        self.assertEqual(previous, self.store.load('test'))

    def test_synthetic_cannot_pass_live_gate(self):
        e = self.engine()
        for c in self.candles:
            e.step(c)
        self.assertFalse(readiness(e.state)['eligible_for_review'])
        with self.assertRaises(ValueError):
            e.step(self.candles[-1] | {'ts': int(time.time())-1}, observed=True)

    def test_stale_live_observations_rejected(self):
        e = Engine(self.store, 'paper')
        with self.assertRaises(ValueError):
            e.step(self.candles[0], observed=True)
        self.assertEqual(e.state['observation_count'], 0)

    def test_readiness_rejects_weak_class_imbalance_and_offline_aging(self):
        state = self.engine().state
        state.update(mode='paper', observation_count=10000, observed_since=1000000,
                     last_ts=1000060, closed=100, equity=11000, gross_profit=200, gross_loss=100)
        from quantum_solana_trader.dex import source_name
        state['source'] = source_name()
        state['model'].update(n=1000, correct=990, brier=20.)
        state['model']['bins'][0] = [1000, 20., 10]
        result = readiness(state)
        self.assertAlmostEqual(result['days'], 60 / 86400)
        self.assertFalse(result['eligible_for_review'])
        stats = metrics(state)
        self.assertEqual(stats['no_opportunity_accuracy'], .99)
        self.assertEqual(stats['no_opportunity_brier'], .01)
        self.assertEqual(stats['brier_skill_vs_no_opportunity'], -1.)
        state['last_ts'] = state['observed_since'] + 31 * 86400
        self.assertFalse(readiness(state)['eligible_for_review'])
        state['model']['brier'] = 5.
        self.assertEqual(metrics(state)['brier_skill_vs_no_opportunity'], .5)
        self.assertTrue(readiness(state)['eligible_for_review'])
        state['model']['bins'][0][2] = 0
        self.assertIsNone(metrics(state)['brier_skill_vs_no_opportunity'])

    def test_live_paper_never_fills_at_an_unavailable_past_open(self):
        e = Engine(self.store, 'paper')
        e.state['pending_order'] = {'side': 'BUY', 'reason': 'test'}
        e.step(dict(ts=int(time.time())-2, open=50, low=49, high=101, close=100, volume=10), observed=True)
        fills = self.store.events('paper', 'fill')
        self.assertEqual(len(fills), 1)
        self.assertGreater(fills[0]['data']['price'], 100)
        self.assertIsNotNone(e.state['position'])

    def test_backup_and_export_survive_reopen(self):
        e = self.engine(); e.step(self.candles[0])
        target = Path(self.temp.name) / 'backup.sqlite'
        self.store.backup(target)
        restored = Store(target)
        self.assertEqual(restored.load('test'), self.store.load('test'))
        rows = [json.loads(line) for line in restored.export('test')]
        self.assertEqual([r['kind'] for r in rows], ['decision', 'equity'])

    def test_single_engine_os_lock(self):
        with engine_lock(self.store.path):
            with self.assertRaises(OSError):
                with engine_lock(self.store.path):
                    pass

    def test_walk_forward_has_embargo_and_real_denominators(self):
        self.store.ingest('synthetic:evaluation', synthetic(650))
        result = walk_forward(self.store, 'synthetic:evaluation', 'eval')
        self.assertTrue(result['synthetic'])
        self.assertEqual(len(result['folds']), 3)
        for fold in result['folds']:
            self.assertGreater(fold['test_start'], 1735689600 + fold['train_bars']*60)
            self.assertTrue(0 <= fold['accuracy'] <= 1)
        with self.assertRaises(ValueError):
            walk_forward(self.store, 'synthetic:evaluation', 'eval')


class ProviderTests(unittest.TestCase):
    def test_binance_excludes_unclosed_candles(self):
        now = int(time.time())
        rows = [[0, 1, 2, 1, 2, 5, (now-5)*1000-1], [0, 1, 2, 1, 2, 5, (now+60)*1000-1]]
        with patch('quantum_solana_trader.providers.request_json', return_value=rows):
            self.assertEqual(len(binance_candles()), 1)

    def test_unknown_authority_and_program_rejected(self):
        account = {'value': {'owner': 'untrusted', 'data': {'parsed': {'type': 'mint', 'info': {'mintAuthority': 'issuer', 'decimals': 6, 'supply': '1000'}}}}}
        with patch('quantum_solana_trader.providers.rpc', side_effect=[account, {'value': [{'amount': '900'}]}]), patch('quantum_solana_trader.providers.jupiter_order', side_effect=ProviderError('missing')):
            result = token_screen('11111111111111111111111111111111')
        self.assertFalse(result['allow'])
        self.assertGreaterEqual(len(result['reasons']), 4)

    def test_jev_fail_closed_on_malformed_probabilities(self):
        response = {'model': 'jev-1.13.0', 'answers': {'entry_risk': {'type': 'choice', 'choice': 'allow', 'confidence': .95, 'probabilities': {'allow': .9, 'block': .05, 'review': .05}}}}
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'test', 'QST_ALLOW_HOSTED_AI': '1'}), patch('quantum_solana_trader.providers.request_json', return_value=response):
            self.assertTrue(jev_review({})['allow'])
            response['answers']['entry_risk']['probabilities']['block'] = .9
            with self.assertRaises(ProviderError):
                jev_review({})

    def test_quote_cannot_change_mint_amount_or_slippage(self):
        quote = dict(inputMint=USDC, outputMint=SOL, inAmount='100', outAmount='1000', otherAmountThreshold='995', slippageBps=50, transaction='test', requestId='test')
        self.assertEqual(validate_quote(quote, USDC, SOL, 100), 995)
        for change in ({'inputMint': SOL}, {'inAmount': '101'}, {'slippageBps': 500}, {'otherAmountThreshold': 0}):
            with self.assertRaises(ValueError):
                validate_quote(quote | change, USDC, SOL, 100)

    def test_wallet_simulation_rejects_drain_and_authority_change(self):
        wallet = SOL  # fixed 32-byte public key for offline fixture only
        raw = bytearray(165); raw[:32] = base58_bytes(USDC); raw[32:64] = base58_bytes(wallet); raw[64:72] = (100_000_000).to_bytes(8, 'little')
        token = {'owner': TOKEN_PROGRAM, 'lamports': 2039280, 'data': [base64.b64encode(raw).decode(), 'base64']}
        native = {'owner': '11111111111111111111111111111111', 'lamports': 100_000_000, 'executable': False}
        before = [native, token]
        changed = bytearray(raw); changed[64:72] = (90_000_000).to_bytes(8, 'little')
        after = [native | {'lamports': 160_000_000}, token | {'data': [base64.b64encode(changed).decode(), 'base64']}]
        validate_simulation([wallet, 'token'], before, after, wallet, 'BUY', 10_000_000, 60_000_000)
        with self.assertRaises(ValueError):
            validate_simulation([wallet, 'token'], before, [native, after[1]], wallet, 'BUY', 10_000_000, 60_000_000)
        changed[72] = 1
        after[1]['data'][0] = base64.b64encode(changed).decode()
        with self.assertRaises(ValueError):
            validate_simulation([wallet, 'token'], before, after, wallet, 'BUY', 10_000_000, 60_000_000)


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'boundary.sqlite')

    def tearDown(self):
        self.temp.cleanup()

    def test_named_paper_session_resume_and_validation(self):
        from quantum_solana_trader.runtime import Runtime
        runtime = Runtime(self.store)
        runtime.stop_event.set()
        runtime._paper({'session': 'paper-check'})
        runtime.engine.control('kill')
        resumed = Runtime(self.store)
        resumed.stop_event.set()
        resumed._paper({'session': 'paper-check'})
        self.assertEqual(resumed.engine.state, runtime.engine.state)
        for name in ('../wallet', 'bad name', 'a' * 65, 12):
            with self.assertRaises(ValueError):
                resumed._paper({'session': name})

    def test_unknown_transaction_stays_blocking_until_finalized(self):
        live = LiveExecutor(self.store)
        live.record('id', 'unknown', {'signature': 'sig', 'notional_usdc': '10'})
        with patch('quantum_solana_trader.live.rpc', return_value={'value': [None]}):
            self.assertEqual(live.reconcile()[0]['status'], 'unknown')
        self.assertEqual(len(live.pending()), 1)
        with patch('quantum_solana_trader.live.rpc', side_effect=[{'value': [{'confirmationStatus': 'finalized', 'err': None}]}, {'meta': {'fee': 5000}}]):
            self.assertEqual(live.reconcile()[0]['status'], 'confirmed')
        self.assertEqual(live.pending(), [])

    def test_mainnet_is_off_without_explicit_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                LiveExecutor(self.store).execute('paper', 'BUY', '10', '/absent', 'I_ACCEPT_MAINNET_LOSS')

    def test_http_host_csrf_path_and_input_boundaries(self):
        server = make_server(self.store, 0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def req(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
            connection.request(method, path, body, {'Host': 'localhost:0', **(headers or {})})
            response = connection.getresponse(); result = (response.status, response.read()); connection.close(); return result
        try:
            status, payload = req('GET', '/api/status')
            self.assertEqual(status, 200)
            csrf = json.loads(payload)['csrf']
            self.assertEqual(req('GET', '/api/status', headers={'Host': 'attacker.example'})[0], 403)
            self.assertEqual(req('GET', '/api/status', headers={'Origin': 'https://attacker.example'})[0], 403)
            self.assertEqual(req('GET', '/../../.env')[0], 404)
            self.assertEqual(req('POST', '/api/control', '{}')[0], 403)
            self.assertEqual(req('POST', '/api/control', '[]', {'X-QST-Token': csrf})[0], 400)
            self.assertEqual(req('POST', '/api/run', '{"operation":"swap"}', {'X-QST-Token': csrf})[0], 400)
        finally:
            server.shutdown(); server.server_close(); thread.join(3)


if __name__ == '__main__':
    unittest.main()
