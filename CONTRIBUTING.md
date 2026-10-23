# 贡献指南

感谢愿意改进这个工具。它首先是一个**只读、只出草稿、可审计**的试点工具，其次才是一个 Python 项目。
任何让这两件事变弱的改动都不会被合并，即使它“更自动”“更高效”。

## 环境

- Python 3.11+（开发用 3.11–3.13 验证；3.14 亦可运行）
- 不需要安装任何运行时依赖
- 不强制使用虚拟环境；建议用 `.venv` 隔离

```bash
git clone <repo-url>
cd expo-ticket-trace

# 方式一：不安装，直接跑
PYTHONPATH=src python -m expo_trace run --export data/demo/export --run-dir runs/dev --now "2026-10-20 10:00"

# 方式二：可编辑安装（需要本机有 setuptools）
python -m pip install -e .
expo-trace --help
```

## 跑测试

```bash
python -m unittest discover -s tests -t .      # 102 项，约 6 秒
PYTHONPATH=src python examples/demo.py         # 端到端示例
bash scripts/verify.sh                         # 测试 + 审计链 + 只读边界 + 严格模式
```

提交前至少跑前两条；改到判定、确认或审计时跑 `verify.sh`。

## 代码规则

1. **零运行时依赖**。只用标准库。确需新增依赖时，先在 issue 里说明为什么标准库做不到。
2. **不新增对外路径**。不写回源系统、不发邮件/短信/IM、不调用外部 API、不自动作收费/资格决定。`drafting.publish` / `send_external` / `write_back` 必须继续只抛 `BlockedAction`。
3. **用户可见文案用中文**，写清楚“为什么”和“下一步找谁”，不要只写错误码。
4. **判定规则必须可解释**：新增或修改排序、冲突、兜底规则时，注释里写依据（需求编号或 f 码），并补测试。
5. **保留原行**：任何新问题类型都要带 `raw`、来源文件与行号。
6. **错误类型与退出码**：输入/配置问题 `TraceError`（2），暂停 `PausedError`（3），确认被拒 `ReviewRejected`（4），越界动作 `BlockedAction`（5）。不要用裸 `Exception`。
7. **写文件用 `RunStore`**，它负责原子写与目录约定；不要在业务代码里直接 `open(..., "w")` 写运行目录。
8. **时间一律带时区**，用 `utils/timeutil.py` 的解析与格式化；不要用 `datetime.now()` 取当前时间，从参数传入。

## 测试规则

- 用标准库 `unittest`；测试文件与 `src/expo_trace/core/` 模块对应。
- 固定时间用 `tests/helpers.FIXED_NOW`，不要依赖真实时钟。
- 临时目录用 `tests/helpers.temp_dir()`（Windows 沙箱下 `tempfile` 不可用）。
- 断言具体原因代码、责任人、原行，而不是只断言“非空”。
- 涉及“不该发生”的行为，要断言异常类型与消息关键词。

## 数据规则

- 只提交合成或已脱敏数据；不要提交真实展商名称、联系人、手机号、邮箱、证件号、报价。
- 不提交 `runs/`、`.tmp/`、`examples/.demo-run/`、密钥或本地配置。
- 示例数据改动需同步 `data/demo/README.md` 的“故意埋的坑”清单。

## 提交信息

用一句话说清“改了什么、为什么”，可参考 Conventional Commits：

```
fix(versioning): 作废版本不再参与当前版本判定

原因：f24 要求以确认状态与有效期为准；作废记录只保留原因。
影响：T-1006 从“可出草稿”变为“人工队列”，测试已更新。
```

## 提 PR

1. Fork 并开分支：`fix/...`、`feat/...`、`docs/...`；
2. 按 `.github/PULL_REQUEST_TEMPLATE.md` 填自检；
3. 确保 CI 在两个系统上都绿（Windows 也要过）；
4. 维护者会重点看：判定规则是否有依据、是否新增对外路径、中文文案是否会让一线误解、审计链是否仍能定位到人。

## 发布

1. 更新 `CHANGELOG.md`（面向使用者的一句话，不要贴 commit 列表）；
2. 提升 `pyproject.toml` 的 `version`；
3. 打标签：`git tag -a v0.1.0 -m "0.1.0"`；
4. 推送标签并在 GitHub 创建 Release，附 wheel 与 `docs/pilot-runbook.md` 链接。

## 行为准则

讨论对事不对人；对“是否突破边界”的争议，以 `docs/design.md` 的非目标与 `config/default.yaml` 的 `safety` 段为准。
