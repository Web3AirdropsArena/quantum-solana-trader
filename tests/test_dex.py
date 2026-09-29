import copy
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from quantum_solana_trader.dex import DEFAULT_POOL, dex_candles, download_dex, scan_dexes, raydium_quote, source_name
from quantum_solana_trader.local_review import LocalReviewer, validate_answer
from quantum_solana_trader.live import read_wallet_file
from quantum_solana_trader.providers import SOL, USDC, ProviderError, jev_review
from quantum_solana_trader.runtime import Runtime
from quantum_solana_trader.store import Store


def pool(dex, address, price=100.):
    return {'chainId': 'solana', 'dexId': dex, 'pairAddress': address,
            'baseToken': {'address': SOL}, 'quoteToken': {'address': USDC},
            'priceNative': str(price), 'liquidity': {'usd': 1_000_000},
            'pairCreatedAt': 1600000000000}


def quote(input_mint, output_mint, atoms):
    out = atoms * 10 if input_mint == USDC else atoms // 10
    return {'out': out, 'minimum_out': out * 9950 // 10000, 'impact_pct': .01, 'pools': [DEFAULT_POOL]}


class DexTests(unittest.TestCase):
    def test_dex_candles_require_mints_completed_times_and_valid_ohlc(self):
        payload = {'meta': {'base': {'address': SOL}, 'quote': {'address': USDC}},
                   'data': {'attributes': {'ohlcv_list': [[960, 100, 102, 99, 101, 3], [900, 99, 101, 98, 100, 5]]}}}
        with patch('quantum_solana_trader.dex.time.time', return_value=1000), patch('quantum_solana_trader.dex.request_json', return_value=payload):
            self.assertEqual([c['ts'] for c in dex_candles(limit=3)], [960])
            payload['meta']['quote']['address'] = SOL
            with self.assertRaises(ProviderError):
                dex_candles(limit=3)
            payload['meta']['quote']['address'] = USDC
            payload['data']['attributes']['ohlcv_list'][1][0] = 901
            with self.assertRaises(ProviderError):
                dex_candles(limit=3)

    def test_backward_download_retains_every_boundary_bar(self):
        def candle(ts):
            return dict(ts=ts, open=100, high=100, low=100, close=100, volume=1)
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'test.sqlite')
            with patch('quantum_solana_trader.dex.dex_candles', side_effect=[[candle(900), candle(960)], [candle(780), candle(840)]]) as get, patch('quantum_solana_trader.dex.time.sleep'):
                _, count = download_dex(store, 720, 960)
            self.assertEqual(count, 4)
            self.assertEqual(get.call_args_list[1].kwargs['before'], 839)
            self.assertEqual([c['ts'] for c in store.candles(source_name())], [780, 840, 900, 960])

    def test_scan_blocks_discrepancy_liquidity_shock_and_missing_quotes(self):
        rows = [pool('orca', DEFAULT_POOL), pool('raydium', SOL, 100.1)]
        with patch('quantum_solana_trader.dex.request_json', return_value=rows), patch('quantum_solana_trader.dex.raydium_quote', side_effect=quote):
            good = scan_dexes(reference_price=100.)
            self.assertTrue(good['allow'])
            self.assertFalse(good['arbitrage_executable'])
            rows[0]['liquidity']['usd'] = 500000
            self.assertFalse(scan_dexes(previous=good)['allow'])
            rows[0]['liquidity']['usd'] = 1000000
            rows[1]['priceNative'] = '120'
            self.assertFalse(scan_dexes()['allow'])
            rows[1]['priceNative'] = '100.1'
            with patch('quantum_solana_trader.dex.raydium_quote', side_effect=ProviderError('unavailable')):
                self.assertFalse(scan_dexes()['allow'])

    def test_scan_does_not_trust_symbols_young_pools_or_duplicate_venues(self):
        rows = [pool('orca', DEFAULT_POOL), pool('orca', SOL)]
        deceptive = pool('raydium', USDC)
        deceptive['baseToken'] = {'address': USDC, 'symbol': 'SOL'}
        rows.append(deceptive)
        with patch('quantum_solana_trader.dex.request_json', return_value=rows), patch('quantum_solana_trader.dex.raydium_quote', side_effect=quote):
            self.assertFalse(scan_dexes()['allow'])
            rows[1]['dexId'] = 'raydium'
            rows[1]['pairCreatedAt'] = int(time.time() * 1000) - 1000
            self.assertFalse(scan_dexes()['allow'])

    def test_quote_rejects_tampered_mint_minimum_and_intermediate_token(self):
        data = dict(inputMint=USDC, outputMint=SOL, inputAmount='10000000', outputAmount='100000000',
                    otherAmountThreshold='99500000', slippageBps=50, priceImpactPct=0,
                    routePlan=[dict(poolId=DEFAULT_POOL, inputMint=USDC, outputMint=SOL, feeAmount='1000')])
        for change in ({'inputMint': SOL}, {'otherAmountThreshold': '1'}, {'slippageBps': 100}):
            with patch('quantum_solana_trader.dex.request_json', return_value={'success': True, 'data': data | change}):
                with self.assertRaises(ProviderError):
                    raydium_quote(USDC, SOL, 10000000)
        data['routePlan'][0]['outputMint'] = DEFAULT_POOL
        with patch('quantum_solana_trader.dex.request_json', return_value={'success': True, 'data': data}):
            with self.assertRaises(ProviderError):
                raydium_quote(USDC, SOL, 10000000)

    def test_hosted_ai_disabled_even_with_a_key(self):
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': 'unused'}, clear=True), patch('quantum_solana_trader.providers.request_json') as network:
            with self.assertRaises(ProviderError):
                jev_review({})
            network.assert_not_called()

    def test_local_laya_confidence_and_missing_assets_fail_closed(self):
        answer = {'type': 'choice', 'choice': 'allow', 'probabilities': {'allow': .9, 'block': .05, 'review': .05}, 'answer_confidence': .9}
        self.assertTrue(validate_answer({'answers': {'entry_risk': answer}})['allow'])
        answer['answer_confidence'] = .99
        with self.assertRaises(ProviderError):
            validate_answer({'answers': {'entry_risk': answer}})
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ProviderError):
                LocalReviewer().review({})

    def test_default_paper_uses_dex_and_never_calls_binance_or_hosted_ai(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Runtime(Store(Path(folder) / 'paper.sqlite'))
            ts = int(time.time()) // 60 * 60
            candle = dict(ts=ts, open=100, high=100, low=100, close=100, volume=1)
            def capture(*args, **kwargs):
                runtime.stop_event.set()
                return [candle]
            scan = dict(allow=True, reasons=[], pools=[], checked_at=time.time())
            with patch('quantum_solana_trader.runtime.dex_candles', side_effect=capture), patch('quantum_solana_trader.runtime.scan_dexes', return_value=scan), patch('quantum_solana_trader.runtime.token_screen', return_value={'allow': True}), patch('quantum_solana_trader.runtime.binance_candles') as cex, patch('quantum_solana_trader.runtime.jev_review') as hosted:
                runtime._paper({'session': 'dex-test'})
                cex.assert_not_called(); hosted.assert_not_called()
            self.assertEqual(runtime.engine.state['source'], source_name())
            self.assertEqual(runtime.engine.state['observation_count'], 1)
            saved = copy.deepcopy(runtime.engine.state)
            resumed = Runtime(runtime.store); resumed.stop_event.set()
            resumed._paper({'session': 'dex-test'})
            self.assertEqual(resumed.engine.state, saved)

    def test_wallet_file_never_loaded_from_project_or_without_path(self):
        with self.assertRaises(ValueError):
            read_wallet_file(None)
        with self.assertRaises(ValueError):
            read_wallet_file(Path(__file__).resolve().parent.parent / 'wallet.json')


if __name__ == '__main__':
    unittest.main()
