"""创建带 bypass 2FA 的 granular token（一次性传完 password + OTP，避免交互超时）。

为什么需要这个脚本
----------------
`npm token create` 是交互式的：先问 password，再问 OTP。
实测连续两次 EOTP（This operation requires a one-time password / timed out）——
原因是「看邮件 → 抄数字 → 打字 → 回车」这个过程本身就花了太久，
OTP 的有效窗口被消耗掉了。

本脚本用 registry 的 REST API 一次请求传完 password + otp + 授权范围，
把「人工等待」压到最短。

关键约束（实测）
--------------
· 必须指定具体包名：`--packages-all` 被服务器拒
    「You must have at least one package / scope or organization added to this token」
· 必须带 bypass_2fa，否则 publish 仍是 403（账号模式 auth-and-writes）
· 邮件 OTP 只能满足「创建 token」，不能满足 publish —— 所以必须先把 token 建出来

用法
----
    $env:NPM_PASS='密码'; python tools/mk_token.py --otp 12345678
    python tools/mk_token.py --otp 12345678 --packages dsh-eu4-modding
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

REG = "https://registry.npmjs.org/-/npm/v1/tokens"


def read_auth() -> str | None:
    p = os.path.expanduser("~/.npmrc")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            ln = ln.strip()
            if ":_authToken=" in ln or ln.startswith("_authToken="):
                return ln.split("=", 1)[1].strip()
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="一次性创建 bypass-2FA granular token")
    ap.add_argument("--name", default="publish-eu4")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--packages", nargs="+", default=None,
                    help="授权哪些包名（与 --scopes 至少给一个）")
    ap.add_argument("--scopes", nargs="+", default=None,
                    help="授权哪些 scope，如 @alice214（scoped 包必须走这个）")
    ap.add_argument("--permission", default="read-write",
                    choices=["read-only", "read-write", "no-access"])
    ap.add_argument("--otp", required=True, help="邮箱收到的 OTP")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    password = os.environ.get("NPM_PASS")
    if not password:
        print("★ 缺 NPM_PASS 环境变量")
        print("   PowerShell: $env:NPM_PASS='你的密码'")
        return 2

    token = read_auth()
    if not token:
        print("★ ~/.npmrc 缺 _authToken；先 npm.cmd login --auth-type=web")
        return 2

    body = {
        "name": args.name,
        "password": password,
        "expiration_days": args.days,
        "packages": args.packages or [],
        "scopes": args.scopes or [],
        "packages_and_scopes_permission": args.permission,
        "bypass_2fa": True,
    }

    if args.dry:
        safe = {k: ("<hidden>" if k == "password" else v) for k, v in body.items()}
        print("POST", REG)
        print("headers: Authorization=Bearer <token>, npm-otp=<otp>")
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return 0

    req = urllib.request.Request(
        REG,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "npm-otp": str(args.otp),
            "npm-command": "token",
            "User-Agent": "dsh-eu4-modding-token-helper",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")
        print(f"★ HTTP {err.code} {err.reason}")
        print(f"   {detail[:400]}")
        low = detail.lower()
        if "otp" in low or err.code == 401 and "one-time" in low:
            print("\n   → OTP 无效或已超时；去邮箱取新的一封，立刻重跑本命令")
        elif "at least one package" in detail:
            print("\n   → 包名未被接受；改用 --packages 指定其它形式")
        return 1
    except (urllib.error.URLError, OSError, ValueError) as err:
        print(f"★ 请求失败：{err}")
        return 1

    new = payload.get("token")
    if not new and isinstance(payload.get("tokens"), list) and payload["tokens"]:
        new = payload["tokens"][0].get("token")
    if not new:
        print("★ 响应无 token 字段：")
        print(json.dumps(payload, ensure_ascii=False, indent=1)[:500])
        return 1

    # 直接写进 ~/.npmrc，省掉一步手工操作
    npmrc = os.path.expanduser("~/.npmrc")
    lines = []
    if os.path.isfile(npmrc):
        with open(npmrc, encoding="utf-8", errors="replace") as fh:
            lines = [ln.rstrip("\n") for ln in fh]
    lines = [ln for ln in lines if ":_authToken=" not in ln and not ln.startswith("_authToken=")]
    lines.append(f"//registry.npmjs.org/:_authToken={new}")
    with open(npmrc, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(ln for ln in lines if ln) + "\n")

    print("创建成功并已写入 ~/.npmrc")
    print(f"  name      : {args.name}")
    print(f"  packages  : {args.packages}")
    print(f"  bypass 2FA: 已启用")
    print(f"  有效期    : {args.days} 天")
    print(f"  token 前缀: {new[:8]}...（长度 {len(new)}）")
    print()
    print("现在可以直接发布：")
    print("  npm.cmd publish --access public")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
