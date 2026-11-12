"""SQLite is authoritative; state.yaml and ledger.jsonl are rebuildable projections."""
import json
import sqlite3
from datetime import datetime, timezone
from .io import atomic


def now():
    return datetime.now(timezone.utc).isoformat()


def local(at=None, seconds=False):
    """A time from now() as people read it, in this machine's time zone, the same as the page: 2026-11-08 02:08."""
    moment = datetime.fromisoformat(at) if at else datetime.now(timezone.utc)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M")


class Store:
    def __init__(self, root, readonly=False):
        self.root, self.readonly = root, readonly
        directory = root / ".mlab"
        if readonly:
            # Views must never change the ledger: no schema setup, and SQLite refuses any write.
            self.db = sqlite3.connect(f"file:{directory / 'ledger.sqlite3'}?mode=ro", uri=True, timeout=20, check_same_thread=False)
            self.db.execute("PRAGMA query_only=1")
            return
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(directory / "ledger.sqlite3", timeout=20)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("CREATE TABLE IF NOT EXISTS objects(kind TEXT,id TEXT,data TEXT,PRIMARY KEY(kind,id));"
                             "CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,at TEXT,type TEXT,data TEXT);")
        self.db.commit()

    def get(self, kind, key):
        row = self.db.execute("SELECT data FROM objects WHERE kind=? AND id=?", (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, kind):
        return [json.loads(row[0]) for row in self.db.execute("SELECT data FROM objects WHERE kind=? ORDER BY id", (kind,))]

    def events(self, after=0):
        return [{"seq": seq, "at": at, "type": kind, "data": json.loads(data)}
                for seq, at, kind, data in self.db.execute("SELECT seq,at,type,data FROM events WHERE seq>? ORDER BY seq", (after,))]

    def last_seq(self):
        return self.db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]

    def put(self, kind, key, data):
        self.db.execute("INSERT OR REPLACE INTO objects VALUES(?,?,?)", (kind, key, json.dumps(data, ensure_ascii=False)))

    def event(self, kind, data):
        self.db.execute("INSERT INTO events(at,type,data) VALUES(?,?,?)", (now(), kind, json.dumps(data, ensure_ascii=False)))

    def begin(self):
        self.db.execute("BEGIN IMMEDIATE")

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def project(self):
        if self.readonly:
            return
        rows = self.db.execute("SELECT seq,at,type,data FROM events ORDER BY seq").fetchall()
        atomic(self.root / ".mlab/ledger.jsonl", "".join(json.dumps({"seq": seq, "at": at, "type": kind, "data": json.loads(data)},
               ensure_ascii=False) + "\n" for seq, at, kind, data in rows), text=True)

    def close(self):
        self.db.close()
