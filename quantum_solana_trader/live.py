"""Opt-in supervised mainnet swaps. Never called by the browser or paper worker.

Every signed intent is durable before transmission. Unknown outcomes block ALL
subsequent sends until finalized on-chain reconciliation. No automatic resends.
"""
import base64
import json
import os
import stat
import time
import uuid
from pathlib import Path

from .engine import money, readiness
from .providers import SOL, USDC, TOKEN_PROGRAM, ProviderError, jupiter_order, request_json, rpc, token_screen
from .store import encode

ACK = 'I_ACCEPT_MAINNET_LOSS'


def read_wallet_file(keyfile):
    """Read an operator-created keyfile outside the checkout; never log its contents."""
    if not keyfile:
        raise ValueError('Provide --keyfile or QST_WALLET_FILE; never enter private keys in the dashboard')
    supplied = Path(keyfile).expanduser()
    if supplied.is_symlink():
        raise ValueError('Wallet file must not be a symbolic link')
    path = supplied.resolve()
    project = Path(__file__).resolve().parent.parent
    if path == project or project in path.parents:
        raise ValueError('Wallet file must be outside the project checkout')
    if os.name == 'nt':
        raise ValueError('Mainnet key loading requires Unix owner-only permissions; use the dedicated Ubuntu machine')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, encoding='utf-8') as file:
        info = os.fstat(file.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_mode & (stat.S_IRWXG | stat.S_IRWXO) or info.st_size > 4096):
            raise ValueError('Wallet file must be small, regular, owned by this user and mode 600 or stricter')
        try:
            raw = json.load(file)
        except (ValueError, UnicodeError):
            raise ValueError('Malformed wallet file') from None
    if not isinstance(raw, list) or len(raw) != 64 or any(type(x) is not int or not 0 <= x <= 255 for x in raw):
        raise ValueError('Expected a Solana CLI 64-byte keypair file')
    return bytes(raw)


def base58_bytes(value):
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    number = 0
    for char in value:
        number = number * 58 + alphabet.index(char)
    return b'\0' * (len(value) - len(value.lstrip('1'))) + number.to_bytes((number.bit_length() + 7) // 8, 'big')


def validate_simulation(keys, before, after, wallet, side, atoms, minimum):
    if len(keys) != len(before) or len(keys) != len(after) or len(set(keys)) != len(keys):
        raise ValueError('Incomplete simulation account snapshot')
    wallet_index = keys.index(wallet)
    if not before[wallet_index] or not after[wallet_index]:
        raise ValueError('Missing fee payer balance')
    native_before = int(before[wallet_index]['lamports'])
    native_after = int(after[wallet_index]['lamports'])
    native_delta = native_after - native_before
    if native_after < 50_000_000:
        raise ValueError('Simulation breaches SOL reserve')
    if before[wallet_index]['owner'] != after[wallet_index]['owner'] or after[wallet_index].get('executable'):
        raise ValueError('Simulation changes wallet ownership')
    owner_bytes, usdc_bytes = base58_bytes(wallet), base58_bytes(USDC)
    changes = {}
    for old, new in zip(before, after):
        def parse(account):
            if not account or account.get('owner') != TOKEN_PROGRAM:
                return None
            raw = base64.b64decode(account['data'][0], validate=True)
            if len(raw) != 165 or raw[32:64] != owner_bytes:
                return None
            return raw
        previous, following = parse(old), parse(new)
        if previous or following:
            if not previous or not following:
                raise ValueError('Route creates/closes wallet token account; supervised review required')
            # Keep owner, delegate, state, native option and close authority unchanged.
            if previous[:64] != following[:64] or previous[72:] != following[72:]:
                raise ValueError('Route mutates token authority or account state')
            mint = previous[:32]
            changes[mint] = changes.get(mint, 0) + int.from_bytes(following[64:72], 'little') - int.from_bytes(previous[64:72], 'little')
    usdc_delta = changes.pop(usdc_bytes, 0)
    if any(v < 0 for v in changes.values()):
        raise ValueError('Route spends an unrelated token')
    fee_cap = 3_000_000  # 0.003 SOL, includes all native debit beyond the requested swap.
    if side == 'BUY':
        if usdc_delta != -atoms or native_delta < minimum - fee_cap:
            raise ValueError('Simulated wallet deltas do not match the buy quote')
    elif side == 'SELL':
        if usdc_delta < minimum or not -atoms - fee_cap <= native_delta <= -atoms:
            raise ValueError('Simulated wallet deltas do not match the sell quote')
    else:
        raise ValueError('Invalid side')


def validate_quote(quote, input_mint, output_mint, atoms):
    if quote.get('inputMint') != input_mint or quote.get('outputMint') != output_mint:
        raise ValueError('Quote mint mismatch')
    if int(quote.get('inAmount', -1)) != atoms:
        raise ValueError('Quote input amount mismatch')
    if not quote.get('transaction') or not quote.get('requestId'):
        raise ValueError('Quote has no transaction/request ID')
    slip = int(quote.get('slippageBps', -1))
    if not 0 <= slip <= 50:
        raise ValueError('Slippage exceeds fixed 50 bps cap or is missing')
    out = int(quote.get('outAmount', 0))
    threshold = int(quote.get('otherAmountThreshold', 0))
    if out <= 0 or threshold < out * (10000 - slip) // 10000:
        raise ValueError('Missing or unsafe minimum output threshold')
    return threshold


class LiveExecutor:
    def __init__(self, store):
        self.store = store

    def pending(self):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM intents WHERE status IN ('signed','submitted','unknown')")]

    def record(self, identifier, status, data):
        with self.store.connect() as db:
            db.execute('INSERT INTO intents VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,data=excluded.data,updated=excluded.updated',
                       (identifier, status, encode(data), time.time()))

    def reconcile(self):
        results = []
        for row in self.pending():
            data = json.loads(row['data'])
            statuses = rpc('getSignatureStatuses', [[data['signature']], {'searchTransactionHistory': True}])
            status = statuses['value'][0]
            if status and status.get('confirmationStatus') == 'finalized':
                outcome = 'failed' if status.get('err') else 'confirmed'
                data['chain_status'] = status
                if outcome == 'confirmed':
                    transaction = rpc('getTransaction', [data['signature'], {'encoding': 'json', 'commitment': 'finalized', 'maxSupportedTransactionVersion': 0}])
                    if not transaction or not transaction.get('meta'):
                        results.append({'id': row['id'], 'status': 'unknown'})
                        continue
                    data['receipt'] = transaction['meta']
                self.record(row['id'], outcome, data)
                self.store.log('mainnet', 'transaction', {'id': row['id'], 'status': outcome, 'signature': data['signature']})
                results.append({'id': row['id'], 'status': outcome})
            else:
                results.append({'id': row['id'], 'status': 'unknown'})
        return results

    def execute(self, evidence_session, side, usdc, keyfile, acknowledgment):
        if acknowledgment != ACK or os.getenv('QST_MAINNET') != 'enabled':
            raise ValueError('Mainnet requires QST_MAINNET=enabled and explicit CLI acknowledgment')
        state = self.store.load(evidence_session)
        if not state or not readiness(state)['eligible_for_review']:
            raise ValueError('Live-paper evidence gates have not passed')
        if time.time() - state['last_ts'] > 120 or state['paused']:
            raise ValueError('Paper evidence is stale or paused')
        if self.pending():
            raise ValueError('Unresolved transaction; reconcile before any new swap')
        if side not in ('BUY', 'SELL'):
            raise ValueError('Side must be BUY or SELL')
        if not money('1') <= money(usdc) <= money('25'):
            raise ValueError('Supervised mainnet notional is capped at 25 USDC per order')
        with self.store.connect() as db:
            used = db.execute("SELECT data FROM intents WHERE updated>=? AND status!='failed'", (time.time() // 86400 * 86400,)).fetchall()
        if sum(money(json.loads(r[0])['notional_usdc']) for r in used) + money(usdc) > 100:
            raise ValueError('100 USDC daily gross turnover cap reached')
        if not token_screen(SOL)['allow'] or not token_screen(USDC)['allow']:
            raise ValueError('Mint screen failed')
        from solders.keypair import Keypair
        from solders.transaction import VersionedTransaction
        from solders.message import to_bytes_versioned
        try:
            signer = Keypair.from_bytes(read_wallet_file(keyfile))
        except ValueError as error:
            if 'keypair' in str(error).lower():
                raise ValueError('Wallet keypair is invalid') from None
            raise
        wallet = str(signer.pubkey())
        balance = rpc('getBalance', [wallet, {'commitment': 'confirmed'}])['value']
        if balance < 50_000_000:
            raise ValueError('At least 0.05 native SOL must remain reserved for gas')
        if side == 'BUY':
            input_mint, output_mint = USDC, SOL
            atoms = int(money(usdc) * 1_000_000)
        else:
            input_mint, output_mint = SOL, USDC
            reference = jupiter_order(USDC, SOL, int(money(usdc) * 1_000_000))
            atoms = int(reference['outAmount'])
            if balance - atoms < 50_000_000:
                raise ValueError('Sell would spend the native SOL reserve')
        quote = jupiter_order(input_mint, output_mint, atoms, wallet)
        minimum = validate_quote(quote, input_mint, output_mint, atoms)
        tx = VersionedTransaction.from_bytes(base64.b64decode(quote['transaction'], validate=True))
        message = tx.message
        if message.header.num_required_signatures != 1 or message.account_keys[0] != signer.pubkey():
            raise ValueError('Only single-signer routes with the wallet as fee payer are supported')
        # Reject ALT routes in this supervised release; do not guess loaded accounts.
        if getattr(message, 'address_table_lookups', []):
            raise ValueError('ALT route requires additional transaction review; refusing to sign')
        allowed = {'ComputeBudget111111111111111111111111111111',
                   'JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4'}
        for instruction in message.instructions:
            if str(message.account_keys[instruction.program_id_index]) not in allowed:
                raise ValueError('Route includes an unreviewed top-level program; refusing to sign')
        encoded = base64.b64encode(bytes(tx)).decode()
        keys = [str(key) for key in message.account_keys]
        if len(keys) > 64:
            raise ValueError('Route exceeds account inspection budget')
        before = rpc('getMultipleAccounts', [keys, {'encoding': 'base64', 'commitment': 'confirmed'}])['value']
        simulation = rpc('simulateTransaction', [encoded, {'encoding': 'base64', 'sigVerify': False,
            'replaceRecentBlockhash': False, 'commitment': 'confirmed',
            'accounts': {'encoding': 'base64', 'addresses': keys}}])['value']
        if simulation.get('err'):
            raise ValueError('Preflight simulation failed')
        validate_simulation(keys, before, simulation.get('accounts', []), wallet, side, atoms, minimum)
        if time.time() - state['last_ts'] > 120:
            raise ValueError('Evidence expired during preflight')
        signed = VersionedTransaction.populate(message, [signer.sign_message(to_bytes_versioned(message))])
        identifier = str(uuid.uuid4())
        data = {'signature': str(signed.signatures[0]), 'request_id': quote['requestId'],
                'side': side, 'input_mint': input_mint, 'output_mint': output_mint,
                'input_atoms': atoms, 'minimum_output_atoms': minimum, 'notional_usdc': str(usdc),
                'created': time.time(), 'evidence_session': evidence_session}
        self.record(identifier, 'signed', data)
        try:
            response = request_json('https://api.jup.ag/swap/v2/execute',
                {'signedTransaction': base64.b64encode(bytes(signed)).decode(), 'requestId': quote['requestId']},
                headers={'x-api-key': os.environ['JUPITER_API_KEY']}, retries=0)
            # HTTP success is not final settlement; always reconcile independently.
            data['reported_status'] = response.get('status')
            if response.get('signature') and response['signature'] != data['signature']:
                raise ProviderError('Provider returned an unexpected signature')
            self.record(identifier, 'submitted', data)
        except Exception:
            self.record(identifier, 'unknown', data)
            raise ProviderError('Submission outcome unknown; do not resend. Run reconcile.') from None
        return {'id': identifier, 'status': 'submitted', 'signature': data['signature']}
