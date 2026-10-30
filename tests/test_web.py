"""阶段 2 Web 层测试：登录、SSE 问答、边界拦截、工具顺序、后台与审计。

依赖是可选的：没装 fastapi 时整组跳过，标准库测试仍可单独跑。
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

from tests.helpers import ROOT, run_demo, temp_dir

try:  # Web 依赖是可选的
    from fastapi.testclient import TestClient

    from expo_trace.web.dataset import Dataset
    from expo_trace.web.settings import Settings
    from expo_trace.web.store import Store
    from expo_trace.web.tools import ToolError, Tools

    WEB_OK = True
except Exception:  # pragma: no cover - 环境缺依赖时跳过
    WEB_OK = False
    TestClient = Dataset = Settings = Store = Tools = None  # type: ignore[assignment]
    ToolError = Exception  # type: ignore[assignment,misc]

PASSWORD = "demo-1234"


@unittest.skipUnless(WEB_OK, "未安装 Web 依赖（pip install -e \".[web]\"）")
class WebBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = temp_dir()
        cls.base = Path(cls.tmp.name)
        # 不能叫 cls.run：会覆盖 unittest.TestCase.run，整个套件在调用测试时崩掉。
        cls.demo = run_demo(cls.base / "run")
        cls.run_dir = cls.demo.store.root
        cls.settings = Settings(
            db_path=cls.base / "app.db",
            run_dir=cls.run_dir,
            secret="test-secret",
            llm_provider="mock",
            embedder="hash",
            seed_password=PASSWORD,
        )
        from expo_trace.web.app import create_app

        cls.app = create_app(settings=cls.settings)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def client(self) -> "TestClient":
        return TestClient(self.app)

    def login(self, client, username: str = "agent") -> str:
        resp = client.post("/api/v1/auth/login",
                           json={"username": username, "password": PASSWORD})
        self.assertEqual(resp.status_code, 200, resp.text[:200])
        return resp.json()["token"]

    def auth(self, client, username: str = "agent") -> dict[str, str]:
        return {"Authorization": "Bearer " + self.login(client, username)}

    def ask(self, client, token: str, question: str, session_id: str) -> list[tuple[str, dict]]:
        with client.stream("POST", "/api/v1/chat", headers={"Authorization": "Bearer " + token},
                           json={"question": question, "session_id": session_id}) as resp:
            self.assertEqual(resp.status_code, 200)
            raw = resp.read().decode("utf-8")
        events = []
        for block in raw.split("\n\n"):
            if not block.strip() or block.startswith("retry:"):
                continue
            event = "message"
            data = None
            for line in block.splitlines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[5:].strip())
            if data is not None:
                events.append((event, data))
        return events

    def new_session(self, client, headers) -> str:
        return client.post("/api/v1/sessions", headers=headers).json()["session_id"]


class ChatTests(WebBase):
    def test_health_and_auth_errors(self) -> None:
        client = self.client()
        health = client.get("/api/v1/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")
        self.assertFalse(health.json()["paused"])

        bad = client.post("/api/v1/auth/login", json={"username": "agent", "password": "wrong"})
        self.assertEqual(bad.status_code, 401)
        # 错误体是扁平的 {code, message}，与 docs/phase2/chat-api.md 第 209 行一致。
        self.assertEqual(bad.json()["code"], "E_AUTH_FAILED")
        self.assertIn("账号或密码", bad.json()["message"])

        self.assertEqual(client.get("/api/v1/auth/me").status_code, 401)

    def test_ticket_answer_carries_version_citation(self) -> None:
        client = self.client()
        headers = self.auth(client)
        sid = self.new_session(client, headers)
        events = self.ask(client, headers["Authorization"].split()[1],
                          "T-1001 的展位安排依据哪一版？", sid)
        kinds = [kind for kind, _ in events]
        self.assertEqual(kinds[0], "meta")
        self.assertIn("status", kinds)
        self.assertIn("citation", kinds)
        self.assertEqual(kinds[-1], "done")

        cite = next(data["citation"] for kind, data in events if kind == "citation")
        self.assertEqual(cite["version_id"], "V-1001")
        self.assertTrue(cite["title"])
        done = events[-1][1]
        self.assertEqual(done["answer_type"], "ok")

        body = "".join(data.get("text", "") for kind, data in events if kind == "delta")
        self.assertIn("V-1001", body)
        self.assertIn("确认人", body)

    def test_boundary_question_is_blocked_not_answered(self) -> None:
        client = self.client()
        headers = self.auth(client)
        sid = self.new_session(client, headers)
        events = self.ask(client, headers["Authorization"].split()[1], "展位费怎么收？", sid)
        done = events[-1][1]
        self.assertEqual(done["answer_type"], "blocked")
        self.assertEqual(done["code"], "E_BOUNDARY_BLOCKED")
        body = "".join(data.get("text", "") for kind, data in events if kind == "delta")
        self.assertIn("不可逆边界", body)
        self.assertIn("审核方", body)

    def test_question_without_evidence_is_refused(self) -> None:
        client = self.client()
        headers = self.auth(client)
        sid = self.new_session(client, headers)
        events = self.ask(client, headers["Authorization"].split()[1],
                          "公司年会在哪里举办？", sid)
        done = events[-1][1]
        self.assertEqual(done["answer_type"], "refused")
        self.assertEqual(done["code"], "E_NO_EVIDENCE")
        body = "".join(data.get("text", "") for kind, data in events if kind == "delta")
        self.assertIn("没有", body)

    def test_draft_flow_composes_and_requests_review(self) -> None:
        client = self.client()
        headers = self.auth(client)
        sid = self.new_session(client, headers)
        events = self.ask(client, headers["Authorization"].split()[1],
                          "T-1007 帮我起草回复", sid)
        done = events[-1][1]
        self.assertEqual(done["answer_type"], "ok", done)
        self.assertTrue(done.get("draft_id"))
        self.assertTrue(done.get("review_id"))
        body = "".join(data.get("text", "") for kind, data in events if kind == "delta")
        self.assertIn("【未确认 · 不可对外】", body)
        self.assertIn("已提交人工复核", body)

    def test_paused_pilot_blocks_all_answers(self) -> None:
        client = self.client()
        headers = self.auth(client)
        sid = self.new_session(client, headers)
        pause_file = self.run_dir / "paused.json"
        pause_file.write_text(json.dumps({"actor": "傅景", "reason": "抽检发现误报"}),
                              encoding="utf-8")
        try:
            events = self.ask(client, headers["Authorization"].split()[1],
                              "T-1001 的时间安排？", sid)
        finally:
            pause_file.unlink(missing_ok=True)
        done = events[-1][1]
        self.assertEqual(done["answer_type"], "blocked")
        self.assertEqual(done["code"], "E_PILOT_PAUSED")

    def test_messages_persist_with_citations(self) -> None:
        client = self.client()
        headers = self.auth(client)
        sid = self.new_session(client, headers)
        self.ask(client, headers["Authorization"].split()[1], "T-1001 依据哪版？", sid)
        rows = client.get(f"/api/v1/sessions/{sid}/messages", headers=headers).json()["messages"]
        self.assertEqual([row["role"] for row in rows], ["user", "assistant"])
        self.assertTrue(rows[1]["citations"])
        self.assertEqual(rows[1]["citations"][0]["version_id"], "V-1001")


class ToolTests(WebBase):
    def tools(self) -> Tools:
        store = Store(self.settings.db_path)
        return Tools(store, Dataset(self.run_dir), self.settings, actor="agent", session_id="s")

    def test_order_violation_before_check_fields(self) -> None:
        tools = self.tools()
        with self.assertRaises(ToolError) as ctx:
            tools.call("compose_draft", ticket_id="T-1007", context_token="x")
        self.assertEqual(ctx.exception.code, "E_ORDER_VIOLATION")

    def test_token_is_single_use(self) -> None:
        tools = self.tools()
        version_id = Dataset(self.run_dir).resolution("T-1007")["version_id"]
        checked = tools.call("check_fields", ticket_id="T-1007", version_id=version_id)
        self.assertTrue(checked["ok"])
        token = checked["context_token"]
        draft = tools.call("compose_draft", ticket_id="T-1007", context_token=token)
        self.assertIn("草稿", draft["body"] + "草稿")
        # 令牌在提交人工复核时消费：Agent 链路要复用同一个令牌走完
        # check_fields → compose_draft → request_human_review，compose 阶段不能消费。
        tools.call("request_human_review", ticket_id="T-1007",
                   draft_id=draft["draft_id"], context_token=token)
        with self.assertRaises(ToolError) as ctx:
            tools.call("compose_draft", ticket_id="T-1007", context_token=token)
        self.assertEqual(ctx.exception.code, "E_CONTEXT_USED")

    def test_compose_without_prior_check_is_rejected(self) -> None:
        tools = self.tools()
        with self.assertRaises(ToolError) as ctx:
            tools.call("compose_draft", ticket_id="T-1001", context_token="bad-token")
        self.assertIn(ctx.exception.code, {"E_ORDER_VIOLATION", "E_CONTEXT_INVALID"})

    def test_pilot_status_reads_pause_file(self) -> None:
        tools = self.tools()
        self.assertFalse(tools.call("get_pilot_status")["paused"])
        pause_file = self.run_dir / "paused.json"
        pause_file.write_text("{}", encoding="utf-8")
        try:
            self.assertTrue(tools.call("get_pilot_status")["paused"])
        finally:
            pause_file.unlink(missing_ok=True)


class AdminTests(WebBase):
    def admin(self):
        client = self.client()
        resp = client.post("/admin/login", data={"username": "admin", "password": PASSWORD},
                           follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        return client

    def csrf(self, client, path: str = "/admin/prompts") -> str:
        page = client.get(path).text
        match = re.search(r'name="csrf" value="([^"]+)"', page)
        self.assertIsNotNone(match, "页面未渲染 CSRF 令牌")
        return match.group(1)

    def test_staff_cannot_open_admin(self) -> None:
        client = self.client()
        client.post("/api/v1/auth/login", json={"username": "agent", "password": PASSWORD})
        resp = client.get("/admin", follow_redirects=False)
        self.assertEqual(resp.status_code, 403)

    def test_prompt_publish_validates_keywords_and_takes_effect(self) -> None:
        client = self.admin()
        token = self.csrf(client)
        bad = client.post("/admin/prompts", data={"content": "太短", "csrf": token})
        self.assertEqual(bad.status_code, 200)
        self.assertIn("未发布", bad.text)

        content = ("你是展会咨询工单助手，只依据检索到的版本资料回答；"
                   "不得编造，资料中没有时明确说资料中没有；收费与资格问题转人工。")
        good = client.post("/admin/prompts", data={"content": content, "csrf": token},
                           follow_redirects=False)
        self.assertEqual(good.status_code, 303)
        self.assertEqual(Store(self.settings.db_path).prompt_active()["content"], content)

    def test_kb_upload_search_and_retire(self) -> None:
        client = self.admin()
        token = self.csrf(client, "/admin/kb")
        markdown = ("# 展会现场规则\n\n## 一、住宿\n\n参展商住宿标准为 650 元，超出部分自理。\n\n"
                    "## 二、发票\n\n发票须在 30 天内提交，逾期不予受理。\n")
        resp = client.post("/admin/kb/upload",
                           data={"title": "现场规则", "scope": "all", "version_label": "v1",
                                 "csrf": token},
                           files={"file": ("rules.md", markdown.encode("utf-8"), "text/markdown")},
                           follow_redirects=False)
        self.assertEqual(resp.status_code, 303)

        store = Store(self.settings.db_path)
        self.assertEqual(len(store.kb_list()), 1)
        from expo_trace.web import kb

        hits = kb.search(store, "住宿标准多少", top_k=1)
        self.assertTrue(hits)
        self.assertIn("650", hits[0].text)

        doc_id = store.kb_list()[0]["id"]
        retired = client.post(f"/admin/kb/{doc_id}/delete", data={"csrf": token},
                              follow_redirects=False)
        self.assertEqual(retired.status_code, 303)
        self.assertEqual(store.kb_list()[0]["status"], "retired")

    def test_flagged_conversation_and_audit(self) -> None:
        client = self.admin()
        headers = self.auth(self.client())
        staff = self.client()
        staff_headers = self.auth(staff)
        sid = self.new_session(staff, staff_headers)
        self.ask(staff, staff_headers["Authorization"].split()[1], "T-1001 依据哪版？", sid)

        page = client.get("/admin/conversations").text
        self.assertIn("对话审计", page)
        # staff 账号是 agent；审计页面要能列出这条对话及其问题
        self.assertIn("agent", page)
        self.assertIn("T-1001 依据哪版？", page)
        self.assertEqual(client.get("/admin/audit").status_code, 200)
        audit = Store(self.settings.db_path).verify_audit()
        self.assertTrue(audit["ok"])
        self.assertGreaterEqual(audit["entries"], 3)
        actions = [row["action"] for row in Store(self.settings.db_path).audit_rows()]
        self.assertIn("login", actions)
        self.assertIn("chat_answered", actions)

    def test_feedback_endpoint_records(self) -> None:
        client = self.client()
        headers = self.auth(client)
        sid = self.new_session(client, headers)
        self.ask(client, headers["Authorization"].split()[1], "T-1001 依据哪版？", sid)
        messages = client.get(f"/api/v1/sessions/{sid}/messages", headers=headers).json()["messages"]
        message_id = messages[-1]["id"]
        resp = client.post("/api/v1/feedback", headers=headers,
                           json={"message_id": message_id, "kind": "未确认", "note": "引用版本需复核"})
        self.assertEqual(resp.status_code, 200)
        store = Store(self.settings.db_path)
        self.assertEqual(len(store.feedback_rows()), 1)


if __name__ == "__main__":
    unittest.main()
