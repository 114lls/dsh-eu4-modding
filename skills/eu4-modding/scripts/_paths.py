"""EU4 路径探测与环境事实采集（全部工具共用）。

设计原则
--------
* 只读，不写任何游戏目录内容（铁律 1）。
* 探测失败返回 None，由调用方决定回退或提示，绝不抛异常打断工具运行。
* 环境变量优先：EU4_GAME / EU4_USER_DIR（Linux-Proton 用户必须显式设后者）。

EU4 的目录分布（与 HOI4 不同，实测 v1.37.5.0）
---------------------------------------------
  游戏目录  <Steam>/steamapps/common/Europa Universalis IV
            └ events/ common/ missions/ decisions/ localisation/ map/ interface/ gfx/
  玩家目录  <Documents>/Paradox Interactive/Europa Universalis IV
            ├ mod/                （.mod 描述文件 + 各模组目录）
            ├ logs/error*.log     （★ 会轮转，默认扫全部）
            └ dlc_load.json       （{"enabled_mods":["mod/xx.mod", ...], "disabled_dlcs":[]}）

API
---
    find_game_dir()   -> Path | None
    find_user_dir()   -> Path | None
    mod_dir()         -> Path | None
    logs_dir()        -> Path | None
    error_logs()      -> list[Path]        按修改时间倒序，扫 error*.log
    read_version()    -> str | None        launcher-settings.json 的 rawVersion
    enabled_mods()    -> list[str]         dlc_load.json 里的 mod 相对路径
    probe()           -> dict              一次性收集环境事实（给 errorlog_triage / lint 用）
"""

from __future__ import annotations

import json
import os
from pathlib import Path

STEAM_REL = Path("steamapps") / "common" / "Europa Universalis IV"
USER_REL = Path("Paradox Interactive") / "Europa Universalis IV"
EXE_NAMES = ("eu4.exe", "eu4")
PATCH_DLL = Path("version.dll")
PATCH_PLUGIN = Path("plugins") / "eu4_unicode_patch.dll"
PATCH_LOG = Path("plugins") / "eu4_unicode_patch.log"


# --------------------------------------------------------------------------- #
# 基础探测
# --------------------------------------------------------------------------- #
def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_dir() else None


def find_game_dir() -> Path | None:
    """游戏安装目录。EU4_GAME 优先，其次常见 Steam 库位置。"""
    env = _env_path("EU4_GAME")
    if env:
        return env

    home = Path.home()
    candidates: list[Path] = []
    if os.name == "nt":
        for drive in ("C:", "D:", "E:", "F:", "G:", "H:"):
            candidates += [
                Path(f"{drive}/Program Files (x86)/Steam") / STEAM_REL,
                Path(f"{drive}/Program Files/Steam") / STEAM_REL,
                Path(f"{drive}/Steam") / STEAM_REL,
                Path(f"{drive}/SteamLibrary") / STEAM_REL,
            ]
    elif os.uname().sysname == "Darwin":  # type: ignore[attr-defined]
        candidates.append(home / "Library/Application Support/Steam" / STEAM_REL)
    else:
        candidates += [
            home / ".steam/steam" / STEAM_REL,
            home / ".local/share/Steam" / STEAM_REL,
            home / ".var/app/com.valvesoftware.Steam/data/Steam" / STEAM_REL,
        ]

    # 先找确有可执行文件的，再退化为「目录存在」
    for p in candidates:
        if p.is_dir() and any((p / n).exists() for n in EXE_NAMES):
            return p
    for p in candidates:
        if p.is_dir():
            return p
    return None


def find_user_dir() -> Path | None:
    """玩家数据目录（mod/ 与 logs/ 所在）。"""
    env = _env_path("EU4_USER_DIR")
    if env:
        return env

    home = Path.home()
    candidates: list[Path] = []
    if os.name == "nt":
        candidates += [
            home / "Documents" / USER_REL,
            home / "OneDrive" / "Documents" / USER_REL,
            home / "OneDrive - Personal" / "Documents" / USER_REL,
        ]
    elif os.uname().sysname == "Darwin":  # type: ignore[attr-defined]
        candidates.append(home / "Documents" / USER_REL)
    else:
        candidates += [
            home / ".local/share" / USER_REL,
            home / "Documents" / USER_REL,
        ]

    for p in candidates:
        if p.is_dir():
            return p

    # Linux + Proton 常见路径：compatdata/236850（EU4 的 Steam AppID）
    if os.name != "nt":
        proton = home / ".steam/steam/steamapps/compatdata/236850/pfx/drive_c/users/steamuser/Documents" / USER_REL
        if proton.is_dir():
            return proton
    return candidates[0] if candidates else None


def mod_dir() -> Path | None:
    user = find_user_dir()
    return (user / "mod") if user else None


def logs_dir() -> Path | None:
    user = find_user_dir()
    return (user / "logs") if user else None


def root_mod_dir() -> Path | None:
    """游戏目录内的 mod/（本地模组也可放这里，但默认不建议）。"""
    game = find_game_dir()
    return (game / "mod") if game else None


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #
def error_logs(user_dir: Path | None = None) -> list[Path]:
    """全部 error*.log（EU4 会轮转出 error_old.log / error_old_old.log）。

    按修改时间倒序：最新的排第一。调用方应默认扫全部，
    否则会漏掉历史语料（本机实测轮转后旧文件里仍有独有的报错类型）。
    """
    d = (user_dir / "logs") if user_dir else logs_dir()
    if not d or not d.is_dir():
        return []
    files = [p for p in d.glob("error*.log") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


# --------------------------------------------------------------------------- #
# 版本与启用清单
# --------------------------------------------------------------------------- #
def read_version(game_dir: Path | None = None) -> str | None:
    """launcher-settings.json 的 rawVersion，例如 'v1.37.5.0'。"""
    g = game_dir or find_game_dir()
    if not g:
        return None
    f = g / "launcher-settings.json"
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, ValueError):
        return None
    ver = data.get("rawVersion") or data.get("version")
    return str(ver) if ver else None


def dlc_load_path(user_dir: Path | None = None) -> Path | None:
    u = user_dir or find_user_dir()
    if not u:
        return None
    p = u / "dlc_load.json"
    return p if p.is_file() else None


def read_dlc_load(user_dir: Path | None = None) -> dict:
    p = dlc_load_path(user_dir)
    if not p:
        return {"enabled_mods": [], "disabled_dlcs": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, ValueError):
        return {"enabled_mods": [], "disabled_dlcs": []}
    if not isinstance(data, dict):
        return {"enabled_mods": [], "disabled_dlcs": []}
    data.setdefault("enabled_mods", [])
    data.setdefault("disabled_dlcs", [])
    return data


def enabled_mods(user_dir: Path | None = None) -> list[str]:
    """启用中的 mod 相对路径，如 'mod/posthumous_names.mod'。"""
    return list(read_dlc_load(user_dir).get("enabled_mods") or [])


def enabled_mod_names(user_dir: Path | None = None) -> list[str]:
    """从 'mod/xxx.mod' 提取 stem：'xxx'。便于与日志里的文件名比对。"""
    out = []
    for m in enabled_mods(user_dir):
        name = Path(m).name
        out.append(name[:-4] if name.lower().endswith(".mod") else name)
    return out


# --------------------------------------------------------------------------- #
# 环境事实
# --------------------------------------------------------------------------- #
def _size(path: Path | None) -> int | None:
    try:
        return path.stat().st_size if path and path.is_file() else None
    except OSError:
        return None


def probe(user_dir: Path | None = None, game_dir: Path | None = None) -> dict:
    """一次性收集环境事实。只读；任何一项失败都只是 None/空列表。

    白名单的可靠度取决于这里的 game_dir / 数据文件完整性 / exe 可信度，
    所以调用方（lint / env_check）应把本结果原样打印给用户看。
    """
    g = game_dir or find_game_dir()
    u = user_dir or find_user_dir()

    data_counts: dict[str, int] = {}
    if g:
        for sub in ("events", "common", "missions", "decisions", "localisation", "map"):
            d = g / sub
            if d.is_dir():
                data_counts[sub] = sum(1 for _ in d.rglob("*") if _.is_file())

    logs = error_logs(u)
    total_bytes = 0
    total_lines = 0
    for p in logs:
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        total_bytes += len(raw)
        total_lines += raw.count(b"\n")

    return {
        "game_dir": str(g) if g else None,
        "user_dir": str(u) if u else None,
        "mod_dir": str(u / "mod") if u else None,
        "logs_dir": str(u / "logs") if u else None,
        "version": read_version(g),
        "data_counts": data_counts,
        "error_logs": [
            {"path": str(p), "bytes": _size(p), "mtime": p.stat().st_mtime}
            for p in logs
        ],
        "error_logs_total_bytes": total_bytes,
        "error_logs_total_lines": total_lines,
        "exe": (str(g / "eu4.exe") if g and (g / "eu4.exe").is_file() else None),
        "exe_size": _size(g / "eu4.exe") if g else None,
        "unicode_patch": {
            "version_dll": str(g / PATCH_DLL) if g and (g / PATCH_DLL).is_file() else None,
            "version_dll_size": _size(g / PATCH_DLL) if g else None,
            "plugin_dll": str(g / PATCH_PLUGIN) if g and (g / PATCH_PLUGIN).is_file() else None,
            "plugin_dll_size": _size(g / PATCH_PLUGIN) if g else None,
            "patch_log_mtime": (
                (g / PATCH_LOG).stat().st_mtime
                if g and (g / PATCH_LOG).is_file()
                else None
            ),
        },
        "enabled_mods": enabled_mods(u),
        "enabled_mod_names": enabled_mod_names(u),
    }


def force_utf8_stdout() -> None:
    """Windows 控制台默认 cp936，中文会变乱码；所有工具的入口都先调这个。"""
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    import sys

    force_utf8_stdout()
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--json" in argv:
        print(json.dumps(probe(), ensure_ascii=False, indent=2))
        return 0
    p = probe()
    print(f"game : {p['game_dir']}")
    print(f"user : {p['user_dir']}")
    print(f"ver  : {p['version']}")
    print(f"data : {p['data_counts']}")
    print(f"log  : {len(p['error_logs'])} 份 / {p['error_logs_total_bytes']:,} B / {p['error_logs_total_lines']:,} 行")
    for item in p["error_logs"]:
        print(f"       {item['path']}  {item['bytes']:,} B")
    print(f"mods : {len(p['enabled_mods'])} 启用")
    up = p["unicode_patch"]
    print(f"patch: version.dll={'有' if up['version_dll'] else '无'} plugin={'有' if up['plugin_dll'] else '无'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
