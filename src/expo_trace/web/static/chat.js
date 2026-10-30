/* 聊天前端：SSE 流式 + 打字机 + 引用卡。
   不引框架，所有插入都用 textContent，避免把模型输出当 HTML 执行。 */
"use strict";

const messagesEl = document.getElementById("messages");
const form = document.getElementById("ask");
const input = document.getElementById("question");
const sendBtn = document.getElementById("send");

let sessionId = null;
let busy = false;

function node(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}

function addMessage(role, text) {
  const box = node("div", "msg " + role);
  const meta = node("div", "meta", role === "user" ? "我" : "助手");
  const body = node("div", "body", text || "");
  box.append(meta, body);
  messagesEl.append(box);
  box.scrollIntoView({ block: "end", behavior: "smooth" });
  return { box, meta, body };
}

function showBanner(text, kind) {
  const el = node("div", "banner " + (kind || "error"), text);
  messagesEl.prepend(el);
}

async function jsonOrNull(resp) {
  try { return await resp.json(); } catch (_) { return null; }
}

async function ensureSession() {
  const resp = await fetch("/api/v1/sessions");
  if (resp.status === 401) { location.href = "/admin/login"; return false; }
  const data = await jsonOrNull(resp);
  if (data && data.sessions && data.sessions.length) {
    sessionId = data.sessions[0].id;
    await loadHistory();
    return true;
  }
  const created = await fetch("/api/v1/sessions", { method: "POST" });
  if (created.status === 401) { location.href = "/admin/login"; return false; }
  const body = await jsonOrNull(created);
  sessionId = body.session_id;
  return true;
}

async function loadHistory() {
  const resp = await fetch("/api/v1/sessions/" + encodeURIComponent(sessionId) + "/messages");
  if (!resp.ok) return;
  const data = await resp.json();
  for (const msg of data.messages) {
    const view = addMessage(msg.role, msg.content);
    if (msg.answer_type && msg.answer_type !== "ok") {
      view.box.classList.add(msg.answer_type === "blocked" ? "blocked" : "refused");
    }
    if (msg.citations && msg.citations.length) renderCitations(view.box, msg.citations);
  }
}

function renderCitations(box, citations) {
  const wrap = node("div", "cites");
  for (const cite of citations) {
    const card = node("div", "cite");
    const title = node("b", "", cite.title || cite.ref_id || "来源");
    const lines = [];
    if (cite.version_id) lines.push("版本 " + cite.version_id);
    if (cite.effective_from) {
      lines.push("有效期 " + String(cite.effective_from).slice(0, 10) + " ~ " +
                 String(cite.effective_to || "长期").slice(0, 10));
    }
    if (cite.confirmed_by) lines.push("确认人 " + cite.confirmed_by);
    if (lines.length) card.append(node("div", "ver", lines.join("｜")));
    if (cite.snippet) card.append(node("div", "", cite.snippet.slice(0, 180)));
    wrap.append(card);
  }
  box.append(wrap);
}

function parseBlock(block) {
  let event = "message";
  const dataLines = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  try { return { event, data: JSON.parse(dataLines.join("\n")) }; }
  catch (_) { return null; }
}

async function ask(question) {
  if (busy || !question.trim()) return;
  busy = true;
  sendBtn.disabled = true;
  addMessage("user", question);
  const view = addMessage("assistant", "");
  const status = node("div", "meta", "正在连接…");
  view.box.prepend(status);

  let citations = [];
  let buffer = "";
  let shown = 0;
  let typing = null;
  let messageId = "";

  function typeLoop() {
    if (typing) return;
    typing = setInterval(() => {
      if (shown >= buffer.length) { clearInterval(typing); typing = null; return; }
      view.body.textContent += buffer.slice(shown, shown + 3);
      shown = Math.min(shown + 3, buffer.length);
    }, 14);
  }
  function flush() {
    if (typing) { clearInterval(typing); typing = null; }
    view.body.textContent = buffer;
    shown = buffer.length;
  }

  let resp;
  try {
    resp = await fetch("/api/v1/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, session_id: sessionId }),
    });
  } catch (err) {
    status.textContent = "网络错误";
    showBanner("网络连接失败，请重试。", "error");
    busy = false; sendBtn.disabled = false; return;
  }
  if (!resp.ok || !resp.body) {
    const payload = await jsonOrNull(resp);
    status.textContent = "请求失败";
    showBanner((payload && (payload.code + "：" + payload.message)) || "请求失败，请重试。", "error");
    busy = false; sendBtn.disabled = false; return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let pending = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    pending += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = pending.indexOf("\n\n")) >= 0) {
      const block = pending.slice(0, idx);
      pending = pending.slice(idx + 2);
      const parsed = parseBlock(block);
      if (!parsed) continue;
      const { event, data } = parsed;
      if (event === "status") {
        status.textContent = data.text || "处理中…";
      } else if (event === "delta") {
        buffer += data.text || "";
        typeLoop();
      } else if (event === "citation") {
        citations.push(data.citation);
      } else if (event === "blocked" || event === "refused" || event === "error") {
        status.textContent = data.code || "已拦截";
        view.box.classList.add(event === "blocked" ? "blocked" : "refused");
      } else if (event === "done") {
        messageId = data.message_id || "";
        if (data.answer_type === "blocked") view.box.classList.add("blocked");
        if (data.answer_type === "refused") view.box.classList.add("refused");
      }
    }
  }
  flush();
  status.textContent = "完成";
  if (citations.length) renderCitations(view.box, citations);
  if (messageId) {
    const fb = node("button", "link", "标记未确认");
    fb.type = "button";
    fb.addEventListener("click", async () => {
      const r = await fetch("/api/v1/feedback", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message_id: messageId, kind: "未确认", note: "用户端标记" }),
      });
      fb.textContent = r.ok ? "已登记" : "登记失败";
      fb.disabled = true;
    });
    view.box.append(fb);
  }
  busy = false;
  sendBtn.disabled = false;
  input.focus();
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = input.value.trim();
  input.value = "";
  ask(question);
});

ensureSession();
