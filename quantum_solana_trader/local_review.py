"""Optional CPU Laya reviewer. Never downloads weights or calls a hosted model."""
import os
from pathlib import Path

from .model import finite
from .providers import ProviderError


def validate_answer(result):
    answer = result['answers']['entry_risk']
    probabilities = answer['probabilities']
    if answer.get('type') != 'choice' or set(probabilities) != {'allow', 'block', 'review'}:
        raise ProviderError('Malformed local reviewer answer')
    probs = {k: finite(v, 0, 1) for k, v in probabilities.items()}
    confidence = finite(answer['answer_confidence'], 0, 1)
    choice = answer['choice']
    if (abs(sum(probs.values()) - 1) > .001 or choice not in probs or
            probs[choice] < max(probs.values()) or abs(confidence - max(probs.values())) > .001):
        raise ProviderError('Inconsistent local reviewer probabilities')
    return {'allow': choice == 'allow' and confidence >= .85 and not answer.get('low_confidence', False),
            'choice': choice, 'probabilities': probs, 'confidence': confidence, 'provider': 'laya-local-cpu'}


class LocalReviewer:
    def __init__(self):
        self.agent = None

    def review(self, facts):
        if self.agent is None:
            folder = os.getenv('QST_LAYA_MODEL_DIR')
            if not folder:
                raise ProviderError('Set QST_LAYA_MODEL_DIR to a downloaded local checkpoint')
            path = Path(folder).expanduser()
            if not path.is_dir() or not all((path / name).exists() for name in
                    ('rl_agent_config.json', 'model.safetensors', 'tokenizer')):
                raise ProviderError('Incomplete local Laya checkpoint; no download attempted')
            # Set before importing HF/transformers: missing local assets fail, never fetch.
            os.environ['HF_HUB_OFFLINE'] = '1'
            os.environ['TRANSFORMERS_OFFLINE'] = '1'
            try:
                import torch
                import laya
                torch.set_num_threads(min(4, os.cpu_count() or 1))
                self.agent = laya.load(str(path.resolve()), device='cpu')
            except Exception:
                raise ProviderError('Local Laya could not load; verify optional dependencies and offline assets') from None
        # No wallet, keys, user-supplied token descriptions, URLs or free-form instructions.
        state = {'features': facts.get('features'), 'risk': facts.get('risk'),
                 'dex_allow': facts.get('screen', {}).get('allow', False),
                 'dex_reasons': facts.get('screen', {}).get('reasons', [])}
        questions = {'entry_risk': {'type': 'choice',
            'instructions': 'Assess whether this evidence supports considering a new spot entry. Missing evidence requires review. Never promise profit.',
            'criteria': {'allow': 'Evidence is sufficient and no risk anomaly is identified',
                         'block': 'A risk check failed or evidence is contradictory',
                         'review': 'Evidence is insufficient or ambiguous'}}}
        try:
            result = self.agent.predict(state, questions, max_len=512, min_confidence=.85)
            return validate_answer(result)
        except Exception:
            raise ProviderError('Local Laya review failed or was malformed; entry blocked') from None
