# -*- coding: utf-8 -*-
"""Panel giris ve ilk kurulum."""

from functools import wraps

from flask import session, redirect, url_for, request
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_setting, set_setting

MIN_PASSWORD_LENGTH = 8

PUBLIC_ENDPOINTS = frozenset(
    {
        "login",
        "setup",
        "static",
    }
)


def is_setup_complete() -> bool:
    return get_setting("setup_complete") == "1"


def create_admin(username: str, password: str) -> None:
    set_setting("admin_username", username.strip())
    set_setting(
        "admin_password_hash",
        generate_password_hash(password),
    )
    set_setting("setup_complete", "1")


def verify_login(username: str, password: str) -> bool:
    if not is_setup_complete():
        return False
    stored_user = get_setting("admin_username")
    stored_hash = get_setting("admin_password_hash")
    if not stored_user or not stored_hash:
        return False
    return (
        username.strip() == stored_user
        and check_password_hash(stored_hash, password)
    )


def login_user() -> None:
    session["logged_in"] = True
    session.permanent = True


def logout_user() -> None:
    session.pop("logged_in", None)


def is_logged_in() -> bool:
    return session.get("logged_in") is True


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_setup_complete():
            return redirect(url_for("setup"))
        if not is_logged_in():
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def register_auth_hooks(app):
    @app.before_request
    def require_auth():
        if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
            return None
        if not is_setup_complete():
            if request.endpoint != "setup":
                return redirect(url_for("setup"))
            return None
        if not is_logged_in():
            if request.endpoint != "login":
                return redirect(url_for("login", next=request.path))
            return None
        return None
