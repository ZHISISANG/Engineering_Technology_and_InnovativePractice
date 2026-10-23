## 这个 PR 做了什么

<!-- 一段话说清问题与做法；关联 issue 用 "Closes #123" -->

## 改动类型

- [ ] 修复
- [ ] 新功能（未突破只读 / 草稿边界）
- [ ] 文档
- [ ] 测试 / 重构（行为不变）

## 自检

- [ ] `python -m unittest discover -s tests -t .` 全部通过
- [ ] `PYTHONPATH=src python examples/demo.py` 可跑完
- [ ] `bash scripts/verify.sh` 通过（含审计链与只读边界）
- [ ] `CHANGELOG.md` 已更新（面向使用者的一句话）
- [ ] 没有提交 `runs/`、`.tmp/`、真实导出、密钥或个人信息
- [ ] 新增/修改的判定规则有对应测试，并说明依据（f 码或业务确认）

## 边界确认

- [ ] 没有新增对外发送、写回源系统、自动收费/资格决定的代码路径
- [ ] 若涉及 `config/default.yaml`，`safety` 段仍为只读 + 只出草稿
- [ ] 若改变输出格式，`docs/api.md` 已同步，且字段只增不删

##  reviewer 需要重点看什么

<!-- 例如：判定排序键、快照兜底、权限校验、中文文案是否会让一线误解 -->
