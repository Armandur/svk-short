from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from app.auth import SECRET_KEY

_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="csrf")
_MAX_AGE = 60 * 60 * 24  # 24 timmar


def generate_csrf_token() -> str:
    return _serializer.dumps("csrf")


def validate_csrf_token(token: str) -> bool:
    try:
        _serializer.loads(token, max_age=_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False
