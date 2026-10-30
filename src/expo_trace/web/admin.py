"""管理后台：服务端渲染，JWT Cookie + CSRF 双提交 + 角色校验。

职责分离：admin 改知识库/提示词/开关；reviewer 只能看审计与标记。
两者都能看概览与审计链。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from ..utils.timeutil import LOCAL_TZ, iso
from . import pages
from .chat import user_from
from .pages import Raw, esc, table
from .security import check_password, csrf_token, sign, verify
from .settings import Settings

router = APIRouter(prefix="/admin")

PROMPT_KEYWORDS = ("版本", "不得编造", "资料中没有", "收费")
MAX_UPLOAD = 1_000_000


def _user(request: Request) -> dict[str, Any] | None:
    try:
        return user_from(request)
    except Exception:
        return None


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _csrf(request: Request, user: dict[str, Any]) -> str:
    return csrf_token(request.app.state.settings.secret, user["jti"])


def _guard(request: Request, roles: tuple[str, ...] = ("admin", "reviewer")):
    user = _user(request)
    if not user:
        return None, _redirect("/admin/login")
    if user["role"] not in roles:
        return None, HTMLResponse(pages.layout(
            "无权限", "<section class=\"auth\"><h1>403</h1><p>当前账号没有该页面权限。</p>"
            "<p><a href=\"/chat\">回到问答</a></p></section>", user=user), status_code=403)
    return user, None


def _verify_csrf(request: Request, user: dict[str, Any], form_token: str) -> bool:
    expected = _csrf(request, user)
    return bool(form_token) and form_token == expected and \
        request.cookies.get("expo_csrf", "") == expected


def _page(request: Request, user: dict[str, Any], title: str, body: str,
          notice: str = "", error: str = "") -> HTMLResponse:
    return HTMLResponse(pages.admin_page(title, body, user, _csrf(request, user), notice, error))


def _notice(request: Request) -> tuple[str, str]:
    return request.query_params.get("ok", ""), request.query_params.get("err", "")


# -- 登录/登出 ------------------------------------------------------------
@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> Response:
    user = _user(request)
    if user:
        return _redirect("/admin" if user["role"] in ("admin", "reviewer") else "/chat")
    return HTMLResponse(pages.login_page())


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    store = request.app.state.store
    row = store.authenticate(username, password)
    if not row:
        store.audit("login_failed", username, "auth", {"username": username})
        return HTMLResponse(pages.login_page("账号或密码不正确"), status_code=401)
    jti = __import__("uuid").uuid4().hex
    token = sign({"sub": row["id"], "role": row["role"], "jti": jti},
                 request.app.state.settings.secret)
    store.audit("login", row["username"], "auth", {"role": row["role"]})
    target = "/admin" if row["role"] in ("admin", "reviewer") else "/chat"
    resp = _redirect(target)
    resp.set_cookie("expo_token", token, httponly=True, samesite="lax", max_age=720 * 3600)
    resp.set_cookie("expo_csrf", csrf_token(request.app.state.settings.secret, jti),
                    samesite="lax", max_age=720 * 3600)
    return resp


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    resp = _redirect("/admin/login")
    resp.delete_cookie("expo_token")
    resp.delete_cookie("expo_csrf")
    return resp


# -- 概览 ----------------------------------------------------------------
@router.get("", response_class=HTMLResponse)
def dashboard(request: Request) -> Response:
    user, response = _guard(request)
    if response:
        return response
    store = request.app.state.store
    with store.conn() as db:
        sessions = db.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
        messages = db.execute("SELECT COUNT(*) AS n FROM messages WHERE role='assistant'").fetchone()["n"]
        flagged = db.execute("SELECT COUNT(DISTINCT message_id) AS n FROM flags").fetchone()["n"]
        docs = db.execute("SELECT COUNT(*) AS n FROM kb_documents WHERE status='active'").fetchone()["n"]
        blocked = db.execute("SELECT COUNT(*) AS n FROM messages WHERE blocked=1").fetchone()["n"]
        recent = db.execute(
            "SELECT m.id, m.created_at, m.answer_type, m.blocked, u.username, s.title"
            " FROM messages m JOIN sessions s ON s.id=m.session_id JOIN users u ON u.id=s.user_id"
            " WHERE m.role='assistant' ORDER BY m.created_at DESC LIMIT 8").fetchall()
    paused = (request.app.state.dataset.run_dir / "paused.json").exists()
    cards = (
        "<div class=\"cards\">"
        f"<div class=\"card\"><b>{sessions}</b><span>会话</span></div>"
        f"<div class=\"card\"><b>{messages}</b><span>回答</span></div>"
        f"<div class=\"card warn\"><b>{blocked}</b><span>被拦截</span></div>"
        f"<div class=\"card\"><b>{flagged}</b><span>已标记</span></div>"
        f"<div class=\"card\"><b>{docs}</b><span>知识库文档</span></div>"
        f"<div class=\"card {'danger' if paused else ''}\"><b>{'已暂停' if paused else '运行中'}</b>"
        "<span>试点状态</span></div></div>"
    )
    rows = [(r["created_at"][:16], r["username"], r["title"][:24] or "-", r["answer_type"],
             "是" if r["blocked"] else "否") for r in recent]
    body = cards + "<h2>最近回答</h2>" + table(("时间", "账号", "会话", "类型", "拦截"), rows)
    return _page(request, user, "概览", body, *_notice(request))


# -- 知识库 --------------------------------------------------------------
@router.get("/kb", response_class=HTMLResponse)
def kb_page(request: Request) -> Response:
    user, response = _guard(request)
    if response:
        return response
    store = request.app.state.store
    rows = []
    for doc in store.kb_list():
        action = (
            f"<form method=\"post\" action=\"/admin/kb/{esc(doc['id'])}/delete\" class=\"inline\">"
            f"<input type=\"hidden\" name=\"csrf\" value=\"{esc(_csrf(request, user))}\">"
            "<button class=\"link danger\" type=\"submit\">停用</button></form>"
            if doc["status"] == "active" else "<span class=\"muted\">已停用</span>"
        )
        rows.append((doc["title"], doc["version_label"] or "-", doc["scope"],
                     f"{doc['chunks']} 片", doc["status"], doc["uploaded_at"][:16], Raw(action)))
    form = f"""
<form method="post" action="/admin/kb/upload" enctype="multipart/form-data" class="grid-form">
  <input type="hidden" name="csrf" value="{esc(_csrf(request, user))}">
  <label>文件（Markdown）<input type="file" name="file" accept=".md,.markdown" required></label>
  <label>标题<input name="title" placeholder="如 2026-S1 展位调整通知"></label>
  <label>适用范围<input name="scope" value="2026-S1" placeholder="批次或 all"></label>
  <label>版本标签<input name="version_label" placeholder="如 N-2026-07 / v2"></label>
  <label>生效起<input name="effective_from" placeholder="2026-07-01"></label>
  <label>生效止<input name="effective_to" placeholder="2026-12-31"></label>
  <button type="submit">上传并切片</button>
</form>"""
    body = form + table(("标题", "版本", "范围", "切片", "状态", "上传时间", "操作"), rows)
    return _page(request, user, "知识库", body, *_notice(request))


@router.post("/kb/upload")
async def kb_upload(request: Request, file: UploadFile = File(...), title: str = Form(""),
                    scope: str = Form("all"), version_label: str = Form(""),
                    effective_from: str = Form(""), effective_to: str = Form(""),
                    csrf: str = Form("")):
    user, response = _guard(request, ("admin",))
    if response:
        return response
    if not _verify_csrf(request, user, csrf):
        return _page(request, user, "知识库", "<p>表单校验失败，请重试。</p>",
                     error="CSRF 校验失败")
    raw = await file.read()
    name = (file.filename or "upload.md").replace("\\", "/").split("/")[-1]
    if not name.lower().endswith((".md", ".markdown")):
        return _page(request, user, "知识库", "<p>只接受 Markdown 文件。</p>",
                     error="首版只支持 Markdown，PDF 请先转换")
    if len(raw) > MAX_UPLOAD:
        return _page(request, user, "知识库", "<p>文件超过 1 MB。</p>", error="文件过大")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _page(request, user, "知识库", "<p>文件必须是 UTF-8。</p>", error="编码不正确")
    from .kb import ingest_markdown
    try:
        doc_id, count = ingest_markdown(
            request.app.state.store, text=text, filename=name, title=title,
            uploaded_by=user["username"], scope=scope or "all", version_label=version_label,
            effective_from=effective_from, effective_to=effective_to,
            provider=request.app.state.settings.embedder)
    except ValueError as exc:
        return _page(request, user, "知识库", f"<p>{esc(str(exc))}</p>", error="切片失败")
    request.app.state.store.audit("kb_uploaded", user["username"], doc_id,
                                  {"filename": name, "chunks": count})
    return _redirect(f"/admin/kb?ok=已入库 {count} 片")


@router.post("/kb/{doc_id}/delete")
def kb_delete(request: Request, doc_id: str, csrf: str = Form("")):
    user, response = _guard(request, ("admin",))
    if response:
        return response
    if not _verify_csrf(request, user, csrf):
        return _page(request, user, "知识库", "<p>表单校验失败。</p>", error="CSRF 校验失败")
    ok = request.app.state.store.kb_retire(doc_id)
    request.app.state.store.audit("kb_retired", user["username"], doc_id, {"ok": ok})
    return _redirect("/admin/kb?ok=已停用" if ok else "/admin/kb?err=文档不存在")


# -- 提示词 --------------------------------------------------------------
@router.get("/prompts", response_class=HTMLResponse)
def prompts_page(request: Request) -> Response:
    user, response = _guard(request)
    if response:
        return response
    store = request.app.state.store
    active = store.prompt_active()
    history = [(row["version"], row["updated_by"], row["updated_at"][:16],
                "启用" if row["active"] else "") for row in store.prompt_versions()]
    form = f"""
<form method="post" action="/admin/prompts" class="stack">
  <input type="hidden" name="csrf" value="{esc(_csrf(request, user))}">
  <label>系统提示词（保存后立即生效，旧版本保留）
    <textarea name="content" rows="14" required>{esc(active['content'])}</textarea></label>
  <p class="muted small">发布前校验关键词：{esc('、'.join(PROMPT_KEYWORDS))}</p>
  <button type="submit">发布新版本</button>
</form>"""
    body = form + "<h2>版本历史</h2>" + table(("版本", "修改人", "时间", "状态"), history)
    return _page(request, user, "提示词模板", body, *_notice(request))


@router.post("/prompts")
def prompts_publish(request: Request, content: str = Form(""), csrf: str = Form("")):
    user, response = _guard(request, ("admin",))
    if response:
        return response
    if not _verify_csrf(request, user, csrf):
        return _page(request, user, "提示词模板", "<p>表单校验失败。</p>", error="CSRF 校验失败")
    missing = [word for word in PROMPT_KEYWORDS if word not in content]
    if len(content) < 50 or missing:
        return _page(request, user, "提示词模板",
                     "<p>提示词缺少必需要素，未发布。</p>",
                     error=f"长度不足或缺少关键词：{'、'.join(missing) or '长度'}")
    version = request.app.state.store.prompt_publish(content, user["username"])
    request.app.state.store.audit("prompt_published", user["username"], str(version),
                                  {"version": version, "chars": len(content)})
    return _redirect(f"/admin/prompts?ok=已发布 v{version}")


# -- 对话审计 ------------------------------------------------------------
@router.get("/conversations", response_class=HTMLResponse)
def conversations(request: Request) -> Response:
    user, response = _guard(request)
    if response:
        return response
    store = request.app.state.store
    username = request.query_params.get("username", "")
    date_from = request.query_params.get("from", "")
    date_to = request.query_params.get("to", "")
    flagged = request.query_params.get("flagged", "") == "1"
    rows = store.conversations(username=username, date_from=date_from, date_to=date_to,
                               only_flagged=flagged)
    cells = []
    for row in rows:
        flags = store.flags_for(row["id"])
        badge = f"<span class=\"badge warn\">{len(flags)}</span>" if flags else ""
        form = (
            f"<form method=\"post\" action=\"/admin/flags\" class=\"inline\">"
            f"<input type=\"hidden\" name=\"csrf\" value=\"{esc(_csrf(request, user))}\">"
            f"<input type=\"hidden\" name=\"message_id\" value=\"{esc(row['id'])}\">"
            "<select name=\"reason\"><option>疑似越权承诺</option><option>版本引用错误</option>"
            "<option>答非所问</option><option>其他</option></select>"
            "<input name=\"note\" placeholder=\"备注\" size=\"8\">"
            "<button class=\"link\" type=\"submit\">标记</button></form>"
        )
        cells.append((row["created_at"][:16], row["username"], row["title"][:18] or "-",
                      row["answer_type"], row["content"][:60].replace("\n", " ") + "…",
                      Raw(badge + form)))
    form = f"""
<form method="get" action="/admin/conversations" class="filters">
  <input name="username" placeholder="账号" value="{esc(username)}">
  <input name="from" placeholder="起 2026-10-20" value="{esc(date_from)}">
  <input name="to" placeholder="止 2026-10-20" value="{esc(date_to)}">
  <label class="check"><input type="checkbox" name="flagged" value="1" {'checked' if flagged else ''}>只看已标记</label>
  <button type="submit">筛选</button>
</form>"""
    body = form + table(("时间", "账号", "会话", "类型", "回答摘要", "标记"), cells)
    return _page(request, user, "对话审计", body, *_notice(request))


@router.post("/flags")
def add_flag(request: Request, message_id: str = Form(...), reason: str = Form(""),
             note: str = Form(""), csrf: str = Form("")):
    user, response = _guard(request)
    if response:
        return response
    if not _verify_csrf(request, user, csrf):
        return _page(request, user, "对话审计", "<p>表单校验失败。</p>", error="CSRF 校验失败")
    request.app.state.store.add_flag(message_id, reason or "其他", note, user["username"])
    request.app.state.store.audit("conversation_flagged", user["username"], message_id,
                                  {"reason": reason, "note": note})
    return _redirect("/admin/conversations?ok=已标记")


# -- 设置与暂停 ----------------------------------------------------------
@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> Response:
    user, response = _guard(request)
    if response:
        return response
    dataset = request.app.state.dataset
    paused_file = dataset.run_dir / "paused.json"
    paused = paused_file.exists()
    info = json.loads(paused_file.read_text(encoding="utf-8")) if paused else {}
    body = f"""
<div class="cards">
  <div class="card {'danger' if paused else ''}"><b>{'已暂停' if paused else '运行中'}</b><span>试点状态</span></div>
  <div class="card"><b>{esc(dataset.run_dir.name)}</b><span>数据目录</span></div>
</div>
<p class="muted">暂停后问答与草稿全部停止，已生成的草稿仍可在审计里查看。</p>
<form method="post" action="/admin/settings" class="stack">
  <input type="hidden" name="csrf" value="{esc(_csrf(request, user))}">
  <label>操作<select name="action"><option value="pause">暂停试点</option>
  <option value="resume">恢复试点</option></select></label>
  <label>原因<textarea name="reason" rows="3" required minlength="4"></textarea></label>
  <button type="submit">执行</button>
</form>
<p class="muted small">当前暂停信息：{esc(json.dumps(info, ensure_ascii=False) if info else '无')}</p>"""
    return _page(request, user, "设置", body, *_notice(request))


@router.post("/settings")
def settings_apply(request: Request, action: str = Form(...), reason: str = Form(""),
                   csrf: str = Form("")):
    user, response = _guard(request, ("admin", "reviewer"))
    if response:
        return response
    if not _verify_csrf(request, user, csrf):
        return _page(request, user, "设置", "<p>表单校验失败。</p>", error="CSRF 校验失败")
    if action not in ("pause", "resume"):
        return _page(request, user, "设置", "<p>未知操作。</p>", error="参数不正确")
    if len(reason) < 4:
        return _page(request, user, "设置", "<p>请填写至少 4 个字的原因。</p>", error="原因太短")
    dataset = request.app.state.dataset
    target = dataset.run_dir / "paused.json"
    if action == "pause":
        target.write_text(json.dumps({"actor": user["username"], "reason": reason,
                                      "at": iso(datetime.now(tz=LOCAL_TZ))},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
    elif target.exists():
        target.unlink()
    request.app.state.store.audit(f"pilot_{action}", user["username"], "pilot", {"reason": reason})
    return _redirect(f"/admin/settings?ok=已{'暂停' if action == 'pause' else '恢复'}")


# -- 审计链 --------------------------------------------------------------
@router.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request) -> Response:
    user, response = _guard(request)
    if response:
        return response
    store = request.app.state.store
    verdict = store.verify_audit()
    state = "完整" if verdict["ok"] else f"断点在第 {verdict['broken_at']} 条"
    rows = [(r["seq"], r["at"][:19], r["actor"], r["action"], r["subject"],
             r["details"][:80]) for r in store.audit_rows(100)]
    head = (f"<p>链状态：<b>{esc(state)}</b>（共 {verdict['entries']} 条）</p>")
    body = head + table(("seq", "时间", "操作人", "动作", "对象", "详情"), rows)
    return _page(request, user, "审计链", body, *_notice(request))
