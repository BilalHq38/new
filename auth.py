import os
from dotenv import load_dotenv
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

load_dotenv()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "pigeon123")
SECRET_KEY = os.getenv("SECRET_KEY", "fallback-secret-key")

serializer = URLSafeTimedSerializer(SECRET_KEY)
SESSION_COOKIE = "admin_session"
SESSION_MAX_AGE = 60 * 60 * 8  # 8 hours


def create_session_token(username: str) -> str:
    return serializer.dumps(username, salt="admin-login")


def verify_session_token(token: str) -> str | None:
    try:
        username = serializer.loads(token, salt="admin-login", max_age=SESSION_MAX_AGE)
        return username
    except (BadSignature, SignatureExpired):
        return None


def get_admin_user(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return verify_session_token(token)


def require_admin(request: Request) -> str:
    user = get_admin_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/admin/login"})
    return user


def check_credentials(username: str, password: str) -> bool:
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD


def login_response(username: str, redirect_to: str = "/admin") -> RedirectResponse:
    token = create_session_token(username)
    response = RedirectResponse(url=redirect_to, status_code=302)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        max_age=SESSION_MAX_AGE,
        samesite="lax"
    )
    return response


def logout_response() -> RedirectResponse:
    response = RedirectResponse(url="/admin/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response
