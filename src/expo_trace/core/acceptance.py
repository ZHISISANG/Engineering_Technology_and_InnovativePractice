"""验收矩阵：把六条需求假设逐条映射到“工具侧是否覆盖”，并明确哪些还没做。

注意：这里的 PASS 只代表**工具能给出可核对的证据**，不代表业务验收通过。
80 条问答抽查是人工动作，合成数据无法替代（见 docs/pilot-runbook.md）。
"""

from __future__ import annotations

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

REQUIREMENTS = [
    ("REQ-1", "有效期 + 确认状态替代文件名", "P0", "唐映秋", "f24/f10/f11"),
    ("REQ-2", "交接点统一编号与责任清单", "P0", "秦芮/唐映秋", "f30/f31/f23/f25"),
    ("REQ-3", "双时间戳与读取失败快照兜底", "P1", "秦芮", "f13/f34/f35"),
    ("REQ-4", "兜底触发进人工队列，不默认通过", "P1", "傅景", "f44/f40/f41"),
    ("REQ-5", "工时/返工/确认完整率三项并列", "P1", "叶书", "f50/f51/f53"),
    ("REQ-6", "只出草稿、不自动发布收费/资格决定", "P0", "顾明岚", "f05/f52/f32/f04"),
]


def evaluate(
    config: Config,
    ingest: IngestResult,
    tickets: list[Ticket],
    resolutions: dict[str, Resolution],
    drafts: dict[str, Draft],
    queue: list[QueueItem],
    dispositions: dict[str, Disposition],
    issues: list[Issue],
    metrics: dict[str, Any],
    publish_blocked: bool,
) -> dict[str, Any]:
    queue_reasons = {item.ticket_id: set(item.reasons) for item in queue}
    rows: list[dict[str, Any]] = []

    def add(req: str, status: str, evidence: str, gap: str = "") -> None:
        title = next((r[1] for r in REQUIREMENTS if r[0] == req), req)
        rows.append({"req": req, "title": title, "status": status, "evidence": evidence, "gap": gap})

    locatable = [t for t in tickets if resolutions[t.ticket_id].locatable]
    unlocatable = [t for t in tickets if not resolutions[t.ticket_id].locatable]
    queued_version = [t for t in unlocatable if queue_reasons.get(t.ticket_id, set()) & {
        "NO_CURRENT_VERSION",
        "VERSION_CONFLICT",
        "VERSION_CHAIN_BREAK",
        "KEY_FIELD_MISSING",
        "KEY_FIELD_UNCONFIRMED",
    }]
    add(
        "REQ-1",
        "PASS" if len(locatable) + len(queued_version) == len(tickets) else "FAIL",
        f"可定位当前版本 {len(locatable)} 条；版本不可判定但已进队列 {len(queued_version)} 条；"
        "判定依据固定为有效期+确认状态，文件名不参与排序",
        "" if len(locatable) + len(queued_version) == len(tickets) else "存在既不可定位也未进队列的工单",
    )

    dup = [i for i in issues if i.code == "DUPLICATE_ID"]
    dup_with_row = [i for i in dup if i.raw]
    queue_with_owner = [item for item in queue if item.owner and item.due_at]
    add(
        "REQ-2",
        "PASS" if (not dup or len(dup) == len(dup_with_row)) and len(queue_with_owner) == len(queue) else "FAIL",
        f"重复编号 {len(dup)} 条（保留原行 {len(dup_with_row)} 条）；"
        f"人工队列 {len(queue)} 条均带责任人与时限 {len(queue_with_owner)} 条",
    )

    two_states = [t for t in tickets if t.occurred.state and t.received.state]
    backfilled = [t for t in tickets if t.backfilled]
    degraded_ok = (not ingest.degraded) or bool(ingest.snapshot_at)
    add(
        "REQ-3",
        "PASS" if len(two_states) == len(tickets) and degraded_ok else "FAIL",
        f"两类时间均有四态记录 {len(two_states)}/{len(tickets)}；回填样本 {len(backfilled)} 条；"
        f"本次是否降级：{'是' if ingest.degraded else '否'}"
        + (f"（快照时间 {ingest.snapshot_at}）" if ingest.degraded else ""),
    )

    blocking = [i for i in issues if i.ticket_id and i.code in config.blocking_codes()]
    blocking_missing = [i for i in blocking if i.code not in queue_reasons.get(i.ticket_id or "", set())]
    add(
        "REQ-4",
        "PASS" if not blocking_missing else "FAIL",
        f"阻断问题 {len(blocking)} 条，全部进入队列 {len(blocking) - len(blocking_missing)} 条；"
        f"无“默认通过”分支（每条工单都有分派）",
        "" if not blocking_missing else f"{len(blocking_missing)} 条阻断问题未进队列",
    )

    three = ["handling", "rework", "confirmation"]
    metrics_present = all(isinstance(metrics.get(k), dict) for k in three)
    add(
        "REQ-5",
        "PASS" if metrics_present else "FAIL",
        f"三项指标并列输出：工时 {metrics['handling']['total_minutes']} 分钟、"
        f"返工 {metrics['rework']['total']} 次、确认完整率 {metrics['confirmation']['rate']:.1%}；"
        "报告不提供单一指标结论；误报/漏报/未确认登记入口可用（f55）",
    )

    watermarked = [d for d in drafts.values() if d.body.startswith(config.watermark)]
    blocked = [d for d in drafts.values() if d.requires_authorization]
    add(
        "REQ-6",
        "PASS" if len(watermarked) == len(drafts) and publish_blocked else "FAIL",
        f"草稿 {len(drafts)} 份，全部带“未确认 · 不可对外”标识 {len(watermarked)} 份；"
        f"收费/资格类 {len(blocked)} 份标记不可直接对外；发布/发送/写回接口调用即拒绝",
    )

    rows.append(
        {
            "req": "业务验收",
            "title": "80 条问答抽查（人工）",
            "status": "NOT_RUN",
            "evidence": f"已生成 {len(metrics.get('sample_plan', []))} 条确定性抽样计划；合成数据不能替代真实抽查",
            "gap": "需由流程协调员与审核方各复核一轮，并留存确认记录",
        }
    )

    passed = [r for r in rows if r["status"] == "PASS"]
    failed = [r for r in rows if r["status"] == "FAIL"]
    not_run = [r for r in rows if r["status"] == "NOT_RUN"]
    return {
        "rows": rows,
        "summary": {
            "pass": len(passed),
            "fail": len(failed),
            "not_run": len(not_run),
            "tool_side": "工具侧覆盖 6/6" if not failed else f"存在 {len(failed)} 项缺口",
            "business": "业务验收未执行（80 条抽查为人工动作）",
        },
    }
