"""EU4 (Clausewitz/PDX) 脚本解析器 —— 全部工具的地基。

为什么需要它
------------
EU4 的失败模式是**静默的**：`treasury = -50` 写在效果位，游戏不崩、不弹窗，
只是整条事件链不生效。要抓住这类问题，前提是能读准结构并给出**精确行号**。
本模块不追求还原游戏语义，只保证：

  * 任何输入都不崩（含残缺文件、BOM、非法编码、未闭合块）
  * 尽量给出可用结构（key = value / key = { } / 裸值列表 / 匿名块）
  * 每个节点都带行号，报错能定位到 文件:行:列

EU4 语法实测结论（v1.37.5.0，全目录扫描，非推测）
------------------------------------------------
1. 注释 `#` 到行尾；**引号内的 `#` 不是注释**（`common/.../Aachen.txt:39` 有 `"Friedrich #0" = 100` 实证）。
2. 字符串用双引号，内部可含转义。
3. `!=` `?=` `<=` `>=` `==` 在 EU4 脚本里**没有有效用法**
   （全目录匹配到的只有注释里的 `#==>` 与许可证文件的分隔线）。
   本解析器仍认这些运算符以保证不崩，但会附一条 warning，方便 lint 抓「从 HOI4 抄过来的写法」。
4. `$PARAM$` 占位符是**普通 token**（scripted_effects 里 2226 处），不是变量引用，照原样保留。
5. `@xxx` 在 vanilla 里 0 处有效用法（只有论坛式 @人名 出现在 patchnotes 注释里）；
   但为容错仍当普通 token 处理。
6. 日期是 `1444.1.24` 这种点分三段数字，整体当一个 token。
7. 裸值列表存在（`reformed` / `protestant` 独立成行，`common/religions/00_religion.txt:23`）。
8. vanilla 脚本 `.txt` 全部无 BOM（抽样 200 个 common 文件 0 个带 BOM）；
   解析器把 BOM 记为 warning 而不是 error —— 判定交给 lint。

API
---
    tree, errors = parse_text(text)          # errors: list[ParseError]
    tree, errors = parse_file(path)          # 自动处理 BOM / 非 UTF-8 编码
    res          = parse_file_info(path)     # 附带 encoding / bom / 行数等元信息

    loc          = parse_localisation(path)       # ★ .yml 本地化（key:0 "value"）
    loc          = parse_localisation_file_info(path)  # 附带 FileInfo

    Block.get("key") -> Entry | None
    Block.get_all("key") -> list[Entry]
    Block.first_scalar("key") / first_block("key") / all_scalars("key")
    Block.walk() -> Iterator[Entry]                     # 深度优先，全部 Entry
    Block.walk_paths() -> Iterator[(tuple[str,...], Entry)]   # ★ 带上下文路径
    Block.find_blocks("key") / find_entries("key")

`walk_paths()` 是分类的关键：判断一个 key 是效果还是触发器，靠的是它**出现在哪个语境**
（`limit = { }` / `trigger = { }` 内是触发器位，`immediate = { }` / 选项体内是效果位），
而不是它出现过几次 —— 这正是 `treasury` 那个坑的机理。

本地化 .yml 是**另一套语法**（不是 `key = value`）：
    l_english:
     KEY:0 "value $VAR$ [Root.GetName]"
     KEY:1 "另一个版本（同 key 不同序号，游戏取序号低者）"
  行首缩进决定层级；`key:0 "..."` 用冒号不是等号；值必须在双引号内。
  `.yml` **必须**带 UTF-8 BOM（vanilla 578 个全都带），与 `.txt` 恰好相反。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterator

__all__ = [
    "ParseError",
    "Token",
    "Value",
    "Entry",
    "Block",
    "FileInfo",
    "parse_text",
    "parse_file",
    "parse_file_info",
    "tokenize",
    "collect_pairs",
    "all_ids",
    "iter_blocks",
    "block_scalar",
    "EU4_ONLY_OPS",
    "KNOWN_UNUSED_OPS",
]

# EU4 里实际不用的运算符：解析保留，但会记为 warning
KNOWN_UNUSED_OPS = ("!=", "<=", ">=", "==", "?=", ">", "<")
EU4_ONLY_OPS = ("=",)


class ParseError(Exception):
    """一处问题。severity: 'error'（会导致不生效）/ 'warning'（可疑但不阻塞）。"""

    __slots__ = ("message", "line", "col", "path", "severity", "code")

    def __init__(
        self,
        message: str,
        line: int,
        col: int = 1,
        path: str | None = None,
        severity: str = "error",
        code: str = "",
    ):
        self.message = message
        self.line = line
        self.col = col
        self.path = path
        self.severity = severity
        self.code = code
        super().__init__(self.__str__())

    def __str__(self) -> str:
        return f"{self.path or '<text>'}:{self.line}:{self.col}: [{self.severity}] {self.message}"

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "line": self.line,
            "col": self.col,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }


@dataclass(slots=True)
class Token:
    kind: str          # 'id' | 'num' | 'str' | '{' | '}' | 'op'
    value: str
    line: int
    col: int


@dataclass(slots=True)
class Value:
    """裸值（列表元素）或 `=` 右侧的标量。"""

    value: str
    kind: str = "id"     # 'id' | 'num' | 'str'
    line: int = 0

    def __str__(self) -> str:
        return self.value

    @property
    def is_quoted(self) -> bool:
        return self.kind == "str"


@dataclass(slots=True)
class Entry:
    key: str
    op: str
    value: "Value | Block"
    line: int
    col: int = 1

    @property
    def scalar(self) -> str | None:
        return self.value.value if isinstance(self.value, Value) else None

    @property
    def block(self) -> "Block | None":
        return self.value if isinstance(self.value, Block) else None

    @property
    def is_bare(self) -> bool:
        """无 `=` 的裸值（列表元素）。"""
        return self.op == ""


@dataclass
class Block:
    entries: list = field(default_factory=list)
    line: int = 0

    # ---------- 查询 ----------
    def pairs(self) -> Iterator[Entry]:
        """只产出带 key 的 Entry（跳过裸值）。"""
        for e in self.entries:
            if isinstance(e, Entry) and e.op:
                yield e

    def get(self, key: str) -> Entry | None:
        for e in self.pairs():
            if e.key == key:
                return e
        return None

    def get_all(self, key: str) -> list[Entry]:
        return [e for e in self.pairs() if e.key == key]

    def first_scalar(self, key: str, default: str | None = None) -> str | None:
        e = self.get(key)
        return e.scalar if e and e.scalar is not None else default

    def first_block(self, key: str) -> "Block | None":
        e = self.get(key)
        return e.block if e else None

    def all_scalars(self, key: str) -> list[str]:
        return [e.scalar for e in self.get_all(key) if e.scalar is not None]

    def walk(self) -> Iterator[Entry]:
        """深度优先遍历全部 Entry（不产出自身）。"""
        for e in self.entries:
            if isinstance(e, Entry):
                yield e
                if isinstance(e.value, Block):
                    yield from e.value.walk()
            elif isinstance(e, Value) and e.kind == "block":
                pass

    def walk_paths(self) -> Iterator[tuple[tuple[str, ...], Entry]]:
        """★ 带上下文路径的遍历。路径 = 祖先 key 组成的元组。

        例：`OR = { treasury = 500 }` 内的 treasury，路径为 ('OR',)；
        若外层还有 `limit = {`，路径为 ('limit','OR')。
        """
        yield from self._walk_paths(())

    def _walk_paths(self, prefix: tuple[str, ...]) -> Iterator[tuple[tuple[str, ...], Entry]]:
        for e in self.entries:
            if not isinstance(e, Entry):
                continue
            yield prefix, e
            if isinstance(e.value, Block):
                yield from e.value._walk_paths(prefix + (e.key,))

    def walk_full(self) -> Iterator[tuple[tuple[str, ...], Entry, tuple["Block", ...]]]:
        """★★ 带祖先**块对象**的遍历：(path, entry, ancestor_blocks)。

        为什么需要它：有些容器的语义**不在名字里，而在内容里**。
        实测 vanilla 的 `modifier = { }` 有**两种完全不同的意思**：
          · `war_priority = { modifier = { factor = 1.1  treasury = 100 } }`
            → 含 factor，是 **AI 权重块**，内部是**触发器位**
          · `marketplace = { modifier = { ... } }`
            → 不含 factor，是**修正定义块**，内部是**修正字段位**
        只有看到块内容才能分辨。硬编码「modifier 一定是某一种」必然出错。
        （核心原则第 6 个实例：同名容器两种语义。）

        ancestor_blocks 从外到内，最后一个是直接父块。
        """
        yield from self._walk_full((), ())

    def _walk_full(self, prefix: tuple[str, ...], ancestors: tuple["Block", ...]
                   ) -> Iterator[tuple[tuple[str, ...], Entry, tuple["Block", ...]]]:
        for e in self.entries:
            if not isinstance(e, Entry):
                continue
            yield prefix, e, ancestors
            if isinstance(e.value, Block):
                yield from e.value._walk_full(prefix + (e.key,), ancestors + (e.value,))

    def find_blocks(self, key: str) -> Iterator["Block"]:
        for e in self.walk():
            if e.key == key and isinstance(e.value, Block):
                yield e.value

    def find_entries(self, key: str) -> Iterator[Entry]:
        for e in self.walk():
            if e.key == key:
                yield e

    def find_paths(self, key: str) -> Iterator[tuple[tuple[str, ...], Entry]]:
        for path, e in self.walk_paths():
            if e.key == key:
                yield path, e


_OPS_LONG_FIRST = ("!=", "<=", ">=", "==", "?=", "<", ">", "=")
_BREAK = ' \t\r\n{}#<>='


# --------------------------------------------------------------------------- #
# 词法
# --------------------------------------------------------------------------- #
def tokenize(text: str, path: str | None = None) -> tuple[list[Token], list[ParseError]]:
    tokens: list[Token] = []
    errors: list[ParseError] = []
    i, n = 0, len(text)
    line, col = 1, 1

    def advance(k: int) -> None:
        nonlocal i, line, col
        for _ in range(k):
            if i < n and text[i] == "\n":
                line += 1
                col = 1
            else:
                col += 1
            i += 1

    while i < n:
        ch = text[i]

        if ch in " \t\r\n":
            advance(1)
            continue

        if ch == "#":                       # 注释到行尾（引号内不会走到这里）
            while i < n and text[i] != "\n":
                advance(1)
            continue

        if ch == '"':
            start_line, start_col = line, col
            advance(1)
            buf: list[str] = []
            closed = False
            while i < n:
                c = text[i]
                if c == "\\" and i + 1 < n:
                    buf.append(text[i])
                    buf.append(text[i + 1])
                    advance(2)
                    continue
                if c == '"':
                    advance(1)
                    closed = True
                    break
                buf.append(c)
                advance(1)
            if not closed:
                errors.append(
                    ParseError("字符串未闭合（缺少结尾双引号）", start_line, start_col, path, "error", "unterminated-string")
                )
            tokens.append(Token("str", "".join(buf), start_line, start_col))
            continue

        if ch in "{}":
            tokens.append(Token(ch, ch, line, col))
            advance(1)
            continue

        matched = None
        for op in _OPS_LONG_FIRST:
            if text.startswith(op, i):
                matched = op
                break
        if matched:
            # `==` `!=` 这些在 EU4 无效；`>` `<` 只在注释里见过，到不了这里
            if matched != "=":
                errors.append(
                    ParseError(
                        f"运算符 {matched!r} 在 EU4 脚本里无有效用法（可能是从 HOI4 写法带过来的）",
                        line,
                        col,
                        path,
                        "warning",
                        "unused-op",
                    )
                )
            tokens.append(Token("op", matched, line, col))
            advance(len(matched))
            continue

        # 裸 token：标识符 / 数字 / 日期 / $PARAM$ / @var / 路径
        start_line, start_col, start_i = line, col, i
        while i < n and text[i] not in _BREAK:
            advance(1)
        raw = text[start_i:i]
        if not raw:                          # 防御：绝不空转
            advance(1)
            continue
        kind = "num" if re.fullmatch(r"-?\d+(\.\d+)*", raw) else "id"
        tokens.append(Token(kind, raw, start_line, start_col))

    return tokens, errors


# --------------------------------------------------------------------------- #
# 语法
# --------------------------------------------------------------------------- #
class _Parser:
    def __init__(self, tokens: list[Token], path: str):
        self.tokens = tokens
        self.path = path
        self.pos = 0
        self.errors: list[ParseError] = []

    def peek(self) -> Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self) -> Token | None:
        tok = self.peek()
        if tok is not None:
            self.pos += 1
        return tok

    def err(self, msg: str, tok: Token | None, code: str = "") -> None:
        if tok is None:
            last = self.tokens[-1] if self.tokens else Token("id", "", 1, 1)
            self.errors.append(ParseError(msg, last.line, last.col, self.path, "error", code))
        else:
            self.errors.append(ParseError(msg, tok.line, tok.col, self.path, "error", code))

    def parse_root(self) -> Block:
        return self.parse_body(top=True, open_tok=None)

    def parse_body(self, top: bool, open_tok: Token | None) -> Block:
        block = Block(line=open_tok.line if open_tok else (self.peek().line if self.peek() else 1))
        while True:
            tok = self.peek()
            if tok is None:
                if not top:
                    self.errors.append(
                        ParseError(
                            "文件结束但块未闭合：缺少 '}'（本行对应它开始的位置）",
                            open_tok.line if open_tok else 1,
                            open_tok.col if open_tok else 1,
                            self.path,
                            "error",
                            "unclosed-block",
                        )
                    )
                return block

            if tok.kind == "}":
                if top:
                    self.err("多余的 '}'（顶层不应出现闭合括号）", tok, "stray-close")
                    self.next()
                    continue
                self.next()
                return block

            if tok.kind == "{":
                # 匿名块：`data = { { ... } }` 这类
                self.next()
                inner = self.parse_body(top=False, open_tok=tok)
                block.entries.append(Value("<anonymous block>", "block", tok.line))
                continue

            key_tok = self.next()
            if key_tok.kind == "op":
                self.err(f"意外的运算符 {key_tok.value!r}（此处应为 key）", key_tok, "stray-op")
                continue

            nxt = self.peek()
            if nxt is not None and nxt.kind == "op":
                op = self.next()
                val_tok = self.peek()
                if val_tok is None:
                    self.err(f"'{key_tok.value} {op.value}' 后缺少值（文件在此结束）", key_tok, "missing-value")
                    continue
                if val_tok.kind == "{":
                    self.next()
                    inner = self.parse_body(top=False, open_tok=val_tok)
                    block.entries.append(Entry(key_tok.value, op.value, inner, key_tok.line, key_tok.col))
                elif val_tok.kind in ("id", "num", "str"):
                    self.next()
                    block.entries.append(
                        Entry(
                            key_tok.value,
                            op.value,
                            Value(val_tok.value, val_tok.kind, val_tok.line),
                            key_tok.line,
                            key_tok.col,
                        )
                    )
                elif val_tok.kind == "}":
                    self.err(f"'{key_tok.value} {op.value}' 后缺少值（紧跟闭合括号）", key_tok, "missing-value")
                elif val_tok.kind == "op":
                    self.err(
                        f"'{key_tok.value} {op.value}' 后又是运算符 {val_tok.value!r}（连续运算符）",
                        val_tok,
                        "double-op",
                    )
                    self.next()
                else:
                    self.err(f"'{key_tok.value} {op.value}' 后出现意外符号 {val_tok.value!r}", val_tok, "unexpected-token")
                    self.next()
            else:
                # 裸值列表元素
                block.entries.append(
                    Entry(key_tok.value, "", Value(key_tok.value, key_tok.kind, key_tok.line), key_tok.line, key_tok.col)
                )


def parse_text(text: str, path: str | None = None, *, strip_bom: bool = True) -> tuple[Block, list[ParseError]]:
    errors: list[ParseError] = []
    if text.startswith("\ufeff"):
        if strip_bom:
            text = text[1:]
        errors.append(
            ParseError(
                "脚本以 UTF-8 BOM 开头（vanilla 词法器不处理 BOM；本机容错来自 Unicode Patch 补丁，不能依赖）",
                1,
                1,
                path,
                "warning",
                "bom",
            )
        )
    tokens, tok_errors = tokenize(text, path)
    errors.extend(tok_errors)
    parser = _Parser(tokens, path or "<text>")
    tree = parser.parse_root()
    errors.extend(parser.errors)
    return tree, errors


@dataclass(slots=True)
class FileInfo:
    path: str
    encoding: str
    bom: bool
    bytes: int
    lines: int
    non_ascii: bool

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "encoding": self.encoding,
            "bom": self.bom,
            "bytes": self.bytes,
            "lines": self.lines,
            "non_ascii": self.non_ascii,
        }


def _decode(raw: bytes, path: str | None) -> tuple[str, str, bool, list[ParseError]]:
    """返回 (text, encoding, had_bom, errors)。

    实测：vanilla 自己就有非 UTF-8 的文件（`common/countries/Morocco.txt` 里
    `Sal\\xe9` = salé 是 CP1252/Latin-1），所以「非 UTF-8」只能算 warning，
    不能算 error —— 判定交给 lint，且 lint 也要区分 vanilla 与模组文件。

    顺序：UTF-8 → GB18030（仅在解出高密度 CJK 时采纳）→ CP1252/Latin-1 兜底。
    """
    errors: list[ParseError] = []
    bom = raw.startswith(b"\xef\xbb\xbf")
    if bom:
        raw = raw[3:]

    try:
        return raw.decode("utf-8"), "utf-8", bom, errors
    except UnicodeDecodeError as exc:
        line = raw[: exc.start].count(b"\n") + 1

    # GB18030：只有解出来的文本以中文为主才采纳（否则是误判）
    #
    # 实测校准（2026-11-01，vanilla 2654 个 .txt 全量）：
    #   vanilla **没有任何中文 .txt** —— 所以任何 GB18030 命中都必须告警，不能默默采纳。
    #   GB18030 是超集，能解码任意字节，西文重音字节（å ä ö é）会被拼成汉字码位。
    #   假阳上限：common/province_names/swedish.txt 密度 2.76%（汉字 17 个，实为 åäö）
    #   真阳下限：实测汉化 mod 最低 6.0%（126_l_english.yml），整体 36.3%
    #   → 有效区间 [3%, 6%)，取 4% 留两侧余量。
    #   （上一轮门槛用 2%，正好把那 1 个假阳当成「真实中文文件」误判，已修正。）
    GB18030_MIN_CJK = 8
    GB18030_MIN_DENSITY = 0.04
    try:
        text = raw.decode("gb18030")
        cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        if cjk >= GB18030_MIN_CJK and cjk / max(len(text), 1) >= GB18030_MIN_DENSITY:
            errors.append(
                ParseError(
                    f"文件不是合法 UTF-8（第 {line} 行附近），GB18030 解码后汉字 {cjk} 个"
                    f"（密度 {cjk/max(len(text),1)*100:.2f}%）。**vanilla 不含中文脚本**，"
                    "这通常是西文重音字符被误拼成汉字码位；确认是中文请转成无 BOM 的 UTF-8",
                    line, 1, path, "warning", "encoding",
                )
            )
            return text, "gb18030", bom, errors
    except UnicodeDecodeError:
        pass

    # CP1252 兜底：0x80-0x9F 有几个未定义位，用 latin-1 保证永不失败
    errors.append(
        ParseError(
            f"文件不是合法 UTF-8（第 {line} 行附近），已按 CP1252/Latin-1 解读（疑似西文重音字符）；"
            "若非有意为之，建议转成无 BOM 的 UTF-8",
            line, 1, path, "warning", "encoding",
        )
    )
    return raw.decode("latin-1"), "latin-1", bom, errors


def parse_file_info(path: str | os.PathLike[str]) -> tuple[Block, list[ParseError], FileInfo]:
    with open(path, "rb") as fh:
        raw = fh.read()
    text, encoding, bom, enc_errors = _decode(raw, str(path))
    tree, errors = parse_text(text, str(path), strip_bom=False)
    if bom:
        errors.insert(
            0,
            ParseError(
                "脚本以 UTF-8 BOM 开头（vanilla 词法器不处理 BOM；本机容错来自 Unicode Patch 补丁，不能依赖）",
                1, 1, str(path), "warning", "bom",
            ),
        )
    info = FileInfo(
        path=str(path),
        encoding=encoding,
        bom=bom,
        bytes=len(raw),
        lines=raw.count(b"\n") + (0 if raw.endswith(b"\n") else 1),
        non_ascii=any(b > 127 for b in raw),
    )
    return tree, enc_errors + errors, info


def parse_file(path: str | os.PathLike[str]) -> tuple[Block, list[ParseError]]:
    tree, errors, _ = parse_file_info(path)
    return tree, errors


# --------------------------------------------------------------------------- #
# 高层辅助（lint / build_reference 复用）
# --------------------------------------------------------------------------- #
def collect_pairs(tree: Block, key: str) -> list[Entry]:
    return [e for e in tree.walk() if e.key == key]


def all_ids(tree: Block) -> list[tuple[str, int]]:
    """全部 `id = xxx`（重复定义检测用）。"""
    out: list[tuple[str, int]] = []
    for e in tree.walk():
        if e.key == "id" and e.scalar is not None:
            out.append((e.scalar, e.line))
    return out


def iter_blocks(tree: Block, key: str) -> Iterator[Block]:
    for e in tree.walk():
        if e.key == key and isinstance(e.value, Block):
            yield e.value


def block_scalar(block: Block, key: str) -> str | None:
    e = block.get(key)
    return e.scalar if e else None


# --------------------------------------------------------------------------- #
# 自检 / 手工检查
# --------------------------------------------------------------------------- #
_SELFTEST = r"""
# 注释里有 # 与 花括号 { } 不应影响解析
namespace = zz_epi_grain

country_event = {
	id = zz_epi_grain.1
	title = "zz_epi_grain.1.t"
	desc = "【中文】描述 # 引号内的 # 不是注释"
	is_triggered_only = yes

	trigger = {
		OR = {
			treasury = 500
			AND = { num_of_cities = 3 has_port = yes }
		}
	}

	immediate = {
		treasury = -50
	}

	option = {
		name = "zz_epi_grain.1.a"
		add_years_of_income = 0.5
	}
}
"""


def _selftest() -> int:
    tree, errs = parse_text(_SELFTEST, "<selftest>")
    print(f"top entries: {len(tree.entries)}")
    print(f"errors: {len(errs)}")
    for e in errs:
        print("   ", e)
    print("namespace =", tree.first_scalar("namespace"))
    ev = tree.first_block("country_event")
    print("event id =", ev.first_scalar("id") if ev else None)
    print("is_triggered_only =", ev.first_scalar("is_triggered_only") if ev else None)
    print("treasury 出现位置（路径 -> 值）：")
    for path, e in tree.find_paths("treasury"):
        print(f"   {'/'.join(path) or '<root>'}  ->  {e.scalar}   (line {e.line})")
    trig = ev.first_block("trigger") if ev else None
    imm = ev.first_block("immediate") if ev else None
    print("trigger 块存在:", trig is not None, " immediate 块存在:", imm is not None)
    return 0


if __name__ == "__main__":
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _paths import force_utf8_stdout

    force_utf8_stdout()
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        raise SystemExit(_selftest())
    for p in args:
        tree, errs, info = parse_file_info(p)
        print(f"== {p}")
        print(f"   {len(tree.entries)} top entries | {info.encoding} | bom={info.bom} | {info.lines} lines")
        for e in errs[:30]:
            print("   ", e)
