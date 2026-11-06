"""服务端渲染页面：登录、聊天、管理后台。

不引 Jinja2 是刻意的：模板少、结构固定，手写 HTML 更容易做输出转义审计，
也少一个模板注入面。所有用户内容一律走 esc()。
"""

from __future__ import annotations

import html
from typing import Any, Iterable, Sequence

NAV = (
    ("/admin", "概览"),
    ("/admin/kb", "知识库"),
    ("/admin/prompts", "提示词"),
    ("/admin/conversations", "对话审计"),
    ("/admin/settings", "设置"),
    ("/admin/audit", "审计链"),
)


class Raw(str):
    """明确标记“这段 HTML 由服务端拼装、可信”，避免把用户输入当结构。"""


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def layout(title: str, body: str, *, user: dict[str, Any] | None = None,
           nav: bool = False, csrf: str = "") -> str:
    head = (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{esc(title)}</title><link rel=\"stylesheet\" href=\"/static/style.css\">"
        "</head><body>"
    )
    bar = ""
    if user:
        links = "".join(f"<a href=\"{esc(href)}\">{esc(label)}</a>" for href, label in NAV) if nav else ""
        backend = user.get("role") in ("admin", "reviewer")
        switch = ""
        if backend and not nav:
            switch = "<a class=\"ghost\" href=\"/admin\">管理后台</a>"
        actions = (
            f"{switch}"
            "<form method=\"post\" action=\"/logout\" class=\"inline\">"
            "<button class=\"link\" type=\"submit\">退出</button></form>"
        )
        bar = (
            "<header class=\"topbar\"><a class=\"brand\" href=\"/chat\">展会工单助手"
            "<span class=\"tag\">v1 试点</span></a>"
            f"<nav>{links}</nav>"
            f"<div class=\"actions\">{actions}</div>"
            f"<div class=\"who\">{esc(user['name'])}<span class=\"role\">{esc(user['role'])}</span></div>"
            "</header>"
        )
    return f"{head}{bar}<main>{body}</main></body></html>"


def login_page(error: str = "", kind: str = "staff") -> str:
    """登录页分两种入口：咨询员（/login）与审核方/管理员（/admin/login）。"""
    banner = f"<div class=\"banner error\" role=\"alert\">{esc(error)}</div>" if error else ""
    staff = kind == "staff"
    action = "/login" if staff else "/admin/login"
    title = "咨询员入口" if staff else "管理后台"
    lead = ("仅咨询员账号：提问、看引用、生成草稿；收费与资格问题一律转人工。"
            if staff else "仅审核方 / 管理员账号：复核草稿、维护规则、查看审计；不代替咨询员对外发送。")
    other = ("<p class=\"muted small\"><a href=\"/admin/login\">管理员 / 审核方入口 →</a></p>"
             if staff else "<p class=\"muted small\"><a href=\"/login\">咨询员入口 →</a></p>")
    body = f"""
<section class="auth">
  <p class="eyebrow">{esc(title)}</p>
  <h1>展会咨询工单助手</h1>
  <p class="muted">{esc(lead)}</p>
  {banner}
  <form method="post" action="{action}" class="stack">
    <label>账号<input name="username" autocomplete="username" required autofocus></label>
    <label>密码<input name="password" type="password" autocomplete="current-password" required></label>
    <button type="submit">登录</button>
  </form>
  <p class="muted small">演示账号：agent（咨询员）· reviewer（审核方）· admin（管理员）</p>
  <p class="muted small">初始密码：<strong>demo-1234</strong>（演示环境；部署时用 EXPO_SEED_PASSWORD 覆盖）</p>
  {other}
</section>"""
    return layout("登录", body)


def chat_page(user: dict[str, Any], *, sessions: list[Any] | None = None,
              active_id: str = "", history: list[Any] | None = None,
              drafts_count: int = 0) -> str:
    sessions = sessions or []
    history = history or []

    def short(sid: str) -> str:
        return esc(sid[:8])

    items = []
    for row in sessions:
        sid = row["id"]
        label = (row["title"] or "新会话").strip()[:18]
        cls = "side-item active" if sid == active_id else "side-item"
        items.append(f'<li><a class="{cls}" href="/chat?session={esc(sid)}">'
                     f'<span class="mono">{short(sid)}</span> {esc(label)}</a></li>')
    if not items:
        items.append('<li class="muted small">还没有会话，点“新建会话”开始。</li>')

    bubbles = []
    for row in history:
        role = row["role"]
        kind = row["answer_type"] or "ok"
        cls = "msg user" if role == "user" else f"msg assistant {kind}"
        who = "我" if role == "user" else "助手"
        note = ""
        if role != "user" and kind in ("blocked", "refused", "error"):
            note = f'<div class="note">{esc(row["error_code"] or kind)}</div>'
        bubbles.append(f'<div class="{cls}"><div class="who-small">{who}</div>'
                       f'<div class="bubble">{esc(row["content"])}</div>{note}</div>')
    if not bubbles:
        bubbles.append('<p class="muted small">这是新会话，输入问题开始。</p>')

    body = f"""
<section class="chat-layout">
  <aside class="side">
    <form method="post" action="/chat/new"><button class="primary block" type="submit">新建会话</button></form>
    <h2 class="side-title">我的会话</h2>
    <ul class="side-list">{''.join(items)}</ul>
    <a class="side-link" href="/drafts">我的草稿（{drafts_count}）→</a>
  </aside>
  <section class="chat-shell" id="chat-shell" data-session="{esc(active_id)}">
    <div class="chat-head">
      <h1>咨询问答</h1>
      <p class="muted">回答只引用现行版本；收费 / 资格问题一律转人工。</p>
    </div>
    <div id="messages" class="messages" aria-live="polite">{''.join(bubbles)}</div>
    <form id="ask" class="ask">
      <input id="question" placeholder="例如：T-1001 现在适用哪个版本？差旅住宿标准是多少？" required>
      <button id="send" type="submit">发送</button>
    </form>
    <div class="hint muted small">提问带 T-1001 会走工单版本判定；不带则检索知识库。</div>
  </section>
</section>
<script src="/static/chat.js" defer></script>"""
    # 审核方/管理员从问答页回后台：导航与「管理后台」入口都要在
    return layout("咨询问答", body, user=user,
                  nav=user.get("role") in ("admin", "reviewer"))


def drafts_page(user: dict[str, Any], rows: list[Any]) -> str:
    if rows:
        body_rows = "".join(
            f'<tr><td class="mono">{esc(r["ticket_id"])}</td><td class="mono">{esc(r["version_id"])}</td>'
            f'<td>{esc(r["status"])}</td><td class="muted small">{esc(r["created_at"][:16])}</td></tr>'
            for r in rows)
        table = ("<table><thead><tr><th>工单</th><th>依据版本</th><th>状态</th><th>生成时间</th></tr>"
                 f"</thead><tbody>{body_rows}</tbody></table>")
    else:
        table = '<p class="muted">还没有草稿。在问答页提出“帮我起草…”即可生成。</p>'
    body = f"""
<section class="chat-shell">
  <div class="chat-head">
    <h1>我的草稿</h1>
    <p class="muted">草稿带「未确认 · 不可对外」水印，只有审核方复核后才能对外使用；系统没有发送通道。</p>
  </div>
  <div class="table-wrap">{table}</div>
  <p class="muted small"><a href="/chat">← 回到问答</a></p>
</section>"""
    return layout("我的草稿", body, user=user)


def admin_page(title: str, body: str, user: dict[str, Any], csrf: str,
               notice: str = "", error: str = "") -> str:
    banners = ""
    if notice:
        banners += f"<div class=\"banner ok\">{esc(notice)}</div>"
    if error:
        banners += f"<div class=\"banner error\">{esc(error)}</div>"
    content = f"<h1>{esc(title)}</h1>{banners}{body}"
    return layout(title, content, user=user, nav=True, csrf=csrf)


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]], *,
          empty: str = "暂无数据") -> str:
    rows = list(rows)
    if not rows:
        return f"<p class=\"muted\">{esc(empty)}</p>"
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell if isinstance(cell, Raw) else esc(cell)}</td>"
                         for cell in row) + "</tr>"
        for row in rows
    )
    return f"<div class=\"table-wrap\"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
