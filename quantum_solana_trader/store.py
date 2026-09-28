"""SQLite is the journal and checkpoint: one transaction commits both."""
import contextlib
import json
import sqlite3
import time
from pathlib import Path


def encode(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False)


class Store:
    def __init__(self, path="data/astra.sqlite"):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO meta VALUES('schema_version','1');
                CREATE TABLE IF NOT EXISTS candles(
                    source TEXT NOT NULL, ts INTEGER NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(source,ts));
                CREATE TABLE IF NOT EXISTS sessions(
                    id TEXT PRIMARY KEY, mode TEXT NOT NULL, state TEXT NOT NULL,
                    updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY, session TEXT NOT NULL, ts REAL NOT NULL,
                    kind TEXT NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS event_lookup ON events(session,kind,id);
                CREATE TABLE IF NOT EXISTS intents(
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL,
                    updated REAL NOT NULL);
            """)
            if db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] != '1':
                raise ValueError("Unsupported database schema; preserve database and migrate first")

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, source, candles):
        from .model import Candle
        count = 0
        with self.connect() as db:
            for item in candles:
                candle = Candle.parse(item)
                payload = encode(candle.to_dict())
                old = db.execute("SELECT data FROM candles WHERE source=? AND ts=?", (source, candle.ts)).fetchone()
                if old and old[0] != payload:
                    raise ValueError("Conflicting duplicate candle; dataset left unchanged")
                if not old:
                    db.execute("INSERT INTO candles VALUES(?,?,?)", (source, candle.ts, payload))
                    count += 1
        return count

    def candles(self, source, after=0):
        with self.connect() as db:
            for row in db.execute("SELECT data FROM candles WHERE source=? AND ts>? ORDER BY ts", (source, after)):
                yield json.loads(row[0])

    def datasets(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT source,COUNT(*) AS count,MIN(ts) AS start,MAX(ts) AS end FROM candles GROUP BY source")]

    def load(self, session):
        with self.connect() as db:
            row = db.execute("SELECT state FROM sessions WHERE id=?", (session,)).fetchone()
            return json.loads(row[0]) if row else None

    def save(self, session, state, events=()):
        # The engine owns one session under a process-wide OS file lock.
        with self.connect() as db:
            db.execute("INSERT INTO sessions VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,updated=excluded.updated",
                       (session, state['mode'], encode(state), time.time()))
            db.executemany("INSERT INTO events(session,ts,kind,data) VALUES(?,?,?,?)",
                           [(session, ts, kind, encode(data)) for ts, kind, data in events])

    def log(self, session, kind, data):
        with self.connect() as db:
            db.execute("INSERT INTO events(session,ts,kind,data) VALUES(?,?,?,?)", (session, time.time(), kind, encode(data)))

    def events(self, session, kind=None, limit=100, before=None):
        query, args = "SELECT * FROM events WHERE session=?", [session]
        if kind:
            query += " AND kind=?"
            args.append(kind)
        if before:
            query += " AND id<?"
            args.append(int(before))
        query += " ORDER BY id DESC LIMIT ?"
        args.append(min(1000, max(1, int(limit))))
        with self.connect() as db:
            return [dict(r) | {'data': json.loads(r['data'])} for r in db.execute(query, args)]

    def sessions(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT id,mode,updated FROM sessions ORDER BY updated DESC")]

    def evaluations(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM events WHERE kind='evaluation' ORDER BY id DESC LIMIT 5")]

    def export(self, session):
        with self.connect() as db:
            for row in db.execute("SELECT * FROM events WHERE session=? ORDER BY id", (session,)):
                yield encode(dict(row) | {'data': json.loads(row['data'])}) + '\n'

    def backup(self, destination):
        if Path(destination).resolve() == Path(self.path).resolve():
            raise ValueError("Backup destination must differ from active database")
        with self.connect() as source, contextlib.closing(sqlite3.connect(destination)) as target:
            source.backup(target)


@contextlib.contextmanager
def engine_lock(path):
    """OS lock is released even on a crash. Never delete the lock file."""
    import os
    file = open(str(path) + '.engine.lock', 'a+b')
    try:
        file.seek(0)
        file.write(b'0')
        file.flush()
        file.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        file.close()
