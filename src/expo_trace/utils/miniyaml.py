"""极简 YAML 子集解析器。

为什么不用 PyYAML：本工具刻意保持零运行时第三方依赖（只读审计工具的供应链面越小越好），
而配置文件只需要“嵌套映射 + 标量 + 标量列表”这一小块能力。

支持的语法::

    key: value              # 标量
    key:                    # 嵌套映射
      child: 1
    key: [a, b, c]          # 内联列表
    key:
      - a                   # 块列表（仅标量）
      - b
    # 整行注释；值中的 # 也会被当作注释起点（引号内除外）

明确不支持：Tab 缩进、锚点/别名、多行字符串、列表套映射、重复键。
遇到不支持的结构会抛 :class:`ConfigError`，而不是猜。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from expo_trace.utils.errors import ConfigError

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_TRUE = {"true", "yes", "on"}
_FALSE = {"false", "no", "off"}
_NULL = {"null", "none", "~"}


def _strip_comment(line: str) -> str:
    out: list[str] = []
    quote: str | None = None
    for ch in line:
        if quote is not None:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _split_inline_list(text: str) -> list[str]:
    body = text.strip()
    if not (body.startswith("[") and body.endswith("]")):
        raise ConfigError(f"内联列表格式错误：{text!r}")
    inner = body[1:-1].strip()
    if not inner:
        return []
    items: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in inner:
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            items.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    items.append("".join(buf).strip())
    if any(item == "" for item in items):
        raise ConfigError(f"内联列表存在空项：{text!r}")
    return items


def _parse_scalar(text: str) -> Any:
    text = text.strip()
    if text == "":
        return ""
    if (text[0] == text[-1]) and text[0] in "\"'" and len(text) >= 2:
        return text[1:-1]
    if text.startswith("["):
        return [_parse_scalar(item) for item in _split_inline_list(text)]
    low = text.lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    if low in _NULL:
        return None
    if _INT_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text):
        return float(text)
    return text


def _tokenize(text: str) -> list[tuple[int, str, int]]:
    tokens: list[tuple[int, str, int]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw[: len(raw) - len(raw.lstrip(" \t"))]:
            raise ConfigError(f"第 {lineno} 行使用 Tab 缩进，请改为空格")
        line = _strip_comment(raw)
        if not line.strip():
            continue
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if indent % 2 != 0:
            raise ConfigError(f"第 {lineno} 行缩进不是 2 的倍数：{indent}")
        tokens.append((indent, stripped, lineno))
    return tokens


def _parse_block(tokens: list[tuple[int, str, int]], pos: int, indent: int) -> tuple[Any, int]:
    if pos >= len(tokens):
        return {}, pos
    is_list = tokens[pos][1].startswith("- ")
    if is_list:
        items: list[Any] = []
        while pos < len(tokens):
            cur_indent, text, lineno = tokens[pos]
            if cur_indent < indent:
                break
            if cur_indent > indent:
                raise ConfigError(f"第 {lineno} 行缩进异常：列表项不应更深")
            if not text.startswith("- "):
                raise ConfigError(f"第 {lineno} 行：同一列表混用了映射语法")
            value = text[2:].strip()
            if not value:
                raise ConfigError(f"第 {lineno} 行：空列表项")
            if value.endswith(":") or (": " in value and not value.startswith("[")):
                raise ConfigError(f"第 {lineno} 行：列表项只支持标量，不支持映射")
            items.append(_parse_scalar(value))
            pos += 1
        return items, pos

    mapping: dict[str, Any] = {}
    while pos < len(tokens):
        cur_indent, text, lineno = tokens[pos]
        if cur_indent < indent:
            break
        if cur_indent > indent:
            raise ConfigError(f"第 {lineno} 行缩进异常：映射项不应更深")
        if text.startswith("- "):
            raise ConfigError(f"第 {lineno} 行：同一层级混用了列表与映射")
        if ":" not in text:
            raise ConfigError(f"第 {lineno} 行缺少冒号：{text!r}")
        key, _, rest = text.partition(":")
        key = key.strip()
        if not key:
            raise ConfigError(f"第 {lineno} 行：键名为空")
        if key in mapping:
            raise ConfigError(f"第 {lineno} 行：重复键 {key!r}")
        pos += 1
        rest = rest.strip()
        if rest:
            mapping[key] = _parse_scalar(rest)
            continue
        # 嵌套块：下一行必须更深
        if pos < len(tokens) and tokens[pos][0] > cur_indent:
            child, pos = _parse_block(tokens, pos, tokens[pos][0])
            mapping[key] = child
        else:
            mapping[key] = None
    return mapping, pos


def loads(text: str) -> dict[str, Any]:
    """解析配置文本，返回字典。"""
    tokens = _tokenize(text)
    if not tokens:
        return {}
    if tokens[0][0] != 0:
        raise ConfigError("首行不应缩进")
    result, pos = _parse_block(tokens, 0, 0)
    if pos != len(tokens):
        lineno = tokens[pos][2]
        raise ConfigError(f"第 {lineno} 行无法解析（缩进不匹配）")
    if not isinstance(result, dict):
        raise ConfigError("配置文件根节点必须是映射")
    return result


def load(path: str | Path) -> dict[str, Any]:
    file = Path(path)
    if not file.is_file():
        raise ConfigError(f"找不到配置文件：{file}")
    try:
        text = file.read_text(encoding="utf-8-sig")
    except OSError as exc:  # pragma: no cover - 文件系统异常
        raise ConfigError(f"读取配置失败：{file}（{exc}）") from exc
    return loads(text)
