# src 2 · MVP 项目说明

本目录不重复代码，只说明 `/src` 下的 MVP 由哪些部分组成、Agent 如何被约束、知识库怎么用，以及**人工确认点在哪里**。
代码本身在 [`src/expo_trace/`](../expo_trace)。

## 1. 工程架构

```
src/expo_trace/
├── main.py            CLI 入口：run / status / review / feedback / report / sample / audit / web
├── core/              领域层（零第三方运行时依赖）
│   ├── pipeline.py    流水线编排：解析导出 → 版本判定 → 草稿 → 队列 → 报告
│   ├── versioning.py  版本判定（有效期 + 确认状态 + 作废/替代链）
│   ├── validation.py  12 个关键字段与缺失/未确认区分
│   ├── drafting.py    草稿生成与水印；publish 恒抛 BlockedAction（无发送通道）
│   ├── review.py      人工确认/退回
│   ├── metrics.py     工时 / 返工 / 确认完整率（按工单去重）
│   └── audit.py       审计哈希链（prev + sha256）
├── web/               阶段 2 Web 层（可选依赖 [web]）
│   ├── app.py         FastAPI 装配、中间件、错误映射
│   ├── chat.py        /api/v1/* 接口与 SSE
│   ├── agent.py       问答编排：工单路径 / 知识问答路径 / 边界拦截
│   ├── tools.py       Agent 工具注册表与一次性 context_token
│   ├── kb.py          知识库：切片、检索、拒答阈值
│   ├── store.py       SQLite：会话、消息、草稿、审计、知识库
│   └── admin.py       后台：登录、提示词、知识库、对话审计、设置
├── utils/             时间、文本、错误、YAML、JSON 等纯函数
└── resources/         静态页面与样式
```

分层约束：`core` 不依赖 `web`；`web` 只调用 `core` 的公开函数；`utils` 不反向依赖业务。

## 2. Agent 框架（工具白名单 + 顺序约束）

Agent 不是一个开放式 ReAct 循环，而是**固定流程 + 白名单工具**，最多三次工具调用：

```
意图路由 → resolve_version → check_fields → compose_draft → request_human_review
                │                  │
                └─ 版本不可定位 ───┴─ 字段缺失 → 转人工（不出草稿）
```

| 工具 | 作用 | 副作用 |
| --- | --- | --- |
| `get_ticket` | 读工单 | 只读 |
| `resolve_version` | 判定当前/过期/未确认，给原因码 | 只读 |
| `check_fields` | 校验关键字段，通过后签发 `context_token` | 只读（签发令牌） |
| `compose_draft` | 生成带水印草稿 | 新建草稿 |
| `request_human_review` | 转人工复核，**消费令牌** | 状态流转 |
| `get_pilot_status` | 试点是否暂停 | 只读 |

跳步会被拒绝（`E_ORDER_VIOLATION`）；令牌一次性（`E_CONTEXT_USED`）；版本变化后失效（`E_VERSION_STALE`）。

## 3. 知识库

- 入库：上传 Markdown/TXT → 按标题结构切片（500 字 / 80 重叠，保留条款号）→ 向量化（hash 或 OpenAI 兼容）→ SQLite 存储。
- 检索：BM25 + 向量混合，先按现行版本过滤再召回；命中分低于阈值直接拒答。
- 拒答：无依据时返回 `E_NO_EVIDENCE`，不调用模型生成。
- 退役：文档只归档不物理删除，保留版本链与审计。

## 4. 人工确认与审核点（6 处）

| 编号 | 名称 | 触发 | 系统行为 | 人工动作 | 代码 |
| --- | --- | --- | --- | --- | --- |
| H-1 | 关键字段核验 | 关键字段缺失 | 不出草稿，列缺项进人工队列 | 补字段或退回 | `core/validation.py`、`web/tools.py` |
| H-2 | 草稿人工复核 | 草稿生成后 | 一次性令牌，仅复核时消费 | 阅读草稿决定确认/退回 | `web/tools.py` |
| H-3 | 审核方四眼确认 | 进入复核队列 | 状态只由人推进，模型无审批权 | 四眼确认内容/版本/来源/口径 | `web/admin.py`、`core/review.py` |
| H-4 | 试点暂停权 | 抽检发现误报 | 读 `paused.json`，全员转人工 | 审核方决定暂停/恢复并留痕 | `web/tools.py`、`web/agent.py` |
| H-5 | 提示词与知识发布 | 发布模板 / 归档知识库 | 强制 CSRF + 角色校验 + 审计 | 审核方确认后发布 | `web/admin.py` |
| H-6 | 对外发送 | 任何对外动作 | 无发送接口，`publish` 恒抛 | 由人在系统外发送并留记录 | `core/drafting.py`、`scripts/verify.sh` |

逐条说明与测试见 [2-1-人工确认点.md](2-1-人工确认点.md) / [2-1-人工确认点.docx](2-1-人工确认点.docx)。

## 5. 怎么跑

```bash
pip install -e ".[web]"          # Web 依赖
expo-trace run --export data/demo/export --run-dir runs/final --now "2026-10-20 10:00"
expo-trace web --port 8000       # http://127.0.0.1:8000/chat
python -m unittest discover -s tests -t .   # 142 项
```

完整启动、运维与故障处置见 [`docs/9-运行手册.docx`](../../docs/9-运行手册.docx)。
