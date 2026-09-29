"""Keyless DEX research data. Indicative prices never authorize arbitrage execution."""
import statistics
import time
import urllib.parse

from .model import Candle, finite
from .providers import SOL, USDC, ProviderError, mint_address, request_json

# Established Orca SOL/USDC pool; addresses and response token metadata are checked.
DEFAULT_POOL = 'Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE'
DEXES = {'orca', 'raydium', 'meteora'}


def source_name(pool=DEFAULT_POOL):
    return 'dex:geckoterminal:' + mint_address(pool) + ':1m'


def dex_candles(pool=DEFAULT_POOL, before=None, limit=1000):
    mint_address(pool)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('DEX candle page size must be 1-1000')
    params = dict(aggregate=1, limit=limit, currency='token', token='base')
    if before is not None:
        params['before_timestamp'] = int(before)
    response = request_json('https://api.geckoterminal.com/api/v2/networks/solana/pools/' + pool +
                            '/ohlcv/minute?' + urllib.parse.urlencode(params))
    meta = response.get('meta', {})
    if meta.get('base', {}).get('address') != SOL or meta.get('quote', {}).get('address') != USDC:
        raise ProviderError('DEX pool must have canonical SOL base and USDC quote')
    rows = response['data']['attributes']['ohlcv_list']
    if not isinstance(rows, list) or len(rows) > limit:
        raise ProviderError('Malformed DEX candle page')
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, list) or len(row) != 6:
            raise ProviderError('Malformed DEX OHLCV row')
        opening = finite(row[0], 1, 4102444800)
        if opening != int(opening) or opening % 60:
            raise ProviderError('DEX candle timestamp is not a minute boundary')
        close_ts = int(opening) + 60
        if close_ts > time.time():
            continue
        if close_ts in seen:
            raise ProviderError('Duplicate DEX candle timestamp')
        seen.add(close_ts)
        result.append(Candle.parse(dict(ts=close_ts, open=row[1], high=row[2], low=row[3],
                                       close=row[4], volume=row[5])).to_dict())
    return sorted(result, key=lambda candle: candle['ts'])


def download_dex(store, start, end, pool=DEFAULT_POOL, progress=None, stop=None):
    if not 0 < start < end <= time.time() + 1:
        raise ValueError('DEX download requires completed past timestamps')
    source, cursor, count = source_name(pool), int(end), 0
    while cursor > start:
        if stop and stop.is_set():
            break
        rows = dex_candles(pool, before=cursor, limit=1000)
        if not rows:
            break
        selected = [c for c in rows if start < c['ts'] <= end]
        count += store.ingest(source, selected)
        following = rows[0]['ts'] - 61  # API timestamps identify bar opening times.
        if following >= cursor:
            raise ProviderError('DEX pagination did not advance')
        cursor = following
        if progress:
            progress(min(1., (end - cursor) / (end - start)), count)
        if cursor <= start:
            break
        # Stay below the public service's typical 30-request/minute allowance.
        if stop:
            if stop.wait(2.2):
                break
        else:
            time.sleep(2.2)
    return source, count


def raydium_quote(input_mint, output_mint, atoms):
    if {input_mint, output_mint} != {SOL, USDC} or type(atoms) is not int or not 0 < atoms < 2 ** 64:
        raise ValueError('Quote requires canonical SOL/USDC and integer base units')
    params = dict(inputMint=input_mint, outputMint=output_mint, amount=str(atoms), slippageBps=50, txVersion='V0')
    result = request_json('https://transaction-v1.raydium.io/compute/swap-base-in?' + urllib.parse.urlencode(params))
    if not result.get('success') or not isinstance(result.get('data'), dict):
        raise ProviderError('Raydium has no quote')
    quote = result['data']
    if (quote.get('inputMint'), quote.get('outputMint'), quote.get('inputAmount')) != (input_mint, output_mint, str(atoms)):
        raise ProviderError('Raydium quote changed mint or amount')
    out, threshold = int(quote.get('outputAmount', 0)), int(quote.get('otherAmountThreshold', 0))
    if quote.get('slippageBps') != 50 or not 0 < threshold <= out or threshold < out * 9950 // 10000:
        raise ProviderError('Raydium quote has unsafe minimum output')
    impact = finite(quote.get('priceImpactPct'), 0, 100)
    route = quote.get('routePlan')
    if not isinstance(route, list) or not route or len(route) > 4:
        raise ProviderError('Raydium route is missing or too complex')
    for hop in route:
        mint_address(hop['poolId'])
        if hop.get('inputMint') not in (SOL, USDC) or hop.get('outputMint') not in (SOL, USDC):
            raise ProviderError('Raydium route includes an unreviewed intermediate token')
        if int(hop.get('feeAmount', -1)) < 0:
            raise ProviderError('Raydium route fee missing')
    return {'out': out, 'minimum_out': threshold, 'impact_pct': impact,
            'pools': [h['poolId'] for h in route]}


def scan_dexes(notional=10., previous=None, reference_price=None):
    """Compare identified pools, then stress a Raydium round trip at the given size."""
    notional = finite(notional, 1, 10000)
    start = time.time()
    rows = request_json('https://api.dexscreener.com/token-pairs/v1/solana/' + SOL)
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ProviderError('Malformed DEX pool inventory')
    pools, rejected, seen = [], [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ProviderError('Malformed DEX pool row')
        if row.get('chainId') != 'solana' or row.get('dexId') not in DEXES:
            continue
        if row.get('baseToken', {}).get('address') != SOL or row.get('quoteToken', {}).get('address') != USDC:
            continue
        try:
            pool = mint_address(row['pairAddress'])
            if pool in seen:
                continue
            seen.add(pool)
            price = finite(row['priceNative'], .01, 1e6)
            liquidity = finite(row['liquidity']['usd'], 0, 1e15)
            created = finite(row.get('pairCreatedAt'), 1, start * 1000) / 1000
            if liquidity < 250000 or start - created < 7 * 86400:
                rejected.append({'pool': pool, 'reason': 'low liquidity or pool younger than 7 days'})
                continue
            pools.append({'pool': pool, 'dex': row['dexId'], 'price': price, 'liquidity_usd': liquidity})
        except (ValueError, KeyError, TypeError):
            rejected.append({'reason': 'invalid or missing pool evidence'})
    # One deepest pool per DEX prevents dozens of pools on one venue dominating the reference.
    venues = {}
    for pool in pools:
        if pool['liquidity_usd'] > venues.get(pool['dex'], {}).get('liquidity_usd', -1):
            venues[pool['dex']] = pool
    reasons = []
    if len(venues) < 2:
        reasons.append('fewer than two independently named DEX venues')
    selected = list(venues.values())
    median = statistics.median(p['price'] for p in selected) if selected else None
    spread = (max(p['price'] for p in selected) / min(p['price'] for p in selected) - 1) if selected else None
    if spread is not None and spread > .02:
        reasons.append('cross-DEX discrepancy above 2%; possible stale data or manipulation')
    if reference_price is not None and (median is None or abs(finite(reference_price, .01, 1e6) / median - 1) > .01):
        reasons.append('candle/reference price differs from DEX median by more than 1%')
    if previous and 0 <= start - previous.get('checked_at', 0) <= 300:
        old = {p['pool']: p['liquidity_usd'] for p in previous.get('pools', [])}
        if any(p['pool'] in old and p['liquidity_usd'] < old[p['pool']] * .8 for p in selected):
            reasons.append('observed pool liquidity fell more than 20%')
    quote = None
    try:
        atoms = int(notional * 1_000_000)
        buy = raydium_quote(USDC, SOL, atoms)
        # Conservative second leg uses first-leg minimum received amount.
        sell = raydium_quote(SOL, USDC, buy['minimum_out'])
        returned = sell['minimum_out'] / 1e6
        quote = {'notional_usdc': notional, 'minimum_roundtrip_usdc': returned,
                 'roundtrip_loss_fraction': 1 - returned / notional,
                 'buy_price': notional / (buy['out'] / 1e9), 'max_impact_pct': max(buy['impact_pct'], sell['impact_pct'])}
        if quote['roundtrip_loss_fraction'] > .02 or quote['max_impact_pct'] > .5:
            reasons.append('round-trip friction or price impact exceeds limit')
        if median is None or abs(quote['buy_price'] / median - 1) > .01:
            reasons.append('size-specific quote differs from DEX median by more than 1%')
    except (ProviderError, ValueError, KeyError, TypeError):
        reasons.append('missing valid two-way Raydium quote')
    if time.time() - start > 30:
        reasons.append('DEX evidence collection exceeded 30 seconds')
    return {'allow': not reasons, 'reasons': reasons, 'pools': selected, 'rejected': rejected[:25],
            'median_price': median, 'raw_spread': spread, 'quote': quote, 'checked_at': time.time(),
            'arbitrage_executable': False,
            'warning': 'Cached indicative prices, no source quote timestamps. No atomic cross-DEX execution proof.'}
