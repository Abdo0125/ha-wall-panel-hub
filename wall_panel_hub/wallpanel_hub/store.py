"""Paired panels, pairing requests and layouts, kept under the add-on's /data."""

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator

PAIRING_TTL_S = 600
MAX_PENDING = 10

_SCHEMA = json.loads((Path(__file__).parent / "layout.schema.json").read_text(encoding="utf-8"))
_VALIDATOR = Draft202012Validator(_SCHEMA)


class LayoutInvalid(ValueError):
    pass


class LayoutConflict(Exception):
    """The panel's layout changed (e.g. edited on the panel) after the draft was started."""

    def __init__(self, current: dict | None):
        super().__init__("layout changed since the draft was started")
        self.current = current


def validate_layout(layout: dict) -> None:
    errors = sorted(_VALIDATOR.iter_errors(layout), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "(root)"
        raise LayoutInvalid(f"{where}: {first.message}")


def layout_entities(layout: dict | None) -> set[str]:
    if not layout:
        return set()
    return {t["entity"] for p in layout.get("pages", []) for t in p.get("tiles", [])}


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass
class Panel:
    id: str
    name: str
    model: str
    key_hash: str
    paired_at: float
    last_seen: float = 0.0


@dataclass
class PairRequest:
    request_id: str
    panel_id: str
    name: str
    model: str
    code: str
    created: float = field(default_factory=time.time)
    # Set once approved; handed to the panel a single time.
    key: str | None = None
    rejected: bool = False


class Store:
    def __init__(self, data_dir: Path):
        self.dir = data_dir
        self.layouts_dir = data_dir / "layouts"
        self.layouts_dir.mkdir(parents=True, exist_ok=True)
        # Editor drafts: changes not yet sent to the panel (the explicit "send", like ETS download).
        self.drafts_dir = data_dir / "drafts"
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.panels_file = data_dir / "panels.json"
        self.panels: dict[str, Panel] = {}
        if self.panels_file.exists():
            raw = json.loads(self.panels_file.read_text(encoding="utf-8"))
            self.panels = {p["id"]: Panel(**p) for p in raw}
        # Pairing requests live in memory only: a restart simply asks the panel to retry.
        self.pending: dict[str, PairRequest] = {}

    # ---- persistence ----

    def _write(self, path: Path, data) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic: a power cut never leaves half a file

    def _save_panels(self) -> None:
        self._write(self.panels_file, [asdict(p) for p in self.panels.values()])

    # ---- pairing ----

    def _expire(self) -> None:
        now = time.time()
        for rid in [r for r, p in self.pending.items() if now - p.created > PAIRING_TTL_S]:
            del self.pending[rid]

    def request_pairing(self, panel_id: str, name: str, model: str) -> PairRequest:
        self._expire()
        # One open request per panel: a retry replaces the previous one.
        for rid in [r for r, p in self.pending.items() if p.panel_id == panel_id]:
            del self.pending[rid]
        while len(self.pending) >= MAX_PENDING:
            del self.pending[min(self.pending.values(), key=lambda p: p.created).request_id]
        req = PairRequest(
            request_id=secrets.token_urlsafe(16),
            panel_id=panel_id,
            name=name[:60] or "Panel",
            model=model[:60],
            code=f"{secrets.randbelow(1_000_000):06d}",
        )
        self.pending[req.request_id] = req
        return req

    def approve(self, request_id: str) -> Panel:
        self._expire()
        req = self.pending[request_id]
        key = secrets.token_urlsafe(32)
        panel = Panel(req.panel_id, req.name, req.model, _hash(key), time.time())
        self.panels[panel.id] = panel
        self._save_panels()
        req.key = key
        return panel

    def reject(self, request_id: str) -> None:
        if request_id in self.pending:
            self.pending[request_id].rejected = True

    def pairing_status(self, request_id: str) -> dict:
        self._expire()
        req = self.pending.get(request_id)
        if req is None:
            return {"status": "expired"}
        if req.rejected:
            del self.pending[request_id]
            return {"status": "rejected"}
        if req.key:
            del self.pending[request_id]  # the key leaves the add-on once, then only its hash remains
            return {"status": "approved", "key": req.key, "panel_id": req.panel_id}
        return {"status": "pending"}

    # ---- panels ----

    def authenticate(self, key: str) -> Panel | None:
        h = _hash(key)
        for panel in self.panels.values():
            if hmac.compare_digest(panel.key_hash, h):
                return panel
        return None

    def seen(self, panel_id: str) -> None:
        if panel_id in self.panels:
            self.panels[panel_id].last_seen = time.time()
            self._save_panels()

    def rename(self, panel_id: str, name: str) -> None:
        self.panels[panel_id].name = name[:60]
        self._save_panels()

    def revoke(self, panel_id: str) -> None:
        self.panels.pop(panel_id, None)
        self._save_panels()
        self.delete_draft(panel_id)

    # ---- layouts ----

    @staticmethod
    def _safe(panel_id: str) -> str:
        return "".join(c for c in panel_id if c.isalnum() or c in "-_")

    def _layout_file(self, panel_id: str) -> Path:
        return self.layouts_dir / f"{self._safe(panel_id)}.json"

    def _draft_file(self, panel_id: str) -> Path:
        return self.drafts_dir / f"{self._safe(panel_id)}.json"

    # ---- drafts (editor) ----

    def get_draft(self, panel_id: str) -> dict | None:
        """{"layout": ..., "base_version": version of the sent layout it started from}."""
        f = self._draft_file(panel_id)
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    def save_draft(self, panel_id: str, layout: dict, base_version: int | None) -> None:
        # Not validated: a draft may be half done. Validation happens on send.
        self._write(self._draft_file(panel_id), {"layout": layout, "base_version": base_version})

    def delete_draft(self, panel_id: str) -> None:
        self._draft_file(panel_id).unlink(missing_ok=True)

    def send_draft(self, panel_id: str, force: bool = False) -> dict:
        """Validates and stores the draft as the panel's layout. Raises LayoutConflict if the
        layout moved on since the draft began, unless forced."""
        draft = self.get_draft(panel_id)
        if draft is None:
            raise KeyError(panel_id)
        current = self.get_layout(panel_id)
        current_version = current["version"] if current else None
        if not force and current_version != draft.get("base_version"):
            raise LayoutConflict(current)
        saved = self.save_layout(panel_id, draft["layout"])
        self.delete_draft(panel_id)
        return saved

    def get_layout(self, panel_id: str) -> dict | None:
        f = self._layout_file(panel_id)
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    def save_layout(self, panel_id: str, layout: dict) -> dict:
        """Stores a new revision. Its version is above both the stored and the submitted one."""
        validate_layout(layout)
        current = self.get_layout(panel_id)
        version = max(layout.get("version", 1), current["version"] if current else 0) + 1
        saved = {**layout, "version": version}
        self._write(self._layout_file(panel_id), saved)
        return saved
