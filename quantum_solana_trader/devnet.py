"""An isolated test-SOL transaction lifecycle. Never reads a mainnet key."""
import base64
import time

from .providers import ProviderError, rpc


def exercise(store):
    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.system_program import TransferParams, transfer
    from solders.transaction import Transaction
    signer = Keypair()
    public = str(signer.pubkey())
    faucet = rpc('requestAirdrop', [public, 100_000_000], devnet=True)
    for _ in range(15):
        if rpc('getBalance', [public, {'commitment': 'confirmed'}], devnet=True)['value'] >= 100_000:
            break
        time.sleep(2)
    else:
        raise ProviderError('Devnet faucet did not fund the ephemeral test account')
    blockhash = rpc('getLatestBlockhash', [{'commitment': 'confirmed'}], devnet=True)['value']['blockhash']
    instruction = transfer(TransferParams(from_pubkey=signer.pubkey(), to_pubkey=signer.pubkey(), lamports=1000))
    tx = Transaction.new_signed_with_payer([instruction], signer.pubkey(), [signer], Hash.from_string(blockhash))
    encoded = base64.b64encode(bytes(tx)).decode()
    simulation = rpc('simulateTransaction', [encoded, {'encoding': 'base64', 'sigVerify': True}], devnet=True)['value']
    if simulation.get('err'):
        raise ProviderError('Devnet self-transfer simulation failed')
    signature = rpc('sendTransaction', [encoded, {'encoding': 'base64', 'skipPreflight': False, 'maxRetries': 0}], devnet=True)
    result = {'cluster': 'devnet', 'wallet': public, 'airdrop_signature': faucet, 'signature': signature,
              'status': 'submitted', 'note': 'Ephemeral test-SOL self-transfer; not a DEX performance test.'}
    store.log('devnet', 'transaction', result)
    for _ in range(15):
        status = rpc('getSignatureStatuses', [[signature], {'searchTransactionHistory': True}], devnet=True)['value'][0]
        if status and status.get('confirmationStatus') in ('confirmed', 'finalized'):
            result['status'] = 'failed' if status.get('err') else status['confirmationStatus']
            store.log('devnet', 'transaction', result)
            return result
        time.sleep(2)
    return result
