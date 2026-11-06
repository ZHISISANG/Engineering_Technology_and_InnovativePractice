# 问答助手归档（v3 裸问答原型）

本目录是阶段 1「裸问答原型」的交付归档（src 1）：检索链路（切片 / BM25 + 向量 / 拒答 / 可配置 LLM）
在阶段 2 已被 Web 层的知识库与 Agent 工具取代，源码仍保留在 `src/expo_trace/rag_qa.py` 供复用，不再单独演进。

## 内容

| 文件 | 说明 |
| --- | --- |
| `rag_qa.py` | 单文件实现（切片、Embedder、VectorStore、BM25、hybrid_search、build_messages、call_llm） |
| `rag_qa_cli.py` | 仓库根的薄壳入口，转调上者 |
| `展商咨询规则.md` | 合成语料（10 节 34 条），self-test 29 项依赖其中的住宿/650 断言 |
| `rag-prototype.md` | 使用说明：命令、链路图、关键设计、已知限制 |
| `qa-assistant-v3.zip` | 上述文件的打包快照（可直接下载） |

## 状态

- 维护状态：**归档**（不再新增功能；修缺陷会同步）
- 自检：`PYTHONPATH=src python src/rag_qa.py --selftest` → 29/29
- 依赖：仅标准库 + numpy；faiss 可选
