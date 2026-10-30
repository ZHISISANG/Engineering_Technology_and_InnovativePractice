"""SQLite 存储层：会话、消息、引用、知识库、提示词、标记与审计链。

设计取舍
--------
- 单文件 SQLite + WAL：试点是单机、低并发，运维成本比 PostgreSQL 低一个量级；
  上量后按 docs/phase2/chat-api.md 的迁移路径换库。
- 审计链落在库里：每条 {seq, at, actor, action, subject, details, prev, hash}，
  hash = sha256(prev + 规范化 JSON)，与 CLI 的 audit.jsonl 同一套规则。
- 向量以 JSON 文本存 kb_chunks.vector：试点语料小（几十万字），
  读进内存算余弦足够；换 pgvector 时这一列迁成 vector 类型即可。
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from ..utils.jsons import canonical_json
from ..utils.timeutil import LOCAL_TZ, iso
from .security import check_password, hash_password

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  role TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL,
  disabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(user_id) REFERENCES users(id));
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL,
  content TEXT NOT NULL, answer_type TEXT NOT NULL DEFAULT 'ok',
  error_code TEXT NOT NULL DEFAULT '', blocked INTEGER NOT NULL DEFAULT 0,
  provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
  latency_ms INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(id));
CREATE TABLE IF NOT EXISTS citations (
  id TEXT PRIMARY KEY, message_id TEXT NOT NULL, kind TEXT NOT NULL,
  ref_id TEXT NOT NULL, version_id TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '', snippet TEXT NOT NULL DEFAULT '',
  effective_from TEXT NOT NULL DEFAULT '', effective_to TEXT NOT NULL DEFAULT '',
  confirmed_by TEXT NOT NULL DEFAULT '', doc_id TEXT NOT NULL DEFAULT '',
  chunk_id TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS feedback (
  id TEXT PRIMARY KEY, message_id TEXT NOT NULL, kind TEXT NOT NULL,
  reporter TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL,
  action TEXT NOT NULL, subject TEXT NOT NULL, details TEXT NOT NULL,
  prev TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kb_documents (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, filename TEXT NOT NULL,
  sha256 TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
  scope TEXT NOT NULL DEFAULT 'all', version_label TEXT NOT NULL DEFAULT '',
  effective_from TEXT NOT NULL DEFAULT '', effective_to TEXT NOT NULL DEFAULT '',
  bytes INTEGER NOT NULL DEFAULT 0, chunks INTEGER NOT NULL DEFAULT 0,
  uploaded_by TEXT NOT NULL, uploaded_at TEXT NOT NULL, retired_at TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS kb_chunks (
  id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, heading TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL, position INTEGER NOT NULL, vector TEXT NOT NULL,
  tokens TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON kb_chunks(doc_id);
CREATE TABLE IF NOT EXISTS prompt_templates (
  id TEXT PRIMARY KEY, key TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL,
  version INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  updated_by TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_version ON prompt_templates(key, version);
CREATE TABLE IF NOT EXISTS flags (
  id TEXT PRIMARY KEY, message_id TEXT NOT NULL, reason TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '', marked_by TEXT NOT NULL, created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open');
CREATE TABLE IF NOT EXISTS used_tokens (
  jti TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, version_id TEXT NOT NULL,
  actor TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  used_at TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS drafts (
  id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, version_id TEXT NOT NULL,
  body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
  created_by TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, ticket_id TEXT NOT NULL,
  requested_by TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
  created_at TEXT NOT NULL);
"""

DEFAULT_PROMPT = (
    "你是展会咨询工单助手，只根据检索到的展会通知与规则版本回答。\n"
    "1) 先确认版本：回答必须引用版本号、通知编号与有效期；无法定位当前版本时明确说“当前版本无法确认”，不得猜测。\n"
    "2) 只使用提供的资料；资料中没有的，回答“资料中没有相关规定”，不得编造时间、费用或资格口径。\n"
    "3) 涉及收费、费用、报价、折扣、退款、参展资格、押金的问题，不给出对外结论，提示转人工确认。\n"
    "4) 回答末尾列出引用来源；不得把历史消息里的过期版本当作现行版本。\n"
    "5) 不承诺、不审批、不代替审核方做决定。"
)


def now_iso() -> str:
    return iso(datetime.now(tz=LOCAL_TZ)) or ""


class Store:
    def __init__(self, db_path: Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as db:
            db.executescript(SCHEMA)

    # -- 连接 -------------------------------------------------------------
    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    # -- 初始化与用户 -----------------------------------------------------
    def seed(self, password: str) -> None:
        users = [
            ("admin", "系统管理员", "admin"),
            ("reviewer", "审核与质量负责人", "reviewer"),
            ("agent", "一线服务专员", "staff"),
        ]
        with self.conn() as db:
            for username, name, role in users:
                row = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
                if row:
                    continue
                db.execute(
                    "INSERT INTO users(id, username, name, role, password_hash, created_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (uuid.uuid4().hex, username, name, role, hash_password(password), now_iso()),
                )
            if not db.execute("SELECT 1 FROM prompt_templates WHERE active=1").fetchone():
                db.execute(
                    "INSERT INTO prompt_templates(id,key,title,content,version,active,updated_by,updated_at)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, "chat_system", "问答系统提示词", DEFAULT_PROMPT, 1, 1,
                     "system", now_iso()),
                )

    def user(self, username: str) -> sqlite3.Row | None:
        with self.conn() as db:
            return db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

    def user_by_id(self, user_id: str) -> sqlite3.Row | None:
        with self.conn() as db:
            return db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

    def authenticate(self, username: str, password: str) -> sqlite3.Row | None:
        row = self.user(username)
        if not row or row["disabled"] or not check_password(password, row["password_hash"]):
            return None
        return row

    # -- 会话与消息 -------------------------------------------------------
    def create_session(self, user_id: str, title: str = "") -> str:
        sid = uuid.uuid4().hex
        stamp = now_iso()
        with self.conn() as db:
            db.execute(
                "INSERT INTO sessions(id,user_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                (sid, user_id, title, stamp, stamp),
            )
        return sid

    def session(self, sid: str) -> sqlite3.Row | None:
        with self.conn() as db:
            return db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()

    def touch_session(self, sid: str, title: str = "") -> None:
        with self.conn() as db:
            if title:
                db.execute("UPDATE sessions SET updated_at=?, title=? WHERE id=?",
                           (now_iso(), title[:120], sid))
            else:
                db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now_iso(), sid))

    def add_message(self, session_id: str, role: str, content: str, **extra: Any) -> str:
        mid = uuid.uuid4().hex
        with self.conn() as db:
            db.execute(
                "INSERT INTO messages(id,session_id,role,content,answer_type,error_code,blocked,"
                "provider,model,latency_ms,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (mid, session_id, role, content, extra.get("answer_type", "ok"),
                 extra.get("error_code", ""), int(bool(extra.get("blocked"))),
                 extra.get("provider", ""), extra.get("model", ""),
                 int(extra.get("latency_ms", 0)), now_iso()),
            )
        return mid

    def history(self, session_id: str, limit: int = 12) -> list[sqlite3.Row]:
        with self.conn() as db:
            rows = db.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return list(reversed(rows))

    def add_citation(self, message_id: str, **kw: Any) -> None:
        with self.conn() as db:
            db.execute(
                "INSERT INTO citations(id,message_id,kind,ref_id,version_id,title,snippet,"
                "effective_from,effective_to,confirmed_by,doc_id,chunk_id)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, message_id, kw.get("kind", "policy"), kw.get("ref_id", ""),
                 kw.get("version_id", ""), kw.get("title", ""), kw.get("snippet", "")[:600],
                 kw.get("effective_from", ""), kw.get("effective_to", ""),
                 kw.get("confirmed_by", ""), kw.get("doc_id", ""), kw.get("chunk_id", "")),
            )

    def citations(self, message_id: str) -> list[sqlite3.Row]:
        with self.conn() as db:
            return db.execute("SELECT * FROM citations WHERE message_id=?", (message_id,)).fetchall()

    # -- 审计链 -----------------------------------------------------------
    def audit(self, action: str, actor: str, subject: str, details: dict[str, Any] | None = None,
              at: str | None = None) -> int:
        stamp = at or now_iso()
        with self.conn() as db:
            last = db.execute("SELECT seq, hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
            prev = last["hash"] if last else "0" * 64
            body = {"at": stamp, "actor": actor, "action": action, "subject": subject,
                    "details": details or {}, "prev": prev}
            digest = __import__("hashlib").sha256(
                (prev + canonical_json(body)).encode()).hexdigest()
            cur = db.execute(
                "INSERT INTO audit_events(at,actor,action,subject,details,prev,hash)"
                " VALUES(?,?,?,?,?,?,?)",
                (stamp, actor, action, subject, canonical_json(details or {}), prev, digest),
            )
            return int(cur.lastrowid)

    def audit_rows(self, limit: int = 200) -> list[sqlite3.Row]:
        with self.conn() as db:
            return db.execute(
                "SELECT * FROM audit_events ORDER BY seq DESC LIMIT ?", (limit,)
            ).fetchall()

    def verify_audit(self) -> dict[str, Any]:
        import hashlib
        with self.conn() as db:
            rows = db.execute("SELECT * FROM audit_events ORDER BY seq ASC").fetchall()
        prev = "0" * 64
        for row in rows:
            body = {"at": row["at"], "actor": row["actor"], "action": row["action"],
                    "subject": row["subject"],
                    "details": json.loads(row["details"]), "prev": prev}
            want = hashlib.sha256((prev + canonical_json(body)).encode()).hexdigest()
            if row["prev"] != prev or row["hash"] != want:
                return {"ok": False, "entries": len(rows), "broken_at": row["seq"]}
            prev = row["hash"]
        return {"ok": True, "entries": len(rows), "broken_at": None}

    # -- 知识库 -----------------------------------------------------------
    def kb_add(self, *, title: str, filename: str, sha256: str, chunks: list[dict[str, Any]],
               scope: str, version_label: str, effective_from: str, effective_to: str,
               size: int, uploaded_by: str) -> str:
        doc_id = uuid.uuid4().hex
        stamp = now_iso()
        with self.conn() as db:
            db.execute(
                "INSERT INTO kb_documents(id,title,filename,sha256,status,scope,version_label,"
                "effective_from,effective_to,bytes,chunks,uploaded_by,uploaded_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, title, filename, sha256, "active", scope, version_label,
                 effective_from, effective_to, size, len(chunks), uploaded_by, stamp),
            )
            for i, chunk in enumerate(chunks):
                db.execute(
                    "INSERT INTO kb_chunks(id,doc_id,heading,text,position,vector,tokens)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, doc_id, chunk["heading"], chunk["text"], i,
                     json.dumps(chunk["vector"]), " ".join(chunk["tokens"])),
                )
        return doc_id

    def feedback_rows(self, limit: int = 200) -> list[sqlite3.Row]:
        with self.conn() as db:
            return db.execute(
                "SELECT * FROM feedback ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()

    def kb_list(self) -> list[sqlite3.Row]:
        with self.conn() as db:
            return db.execute("SELECT * FROM kb_documents ORDER BY uploaded_at DESC").fetchall()

    def kb_retire(self, doc_id: str) -> bool:
        with self.conn() as db:
            cur = db.execute(
                "UPDATE kb_documents SET status='retired', retired_at=? WHERE id=? AND status='active'",
                (now_iso(), doc_id),
            )
            return cur.rowcount > 0

    def kb_active_chunks(self) -> list[sqlite3.Row]:
        with self.conn() as db:
            return db.execute(
                "SELECT c.*, d.title, d.version_label, d.scope, d.effective_from, d.effective_to,"
                " d.status FROM kb_chunks c JOIN kb_documents d ON d.id=c.doc_id"
                " WHERE d.status='active'"
            ).fetchall()

    # -- 提示词模板 -------------------------------------------------------
    def prompt_active(self) -> sqlite3.Row:
        with self.conn() as db:
            row = db.execute(
                "SELECT * FROM prompt_templates WHERE key='chat_system' AND active=1"
                " ORDER BY version DESC LIMIT 1").fetchone()
        return row

    def prompt_versions(self) -> list[sqlite3.Row]:
        with self.conn() as db:
            return db.execute(
                "SELECT * FROM prompt_templates WHERE key='chat_system' ORDER BY version DESC"
            ).fetchall()

    def prompt_publish(self, content: str, actor: str) -> int:
        with self.conn() as db:
            last = db.execute(
                "SELECT MAX(version) AS v FROM prompt_templates WHERE key='chat_system'"
            ).fetchone()
            version = int(last["v"] or 0) + 1
            db.execute("UPDATE prompt_templates SET active=0 WHERE key='chat_system'")
            db.execute(
                "INSERT INTO prompt_templates(id,key,title,content,version,active,updated_by,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, "chat_system", "问答系统提示词", content, version, 1, actor,
                 now_iso()),
            )
        return version

    # -- 标记与审计查询 ---------------------------------------------------
    def add_flag(self, message_id: str, reason: str, note: str, actor: str) -> str:
        fid = uuid.uuid4().hex
        with self.conn() as db:
            db.execute(
                "INSERT INTO flags(id,message_id,reason,note,marked_by,created_at,status)"
                " VALUES(?,?,?,?,?,?, 'open')",
                (fid, message_id, reason, note, actor, now_iso()),
            )
        return fid

    def flags_for(self, message_id: str) -> list[sqlite3.Row]:
        with self.conn() as db:
            return db.execute("SELECT * FROM flags WHERE message_id=?", (message_id,)).fetchall()

    def conversations(self, *, username: str = "", date_from: str = "", date_to: str = "",
                      only_flagged: bool = False, limit: int = 100) -> list[sqlite3.Row]:
        sql = (
            "SELECT m.*, s.title, u.username, u.name,"
            " (SELECT COUNT(*) FROM flags f WHERE f.message_id=m.id) AS flag_count"
            " FROM messages m JOIN sessions s ON s.id=m.session_id"
            " JOIN users u ON u.id=s.user_id WHERE m.role='assistant'"
        )
        args: list[Any] = []
        if username:
            sql += " AND u.username=?"
            args.append(username)
        if date_from:
            sql += " AND m.created_at>=?"
            args.append(date_from)
        if date_to:
            sql += " AND m.created_at<=?"
            args.append(date_to + " 23:59:59+08:00")
        if only_flagged:
            sql += " AND EXISTS (SELECT 1 FROM flags f WHERE f.message_id=m.id)"
        sql += " ORDER BY m.created_at DESC LIMIT ?"
        args.append(limit)
        with self.conn() as db:
            return db.execute(sql, args).fetchall()

    # -- 草稿与复核 -------------------------------------------------------
    def draft_add(self, ticket_id: str, version_id: str, body: str, actor: str) -> str:
        did = uuid.uuid4().hex
        with self.conn() as db:
            db.execute(
                "INSERT INTO drafts(id,ticket_id,version_id,body,status,created_by,created_at)"
                " VALUES(?,?,?,?, 'draft',?,?)",
                (did, ticket_id, version_id, body, actor, now_iso()),
            )
        return did

    def draft(self, draft_id: str) -> sqlite3.Row | None:
        with self.conn() as db:
            return db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()

    def review_add(self, draft_id: str, ticket_id: str, actor: str) -> str:
        rid = uuid.uuid4().hex
        with self.conn() as db:
            db.execute(
                "INSERT INTO reviews(id,draft_id,ticket_id,requested_by,status,created_at)"
                " VALUES(?,?,?,?, 'queued',?)",
                (rid, draft_id, ticket_id, actor, now_iso()),
            )
        return rid

    # -- 一次性上下文令牌 -------------------------------------------------
    def token_issue(self, jti: str, ticket_id: str, version_id: str, actor: str,
                    expires_at: str) -> None:
        with self.conn() as db:
            db.execute(
                "INSERT INTO used_tokens(jti,ticket_id,version_id,actor,created_at,expires_at)"
                " VALUES(?,?,?,?,?,?)",
                (jti, ticket_id, version_id, actor, now_iso(), expires_at),
            )

    def token_get(self, jti: str) -> sqlite3.Row | None:
        with self.conn() as db:
            return db.execute("SELECT * FROM used_tokens WHERE jti=?", (jti,)).fetchone()

    def token_consume(self, jti: str) -> None:
        with self.conn() as db:
            db.execute("UPDATE used_tokens SET used_at=? WHERE jti=? AND used_at=''",
                       (now_iso(), jti))


def new_store(path: Path, password: str) -> Store:
    store = Store(path)
    store.seed(password)
    return store
