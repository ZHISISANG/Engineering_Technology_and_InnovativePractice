---
name: Bug report
about: 报告工具行为与预期不符（请勿粘贴真实展商信息）
title: "[bug] "
labels: bug
assignees: ""
---

## 发生了什么

<!-- 一句话描述，不要贴真实手机号、邮箱、证件号或展商名称 -->

## 复现

```bash
# 使用的命令（把真实导出换成 data/demo/export 或脱敏样本）
PYTHONPATH=src python -m expo_trace run --export data/demo/export --run-dir /tmp/repro --now "..."
```

## 期望

## 实际

<!-- 错误输出、退出码；如果涉及判定，请附 resolutions.json 中的对应条目 -->

## 环境

- 操作系统：
- Python 版本（`python -V`）：
- 工具版本（`expo-trace --version` 或 `pip show expo-ticket-trace`）：
- 是否使用快照兜底（报告里 `degraded` 是否为 true）：

## 审计链是否完整

```bash
PYTHONPATH=src python -m expo_trace audit verify --run-dir <run>
```

## 其他

<!-- 是否涉及收费/资格类草稿？是否发生对外发送？如是，请先停止试点并联系审核方 -->
