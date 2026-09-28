"""Reproducible chronological evaluation and labeled synthetic stress data."""
import copy
import hashlib
import math
import random
import time

from .engine import Engine, Policy, initial_state, metrics
from .store import encode


def synthetic(count=1800, seed=7):
    rng, price = random.Random(seed), 140.
    start = 1735689600
    for i in range(count):
        opening = price
        trend = .0018 * math.sin(i / 110)
        shock = -.12 if i in (600, 1400) else 0
        price *= math.exp(trend + rng.gauss(0, .003) + shock)
        yield dict(ts=start + (i + 1) * 60, open=opening, high=max(opening, price) * 1.001,
                   low=min(opening, price) * .999, close=price, volume=rng.uniform(1000, 5000))


def replay(store, source, session, policy=None, progress=None, stop=None):
    mode = 'demo' if source.startswith('synthetic:') else 'replay'
    engine = Engine(store, session, mode, policy, source)
    count = next((d['count'] for d in store.datasets() if d['source'] == source), 0)
    if count == 0:
        raise ValueError('Dataset is empty')
    begin = time.monotonic()
    for candle in store.candles(source, engine.state['last_ts']):
        if stop and stop.is_set():
            break
        engine.step(candle)
        if progress and engine.state['bars'] % 100 == 0:
            fraction = engine.state['bars'] / count
            progress(fraction, max(0, (time.monotonic() - begin) * (1 - fraction) / max(fraction, .001)))
    return engine


def walk_forward(store, source, prefix, policy=None, progress=None, stop=None):
    policy = policy or Policy()
    data = list(store.candles(source))
    if len(data) < 600:
        raise ValueError('At least 600 candles required; much longer samples are needed for meaningful research')
    # Three chronological folds with an expanding training set and an embargo.
    reports = []
    for fold, fraction in enumerate((.5, .65, .8)):
        split = int(len(data) * fraction)
        end = min(len(data), split + int(len(data) * .15))
        train_id, test_id = f'{prefix}-train-{fold}', f'{prefix}-test-{fold}'
        if store.load(train_id) or store.load(test_id):
            raise ValueError('Evaluation prefix already exists; use a fresh prefix to prevent contamination')
        train = Engine(store, train_id, 'research', policy, source)
        for i, candle in enumerate(data[:split]):
            if stop and stop.is_set():
                raise ValueError('Evaluation interrupted; partial checkpoints retained, no report issued')
            train.step(candle)
            if progress and i % 100 == 0:
                progress((fold + .8 * i / split) / 3, fold)
        state = initial_state('research', policy, source)
        state['model'] = copy.deepcopy(train.state['model'])
        # Metrics count only genuinely out-of-sample labels, not training labels.
        state['model'].update(correct=0, brier=0., bins=[[0, 0., 0] for _ in range(10)])
        trained_n = state['model']['n']
        state['metric_origin_n'] = trained_n
        test = Engine(store, test_id, 'research', policy, source)
        store.save(test_id, state)
        test.state = state
        test_data = data[split + policy.horizon:end]
        for candle in test_data:
            if stop and stop.is_set():
                raise ValueError('Evaluation interrupted; partial checkpoints retained, no report issued')
            test.step(candle)
        report = metrics(test.state)
        n = test.state['model']['n'] - trained_n
        report.update(accuracy=test.state['model']['correct'] / n if n else None,
                      brier=test.state['model']['brier'] / n if n else None,
                      fold=fold, train_bars=split, test_bars=len(test_data),
                      test_start=test_data[0]['ts'], test_end=test_data[-1]['ts'],
                      buy_hold_return=test_data[-1]['close'] / test_data[0]['open'] - 1,
                      strategy_return=report['pnl'] / policy.capital,
                      labeled_samples=n, closed_trade_win_interval=wilson(test.state['wins'], test.state['closed']))
        reports.append(report)
    result = {'source': source, 'synthetic': source.startswith('synthetic:'),
              'dataset_sha256': hashlib.sha256(encode(data).encode()).hexdigest(), 'folds': reports,
              'policy': policy.__dict__, 'created': time.time(),
              'method': 'expanding train / embargo / prequential adaptive test; no test-based parameter selection',
              'limitations': ['CEX candles do not model DEX liquidity', 'OHLC stops use pessimistic ordering',
                             'correlated observations; Wilson interval is descriptive, not a guarantee',
                             'no Sharpe annualization inferred from irregular samples']}
    store.log(prefix, 'evaluation', result)
    return result


def wilson(wins, total):
    if not total:
        return None
    z, p = 1.96, wins / total
    center = (p + z * z / (2 * total)) / (1 + z * z / total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [max(0., center - margin), min(1., center + margin)]
