"""只存带独立盐的慢速密码哈希；会话令牌使用独立的随机值。"""

import hashlib
import hmac
import json
import secrets


ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, expected_hex = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(iterations) != ITERATIONS:
            return False
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(expected_hex)
        if len(salt) != 16 or len(expected) != 32:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_email_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def email_code_digest(secret: str, record_id: str, email: str, purpose: str, code: str) -> str:
    payload = json.dumps([record_id, email, purpose, code], separators=(",", ":"), ensure_ascii=True)
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


# 未找到账号也执行相同的密码验证流程。
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))
