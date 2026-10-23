"""报告与清单渲染：给人看的产物，必须能直接拿去开会。

- ``report.md``：周报，三项指标并列 + 验收矩阵 + 审计与复现信息；
- ``checklist.md``：一线要的“一页缺项清单”，每项标到来源文件与行号（f15）；
- ``queue.csv`` / ``issues.csv``：给协调员转派和逐条核对。
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from expo_trace.core.config import Config
from expo_trace.core.models import (
    Disposition,
    Draft,
    IngestResult,
    Issue,
    QueueItem,
    Resolution,
    Ticket,
)
from expo_trace.core.store import RunStore
from expo_trace.utils.timeutil import iso

MAX_TABLE_ROWS = 30


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join("" if cell is None else str(cell).replace("|", "\\|") for cell in row) + " |")
    return "\n".join(out)


def _csv_text(headers: list[str], rows: list[list[Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue()


def render_report(
    *,
    config: Config,
    store: RunStore,
    manifest: dict[str, Any],
    ingest: IngestResult,
    tickets: list[Ticket],
    resolutions: dict[str, Resolution],
    drafts: dict[str, Draft],
    dispositions: dict[str, Disposition],
    queue: list[QueueItem],
    issues: list[Issue],
    metrics: dict[str, Any],
    acceptance: dict[str, Any],
    paused: bool,
) -> str:
    counts = metrics["dispositions"]
    lines: list[str] = []
    lines.append(f"# {config.get('report.title', '展商咨询工单只读试点周报')}")
    lines.append("")
    lines.append(f"- 运行目录：`{store.root.as_posix()}`")
    lines.append(f"- 生成时间：{metrics['generated_at']}")
    lines.append(f"- 操作者：{config.actor}")
    lines.append(f"- 试点批次：{config.get('pilot.batch')}（只覆盖一个批次）")
    lines.append(f"- 数据性质：合成演练样本，不代表企业真实经营数据；访谈覆盖率不等于验收通过")
    if paused:
        lines.append("")
        lines.append("> **试点当前处于暂停状态**：暂停期间生成的草稿一律不得对外使用。")
    if ingest.degraded:
        lines.append("")
        lines.append(
            f"> **本次运行处于降级状态**：{ingest.degraded_reason}；"
            f"使用快照时间 {ingest.snapshot_at or '未知'}，**必须人工核查**，不得假装是最新状态。"
        )

    lines.extend(["", "## 一、结论摘要", ""])
    lines.append(
        _table(
            ["指标", "数值"],
            [
                ["工单总数", metrics["tickets"]],
                ["可出草稿", counts.get("draft_ready", 0)],
                ["进人工队列", counts.get("human_review", 0)],
                ["不可逆边界阻塞", counts.get("blocked", 0)],
                ["问题记录", f"{metrics['issues']['total']}（error {metrics['issues']['errors']} / warn {metrics['issues']['warnings']}）"],
                ["人工确认记录", metrics["reviews"]],
                ["审计链", "完整" if metrics["audit"]["ok"] else f"断点 seq={metrics['audit'].get('broken_at')}"],
            ],
        )
    )
    lines.append("")
    lines.append(
        "口径提醒：每条工单要么可出草稿、要么带原因进人工队列、要么被不可逆边界阻塞；"
        "**没有“默认通过”**。"
    )

    lines.extend(["", "## 二、三项指标（并列，不提供单一效率结论）", ""])
    handling = metrics["handling"]
    rework = metrics["rework"]
    confirmation = metrics["confirmation"]
    lines.append(
        _table(
            ["指标", "数值", "口径"],
            [
                [
                    "处理工时",
                    f"{handling['total_minutes']} 分钟（干净口径 {handling['clean_total_minutes']} 分钟）",
                    f"覆盖 {handling['coverage']:.1%} 工单；中位数 {handling['median_minutes']}；"
                    f"排除回填样本 {handling['backfilled_excluded']} 条",
                ],
                ["返工次数", f"{rework['total']} 次", f"退回 {rework['returned']} 次 + 重复确认 {rework['repeated_reviews']} 次"],
                [
                    "确认完整率",
                    f"{confirmation['rate']:.1%}",
                    f"已确认 {confirmation['covered_items']}/{confirmation['required_items']} 个必确认项",
                ],
            ],
        )
    )
    lines.append("")
    for key, formula in metrics["formulas"].items():
        lines.append(f"- `{key}`：{formula}")
    lines.append("")
    lines.append(
        "> 只用工时判断效果会掩盖返工与确认不完整（f51）；三项必须同时看，周报不得只报一项。"
    )

    lines.extend(["", "## 三、人工队列", ""])
    if not queue:
        lines.append("无待处理项。")
    else:
        rows = [
            [
                item.ticket_id,
                "、".join(item.reasons),
                item.owner,
                item.due_at,
                "是" if item.requires_authorization else "否",
            ]
            for item in queue[:MAX_TABLE_ROWS]
        ]
        lines.append(_table(["工单", "原因代码", "责任人", "时限", "需授权"], rows))
        if len(queue) > MAX_TABLE_ROWS:
            lines.append("")
            lines.append(f"（仅显示前 {MAX_TABLE_ROWS} 条，完整清单见 `output/queue.csv`）")

    lines.extend(["", "## 四、问题分布", ""])
    by_code: dict[str, int] = {}
    for issue in issues:
        by_code[issue.code] = by_code.get(issue.code, 0) + 1
    if by_code:
        lines.append(_table(["问题代码", "条数"], [[k, v] for k, v in sorted(by_code.items(), key=lambda kv: (-kv[1], kv[0]))]))
    else:
        lines.append("未发现问题。")

    feedback = metrics.get("feedback", {})
    lines.extend(["", "### 误报 / 漏报 / 未确认（本周登记）", ""])
    if feedback.get("total"):
        lines.append(
            _table(["类型", "条数"], [[k, v] for k, v in feedback.get("by_kind", {}).items()])
        )
        lines.append("")
        for row in feedback.get("items", []):
            lines.append(
                f"- `{row.get('ticket_id')}`（{row.get('kind_label')}）：{row.get('note')}"
                f" —— {row.get('reporter')}"
            )
    else:
        lines.append(
            "本周尚未登记。按 f55，周会需逐条过误报/漏报/未确认项；"
            "登记入口：`expo-trace feedback --ticket ... --kind 误报 --reporter ... --note ...`。"
        )

    lines.extend(["", "## 五、需求验收矩阵（工具侧）", ""])
    rows = []
    for row in acceptance["rows"]:
        rows.append([row["req"], row["title"], row["status"], row["evidence"]])
    lines.append(_table(["需求", "内容", "状态", "证据"], rows))
    lines.append("")
    lines.append(f"**小结**：{acceptance['summary']['tool_side']}；{acceptance['summary']['business']}。")
    gaps = [row for row in acceptance["rows"] if row.get("gap")]
    for row in gaps:
        lines.append(f"- {row['req']} 缺口：{row['gap']}")

    lines.extend(["", "## 六、抽查计划", ""])
    plan = metrics.get("sample_plan", [])
    lines.append(
        f"按配置抽样量 {metrics['sample_size']} 条，实际可抽 {len(plan)} 条（当前数据只有 {metrics['tickets']} 条工单）。"
        "抽样按问题类别轮转、结果确定可复现；正式试点是否沿用 80 条需走审批（f42）。"
    )
    if plan:
        lines.append("")
        lines.append("候选工单：" + "、".join(f"`{tid}`" for tid in plan[:40]))

    lines.extend(["", "## 七、可复现与审计", ""])
    lines.append(_table(["输入文件", "sha256"], [[name, digest[:16] + "…"] for name, digest in sorted(manifest.get("inputs", {}).items())]))
    lines.append("")
    lines.append(f"- 配置指纹：`{manifest.get('config_sha256', '')[:16]}…`（本次运行的配置副本在 `config.yaml`）")
    lines.append(
        f"- 审计记录：{metrics['audit']['entries']} 条（截至报告生成；本次运行的 run_completed 随后追加，"
        f"最终条数以 `audit verify` 为准）；哈希链：{'完整' if metrics['audit']['ok'] else '已断裂'}"
    )
    lines.append(f"- 复现命令：`expo-trace run --export {manifest.get('export_dir', '<导出目录>')} --run-dir {store.root.as_posix()}`")
    lines.append("- 审计可回答：谁查过哪张工单、用了哪个版本、提了什么建议、谁确认（f45）")

    lines.extend(["", "## 八、限制与未核实项", ""])
    lines.append("- 数据为合成样本；f12 的 900 次咨询/55% 重复为虚构基线，不能当作真实单量或效果基线。")
    lines.append("- 旧版本实际占比、检索耗时、快照保留期限、例外认定权归属、正式抽查量均未核实。")
    lines.append("- 本工具只做只读核对与草稿组装，不判断业务对错，也不替代任何审批。")
    lines.append("")
    return "\n".join(lines)


def render_checklist(
    tickets: list[Ticket],
    resolutions: dict[str, Resolution],
    drafts: dict[str, Draft],
    issues: list[Issue],
    config: Config,
) -> str:
    lines = ["# 一页缺项清单（按工单）", "", "每项都标到来源文件与行号，便于回原单核对；不带结论、不替人判断。", ""]
    issues_by_ticket: dict[str, list[Issue]] = {}
    for issue in issues:
        if issue.ticket_id:
            issues_by_ticket.setdefault(issue.ticket_id, []).append(issue)
    for ticket in tickets:
        resolution = resolutions[ticket.ticket_id]
        draft = drafts[ticket.ticket_id]
        lines.append(f"## {ticket.ticket_id} · {ticket.category or '未分类'}")
        lines.append("")
        lines.append(f"- 来源：`{ticket.source}` 第 {ticket.line} 行")
        lines.append(f"- 处理人：{ticket.assignee or '未记录'}")
        lines.append(
            f"- 时间：发生 {iso(ticket.occurred_at) or '缺失/未确认'} / 接收 {iso(ticket.received_at) or '缺失/未确认'}"
            + (f"（回填差 {ticket.backfill_gap} 小时）" if ticket.backfilled else "")
        )
        lines.append(f"- 版本判定：**{resolution.state.value}** — {resolution.reason}")
        lines.append(f"- 草稿状态：{draft.status.value}" + ("（需授权）" if draft.requires_authorization else ""))
        items = issues_by_ticket.get(ticket.ticket_id, [])
        lines.append("- 缺项 / 待确认：")
        if not items:
            lines.append("  - 无（仍需人工确认后才能对外）")
        else:
            for issue in items:
                location = f"{issue.source} 第 {issue.line} 行" if issue.line else (issue.source or "来源未记录")
                lines.append(f"  - [ ] `{issue.code}` {issue.detail}（{location}）")
        lines.append("- 未确认事项（草稿内）：")
        for item in draft.unconfirmed_items:
            lines.append(f"  - [ ] {item}")
        lines.append("")
        lines.append(f"> {config.watermark} 本清单不得作为对外答复。")
        lines.append("")
    return "\n".join(lines)


def queue_csv(queue: list[QueueItem]) -> str:
    return _csv_text(
        ["ticket_id", "reasons", "owner", "due_at", "requires_authorization", "note"],
        [
            [item.ticket_id, ";".join(item.reasons), item.owner, item.due_at, item.requires_authorization, item.note]
            for item in queue
        ],
    )


def issues_csv(issues: list[Issue]) -> str:
    return _csv_text(
        ["code", "severity", "ticket_id", "field", "detail", "source", "line", "raw_row"],
        [
            [
                issue.code,
                str(issue.severity),
                issue.ticket_id or "",
                issue.field_name,
                issue.detail,
                issue.source,
                issue.line,
                json.dumps(issue.raw, ensure_ascii=False, sort_keys=True) if issue.raw else "",
            ]
            for issue in issues
        ],
    )
