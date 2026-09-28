"""Small CPU model. Features and labels have explicit event-time ordering."""
import math
import statistics
from dataclasses import asdict, dataclass


def finite(value, low=-1e15, high=1e15):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a number")
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError("Number outside permitted range")
    return result


def clip(value, low=-3., high=3.):
    return max(low, min(high, value))


@dataclass(frozen=True)
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def parse(cls, data):
        ts = finite(data['ts'], 1, 4102444800)
        if ts != int(ts):
            raise ValueError("Timestamp must be integer UTC seconds")
        prices = [finite(data[k], 1e-12, 1e9) for k in ('open', 'high', 'low', 'close')]
        candle = cls(int(ts), *prices, finite(data['volume'], 0, 1e18))
        if not candle.low <= min(candle.open, candle.close) <= max(candle.open, candle.close) <= candle.high:
            raise ValueError("Invalid OHLC range")
        return candle

    def to_dict(self):
        return asdict(self)


def features(history):
    if len(history) < 32:
        return None
    closes = [c['close'] for c in history[-32:]]
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    vol = max(statistics.pstdev(returns), 1e-5)
    ewma = returns[0] ** 2
    for change in returns[1:]:
        ewma = .94 * ewma + .06 * change ** 2
    tail_count = max(2, math.ceil(len(returns) * .05))
    expected_shortfall = max(0., -statistics.fmean(sorted(returns)[:tail_count]))
    mean = statistics.fmean(closes[-20:])
    spread = max(statistics.pstdev(closes[-20:]), closes[-1] * 1e-5)
    trend = math.log(closes[-1] / closes[-9]) / (vol * math.sqrt(8))
    reversion = (mean - closes[-1]) / spread
    breakout = (closes[-1] - max(closes[-21:-1])) / spread
    volumes = [c['volume'] for c in history[-20:]]
    relative_volume = volumes[-1] / max(statistics.fmean(volumes), 1e-9) - 1
    x = [1., clip(returns[-1] / vol), clip(trend), clip(reversion), clip(breakout), clip(relative_volume)]
    return {'x': x, 'volatility': vol, 'ewma_volatility': math.sqrt(ewma),
            'expected_shortfall_95': expected_shortfall, 'trend': trend, 'reversion': reversion,
            'breakout': breakout, 'regime': 'volatile' if vol > .025 else 'trend' if abs(trend) > 1.5 else 'range'}


def new_model():
    return {'weights': [0.] * 6, 'n': 0, 'correct': 0, 'brier': 0., 'recent_loss': [],
            'experts': [0., 0., 0.], 'bins': [[0, 0., 0] for _ in range(10)], 'drift': False}


def predict(model, feature):
    score = sum(a * b for a, b in zip(model['weights'], feature['x']))
    probability = 1 / (1 + math.exp(-clip(score, -20, 20)))
    experts = [float(feature['trend'] > .7), float(feature['reversion'] > 1), float(feature['breakout'] > 0)]
    weights = [math.exp(clip(w, -8, 8)) for w in model['experts']]
    votes = sum(a * b for a, b in zip(experts, weights)) / sum(weights)
    return probability, experts, votes


def learn(model, pending, close, costs):
    outcome = math.log(close / pending['price'])
    label = int(outcome > costs)
    probability = pending['p']
    loss = (probability - label) ** 2
    rate = .03 / math.sqrt(1 + model['n'] / 5000)
    for i, value in enumerate(pending['x']):
        model['weights'][i] = clip(model['weights'][i] + rate * ((label - probability) * value - .001 * model['weights'][i]), -10, 10)
    for i, exposure in enumerate(pending['experts']):
        # Reward counterfactual experts net of a conservative round-trip cost.
        reward = exposure * (outcome - costs)
        model['experts'][i] = clip(.999 * model['experts'][i] + 10 * clip(reward, -.1, .1), -8, 8)
    model['n'] += 1
    model['correct'] += int((probability >= .5) == bool(label))
    model['brier'] += loss
    bucket = model['bins'][min(9, int(probability * 10))]
    bucket[0] += 1
    bucket[1] += probability
    bucket[2] += label
    model['recent_loss'] = (model['recent_loss'] + [loss])[-200:]
    recent = model['recent_loss']
    model['drift'] = len(recent) == 200 and statistics.fmean(recent[-50:]) > statistics.fmean(recent[:150]) + .12
    return {'prediction_ts': pending['ts'], 'probability': probability, 'label': label,
            'return': outcome, 'brier': loss, 'drift': model['drift']}
