"""Local worker coordination and platform telemetry."""
import ctypes
import os
import re
import shutil
import threading
import time

from .engine import Engine, Policy, metrics, readiness
from .model import features
from .providers import INTERVALS, ProviderError, binance_candles, download, jev_review, token_screen, SOL, USDC
from .dex import DEFAULT_POOL, dex_candles, download_dex, scan_dexes, source_name
from .local_review import LocalReviewer
from .research import replay, synthetic, walk_forward

_cpu_previous = None


class Runtime:
    def __init__(self, store):
        self.store = store
        self.started = time.time()
        self.lock = threading.RLock()
        self.engine = None
        self.job = {'stage': 'idle', 'progress': 0, 'eta': None, 'message': 'Choose a dataset or run the synthetic demo.'}
        self.thread = None
        self.stop_event = threading.Event()
        self.session = next((s['id'] for s in store.sessions()), None)
        self.last_error = None

    def update(self, stage, progress, message, eta=None):
        with self.lock:
            self.job = dict(stage=stage, progress=progress, message=message, eta=eta)

    def launch(self, operation, options):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError('A worker is already running; stop it before starting another')
            self.stop_event.clear()
            self.last_error = None
            self.engine = None
            self.update(operation, 0, 'Starting ' + operation)
            self.thread = threading.Thread(target=self._run, args=(operation, options), daemon=True)
            self.thread.start()

    def _run(self, operation, options):
        try:
            identifier = operation + '-' + str(time.time_ns())
            if operation == 'demo':
                source = 'synthetic:stress:v1'
                self.store.ingest(source, synthetic())
                self.session = identifier
                self.engine = replay(self.store, source, identifier,
                    progress=lambda p, eta: self.update('replay', p, 'Synthetic stress replay • no real funds', eta), stop=self.stop_event)
            elif operation == 'download':
                days = int(options.get('days', 7))
                if not 1 <= days <= 365:
                    raise ValueError('Choose 1–365 days')
                end = int(time.time()) // 60 * 60
                source, count = download_dex(self.store, end - days * 86400, end,
                    progress=lambda p, n: self.update('download', p, f'{n:,} new historical candles'), stop=self.stop_event)
                self.store.log('system', 'download', {'source': source, 'candles': count})
            elif operation == 'train':
                source = options.get('source', source_name())
                if source not in [d['source'] for d in self.store.datasets()]:
                    raise ValueError('Select an imported dataset')
                self.session = identifier
                interval = INTERVALS.get(source.rsplit(':', 1)[-1], 60)
                self.engine = replay(self.store, source, identifier, Policy(interval=interval),
                    progress=lambda p, eta: self.update('training', p, 'Chronological online learning', eta), stop=self.stop_event)
            elif operation == 'paper':
                self._paper(options)
            elif operation == 'scan':
                result = scan_dexes()
                self.store.log('system', 'dex_scan', result)
                self.update('complete', 1, 'DEX research scan saved; price gaps are not executable arbitrage.')
            elif operation == 'evaluate':
                source = options.get('source', source_name())
                if source not in [d['source'] for d in self.store.datasets()]:
                    raise ValueError('Select an imported dataset')
                interval = INTERVALS.get(source.rsplit(':', 1)[-1], 60)
                walk_forward(self.store, source, identifier, Policy(interval=interval),
                    progress=lambda p, fold: self.update('evaluation', p, f'Chronological fold {fold+1} of 3', None), stop=self.stop_event)
            else:
                raise ValueError('Unknown worker operation')
            self.update('stopped' if self.stop_event.is_set() else 'complete', None if self.stop_event.is_set() else 1,
                        'Worker stopped; committed state retained.' if self.stop_event.is_set() else 'Worker finished. Check evidence before advancing.')
        except Exception as error:
            # Avoid logging exception strings from unknown SDKs (may contain URLs/keys).
            self.last_error = str(error) if isinstance(error, (ValueError, ProviderError)) else type(error).__name__
            self.store.log(self.session or 'system', 'error', {'operation': operation, 'error': self.last_error})
            self.update('failed', 0, self.last_error)

    def _paper(self, options):
        with self.lock:
            self.session = options.get('session') or 'live-dex-paper'
            if not isinstance(self.session, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', self.session):
                raise ValueError('Paper session must be 1–64 letters, numbers, underscores or hyphens')
            pool = options.get('pool', DEFAULT_POOL)
            feed = options.get('feed', 'dex')
            if feed not in ('dex', 'binance'):
                raise ValueError('Unknown market feed')
            source = source_name(pool) if feed == 'dex' else 'binance:SOLUSDC:1m'
            if options.get('jev') and os.getenv('QST_ALLOW_HOSTED_AI') != '1':
                raise ValueError('Hosted AI disabled by zero-paid-service policy')
            self.engine = Engine(self.store, self.session, 'paper', source=source)
            checkpoint = options.get('checkpoint')
            if checkpoint and self.engine.state['bars'] == 0:
                trained = self.store.load(checkpoint)
                if not trained or trained['source'] != source or trained['mode'] != 'replay':
                    raise ValueError('Warm start requires a replay checkpoint from this exact market/pool source')
                import copy
                self.engine.state['model'] = copy.deepcopy(trained['model'])
                self.engine.state['model'].update(correct=0, brier=0., bins=[[0, 0., 0] for _ in range(10)])
                self.engine.state['metric_origin_n'] = trained['model']['n']
                self.store.save(self.session, self.engine.state)
        errors, mint_review, screened_at = 0, None, 0
        local_reviewer = LocalReviewer() if options.get('laya') else None
        while not self.stop_event.is_set():
            try:
                candles = dex_candles(pool, limit=3) if feed == 'dex' else binance_candles(limit=2)
                if not candles or time.time() - candles[-1]['ts'] > 90:
                    raise ProviderError('Market data stale; waiting without opening new exposure')
                candle = candles[-1]
                if candle['ts'] <= self.engine.state['last_ts']:
                    self.stop_event.wait(5)
                    continue
                review = None
                if feed == 'dex':
                    prior = self.store.events(self.session, 'dex_scan', 1)
                    notional = min(10000., max(1., self.engine.state['equity'] * self.engine.policy.allocation))
                    review = scan_dexes(notional, prior[0]['data'] if prior else None, candle['close'])
                    if time.time() - screened_at > 600:
                        try:
                            screens = [token_screen(SOL), token_screen(USDC)]
                            mint_review = all(s['allow'] for s in screens)
                            self.store.log(self.session, 'mint_screen', {'allow': mint_review, 'checks': screens})
                        except (ProviderError, ValueError, KeyError, TypeError):
                            mint_review = False
                        screened_at = time.time()
                    if not mint_review:
                        review['allow'] = False
                        review['reasons'].append('canonical mint verification unavailable or failed')
                    self.store.log(self.session, 'dex_scan', review)
                if options.get('jev'):
                    try:
                        hosted = jev_review({'features': features(self.engine.state['history'] + [candle]),
                            'risk': {'drawdown': self.engine.state['drawdown'], 'policy': self.engine.state['policy']},
                            'screen': review})
                        self.store.log(self.session, 'jev_review', hosted)
                        if not hosted['allow']:
                            review = {'allow': False, 'reason': 'Jev review blocked entry'}
                    except (ProviderError, ValueError, KeyError, TypeError):
                        review = {'allow': False, 'reason': 'Jev unavailable or invalid'}
                if local_reviewer:
                    try:
                        local = local_reviewer.review({'features': features(self.engine.state['history'] + [candle]),
                            'risk': {'drawdown': self.engine.state['drawdown'], 'policy': self.engine.state['policy']},
                            'screen': review or {}})
                        self.store.log(self.session, 'laya_review', local)
                        if not local['allow']:
                            review = {'allow': False, 'reason': 'Local Laya blocked or deferred entry'}
                    except ProviderError:
                        review = {'allow': False, 'reason': 'Local Laya unavailable or invalid'}
                with self.lock:
                    self.store.ingest(source, [candle])
                    self.engine.step(candle, observed=True, risk_review=review)
                errors = 0
                self.update('paper', None, f'Observing {feed} SOL/USDC candles; paper fills only. ETA is open-ended.')
            except (ProviderError, ValueError, KeyError, TypeError) as error:
                errors += 1
                with self.lock:
                    self.engine.incident(str(error) if isinstance(error, ProviderError) else 'Invalid market response')
                self.update('waiting', None, f'Feed unavailable • retry {errors}; entries blocked')
            self.stop_event.wait(min(60, 5 * 2 ** min(errors, 4)))

    def control(self, action):
        with self.lock:
            if action == 'stop':
                self.stop_event.set()
                return
            if not self.engine:
                raise ValueError('No active engine')
            if self.thread and self.thread.is_alive() and self.engine.state['mode'] != 'paper':
                raise ValueError('Use Stop worker to interrupt a historical run')
            self.engine.control(action)

    def snapshot(self, session=None):
        with self.lock:
            chosen = session or self.session
            state = self.store.load(chosen) if chosen else None
            return {'session': chosen, 'sessions': self.store.sessions(), 'datasets': self.store.datasets(),
                    'active_session': self.session, 'active_engine': self.engine is not None,
                    'state': state, 'metrics': metrics(state) if state else None, 'evaluations': self.store.evaluations(),
                    'readiness': readiness(state) if state else None, 'job': self.job,
                    'dex_scan': (self.store.events(chosen, 'dex_scan', 1) or self.store.events('system', 'dex_scan', 1) or [None])[0],
                    'worker_running': bool(self.thread and self.thread.is_alive()),
                    'telemetry': telemetry(self.started, self.store.path),
                    'providers': {'jupiter': bool(os.getenv('JUPITER_API_KEY')), 'jev': bool(os.getenv('TYPESAFE_API_KEY')) and os.getenv('QST_ALLOW_HOSTED_AI') == '1',
                                  'hosted_ai_allowed': os.getenv('QST_ALLOW_HOSTED_AI') == '1',
                                  'laya': bool(os.getenv('QST_LAYA_MODEL_DIR'))},
                    'mainnet': 'Locked • CLI-only supervised execution'}


def telemetry(started, path):
    global _cpu_previous
    total, available, cpu = None, None, None
    try:
        import psutil
        ram = psutil.virtual_memory()
        total, available = ram.total, ram.available
        times = psutil.cpu_times()
        ticks = sum(times) - getattr(times, 'guest', 0) - getattr(times, 'guest_nice', 0)
        idle = times.idle + getattr(times, 'iowait', 0)
        if _cpu_previous and ticks > _cpu_previous[0]:
            cpu = max(0., min(100., 100 * (1 - (idle - _cpu_previous[1]) / (ticks - _cpu_previous[0]))))
        _cpu_previous = (ticks, idle)
    except ImportError:
        if os.name == 'nt':
            class Memory(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(k, ctypes.c_ulonglong) for k in ('total', 'avail', 'page', 'avail_page', 'virtual', 'avail_virtual', 'extended')]
            mem = Memory()
            mem.length = ctypes.sizeof(mem)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
                total, available = mem.total, mem.avail
        elif os.path.exists('/proc/meminfo'):
            with open('/proc/meminfo') as file:
                values = {line.split(':')[0]: int(line.split()[1]) * 1024 for line in file}
            total, available = values['MemTotal'], values['MemAvailable']
    disk = shutil.disk_usage(os.path.dirname(os.path.abspath(path)))
    return {'cpu_percent': cpu, 'ram_total': total, 'ram_used': total - available if total else None,
            'disk_total': disk.total, 'disk_used': disk.used, 'uptime': time.time() - started,
            'cpu_count': os.cpu_count(), 'cpu_note': 'Install optional psutil for CPU utilization' if cpu is None else None}
