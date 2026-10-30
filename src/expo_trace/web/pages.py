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
        bar = (
            "<header class=\"topbar\"><div class=\"brand\">展会工单助手"
            "<span class=\"tag\">v1 试点</span></div>"
            f"<nav>{links}</nav>"
            f"<div class=\"who\">{esc(user['name'])}<span class=\"role\">{esc(user['role'])}</span></div>"
            "</header>"
        )
    return f"{head}{bar}<main>{body}</main></body></html>"


def login_page(error: str = "") -> str:
    banner = f"<div class=\"banner error\" role=\"alert\">{esc(error)}</div>" if error else ""
    body = f"""
<section class="auth">
  <h1>展会咨询工单助手</h1>
  <p class="muted">只读试点 · 只生成草稿 · 不自动对外</p>
  {banner}
  <form method="post" action="/admin/login" class="stack">
    <label>账号<input name="username" autocomplete="username" required autofocus></label>
    <label>密码<input name="password" type="password" autocomplete="current-password" required></label>
    <button type="submit">登录</button>
  </form>
  <p class="muted small">演示账号：admin / reviewer / agent，初始密码见部署说明。</p>
</section>"""
    return layout("登录", body)


def chat_page(user: dict[str, Any]) -> str:
    body = f"""
<section class="chat-shell">
  <div class="chat-head">
    <h1>咨询问答</h1>
    <p class="muted">回答只引用现行版本；收费 / 资格问题一律转人工。</p>
  </div>
  <div id="messages" class="messages" aria-live="polite"></div>
  <form id="ask" class="ask">
    <input id="question" placeholder="例如：T-1001 现在适用哪个版本？差旅住宿标准是多少？" required>
    <button id="send" type="submit">发送</button>
  </form>
  <div class="hint muted small">提问带 T-1001 会走工单版本判定；不带则检索知识库。</div>
</section>
<script src="/static/chat.js" defer></script>"""
    return layout("咨询问答", body, user=user)


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
