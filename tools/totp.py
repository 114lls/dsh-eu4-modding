"""TOTP 生成器 —— 不依赖手机 App，本地直接算出 6 位码。

为什么需要它
-----------
用户在 npm 绑定 2FA 时，扫码一直卡在「正在连接设备」——那是 **passkey 跨设备
认证**（需要手机与 PC 同网络、同微软账号、蓝牙近场），不是 TOTP。

但 TOTP 的本质只是「密钥 + 时间」的 HMAC 运算，**不需要任何 App、任何网络**。
把 npm 页面上那串 Setup key（base32 文本，通常写作 otpauth://totp/... 或
单独的密钥串）拿过来，本地就能算出与服务器一致的 6 位码。

用法
----
    python tools/totp.py <base32密钥>
    python tools/totp.py <base32密钥> --digits 8      # 8 位（RFC 测试向量用）
    python tools/totp.py <base32密钥> --watch         # 每秒刷新，盯着看
    python tools/totp.py --self-test                  # 用 RFC 6238 向量自检

安全说明
--------
密钥等价于账号凭据。本脚本只做本地计算，不联网、不写磁盘、不记录参数。
别把密钥贴进聊天窗口或提交进仓库。

实现依据 RFC 6238（TOTP）+ RFC 4226（HOTP），仅用标准库。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import re
import struct
import sys
import time


def parse_secret(raw: str) -> bytes:
    """接受多种写法：纯 base32 / otpauth:// URL / 带空格横线的 base32。"""
    s = raw.strip()

    # otpauth://totp/npm:users%40example.com?secret=BASE32&issuer=npm
    if s.lower().startswith("otpauth://"):
        m = re.search(r"secret=([A-Za-z0-9+/=_\-]+)", s, re.I)
        if not m:
            raise ValueError("otpauth URL 里找不到 secret 参数")
        s = m.group(1)

    # 去掉常见分隔符（App 里显示的密钥常写成 XXXX-XXXX-XXXX）
    s = re.sub(r"[\s\-_]", "", s).rstrip("=")

    # base32 标准字母表 A-Z 2-7；容忍小写
    s = s.upper()
    if not re.fullmatch(r"[A-Z2-7]+", s):
        raise ValueError(
            f"密钥含非法字符（base32 只允许 A-Z 与 2-7）：{s[:8]}…"
        )
    # 补齐 padding
    pad = (-len(s)) % 8
    return base64.b32decode(s + "=" * pad)


def hotp(secret: bytes, counter: int, digits: int = 6) -> str:
    mac = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def totp(secret: bytes, digits: int = 6, period: int = 30, at: float | None = None) -> tuple[str, float]:
    """返回 (6 位码, 距离过期的秒数)。"""
    now = time.time() if at is None else at
    counter = int(now // period)
    code = hotp(secret, counter, digits)
    remain = period - (now % period)
    return code, remain


def self_test() -> int:
    """RFC 6238 附录 B 的测试向量（SHA1 / 8 位 / 时间 59）。"""
    secret = base64.b32encode(b"12345678901234567890")
    cases = [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
    ]
    ok = True
    raw = parse_secret(secret.decode())
    for t, expect in cases:
        got, _ = totp(raw, digits=8, at=t)
        flag = "PASS" if got == expect else "★FAIL"
        if got != expect:
            ok = False
        print(f"  {flag}  t={t:>10}  期望 {expect}  得到 {got}")
    print("\nRFC 6238 自检：", "全部通过" if ok else "★ 有不符")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="本地生成 TOTP 6 位码（不需要手机 App）")
    ap.add_argument("secret", nargs="?", help="base32 密钥，或 otpauth:// URL")
    ap.add_argument("--digits", type=int, default=6)
    ap.add_argument("--period", type=int, default=30)
    ap.add_argument("--watch", action="store_true", help="每秒刷新")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if args.self_test:
        return self_test()

    if not args.secret:
        ap.print_help()
        return 2

    try:
        key = parse_secret(args.secret)
    except ValueError as exc:
        print(f"★ {exc}")
        return 2

    if not args.watch:
        code, remain = totp(key, args.digits, args.period)
        print(f"当前验证码：{code}")
        print(f"剩余有效：{remain:.0f} 秒")
        print("\n发布：npm.cmd publish --access public --otp=" + code)
        return 0

    print("每秒刷新（Ctrl+C 退出）。剩 5 秒内会自动换下一个。")
    try:
        while True:
            code, remain = totp(key, args.digits, args.period)
            bar = "#" * int(remain / args.period * 20)
            sys.stdout.write(f"\r  {code}   [{bar:<20}] {remain:2.0f}s  ")
            sys.stdout.flush()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
