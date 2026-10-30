# 更新记录

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；版本号遵循语义化版本。
日期为发布日（Asia/Shanghai）。

## [Unreleased]

### 新增（阶段 2 实现）

- `src/expo_trace/web/`：阶段 2 Web 层落地——FastAPI 应用装配（`app.py`）、SSE 问答（`chat.py` + `agent.py`）、SQLite 会话与审计哈希链（`store.py`）、管理后台与页面（`admin.py` / `pages.py`）、知识库（`kb.py`）、10 个 Agent 工具与一次性 `context_token`（`tools.py`）、聊天页静态资源。
- `src/expo_trace/main.py` 增 `expo_trace web` 子命令（`--host/--port/--db/--run-dir/--reload`）；缺 Web 依赖时打印安装提示并返回 2。
- `tests/test_web.py`：16 项 Web 层测试，依赖缺失时整组跳过。
- `pyproject.toml` 增可选依赖组 `web = [fastapi, uvicorn, python-multipart]` 与 `package-data`（`expo_trace.web` 的 `static/*`）；版本号 0.1.0 → 0.2.0。

### 修复（阶段 2）

- `tests/test_web.py` 的 `setUpClass` 把演示产物挂到 `cls.run`，覆盖了 `unittest.TestCase.run`，装了 Web 依赖后整套测试崩在 `TypeError: 'RunResult' object is not callable`；改为 `cls.demo`。
- SSE `done` 事件里原因码字段名是 `error_code`，与接口文档的 `code` 不一致；统一为 `code`（数据库列名保持 `error_code`）。
- 测试断言与实现/文档对齐：HTTP 错误体是扁平的 `{code, message}`（不是 `{"detail": {...}}`）；`context_token` 在 `request_human_review` 才消费，`compose_draft` 可复用同一令牌走完 `check_fields → compose_draft → request_human_review`；对话审计页按实际渲染的账号与问题断言。
- 文档：`docs/phase2/README.md` 的"只出设计、未写代码"与"需要你确认的 3 件事"过时，改为"已定事项 + 实现状态"。

### 变更（项目对象统一）

- `data/policy/报销制度.md` → `data/policy/展商咨询规则.md`：语料改写为展商咨询口径（版本有效期与确认状态、关键字段、住宿代订标准 650/500/380、发票 30 天、收费与资格边界、兜底触发、工时/返工/确认完整率），RAG 自检 29 项仍全通过。
- `src/expo_trace/rag_qa.py`：模块说明、`SYSTEM_PROMPT`、argparse 描述、`--policy` 默认路径、拒答话术与注释改为展商咨询口径。
- `README.md`、`docs/rag-prototype.md`、`docs/architecture-v2/README.md`、本文件的历史条目统一去掉"报销助手"表述。
- 删除仓库根目录的历史打包文件 `报销助手_f24011321.zip`（约 11MB），`.gitignore` 增 `*.zip`。


### 新增（阶段 2 设计，仅文档）
- `docs/phase2/`：把三份提示词（员工聊天入口、管理后台、Agent 工具）的对象统一替换为“展商咨询工单”，给出可评审设计。
  - `chat-api.md`：`POST /api/v1/chat` SSE 事件契约、7 个接口、SQLite 会话/消息/引用/审计表、最近 6 轮上下文规则、前端状态机、10 条验收。
  - `admin-console.md`：7 个页面、分角色接口清单、JWT（HS256，标准库实现）与职责分离、知识库切片入库、提示词模板版本与热更新、审计标记与导出。
  - `agent-tools.md`：10 个 Function Calling 工具（6 读 3 写，无发送类）、调用顺序状态机、一次性 `context_token` 防跳步机制、系统提示词硬约束段、测试矩阵。
- 根 `README.md` 增“阶段 2 设计（待确认）”索引，并修正结构树中 `src/` 的层级错误。

### 变更（目录整合）
- 三批交付物归位到模板目录：`v1设计方案/` → `docs/solution-v1/`、`v2-展商咨询工单助手架构方案/` → `docs/architecture-v2/`、`v3-裸问答原型/rag_qa.py` → `src/rag_qa.py`、规则原文 → `data/policy/展商咨询规则.md`、v3 说明 → `docs/rag-prototype.md`。
- `src/rag_qa.py` 默认路径改为仓库根：`data/policy/展商咨询规则.md` 与 `.rag-index/`；索引目录改名并加入 `.gitignore`。
- 更新 `README.md` 链接、结构树与“交付物放在哪”对照表；未改动 `src/expo_trace/` 任何文件。

### 新增

- `docs/solution-v1/`：业务挖掘报告、行业分析、流程图说明（现状 / 数智化转型后 / 总体架构）、报价单与设计方案，共 6 份可交付文件与 3 张 PNG 原图；根 README 增加索引。
- `src/rag_qa.py`（说明见 `docs/rag-prototype.md`）：单文件 RAG 原型（500 字切片、FAISS/numpy 索引、BM25+向量混合检索、可配置 LLM、拒答判据），含 29 项离线自检与使用说明；根 README 增加 v3 索引。
- `docs/architecture-v2/`：展商咨询工单助手的四层架构选型（FastAPI、LLM 可替换、规则向量检索、两角色），含 30 张表的 Word 方案与 1700×1320 架构图；根 README 增加 v2 索引。

### 修复

- `status` 命令把分派结果误当计数读，始终显示 0：改为按 16 张工单的分派结果汇总。
- Windows 非 UTF-8 控制台（如 CI 的 cp1252 runner）下中文输出崩溃：CLI 与示例启动时把 stdout/stderr 切到 UTF-8，CI 同时设置 `PYTHONUTF8=1`。
- 确认完整率重复计数：同一工单被多人确认时，必确认项逐条累加，完整率可被推到 100% 以上。改为按工单取并集，并新增 `confirmed_tickets` 字段；不在工单清单里的确认记录不再计入。
- 台账小数时长被截断：`25.7` 分钟被 `int()` 截成 `25`。改为保留一位小数，整数时回落为整数，演示数据仍为 452 分钟。
- 输入有误时留下空壳运行目录：导出目录缺文件现在会在创建运行目录之前报错。
- 运行目录被同名文件占住时抛出未捕获的 `OSError`（用户看到栈）：改为中文提示并返回 `2`；`report` 在试点暂停时与 `run` 一致返回 `3`。
- `sample --size 0` / `-5` 静默成功：改为返回 `2`，提示必须为正整数。
- `status` 的「队列」含阻塞项，与 `run` 的「人工队列」口径不一：输出改为标注「（含阻塞项）」。
- `audit show` 的中文列名把表格挤歪：按终端显示宽度对齐。
- `pause` 在一次命令里多次取当前时间，暂停记录与审计时间可能不一致：改为只取一次。
- 新增 9 项回归测试（`tests/test_regressions.py`），测试总数 103 → 112。

### 变更

- LICENSE 恢复为纯 MIT 文本，数据为合成样本的声明移至 `NOTICE`（便于 GitHub 识别许可证）。
- README 补充 `NOTICE` 与仓库根目录历史文件的说明。

### 计划中（均需业务确认后才做）

- 版本台账的增量导入（当前每次全量读取导出）
- 审计凭证导出为可离线校验的包（保存期与格式待审核方确认）
- 80 条抽查的抽样分层可配置（当前按问题类别轮转）

## [0.1.0] - 2026-10-23

首个可运行版本：只读导入 → 版本判定 → 草稿/队列 → 人工确认 → 周报与审计链。

### 新增

- **只读流水线**：`expo-trace run` 读取服务台、业务台账、文件库、交接四份脱敏导出，产出工单、版本判定、问题清单、草稿、队列、周报与验收矩阵。
- **版本判定**：以业务发生时间为时点，按“有效期覆盖 + 确认状态 > 作废状态 > 生效起始 > 确认时间 > 接收时间”排序；**文件名与修改时间不参与判定**；作废保留原因；替代链断裂即进人工队列。
- **双时间戳**：业务发生时间与系统接收时间分别记录四态（正常/缺失/未确认/非法）；回填超过 8 小时标记并从“干净工时”剔除；版本判定不使用接收时间。
- **快照兜底**：导出读取失败时回放上次成功读取的原始文本，标注快照时间与降级原因，提示人工核查；不编造、不静默重试。
- **人工队列**：版本不可定位、版本冲突、链断、关键字段缺失/未确认、来源不完整、授权不清、收费/资格、交接缺项、版本漂移、试点暂停全部进队列，带责任人、时限与原因；每条工单必有分派。
- **草稿**：首行固定 `【未确认 · 不可对外】`；列出依据版本、有效期、确认人、判定依据、未确认事项、已排除版本与来源行号；收费/资格类标记禁止对外；`publish` / `send_external` / `write_back` 恒抛 `BlockedAction`。
- **人工确认**：四项确认齐全才可通过；按角色校验权限；收费/资格仅指定审核人可确认；退回需写原因；同一时刻重复提交被拒。
- **误报/漏报登记**：`expo-trace feedback` 追加记录并写审计，进入周报“误报 / 漏报 / 未确认”小节。
- **三项指标**：处理工时、返工次数、确认完整率并列输出并附公式；回填样本单列；报告明确“只用工时判断效果会掩盖返工与确认不完整”。
- **审计链**：`audit.jsonl` 逐条哈希链接，记录谁在何时对哪张工单做了什么（导入、判定、确认、登记、暂停、恢复）；每次运行还为每张工单写 `version_resolved`（版本、状态、原因码），`audit show --ticket` 写 `audit_viewed`；`audit verify` 可定位断点；`audit show --ticket` 还原单张工单的全部动作。
- **CLI**：`run` / `status` / `review` / `feedback` / `pause` / `resume` / `report` / `audit` / `sample`，退出码 0/2/3/4/5 有明确含义。
- **文档**：README、设计说明、API、试点手册、需求追溯矩阵、5 张 Mermaid 流程图与离线 HTML 可视化。
- **示例数据**：16 张工单、12 个版本、12 类故意埋设的问题（重复编号、空时间、链断、冲突、单位差异、跨批次、文件名陷阱、回填、交接缺项/漂移等），全部为合成样本。

### 安全

- 配置强制只读：`safety.read_only` / `draft_only` 必须为 true，`auto_publish` / `write_back` / `external_message` 必须为 false，否则拒绝启动。
- 个人字段在进入运行目录前掩码；`raw` 只保留业务字段与原行结构。
- 无第三方运行时依赖，无网络调用。

### 已知限制

- 审计链是本地哈希链，没有外部锚点：拥有运行目录写权限的人可以重算整条链。需要定期把 `audit.jsonl` 导出到独立存储。
- 工单编号仍来自源系统，不是跨系统新生成的统一编号。
- 工时来自台账，台账本身可能不准；线下返工无法统计。
- 业务验收（80 条抽查、六条需求的人工确认）尚未执行，`acceptance.json` 中恒为 `NOT_RUN`。
- 示例数据为合成样本，不能用于估算真实旧版本占比或效率提升。
