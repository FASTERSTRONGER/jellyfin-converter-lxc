import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from http.cookies import SimpleCookie
from database import get_setting, set_setting

SESSION_COOKIE_NAME = "jfc_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600
PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    """Hashes a plaintext password with a random 16-byte salt using PBKDF2-SHA256."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verifies a plaintext password against a stored salt$hash string."""
    if not stored_hash or "$" not in stored_hash:
        return False
    try:
        salt_hex, dk_hex = stored_hash.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected_dk = bytes.fromhex(dk_hex)
        actual_dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
        return hmac.compare_digest(actual_dk, expected_dk)
    except Exception:
        return False


def _get_session_secret() -> bytes:
    secret = get_setting("session_secret", "")
    if not secret:
        secret = secrets.token_hex(32)
        set_setting("session_secret", secret)
    return secret.encode("utf-8")


def ensure_admin_initialized():
    """Ensures an admin account and session secret exist on first startup."""
    _get_session_secret()
    existing_hash = get_setting("admin_password_hash", "")
    if not existing_hash:
        username = os.environ.get("JFC_ADMIN_USER", "admin").strip() or "admin"
        password = os.environ.get("JFC_ADMIN_PASS", "").strip()
        generated = False
        if not password:
            password = secrets.token_urlsafe(9)
            generated = True
        set_setting("admin_username", username)
        set_setting("admin_password_hash", hash_password(password))
        if generated:
            print(f"[AUTH] Akun admin awal dibuat -> username: {username} | password: {password}")
        else:
            print(f"[AUTH] Akun admin awal dibuat dari environment -> username: {username}")


def set_admin_credentials(username: str, password: str):
    """Updates the stored admin username and password hash."""
    username = username.strip() or "admin"
    set_setting("admin_username", username)
    set_setting("admin_password_hash", hash_password(password))


def authenticate_user(username: str, password: str) -> bool:
    """Validates login credentials against stored admin settings."""
    stored_user = get_setting("admin_username", "admin")
    stored_hash = get_setting("admin_password_hash", "")
    if not hmac.compare_digest(username.strip(), stored_user):
        return False
    return verify_password(password, stored_hash)


def create_session_token(username: str, ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
    """Creates an HMAC-SHA256 signed session token."""
    payload = json.dumps({"u": username, "exp": int(time.time()) + ttl_seconds}, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    sig = hmac.new(_get_session_secret(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_session_token(token: str):
    """Verifies a signed session token and returns the username if valid and unexpired."""
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig = token.split(".", 1)
        expected_sig = hmac.new(_get_session_secret(), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload.get("u")
    except Exception:
        return None


def get_authenticated_user(headers):
    """Extracts and validates the session cookie from HTTP request headers."""
    cookie_header = headers.get("Cookie", "")
    if not cookie_header:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception:
        return None
    morsel = cookie.get(SESSION_COOKIE_NAME)
    if not morsel:
        return None
    return verify_session_token(morsel.value)
