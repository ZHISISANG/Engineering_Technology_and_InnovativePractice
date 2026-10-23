# 流程图：从现场链路到只读追溯

本页把《展商咨询知识与服务转派》需求发现材料里的业务链路、断点、目标流程、版本判定与审计链画成 5 张图。
图源是 Mermaid，GitHub 会直接渲染；同内容的可视化版本见 [`flowchart.html`](flowchart.html)（离线可开，无外部依赖）。

> 材料性质：访谈与数据均为合成演练样本，人物为虚构角色，`900 次咨询 / 55% 重复` 是虚构基线，不能当作真实单量或效果承诺。

---

## 图 1　现状链路与三处断点

```mermaid
flowchart LR
    A["展商咨询<br/>电话 / 邮件 / 群消息"] --> B["问题分类"]
    B --> C["政策版本检索<br/>内部服务台 / 业务台账 / 文件库"]
    C --> D["转派交接<br/>接收人 · 版本 · 未结项"]
    D --> E["回复审核"]
    E --> F["对外答复"]

    A -.->|"先群消息答复"| G["事后补填表格<br/>回填时间 ≠ 发生时间"]
    G -.-> D
    C -.->|"三套系统无统一编号"| H["人工比对<br/>旧通知 / 旧展位说明"]
    H -.-> E

    classDef pain fill:#fff1f0,stroke:#cf1322,color:#7a0b0b;
    classDef normal fill:#f6ffed,stroke:#389e0d,color:#135200;
    class G,H pain;
    class A,B,C,D,E,F normal;
```

**读图要点**

- 答复动作（A→G）发生在登记（G→D）之前，所以台账里的“接收时间”只是回填时间，不能当业务发生时间（f13）。
- 检索横跨三套系统，工单没有统一关联编号，交接时只能人工比对（f30）。
- 旧通知、旧展位说明仍在被引用，文件名看起来“最新”不代表有效（f10 / f11）。

---

## 图 2　目标流水线：只读、只出草稿、失败进队列

```mermaid
flowchart TD
    IN["脱敏只读导出<br/>service_desk / ledger / file_index / handoff"] --> M["字段映射<br/>别名匹配 · 保留原行"]
    M --> Q["数据核对<br/>重复编号 · 空时间 · 单位差异 · 版本链"]
    Q --> V{"版本判定<br/>有效期覆盖 + 确认状态"}
    V -->|"可定位"| D["草稿组装<br/>引用版本 · 来源 · 行号"]
    V -->|"不可定位 / 冲突 / 链断 / 缺字段 / 授权不清"| H["人工队列<br/>原因 · 责任人 · 时限"]
    D --> G{"不可逆边界<br/>收费 / 资格"}
    G -->|"否"| R["待人工确认草稿"]
    G -->|"是"| X["禁止对外<br/>业务负责人 + 审核方"]
    H --> C["人工确认 / 退回"]
    R --> C
    X --> C
    C --> REP["周报<br/>工时 · 返工 · 确认完整率"]
    IN -.->|"读取失败"| S["上次快照 + 快照时间<br/>提示人工核查"]
    S --> H

    classDef draft fill:#f6ffed,stroke:#389e0d,color:#135200;
    classDef queue fill:#fff7e6,stroke:#d46b08,color:#873800;
    classDef block fill:#fff1f0,stroke:#cf1322,color:#7a0b0b;
    classDef data fill:#f0f5ff,stroke:#2f54eb,color:#10239e;
    class R draft;
    class H,C queue;
    class X block;
    class IN,M,Q,V,D,S data;
```

**读图要点**

- 只有两条出口：**可出草稿**或**带原因进队列**；收费/资格再叠一层“禁止对外”。没有“默认通过”的边（f44）。
- 读取失败不允许编造，也不允许悄悄重试：用上次成功快照续跑，并在报告里标注快照时间与“需人工核查”（f35 / REQ-3）。
- 草稿不是答复：它是把“依据哪一版、谁确认、哪些还没确认”摆到审核人面前（f22 / f42）。

---

## 图 3　版本判定：为什么文件名不参与排序

```mermaid
flowchart TD
    T["工单<br/>批次 · 类别 · 业务发生时间"] --> T1{"发生时间可判定?"}
    T1 -->|"否：缺失 / 未确认 / 非法"| Q1["人工队列<br/>KEY_FIELD_MISSING / KEY_FIELD_UNCONFIRMED"]
    T1 -->|"是"| C{"有效期内有覆盖版本?"}
    C -->|"否"| Q2["人工队列<br/>NO_CURRENT_VERSION<br/>附最近过期版本与作废原因"]
    C -->|"是"| K{"存在已确认版本?"}
    K -->|"否"| Q3["人工队列<br/>KEY_FIELD_UNCONFIRMED"]
    K -->|"是"| CH{"替代链完整?"}
    CH -->|"否"| Q5["人工队列<br/>VERSION_CHAIN_BREAK"]
    CH -->|"是"| S{"多个已确认且互不替代?"}
    S -->|"是"| Q4["人工队列<br/>VERSION_CONFLICT"]
    S -->|"否"| OK["current<br/>草稿可引用 version_id"]

    classDef ok fill:#f6ffed,stroke:#389e0d,color:#135200;
    classDef bad fill:#fff7e6,stroke:#d46b08,color:#873800;
    class OK ok;
    class Q1,Q2,Q3,Q4,Q5 bad;
```

**读图要点**

- 判定只使用：**有效期是否覆盖业务发生时间** + **是否已确认**；排序键里没有文件名、没有修改时间、没有“看起来最新”。
- 作废的版本不删除，保留作废原因，只退出“当前版本”的竞争（f24）。
- 多个“已确认”版本同时覆盖同一天、又互不替代时，工具**不猜**，进冲突队列（f34）。
- 替代链断掉（`supersedes` 指向不存在的版本）时同样进队列，版本链不能断（f43）。

---

## 图 4　工单状态机：确认、退回与暂停

```mermaid
stateDiagram-v2
    [*] --> RECEIVED
    state "已接收" as RECEIVED
    state "草稿待确认" as DRAFT
    state "人工队列" as QUEUE
    state "边界阻塞" as BLOCKED
    state "已确认" as CONFIRMED
    state "已退回" as RETURNED
    state "试点暂停" as PAUSED

    RECEIVED --> DRAFT: 版本可定位且无阻断
    RECEIVED --> QUEUE: 不可定位 / 缺字段 / 授权不清
    RECEIVED --> BLOCKED: 收费 / 资格
    DRAFT --> CONFIRMED: 审核人 confirm（四项齐全）
    DRAFT --> RETURNED: 审核人 return（须写原因）
    QUEUE --> DRAFT: 补齐后重跑
    QUEUE --> BLOCKED: 复核发现收费或资格
    BLOCKED --> CONFIRMED: 业务负责人 + 审核方书面确认
    RETURNED --> DRAFT: 修订后重跑
    CONFIRMED --> [*]
    QUEUE --> PAUSED: 审核方行使停止权
    DRAFT --> PAUSED
    BLOCKED --> PAUSED
    PAUSED --> QUEUE: 恢复后重跑
```

**读图要点**

- `confirm` 不是点一下通过：必须覆盖配置里的四项确认（版本可定位、来源可追溯、未确认项已列明、用途与授权范围相符）。
- `return` 必须写原因（≥4 字），并计入返工次数。
- 暂停是**审核方的权限**，不是系统管理员的开关；暂停期间不生成任何草稿（f04）。

---

## 图 5　数据流与审计链：谁查过、用哪版、谁确认

```mermaid
flowchart LR
    subgraph IN["输入（只读）"]
      A1["脱敏导出 4 表 + manifest"]
      A2["配置副本 config.yaml"]
    end
    subgraph RUN["运行目录（可复现）"]
      B1["tickets.jsonl<br/>保留原行 · 脱敏"]
      B2["resolutions.json<br/>判定依据 + 已排除版本"]
      B3["drafts/*.md<br/>未确认 · 不可对外"]
      B4["queue.csv<br/>原因 · 责任人 · 时限"]
      B5["reviews.jsonl<br/>确认项 · 建议 · 时间"]
      B6["audit.jsonl<br/>哈希链"]
    end
    subgraph OUT["输出"]
      C1["report.md<br/>周报 + 验收矩阵"]
      C2["checklist.md<br/>一页缺项清单"]
      C3["metrics.json / acceptance.json"]
    end
    A1 --> B1
    A2 --> B1
    B1 --> B2
    B2 --> B3
    B2 --> B4
    B3 --> B5
    B4 --> C1
    B5 --> C1
    B6 --> C1
    B1 --> C2
    B2 --> C3
```

**读图要点**

- 每条审计记录都带前一条的哈希：改一行，后续全部对不上，`expo-trace audit verify` 能定位到断点（f45）。
- 确认记录保留“提了什么建议、确认了哪几项、什么时候”，不是只留一句“已处理”（f45 / f43）。
- 原始行、原始版本、作废原因都留在运行目录里；只对个人字段做掩码，不改变业务字段（f43）。

---

## 图与需求的对应

| 图 | 回答的问题 | 对应需求 / 证据 |
| --- | --- | --- |
| 图 1 | 现在为什么会对不上？ | f10 / f11 / f13 / f30 |
| 图 2 | 工具在这条链路里做什么、不做什么？ | REQ-4、REQ-6；f32 / f44 / f52 |
| 图 3 | 凭什么说这一版是“当前版本”？ | REQ-1、REQ-2、REQ-3；f24 / f34 / f43 |
| 图 4 | 草稿怎么才算确认，谁能停？ | REQ-6；f04 / f22 / f42 |
| 图 5 | 出了争议怎么复原？ | f45；审计与留存要求 |

> 这些图描述的是**工具侧能力**，不等于业务验收通过。80 条问答抽查、旧版本占比、快照保留期等仍需在真实试点中核实（见 `docs/pilot-runbook.md`）。
