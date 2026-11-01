"""EU4 本地化 `.yml` 解析（独立于脚本 .txt 的那套语法）。

为什么必须单独一路
------------------
`.yml` 不是 `key = value`：
    l_english:
     KEY:0 "value"
     KEY:1 "alternate"          # 同 key 不同序号，游戏取序号最低的那个

vanilla 实测（v1.37.5.0）：
  * localisation/*.yml 共 578 个，**全部带 UTF-8 BOM**（与脚本 .txt 恰好相反）
  * 首行根键与文件名语言后缀一致：`l_english:` / `l_french:` / `l_german:` / `l_spanish:`
  * 值必须在双引号内；`$VAR$` / `[Root.GetName]` / `£icon£` 原样保留
  * `#` 注释只在行首（缩进后仍算行首）；引号内 `#` 不是注释

本模块只做「读」，不写。写 .yml 由 new_mod / install_mod 负责（必须带 BOM）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from pdx_parser import FileInfo, ParseError

__all__ = [
    "LocLine",
    "LocFile",
    "parse_localisation",
    "parse_localisation_file_info",
    "SUPPORTED_LANGS",
]

SUPPORTED_LANGS = ("english", "french", "german", "spanish", "polish", "russian", "brazilian_portuguese")

_ROOT_RE = re.compile(r"^(l_[a-z_]+)\s*:\s*$")
_ENTRY_RE = re.compile(r"^([A-Za-z0-9_.\-\[\]]+):(\d+)\s*(.*)$")


@dataclass(slots=True)
class LocLine:
    key: str
    index: int
    value: str
    line: int
    lang: str = ""
    raw: str = ""

    @property
    def identity(self) -> str:
        return f"{self.key}:{self.index}"


@dataclass
class LocFile:
    path: str = ""
    lang: str = ""
    root_key: str = ""
    bom_seen: bool = False
    entries: list[LocLine] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)
    info: FileInfo | None = None

    def keys(self) -> set[str]:
        return {e.key for e in self.entries}

    def by_key(self) -> dict[str, list[LocLine]]:
        out: dict[str, list[LocLine]] = {}
        for e in self.entries:
            out.setdefault(e.key, []).append(e)
        return out


def parse_localisation(
    text: str, path: str | None = None, *, expect_bom: bool = True
) -> LocFile:
    """解析 .yml 文本。`expect_bom` 只为产出诊断，不影响解析。"""
    errors: list[ParseError] = []
    loc = LocFile(path=path or "<text>")

    if text.startswith("\ufeff"):
        text = text[1:]
        loc.bom_seen = True
    else:
        loc.bom_seen = False
        if expect_bom:
            errors.append(
                ParseError(
                    "本地化 .yml 缺少 UTF-8 BOM（vanilla 578 个 .yml 全部带 BOM；"
                    "缺 BOM 时部分语言/工具链会整份忽略该文件）",
                    1, 1, path, "error", "missing-bom",
                )
            )

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip("\r")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        m = _ROOT_RE.match(line)
        if m:
            loc.root_key = m.group(1)
            loc.lang = m.group(1)[2:]
            if loc.lang not in SUPPORTED_LANGS:
                errors.append(
                    ParseError(
                        f"未知语言根键 {m.group(1)!r}（应为 l_english / l_french / l_german / l_spanish 等）",
                        lineno, 1, path, "warning", "unknown-lang",
                    )
                )
            continue

        m = _ENTRY_RE.match(line.lstrip())
        if not m:
            errors.append(
                ParseError(
                    f"无法解析的本地化行：{stripped[:60]!r}（应为 KEY:0 \"value\"）",
                    lineno, 1, path, "error", "bad-loc-line",
                )
            )
            continue

        key, idx, rest = m.group(1), int(m.group(2)), m.group(3).strip()
        if not rest:
            errors.append(
                ParseError(f"键 {key}:{idx} 没有值", lineno, 1, path, "error", "empty-loc-value")
            )
            continue

        # ★ 值后面允许跟行尾注释：`KEY:0 "值" # [LocEditor:AwaitingBatchUpdate]`
        #   vanilla 有大量这种行（109 个文件），不能因为「不以引号结尾」就判成未加引号。
        value, trailing, quoted = _split_value_and_comment(rest)
        if not quoted:
            errors.append(
                ParseError(
                    f"键 {key}:{idx} 的值未用双引号包裹（游戏会忽略该条）",
                    lineno, 1, path, "warning", "unquoted-loc-value",
                )
            )
        elif trailing and not trailing.startswith("#"):
            errors.append(
                ParseError(
                    f"键 {key}:{idx} 的引号后有多余内容：{trailing[:40]!r}",
                    lineno, 1, path, "warning", "trailing-garbage",
                )
            )

        loc.entries.append(
            LocLine(key=key, index=idx, value=value, line=lineno, lang=loc.lang, raw=line.strip())
        )

    # 根键缺失：若文件通篇是注释（vanilla 有这种空占位文件），只能算 info 级提示
    if not loc.root_key:
        has_content = bool(loc.entries)
        errors.append(
            ParseError(
                "缺少语言根键（首行应为 l_english: 之类）"
                + ("" if has_content else "；本文件没有任何有效键，疑似空占位文件"),
                1, 1, path,
                "error" if has_content else "warning",
                "missing-root-key",
            )
        )
    loc.errors = errors
    return loc


def _split_value_and_comment(rest: str) -> tuple[str, str, bool]:
    """把 `"值" # 注释` 拆成 (值, 尾随内容, 是否被引号包裹)。

    两个必须支持的 vanilla 现实（实测 localisation/*.yml）：
      1. 值后面跟行尾注释：`KEY:0 "值" # [LocEditor:AwaitingBatchUpdate]`
      2. **值内部含未转义的引号**：`"I had a dream," [Root.Monarch.GetSheHe] announces, ..."`
         —— PDX 本地化以**该行最后一个引号**作为终止符，不是第一个。
         若按第一个引号截断，3307 行会被误判成「引号后有多余内容」。

    所以策略：先试「最后一个引号」；若其后内容不是注释也不是空，再退回「第一个引号」。
    引号未闭合（找不到任何结束引号）返回 quoted=False。
    """
    if not rest.startswith('"'):
        return rest, "", False

    last = rest.rfind('"')
    if last > 0:
        after = rest[last + 1:].strip()
        if after == "" or after.startswith("#"):
            return rest[1:last], after, True

    # 退回：第一个结束引号（此时引号后确有非注释内容，交给调用方告警）
    i, n = 1, len(rest)
    buf: list[str] = []
    while i < n:
        c = rest[i]
        if c == "\\" and i + 1 < n:
            buf.append(rest[i])
            buf.append(rest[i + 1])
            i += 2
            continue
        if c == '"':
            return "".join(buf), rest[i + 1:].strip(), True
        buf.append(c)
        i += 1
    return "".join(buf), "", False


def parse_localisation_file_info(path: str | os.PathLike[str]) -> LocFile:
    with open(path, "rb") as fh:
        raw = fh.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    body = raw[3:] if bom else raw
    try:
        text = body.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = body.decode("latin-1")
        encoding = "latin-1"

    loc = parse_localisation(text, str(path), expect_bom=True)
    if bom:
        loc.bom_seen = True
        loc.errors = [e for e in loc.errors if e.code != "missing-bom"]
    loc.info = FileInfo(
        path=str(path),
        encoding=encoding,
        bom=bom,
        bytes=len(raw),
        lines=raw.count(b"\n") + (0 if raw.endswith(b"\n") else 1),
        non_ascii=any(b > 127 for b in raw),
    )
    return loc


if __name__ == "__main__":
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _paths import force_utf8_stdout

    force_utf8_stdout()
    for p in sys.argv[1:]:
        loc = parse_localisation_file_info(p)
        n_err = sum(1 for e in loc.errors if e.severity == "error")
        n_warn = len(loc.errors) - n_err
        print(f"== {p}")
        print(f"   lang={loc.lang} root={loc.root_key} entries={len(loc.entries)} "
              f"bom={loc.info.bom if loc.info else '?'} err={n_err} warn={n_warn}")
        for e in loc.errors[:20]:
            print("   ", e)
