"""登录鉴权：PBKDF2 密码哈希（stdlib，零 C 扩展依赖，LoongArch 友好）
+ 内存会话 token。

单角色管理员（设计文档 M2 范围）；token 不持久化，服务重启需重新登录。
"""
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time

_ITERATIONS = 200_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             salt.encode("ascii"), _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, expected = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt.encode("ascii"), int(iterations))
        return hmac.compare_digest(dk.hex(), expected)
    except (ValueError, AttributeError):
        return False


class AuthStore:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def ensure_admin(self, username: str, password: str) -> None:
        """无此用户时创建；已存在不覆盖（避免重启重置密码）。空密码跳过。"""
        if not username or not password:
            return
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
            if row:
                return
            self._conn.execute(
                "INSERT INTO users(username, password_hash, created_at) "
                "VALUES (?,?,?)",
                (username, hash_password(password), time.time()))
            self._conn.commit()

    def verify(self, username: str, password: str) -> bool:
        row = self._conn.execute(
            "SELECT password_hash FROM users WHERE username=?",
            (username,)).fetchone()
        if not row:
            return False
        return verify_password(password, row[0])

    def close(self) -> None:
        self._conn.close()


class TokenManager:
    def __init__(self, ttl_seconds: int = 12 * 3600):
        self._ttl = ttl_seconds
        self._tokens: dict[str, tuple[str, float]] = {}  # token → (user, 到期)

    def issue(self, username: str) -> str:
        token = secrets.token_hex(32)
        self._tokens[token] = (username, time.time() + self._ttl)
        return token

    def validate(self, token: str) -> str | None:
        entry = self._tokens.get(token)
        if entry is None:
            return None
        username, expires = entry
        if time.time() >= expires:
            self._tokens.pop(token, None)
            return None
        return username

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)
