"""认证与密码：JWT(HS256) 与 scrypt 口令散列，全部用标准库实现。

为什么不用 python-jose / passlib：这两个包会把依赖面扩大一圈，
而本项目的安全边界恰恰是“少依赖、可审计”。HS256 的签名就是
HMAC-SHA256，标准库足够；scrypt 也在 hashlib 里。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from ..utils.errors import TraceError


def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def sign(payload: dict[str, Any], secret: str, ttl_seconds: int = 720 * 3600) -> str:
    """签发 HS256 令牌；``exp`` 必须写入，否则校验端会立刻判过期。"""
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    body = {**payload, "iat": now, "exp": now + ttl_seconds}
    head = b64e(json.dumps(header, separators=(",", ":")).encode())
    data = b64e(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode())
    mac = hmac.new(secret.encode(), f"{head}.{data}".encode(), hashlib.sha256).digest()
    return f"{head}.{data}.{b64e(mac)}"


def verify(token: str, secret: str) -> dict[str, Any]:
    try:
        head, data, mac = token.split(".")
    except ValueError as exc:
        raise TraceError("令牌格式不正确") from exc
    expected = hmac.new(secret.encode(), f"{head}.{data}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, b64d(mac)):
        raise TraceError("令牌签名无效")
    payload = json.loads(b64d(data))
    if payload.get("exp", 0) < time.time():
        raise TraceError("登录已过期，请重新登录")
    return payload


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${b64e(salt)}${b64e(digest)}"


def check_password(password: str, stored: str) -> bool:
    try:
        algo, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=b64d(salt_b64), n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(digest, b64d(digest_b64))


def csrf_token(secret: str, session_id: str) -> str:
    """双提交 CSRF：值由服务端密钥派生，页面与 Cookie 各持一份。"""
    return hmac.new(secret.encode(), f"csrf:{session_id}".encode(), hashlib.sha256).hexdigest()[:32]
