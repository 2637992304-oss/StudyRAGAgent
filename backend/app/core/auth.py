import hashlib
import hmac
import os

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import User
from app.db.session import get_db

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split(":", 1)
        candidate = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=16384, r=8, p=1
        )
        return hmac.compare_digest(candidate, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


def token_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().auth_secret, salt="studyrag-auth-v1")


def create_token(user_id: str) -> str:
    return token_serializer().dumps({"uid": user_id})


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(401, "请先登录")
    try:
        payload = token_serializer().loads(credentials.credentials, max_age=86400)
        user_id = payload["uid"]
    except (BadSignature, SignatureExpired, KeyError, TypeError) as exc:
        raise HTTPException(401, "登录已失效") from exc
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(401, "账号不存在")
    return user
