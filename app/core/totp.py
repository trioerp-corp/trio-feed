"""RFC 6238 time-based one-time codes (the same codes Google/Microsoft Authenticator show)."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP, DIGITS = 30, 6


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret, counter):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 15
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** DIGITS).zfill(DIGITS)


def now_code(secret, at=None):
    return _code(secret, int((at or time.time()) // STEP))


def verify(secret, code, last_counter=0, at=None):
    """Return the matched counter (allowing ±1 step of clock drift), or None. Codes at/below last_counter are refused."""
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != DIGITS:
        return None
    c = int((at or time.time()) // STEP)
    for k in (c - 1, c, c + 1):
        if k > last_counter and hmac.compare_digest(_code(secret, k), code):
            return k
    return None


def uri(secret, account, issuer):
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP}"


def new_backup_codes(n=8):
    plain = [f"{secrets.randbelow(10**8):08d}" for _ in range(n)]
    return plain, [hash_code(p) for p in plain]


def hash_code(code):
    return hashlib.sha256(("gg-2fa:" + "".join(ch for ch in str(code) if ch.isdigit())).encode()).hexdigest()
