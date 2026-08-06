"""TOML basic string 的集中编码工具。

所有平台适配器写入 TOML 字符串时都必须使用本模块的编码函数，
不要在各适配器中重复实现转义逻辑。
"""

from __future__ import annotations


def toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def toml_unescape(value: str) -> str:
    """单遍解码 TOML basic string 的常见转义（双反斜杠、引号、n/t/r、u 四位十六进制）。

    与 tomllib 的解析语义一致：双反斜杠加引号应还原为单反斜杠加引号，
    而不是两个反斜杠。
    """

    out: list[str] = []
    i = 0
    simple = {"\\": "\\", '"': '"', "n": "\n", "t": "\t", "r": "\r"}
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
                continue
            if nxt == "u" and i + 5 < len(value):
                try:
                    out.append(chr(int(value[i + 2 : i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
        out.append(ch)
        i += 1
    return "".join(out)
