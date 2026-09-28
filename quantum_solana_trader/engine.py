"""One-market spot engine: causal decisions, delayed fills, durable learning."""
import copy
import math
import time
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_DOWN

from .model import Candle, features, finite, learn, new_model, predict


def money(value):
    return Decimal(str(value))


def amount(value):
    return str(money(value).quantize(Decimal('.000000001'), rounding=ROUND_DOWN))


@dataclass(frozen=True)
class Policy:
    capital: float = 10000.
    allocation: float = .10
    risk_per_trade: float = .005
    daily_loss: float = .02
    max_drawdown: float = .08
    stop_loss: float = .025
    take_profit: float = .05
    fee_bps: float = 10.
    slippage_bps: float = 15.
    gas_usdc: float = .005
    min_order: float = 10.
    min_probability: float = .58
    max_volatility: float = .03
    warmup: int = 100
    horizon: int = 5
    interval: int = 60

    def __post_init__(self):
        bounds = {'capital': (20, 1e8), 'allocation': (.001, .25), 'risk_per_trade': (.0001, .02),
                  'daily_loss': (.001, .10), 'max_drawdown': (.001, .30), 'stop_loss': (.001, .20),
                  'take_profit': (.001, 1), 'fee_bps': (0, 200), 'slippage_bps': (0, 300),
                  'gas_usdc': (0, 10), 'min_order': (1, 10000), 'min_probability': (.51, .99),
                  'max_volatility': (.001, .2), 'warmup': (32, 100000), 'horizon': (1, 100), 'interval': (1, 86400)}
        for key, (low, high) in bounds.items():
            value = finite(getattr(self, key), low, high)
            if key in ('warmup', 'horizon', 'interval') and value != int(value):
                raise ValueError(f'{key} must be an integer')

    @property
    def costs(self):
        return 2 * (self.fee_bps + self.slippage_bps) / 10000


def initial_state(mode, policy, source):
    return {'version': 1, 'mode': mode, 'source': source, 'policy': asdict(policy), 'model': new_model(),
            'cash': amount(policy.capital), 'position': None, 'history': [], 'pending_labels': [],
            'pending_order': None, 'last_ts': 0, 'bars': 0, 'equity': policy.capital,
            'peak': policy.capital, 'drawdown': 0., 'max_drawdown_seen': 0., 'day': 0,
            'day_start': policy.capital, 'paused': False, 'killed': False, 'halt_reason': None,
            'realized': 0., 'fees': 0., 'wins': 0, 'losses': 0, 'breakeven': 0,
            'closed': 0, 'gross_profit': 0., 'gross_loss': 0., 'last_decision': None,
            'trade_memory': {'loss_streak': 0, 'risk_scale': 1., 'cooldown_until': 0, 'by_exit': {}},
            'started': time.time(), 'observed_since': None, 'observation_count': 0,
            'latest_price': None, 'equity_curve': [], 'incidents': 0}


class Engine:
    def __init__(self, store, session, mode='paper', policy=None, source='binance:SOLUSDC:1m'):
        self.store, self.session = store, session
        self.state = store.load(session) or initial_state(mode, policy or Policy(), source)
        if self.state['version'] != 1 or self.state['mode'] != mode or self.state['source'] != source:
            raise ValueError('Session version/mode/source differs; use a new session')
        self.policy = Policy(**self.state['policy'])
        self.store.save(session, self.state)

    def control(self, action):
        state = copy.deepcopy(self.state)
        if action == 'pause':
            state['paused'] = True
            state['pending_order'] = None
        elif action == 'resume':
            if state['killed'] or state['halt_reason']:
                raise ValueError('Latched kill/risk halt cannot be resumed; review the incident first')
            state['paused'] = False
        elif action == 'kill':
            state['killed'] = True
            state['pending_order'] = {'side': 'SELL', 'reason': 'operator kill'} if state['position'] else None
        else:
            raise ValueError('Unknown control')
        self.store.save(self.session, state, [(time.time(), 'control', {'action': action})])
        self.state = state

    def incident(self, reason):
        state = copy.deepcopy(self.state)
        state['incidents'] += 1
        state['pending_order'] = None
        self.store.save(self.session, state, [(time.time(), 'incident', {'reason': reason})])
        self.state = state

    def _fill(self, state, side, price, ts, reason, events):
        p = self.policy
        price = money(price) * (1 + money(p.slippage_bps) / 10000 * (1 if side == 'BUY' else -1))
        cash, gas = money(state['cash']), money(p.gas_usdc)
        rate = money(p.fee_bps) / 10000
        if side == 'BUY':
            if state['position'] or state['paused'] or state['killed'] or state['halt_reason']:
                return
            fraction = min(p.allocation, p.risk_per_trade / p.stop_loss)
            fraction *= state.get('trade_memory', {}).get('risk_scale', 1.)
            risk_features = features(state['history'])
            if risk_features:
                # ponytail: 31 returns give noisy tail estimates; longer regime-specific windows need validation.
                stress = max(p.stop_loss, 3 * risk_features['ewma_volatility'], risk_features['expected_shortfall_95'])
                fraction = min(fraction, p.risk_per_trade / stress)
            budget = min(cash, money(state['equity']) * money(fraction))
            quantity = money(amount((budget - gas) / (price * (1 + rate))))
            if quantity <= 0 or quantity * price < money(p.min_order):
                return
            fee = quantity * price * rate + gas
            cost = quantity * price + fee
            state['cash'] = amount(cash - cost)
            state['position'] = {'quantity': str(quantity), 'entry': float(price), 'cost': str(cost),
                                 'opened': ts, 'high_water': float(price)}
            payload = {'side': side, 'price': float(price), 'quantity': str(quantity), 'fee': float(fee), 'reason': reason}
        else:
            pos = state['position']
            if not pos:
                return
            quantity = money(pos['quantity'])
            fee = quantity * price * rate + gas
            proceeds = quantity * price - fee
            pnl = proceeds - money(pos['cost'])
            state['cash'] = amount(cash + proceeds)
            state['realized'] += float(pnl)
            state['closed'] += 1
            state['wins'] += int(pnl > 0)
            state['losses'] += int(pnl < 0)
            state['breakeven'] += int(pnl == 0)
            state['gross_profit'] += max(0., float(pnl))
            state['gross_loss'] += max(0., float(-pnl))
            state['position'] = None
            payload = {'side': side, 'price': float(price), 'quantity': str(quantity), 'fee': float(fee),
                       'pnl': float(pnl), 'reason': reason, 'opened': pos['opened']}
            memory = state.setdefault('trade_memory', {'loss_streak': 0, 'risk_scale': 1., 'cooldown_until': 0, 'by_exit': {}})
            memory['loss_streak'] = memory['loss_streak'] + 1 if pnl < 0 else 0
            memory['risk_scale'] = max(.25, memory['risk_scale'] * .8) if pnl < 0 else min(1., memory['risk_scale'] + .05)
            if memory['loss_streak'] >= 3:
                memory['cooldown_until'] = state['bars'] + 30
            lesson = memory['by_exit'].setdefault(reason, {'count': 0, 'net_pnl': 0.})
            lesson['count'] += 1
            lesson['net_pnl'] += float(pnl)
            events.append((ts, 'trade_lesson', {'reason': reason, 'net_pnl': float(pnl),
                'loss_streak': memory['loss_streak'], 'risk_scale': memory['risk_scale'],
                'cooldown_until': memory['cooldown_until']}))
        state['fees'] += float(fee)
        events.append((ts, 'fill', payload))

    def step(self, candle, *, observed=False, risk_review=None):
        candle = Candle.parse(candle).to_dict()
        if observed and (self.state['mode'] != 'paper' or not 0 <= time.time() - candle['ts'] <= 90):
            raise ValueError('Live observations must be fresh completed paper-market bars')
        old = self.state
        if candle['ts'] <= old['last_ts']:
            return False
        # Copy first: a failed database commit cannot leave RAM ahead of durable state.
        state, events = copy.deepcopy(old), []
        p, ts = self.policy, candle['ts']
        gap = bool(state['last_ts'] and ts - state['last_ts'] != p.interval)
        if gap:
            state['history'], state['pending_labels'], state['pending_order'] = [], [], None
            events.append((ts, 'incident', {'reason': 'data gap; features and unfilled orders reset'}))
            state['incidents'] += 1
        # External safety failures also cancel a previously queued entry before filling.
        if risk_review and not risk_review.get('allow', False) and state['pending_order'] and state['pending_order']['side'] == 'BUY':
            state['pending_order'] = None
        state['bars'] += 1
        state['last_ts'] = ts
        if observed:
            state['observed_since'] = state['observed_since'] or time.time()
            state['observation_count'] += 1
        # Replay uses next open. Live paper uses the newly observed close, never a past open.
        execution = candle if not observed else candle | {k: candle['close'] for k in ('open', 'high', 'low')}
        order = state.pop('pending_order', None)
        state['pending_order'] = None
        if order:
            self._fill(state, order['side'], execution['open'], ts, order['reason'], events)
        pos = state['position']
        if pos:
            stop = max(pos['entry'] * (1 - p.stop_loss), pos['high_water'] * (1 - p.stop_loss))
            target = pos['entry'] * (1 + p.take_profit)
            if state['killed'] or state['halt_reason']:
                self._fill(state, 'SELL', execution['open'], ts, state['halt_reason'] or 'operator kill', events)
            elif execution['low'] <= stop:
                self._fill(state, 'SELL', min(execution['open'], stop), ts, 'protective stop (gap-aware)', events)
            elif execution['high'] >= target:
                self._fill(state, 'SELL', execution['close'] if observed else target, ts, 'profit target', events)
            else:
                pos['high_water'] = max(pos['high_water'], candle['close'])
        state['latest_price'] = candle['close']
        state['equity'] = float(money(state['cash']) + (money(state['position']['quantity']) * money(candle['close']) if state['position'] else 0))
        day = ts // 86400
        if state['day'] != day:
            state['day'], state['day_start'] = day, old['equity']
        state['peak'] = max(state['peak'], state['equity'])
        state['drawdown'] = 1 - state['equity'] / state['peak']
        state['max_drawdown_seen'] = max(state['max_drawdown_seen'], state['drawdown'])
        if state['drawdown'] >= p.max_drawdown:
            state['halt_reason'] = 'maximum drawdown reached'
        if state['equity'] <= state['day_start'] * (1 - p.daily_loss):
            state['halt_reason'] = state['halt_reason'] or 'daily loss limit reached'
        state['history'] = (state['history'] + [candle])[-64:]
        remaining = []
        for pending in state['pending_labels']:
            if pending['due'] <= state['bars']:
                events.append((ts, 'outcome', learn(state['model'], pending, candle['close'], p.costs)))
            else:
                remaining.append(pending)
        state['pending_labels'] = remaining
        feature = features(state['history'])
        reason, side, probability, votes = [], 'HOLD', None, 0.
        if feature:
            probability, experts, votes = predict(state['model'], feature)
            state['pending_labels'].append({'due': state['bars'] + p.horizon, 'ts': ts,
                'price': candle['close'], 'p': probability, 'x': feature['x'], 'experts': experts})
            if state['model']['n'] < p.warmup:
                reason.append('model warmup')
            if feature['volatility'] > p.max_volatility:
                reason.append('volatility limit')
            if state['model']['drift']:
                reason.append('prediction-loss drift')
            if state.get('trade_memory', {}).get('cooldown_until', 0) > state['bars']:
                reason.append('post-loss cooldown')
            if risk_review and not risk_review.get('allow', False):
                reason.append('external risk review blocked entry')
            if state['paused'] or state['killed'] or state['halt_reason']:
                reason.append(state['halt_reason'] or ('operator kill' if state['killed'] else 'paused'))
            if state['position'] and (probability < .42 or state['killed'] or state['halt_reason']):
                side = 'SELL'
                reason.append('exit exposure')
            elif not state['position'] and not reason and probability >= p.min_probability and votes >= .5:
                side = 'BUY'
                reason.append('probability and adaptive expert agreement')
            else:
                reason.append('abstain / maintain current exposure')
        else:
            reason.append('feature warmup')
        if side != 'HOLD':
            state['pending_order'] = {'side': side, 'reason': '; '.join(reason)}
        decision = {'side': side, 'probability': probability, 'expert_vote': votes,
                    'features': feature, 'reasons': reason, 'risk_review': risk_review,
                    'ts': ts, 'model_samples': state['model']['n'], 'mode': state['mode']}
        state['last_decision'] = decision
        state['equity_curve'] = (state['equity_curve'] + [{'ts': ts, 'equity': state['equity']}])[-500:]
        events.extend([(ts, 'decision', decision), (ts, 'equity', {'equity': state['equity'], 'drawdown': state['drawdown']})])
        self.store.save(self.session, state, events)
        self.state = state
        return True


def metrics(state):
    model = state['model']
    n = model['n'] - state.get('metric_origin_n', 0)
    positives = sum(bucket[2] for bucket in model['bins'])
    base_rate = positives / n if n else None
    calibration = sum(abs(bucket[1] - bucket[2]) for bucket in model['bins']) / n if n else None
    return {'equity': state['equity'], 'pnl': state['equity'] - state['policy']['capital'],
            'realized': state['realized'], 'fees': state['fees'], 'closed': state['closed'],
            'win_rate': state['wins'] / state['closed'] if state['closed'] else None,
            'loss_rate': state['losses'] / state['closed'] if state['closed'] else None,
            'accuracy': model['correct'] / n if n else None, 'brier': model['brier'] / n if n else None,
            'no_opportunity_accuracy': 1 - base_rate if n else None,
            'no_opportunity_brier': base_rate,
            'brier_skill_vs_no_opportunity': 1 - model['brier'] / positives if positives else None,
            'base_rate': base_rate, 'calibration_error': calibration,
            'profit_factor': state['gross_profit'] / state['gross_loss'] if state['gross_loss'] else None,
            'max_drawdown': state['max_drawdown_seen']}


def readiness(state):
    stats = metrics(state)
    # Offline time must not advance the observation-duration gate.
    days = max(0., (state['last_ts'] - state['observed_since']) / 86400) if state['observed_since'] else 0
    checks = [
        ('real live paper observations', state['mode'] == 'paper' and state['observation_count'] >= 10000),
        ('at least 30 elapsed observation days', days >= 30),
        ('at least 100 closed paper trades', state['closed'] >= 100),
        ('positive net paper PnL', stats['pnl'] > 0),
        ('profit factor at least 1.2', (stats['profit_factor'] or 0) >= 1.2),
        ('drawdown within configured limit', state['max_drawdown_seen'] < state['policy']['max_drawdown']),
        ('Brier score below 0.25', stats['brier'] is not None and stats['brier'] < .25),
        ('better Brier than always predicting no opportunity',
         stats['brier_skill_vs_no_opportunity'] is not None and stats['brier_skill_vs_no_opportunity'] > 0),
        ('no latched halt or model drift', not state['halt_reason'] and not state['killed'] and not state['model']['drift']),
    ]
    return {'eligible_for_review': all(ok for _, ok in checks),
            'checks': [{'name': name, 'passed': ok} for name, ok in checks],
            'note': 'Research gates only. Does not prove profitability or authorize mainnet.', 'days': days}
