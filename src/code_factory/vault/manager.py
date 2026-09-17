import json
import subprocess
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import yaml

from .models import RunRecord, StructuralMatch, Ticket, TicketStatus, TicketSummary


class VaultManager:
    def __init__(self, root: Path, git_enabled: bool = False):
        self.root = root
        self.git_enabled = git_enabled
        self.tickets_dir = root / "tickets"
        self.registry_path = root / "registry.json"
        self._ensure_init()

    def _ensure_init(self):
        self.tickets_dir.mkdir(parents=True, exist_ok=True)
        new_registry = not self.registry_path.exists()
        if new_registry:
            self.registry_path.write_text("[]")
        if self.git_enabled:
            if not (self.root / ".git").exists():
                self._git("init")
            if new_registry:
                self._git("add", "registry.json")
                self._git("commit", "-m", "init vault")

    def _git(self, *args: str):
        if not self.git_enabled:
            return
        subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
        )

    def next_id(self) -> str:
        max_n = 0
        if self.tickets_dir.exists():
            for p in self.tickets_dir.iterdir():
                if p.is_dir() and p.name.startswith("TICKET-"):
                    try:
                        max_n = max(max_n, int(p.name.split("-")[1]))
                    except ValueError:
                        pass
        return f"TICKET-{max_n + 1:03d}"

    def ticket_dir(self, ticket_id: str) -> Path:
        return self.tickets_dir / ticket_id

    def create_ticket(self, title: str, requirements: str, tags: list[str] | None = None,
                      host_functions: list[str] | None = None) -> Ticket:
        ticket = Ticket(
            id=self.next_id(),
            title=title,
            requirements=requirements,
            tags=tags or [],
            host_function_allowlist=host_functions or [],
        )
        d = self.ticket_dir(ticket.id)
        d.mkdir(parents=True)
        (d / "runs").mkdir()
        self.save_ticket(ticket)
        self._update_registry()
        self.commit(ticket.id, "create ticket")
        return ticket

    def save_ticket(self, ticket: Ticket):
        ticket.updated_at = datetime.now()
        d = self.ticket_dir(ticket.id)
        (d / "ticket.yaml").write_text(yaml.dump(ticket.model_dump(mode="json"), default_flow_style=False))
        self._update_registry()

    def load_ticket(self, ticket_id: str) -> Ticket | None:
        p = self.ticket_dir(ticket_id) / "ticket.yaml"
        if not p.exists():
            return None
        return Ticket.model_validate(yaml.safe_load(p.read_text()))

    def write_stage_file(self, ticket_id: str, filename: str, content: str):
        (self.ticket_dir(ticket_id) / filename).write_text(content)

    def read_stage_file(self, ticket_id: str, filename: str) -> str | None:
        p = self.ticket_dir(ticket_id) / filename
        return p.read_text() if p.exists() else None

    def save_run(self, ticket_id: str, record: RunRecord):
        runs_dir = self.ticket_dir(ticket_id) / "runs"
        existing = list(runs_dir.glob("run_*.json"))
        n = len(existing) + 1
        (runs_dir / f"run_{n:03d}.json").write_text(record.model_dump_json(indent=2))

    def commit(self, ticket_id: str, message: str):
        if not self.git_enabled:
            return
        self._git("add", "-A")
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=self.root, capture_output=True,
        )
        if result.returncode != 0:
            self._git("commit", "-m", f"[{ticket_id}] {message}")

    def _update_registry(self):
        entries = []
        if not self.tickets_dir.exists():
            return
        for d in sorted(self.tickets_dir.iterdir()):
            ticket_file = d / "ticket.yaml"
            if ticket_file.exists():
                t = Ticket.model_validate(yaml.safe_load(ticket_file.read_text()))
                entries.append(TicketSummary(
                    id=t.id, title=t.title, tags=t.tags,
                    summary=t.requirements[:120], status=t.status,
                    host_functions=t.host_function_allowlist,
                    input_keys=list(t.input_schema.keys()),
                ).model_dump(mode="json"))
        self.registry_path.write_text(json.dumps(entries, indent=2))

    def search(self, query: str, top_n: int = 5) -> list[TicketSummary]:
        if not self.registry_path.exists():
            return []
        entries = [TicketSummary.model_validate(e) for e in json.loads(self.registry_path.read_text())]
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
        if not self.registry_path.exists():
            return []
        entries = [TicketSummary.model_validate(e) for e in json.loads(self.registry_path.read_text())]
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
