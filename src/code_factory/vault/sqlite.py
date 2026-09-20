import json
import sqlite3
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from .models import RunRecord, StructuralMatch, Ticket, TicketStatus, TicketSummary
from .storage import VaultStorage


class SqliteVaultManager(VaultStorage):
    """SQLite-backed vault storage."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                requirements TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'new',
                host_function_allowlist TEXT NOT NULL DEFAULT '[]',
                input_schema TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stage_files (
                ticket_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                content TEXT NOT NULL,
                PRIMARY KEY (ticket_id, filename),
                FOREIGN KEY (ticket_id) REFERENCES tickets(id)
            );
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id TEXT NOT NULL,
                data TEXT NOT NULL,
                FOREIGN KEY (ticket_id) REFERENCES tickets(id)
            );
        """)

    def next_id(self) -> str:
        row = self._conn.execute(
            "SELECT id FROM tickets ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return "TICKET-001"
        try:
            n = int(row["id"].split("-")[1])
        except (ValueError, IndexError):
            n = 0
        return f"TICKET-{n + 1:03d}"

    def create_ticket(self, title: str, requirements: str, tags: list[str] | None = None,
                      host_functions: list[str] | None = None) -> Ticket:
        ticket = Ticket(
            id=self.next_id(),
            title=title,
            requirements=requirements,
            tags=tags or [],
            host_function_allowlist=host_functions or [],
        )
        now = datetime.now().isoformat()
        self._conn.execute(
            "INSERT INTO tickets (id, title, requirements, tags, status, host_function_allowlist, input_schema, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ticket.id, ticket.title, ticket.requirements,
             json.dumps(ticket.tags), ticket.status.value,
             json.dumps(ticket.host_function_allowlist),
             json.dumps(ticket.input_schema), now, now),
        )
        self._conn.commit()
        return ticket

    def save_ticket(self, ticket: Ticket) -> None:
        ticket.updated_at = datetime.now()
        data = ticket.model_dump(mode="json")
        self._conn.execute(
            "UPDATE tickets SET title=?, requirements=?, tags=?, status=?, host_function_allowlist=?, input_schema=?, updated_at=? WHERE id=?",
            (data["title"], data["requirements"], json.dumps(data["tags"]),
             data["status"], json.dumps(data["host_function_allowlist"]),
             json.dumps(data["input_schema"]), data["updated_at"], data["id"]),
        )
        self._conn.commit()

    def load_ticket(self, ticket_id: str) -> Ticket | None:
        row = self._conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        if not row:
            return None
        return Ticket(
            id=row["id"], title=row["title"], requirements=row["requirements"],
            tags=json.loads(row["tags"]), status=TicketStatus(row["status"]),
            host_function_allowlist=json.loads(row["host_function_allowlist"]),
            input_schema=json.loads(row["input_schema"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def write_stage_file(self, ticket_id: str, filename: str, content: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO stage_files (ticket_id, filename, content) VALUES (?, ?, ?)",
            (ticket_id, filename, content),
        )
        self._conn.commit()

    def read_stage_file(self, ticket_id: str, filename: str) -> str | None:
        row = self._conn.execute(
            "SELECT content FROM stage_files WHERE ticket_id=? AND filename=?",
            (ticket_id, filename),
        ).fetchone()
        return row["content"] if row else None

    def save_run(self, ticket_id: str, record: RunRecord) -> None:
        self._conn.execute(
            "INSERT INTO runs (ticket_id, data) VALUES (?, ?)",
            (ticket_id, record.model_dump_json()),
        )
        self._conn.commit()

    def get_latest_run(self, ticket_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT data FROM runs WHERE ticket_id=? ORDER BY id DESC LIMIT 1",
            (ticket_id,),
        ).fetchone()
        if not row:
            return None
        return json.loads(row["data"])

    def list_tickets(self) -> list[str]:
        rows = self._conn.execute("SELECT id FROM tickets ORDER BY id").fetchall()
        return [r["id"] for r in rows]

    def list_stage_files(self, ticket_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT filename FROM stage_files WHERE ticket_id=? ORDER BY filename",
            (ticket_id,),
        ).fetchall()
        return [r["filename"] for r in rows]

    def list_runs(self, ticket_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT data FROM runs WHERE ticket_id=? ORDER BY id",
            (ticket_id,),
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def commit(self, ticket_id: str, message: str) -> None:
        pass

    def _get_summaries(self) -> list[TicketSummary]:
        rows = self._conn.execute("SELECT * FROM tickets ORDER BY id").fetchall()
        return [
            TicketSummary(
                id=r["id"], title=r["title"],
                tags=json.loads(r["tags"]),
                summary=r["requirements"][:120],
                status=TicketStatus(r["status"]),
                host_functions=json.loads(r["host_function_allowlist"]),
                input_keys=list(json.loads(r["input_schema"]).keys()),
            )
            for r in rows
        ]

    def search(self, query: str, top_n: int = 5) -> list[TicketSummary]:
        entries = self._get_summaries()
        scored = []
        q_lower = query.lower()
        q_words = set(q_lower.split())
        for e in entries:
            text = f"{e.title} {' '.join(e.tags)} {e.summary}".lower()
            seq_ratio = SequenceMatcher(None, q_lower, text).ratio()
            text_words = set(text.split())
            common = q_words & text_words
            keyword_ratio = len(common) / max(len(q_words), 1)
            score = max(seq_ratio, keyword_ratio)
            if score > 0.45:
                scored.append((score, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:top_n]]

    def search_structural(self, host_functions: list[str], input_keys: list[str],
                          top_n: int = 5) -> list[StructuralMatch]:
        entries = self._get_summaries()
        query_fns = set(host_functions)
        query_inputs = set(input_keys)
        matches = []
        for e in entries:
            entry_fns = set(e.host_functions)
            entry_inputs = set(e.input_keys)
            fn_union = query_fns | entry_fns
            fn_overlap = len(query_fns & entry_fns) / len(fn_union) if fn_union else 0.0
            input_union = query_inputs | entry_inputs
            input_overlap = len(query_inputs & entry_inputs) / len(input_union) if input_union else 0.0
            if fn_overlap < 0.5:
                continue
            if fn_overlap == 1.0 and input_overlap == 1.0:
                match_type = "exact"
            elif query_fns >= entry_fns and query_inputs >= entry_inputs:
                match_type = "superset"
            elif entry_fns >= query_fns and entry_inputs >= query_inputs:
                match_type = "subset"
            else:
                match_type = "partial"
            matches.append(StructuralMatch(
                ticket=e, fn_overlap=fn_overlap, input_overlap=input_overlap,
                match_type=match_type,
            ))
        matches.sort(key=lambda m: (m.fn_overlap + m.input_overlap), reverse=True)
        return matches[:top_n]
