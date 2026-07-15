from __future__ import annotations

import sqlite3


MIN_PASSWORD_LENGTH = 8


def clean_username(username: str) -> str:
    return username.strip()


def validate_username(username: str) -> str:
    cleaned_username = clean_username(username)
    if not cleaned_username:
        raise ValueError("用户名不能为空")
    if len(cleaned_username) > 50:
        raise ValueError("用户名不能超过 50 个字符")
    return cleaned_username


def validate_password_pair(password: str, confirm_password: str) -> str:
    if password != confirm_password:
        raise ValueError("两次输入的新密码不一致")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"密码长度不能少于 {MIN_PASSWORD_LENGTH} 位")
    return password


def validate_admin_status_change(
    target_user: sqlite3.Row,
    new_is_admin: bool,
    active_admin_count: int,
) -> None:
    if (
        bool(target_user["is_admin"])
        and bool(target_user["is_active"])
        and not new_is_admin
        and active_admin_count <= 1
    ):
        raise ValueError("至少需要保留一个管理员")


def validate_user_active_status_change(
    target_user: sqlite3.Row,
    current_user_id: int,
    new_is_active: bool,
    active_admin_count: int,
) -> None:
    if not new_is_active and int(target_user["id"]) == current_user_id:
        raise ValueError("不能停用当前登录账号")
    if (
        bool(target_user["is_admin"])
        and bool(target_user["is_active"])
        and not new_is_active
        and active_admin_count <= 1
    ):
        raise ValueError("至少需要保留一个已启用的管理员")
