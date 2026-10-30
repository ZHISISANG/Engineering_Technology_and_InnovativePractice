"""命令行入口（薄壳）：``python src/rag_qa.py``。

实现位于 ``src/expo_trace/rag_qa.py``——放进包里是为了让 Web 层能直接复用
切片、混合检索与 LLM 调用，而不必按路径加载文件。命令与参数完全不变。
"""

from expo_trace.rag_qa import main

if __name__ == "__main__":
    raise SystemExit(main())
