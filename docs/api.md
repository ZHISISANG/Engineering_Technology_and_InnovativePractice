# 接口说明

本文件覆盖三部分：命令行接口、运行目录产物格式、可复用的 Python API。
工具版本 `0.1.0`；在 `1.0` 之前产物字段可能增加，但不会静默删除已有字段。

---

## 1. 通用约定

### 1.1 安装与入口

```bash
pip install -e .          # 提供 expo-trace 命令
# 或免安装：
PYTHONPATH=src python -m expo_trace ...
```

### 1.2 退出码

| 码 | 含义 |
| --- | --- |
| `0` | 成功（`--strict` 下表示工具侧验收无 FAIL） |
| `2` | 输入/配置错误（`InputError` / `ConfigError` / `TraceError`） |
| `3` | 试点已暂停（`PausedError`），不生成草稿 |
| `4` | 人工确认被拒绝（`ReviewRejected`），或 `--strict` 下验收失败 |
| `5` | 越界动作被阻止（`BlockedAction`） |

### 1.3 时间

- 所有输出时间为带时区的 ISO 8601（默认 `+08:00`）。
- `--now "2026-10-20 10:00"` 可固定运行时间，用于复现与测试。
- 输入时间支持 ISO、`YYYY/MM/DD`、`YYYY年MM月DD日`、纯日期；无时区按 `+08:00` 解释。

---

## 2. 命令行

### 2.1 `run` —— 跑完整流水线

```bash
expo-trace run \
  --export data/demo/export \
  --run-dir runs/demo \
  --config config/default.yaml \
  --actor 演练操作员 \
  --now "2026-10-20 10:00"
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--export` | `data/demo/export` | 脱敏只读导出目录（需含 4 张 CSV + `manifest.json`） |
| `--run-dir` | `runs/demo` | 运行目录，不存在则创建 |
| `--config` | `config/default.yaml` | 配置；运行时会在运行目录留副本 |
| `--actor` | 配置里的 `actor` | 写入审计记录 |
| `--run-name` | 目录名 | 报告与清单中显示 |
| `--now` | 当前时间 | 固定运行时间 |
| `--simulate-failure` | 否 | 模拟导出读取失败，验证快照兜底 |
| `--strict` | 否 | 工具侧验收有 FAIL 时返回 `4` |
| `--json` | 否 | 输出 JSON（`run_dir` / `manifest` / `metrics` / `acceptance`） |

流程：复制输入与配置 → 更新快照 → 读取与映射 → 版本判定 → 数据核对 → 草稿 → 分派 → 指标 → 验收 → 写产物 → 审计。

### 2.2 `status` —— 运行状态

```bash
expo-trace status --run-dir runs/demo [--json]
```

输出运行时间、工单/草稿/队列计数、确认记录数、是否暂停、报告是否存在。

### 2.3 `review` —— 提交人工确认或退回

```bash
expo-trace review \
  --run-dir runs/demo \
  --ticket T-1001 \
  --reviewer 傅景 \
  --decision confirm \
  --item "版本可定位" \
  --item "来源可追溯" \
  --item "未确认项已列明" \
  --item "用途与授权范围相符" \
  --note "已核对展位与批次"
```

| 参数 | 说明 |
| --- | --- |
| `--ticket` | 工单编号（必填） |
| `--reviewer` | 必须在 `review.reviewers` 名单内 |
| `--decision` | `confirm` 或 `return` |
| `--item` | 已确认项，可重复；`confirm` 时必须覆盖 `review.required_confirmations` 全部四项 |
| `--note` | `return` 时至少 4 个字 |

规则：

- 无 `version_id`（版本不可定位）的工单不能 `confirm`；
- 收费/资格类草稿只能由 `safety.irreversible_reviewers` 确认；
- 同一时刻对同一工单重复提交会被拒绝（幂等保护）；
- 成功写入 `reviews.jsonl` 与 `audit.jsonl`，返回 `review_id` 与 `draft_digest`。

### 2.4 `feedback` —— 登记误报 / 漏报 / 未确认

```bash
expo-trace feedback \
  --run-dir runs/demo \
  --ticket T-1005 \
  --kind 误报 \
  --reporter 傅景 \
  --note "冲突已由业务确认，工具判为冲突属于误报"
```

| 参数 | 说明 |
| --- | --- |
| `--ticket` | 工单编号（必填） |
| `--kind` | `误报` / `漏报` / `未确认` / `其他` |
| `--reporter` | 登记人 |
| `--note` | 具体说明，至少 4 个字；周会逐条复盘（f55） |

登记只追加记录，不自动改变工单判定；写入 `feedback.jsonl` 与 `audit.jsonl`，并在周报“误报 / 漏报 / 未确认（本周登记）”一节汇总。

### 2.5 `pause` / `resume` —— 暂停与恢复试点

```bash
expo-trace pause  --run-dir runs/demo --actor 傅景 --reason "发现版本冲突未澄清"
expo-trace resume --run-dir runs/demo --actor 傅景
```

`--actor` 必须在 `review.pause_roles` 内，否则返回 `4`。
暂停期间 `run` 返回 `3`，所有工单进队列（原因 `PILOT_PAUSED`），不生成草稿。

### 2.6 `report` —— 重跑并更新报告

```bash
expo-trace report --run-dir runs/demo [--now "..."] [--json]
```

从运行目录内的输入副本与配置副本重新计算，更新 `output/report.md` 等产物；不修改原始导出。

### 2.7 `audit` —— 审计链

```bash
expo-trace audit verify --run-dir runs/demo
expo-trace audit show   --run-dir runs/demo --ticket T-1001 --limit 20
expo-trace audit show   --run-dir runs/demo --json
```

`verify` 输出 `{ok, entries, broken_at, reason}`；`show` 按工单过滤时只匹配 `subject`（或 `details.ticket_id`），
不会把包含该编号的汇总事件混进来。每次 `show --ticket` 会追加一条 `audit_viewed`（操作人取 `--actor`），
用于回答“谁查过这张工单”（f45）。

### 2.8 `sample` —— 确定性抽查计划

```bash
expo-trace sample --run-dir runs/demo --size 80 [--json]
```

按问题类别轮转抽样，结果可复现；实际数量超过工单数时只返回全部工单。
正式抽查量是否沿用 80 条需审批（f42）。

---

## 3. 运行目录产物

```text
runs/demo/
├── manifest.json          # 本次运行的摘要与计数
├── config.yaml            # 配置副本（复现依据）
├── input/                 # 4 张 CSV + manifest.json 的副本
├── snapshots/last/        # 上次成功读取的原始文件 + meta.json
├── audit.jsonl            # 哈希链
├── reviews.jsonl          # 人工确认/退回（追加）
├── feedback.jsonl         # 误报/漏报/未确认登记（追加）
├── paused.json            # 存在即暂停（含操作人、原因、时间）
└── output/
    ├── tickets.jsonl
    ├── versions.jsonl
    ├── resolutions.json
    ├── issues.jsonl / issues.csv
    ├── drafts.jsonl / drafts/<ticket>.md
    ├── queue.jsonl / queue.csv
    ├── checklist.md
    ├── report.md
    ├── metrics.json
    └── acceptance.json
```

### 3.1 `manifest.json`

| 字段 | 说明 |
| --- | --- |
| `inputs` | 文件名 → sha256 |
| `config_sha256` | 配置指纹 |
| `export_meta` | 导出清单原文（批次、授权范围、用途、有效期） |
| `run_name` / `created_at` / `actor` | 运行标识 |
| `degraded` / `degraded_reason` / `snapshot_at` | 是否使用快照兜底 |
| `counts` | `tickets` / `versions` / `issues` / `queue` |
| `dispositions` | 工单 → `draft_ready` / `human_review` / `blocked` |
| `paused` | 运行时是否处于暂停状态 |
| `audit_verify` | `{ok, entries, broken_at}` |

### 3.2 `tickets.jsonl`

一行一张工单。关键字段：

```json
{
  "ticket_id": "T-1001",
  "batch": "2026-S1",
  "category": "schedule",
  "question": "开幕式当天观众入场时间从几点开始",
  "occurred": {"value": "2026-10-12T09:10:00+08:00", "state": "ok", "raw": "2026-10-12 09:10"},
  "received": {"value": "2026-10-12T09:12:00+08:00", "state": "ok", "raw": "2026-10-12 09:12"},
  "assignee": "林亦舟",
  "contact": "138****0000",
  "backfilled": false,
  "backfill_gap": 0.03,
  "handling_minutes": 25,
  "raw": {"工单编号": "T-1001", "...": "..."}
}
```

`raw` 保留原始行（个人字段已掩码），用于“保留原行、只改判定”的追溯要求（f33 / f43）。

### 3.3 `resolutions.json`

```json
{
  "T-1005": {
    "ticket_id": "T-1005",
    "state": "unconfirmed",
    "version_id": null,
    "reason": "同一天有两个已确认版本都覆盖，无法判定哪一版有效",
    "reason_code": "VERSION_CONFLICT",
    "basis": "有效期覆盖 + 确认状态 > 作废状态 > 生效起始 > 确认时间 > 接收时间；不使用文件名/修改时间",
    "chain_break": false,
    "considered": [
      {"version_id": "V-1008", "notice_id": "N-2026-0703", "state": "confirmed",
       "effective_from": "...", "effective_to": "...", "why": "有效期内且已确认"}
    ]
  }
}
```

`considered` 同时包含被排除的版本与理由，审核人可核对“工具没选谁、为什么”。

### 3.4 `drafts.jsonl` 与 `drafts/*.md`

```json
{
  "ticket_id": "T-1003",
  "status": "blocked",
  "watermark": "【未确认 · 不可对外】",
  "subject": "关于 2026-S1 fee 咨询的回复草稿（待人工确认）",
  "body": "【未确认 · 不可对外】\n\n**主题**：...",
  "version_id": "V-1003",
  "sources": [{"file": "file_index.csv", "line": 4, "version_id": "V-1003"}],
  "blocked_actions": ["自动向展商发布或发送任何内容", "作出收费、报价、折扣或参展资格决定", "..."],
  "requires_authorization": true,
  "unconfirmed_items": ["该规则是否适用于本展商的具体情形", "..."],
  "created_at": "2026-10-20T10:00:00+08:00",
  "statement": "展位费按 9 平方米标准收取，具体金额以合同为准"
}
```

- `status=blocked` 表示命中收费/资格等不可逆边界，草稿**不得直接对外**；
- `status=human_review` 且 `version_id=null` 表示版本不可定位，正文只说明原因与已核查版本，不生成答复内容；
- 所有草稿首行都是 `safety.watermark`。

### 3.5 `queue.csv`

列：`ticket_id, reasons, owner, due_at, requires_authorization, note`。
`reasons` 以 `|` 分隔，主原因在前。

### 3.6 `issues.jsonl` / `issues.csv`

```json
{
  "code": "DUPLICATE_ID",
  "ticket_id": "T-1004",
  "field_name": "ticket_id",
  "detail": "工单编号 T-1004 在 service_desk.csv 出现 2 次（第 5、6 行），仅保留首行",
  "source": "service_desk.csv",
  "line": 6,
  "raw": {"工单编号": "T-1004", "...": "..."},
  "severity": "error"
}
```

问题代码是稳定接口，新增代码必须同时更新 `core/codes.py` 的 `TITLE`/`SEVERITY` 与
`config/default.yaml` 的 `routing.owners`、`routing.blocking_codes`（`test_config.py` 会检查 owner 覆盖）。

### 3.7 `metrics.json`

| 键 | 说明 |
| --- | --- |
| `dispositions` | 各状态计数 |
| `drafts` / `watermarked` / `blocked_irreversible` | 草稿与水印覆盖、边界阻塞数 |
| `handling` | `total_minutes` / `clean_total_minutes` / `samples` / `coverage` / `median_minutes` / `backfilled_excluded` |
| `rework` | `returned` / `repeated_reviews` / `total` |
| `confirmation` | `confirmed_reviews` / `covered_items` / `required_items` / `rate` |
| `queue_by_reason` | 原因 → 条数 |
| `feedback` | `total` / `by_kind` / `items`（最近 5 条） |
| `issues` | `errors` / `warnings` / `total` |
| `audit` | 审计链校验结果 |
| `sample_size` / `sample_plan` | 抽查量与确定性计划 |
| `formulas` | 三项指标的公式文本 |

### 3.8 `acceptance.json`

```json
{
  "rows": [
    {"req": "REQ-1", "title": "有效期 + 确认状态替代文件名", "status": "PASS",
     "evidence": "可定位当前版本 11 条；...", "gap": ""}
  ],
  "summary": {
    "pass": 6, "fail": 0, "not_run": 1,
    "tool_side": "工具侧覆盖 6/6",
    "business": "业务验收未执行（80 条抽查为人工动作）"
  }
}
```

`PASS` 只代表**工具侧检查通过**，不代表业务验收通过；`业务验收` 一行恒为 `NOT_RUN`。

### 3.9 `audit.jsonl`

```json
{"seq": 1, "at": "2026-10-20T10:00:00+08:00", "actor": "演练操作员",
 "action": "run_started", "subject": "demo",
 "details": {"export_dir": "...", "config": "...", "degraded": false},
 "prev": "0000000000000000000000000000000000000000000000000000000000000000",
 "hash": "..."}
```

### 3.10 `feedback.jsonl`

```json
{"feedback_id": "FB-...", "ticket_id": "T-1005", "kind": "false_positive",
 "kind_label": "误报", "reporter": "傅景", "note": "...",
 "created_at": "2026-10-20T11:30:00+08:00", "actor": "演练操作员"}
```

### 3.11 `reviews.jsonl`

```json
{"review_id": "RV-9cbee67777d7", "ticket_id": "T-1001", "reviewer": "傅景",
 "decision": "confirm", "note": "已核对展位与批次",
 "version_id": "V-1001",
 "confirmed_items": ["版本可定位", "来源可追溯", "未确认项已列明", "用途与授权范围相符"],
 "created_at": "2026-10-20T11:00:00+08:00", "draft_digest": "..."}
```

---

## 4. Python API

```python
from expo_trace.core.pipeline import execute, status, clean_run

result = execute(
    export_dir="data/demo/export",
    run_dir="runs/demo",
    config_path="config/default.yaml",
    actor="演练操作员",
    now=None,                 # datetime | None
    simulate_failure=False,
    write=True,
)
print(result.metrics["confirmation"]["rate"])
print(result.acceptance["summary"])
```

`RunResult` 字段：`store, config, manifest, ingest, tickets, resolutions, issues, drafts, dispositions, queue, metrics, acceptance, paused, report_path, manifest_path`。

低层函数（测试与二次开发使用）：

```python
from expo_trace.core.config import load_config
from expo_trace.core.store import RunStore
from expo_trace.core.ingest import ingest
from expo_trace.core.versioning import resolve_all, resolve_ticket
from expo_trace.core.validation import run_checks, blocking_issues
from expo_trace.core.drafting import build_drafts, build_draft, publish
from expo_trace.core.routing import route
from expo_trace.core.metrics import compute, sample_plan
from expo_trace.core.review import submit_review
from expo_trace.core.feedback import submit_feedback
from expo_trace.core.audit import AuditLog
```

| 函数 | 签名要点 |
| --- | --- |
| `load_config(path)` | 解析 + 强制只读边界校验，失败抛 `ConfigError` |
| `RunStore(root)` | 运行目录读写、快照、暂停、确认记录 |
| `ingest(store, config, now, simulate_failure=False)` | 返回 `IngestResult`；失败时快照兜底 |
| `resolve_all(tickets, versions, unconfirmed_tokens)` | 返回 `{ticket_id: Resolution}` |
| `run_checks(ingest_result, config, resolutions, now)` | 返回按工单/代码/行号排序的 `Issue` 列表 |
| `build_drafts(...)` | 返回 `{ticket_id: Draft}`，含水印与边界标记 |
| `route(...)` | 返回 `({ticket_id: Disposition}, [QueueItem])`，断言无静默跳过 |
| `compute(...)` | 返回 `metrics.json` 的字典 |
| `submit_review(store, config, ...)` | 校验并追加确认记录，返回含 `draft_digest` 的字典 |
| `submit_feedback(store, config, ...)` | 登记误报/漏报/未确认并写审计，返回记录 |
| `publish(...)` / `send_external(...)` / `write_back(...)` | 恒抛 `BlockedAction` |

`Draft.publish` 不在任何公开接口里 —— 需要外发时，只能由人复制草稿、人工确认后通过现有业务流程发送。

---

## 5. 扩展点

| 想扩展 | 改哪里 | 注意 |
| --- | --- | --- |
| 新表头/字段别名 | `config/default.yaml` 的 `mapping` | 别名命中后仍保留原行 |
| 新问题代码 | `core/codes.py` + `routing.owners` + `routing.blocking_codes` | 必须补 owner，否则测试失败 |
| 新不可逆类别 | `safety.irreversible_categories` / `irreversible_keywords` | 必须同时指定 `irreversible_reviewers` |
| 新指标 | `core/metrics.py` + `core/report.py` | 不得替换现有三项，只能并列增加 |
| 新导出格式 | `core/ingest.py` | 保持“读取失败→快照→人工核查”的路径 |

---

## 6. 兼容性

- 产物字段只增不删；新增问题代码会同步写入 `CHANGELOG.md`。
- 配置新增键会给出默认值，删除/改名会在大版本说明中标注。
- 审计记录格式（`seq/prev/hash`）视为稳定接口，不兼容变更需升大版本。
