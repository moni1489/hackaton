"""Аутентификация, RBAC и неизменяемый аудит.

Модель доверия:
  • Веб-пользователь  → JWT (sub=user:<id>) + роль в payload.
  • Устройство-агент  → JWT (sub=device:<device_id>) + hash железа (hwfp).
                        School ID и Device ID берутся из БД/токена, а не из тела запроса,
                        поэтому подделать их в запросе нельзя. Отпечаток в токене сверяется
                        с записью устройства: это отсекает токены, выданные до перерегистрации
                        на другом оборудовании. Это НЕ доказательство, что запрос пришёл с
                        исходного ПК: hwfp лежит в токене открытым текстом, а токен вместе с
                        файлом состояния агента можно скопировать. Привязку к железу даёт
                        только mTLS с неэкспортируемым ключом (REQUIRE_MTLS); без него
                        компенсирующие меры — отзыв устройства и журнал аудита.
  • mTLS              → отпечаток клиентского сертификата приходит от TLS-терминатора
                        в заголовке X-Client-Cert-Fingerprint и сверяется с БД. Терминатор
                        обязан вычищать этот заголовок из входящих запросов.
"""
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import false
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import AuditLog, Device, School, User

bearer = HTTPBearer(auto_error=False)

# --- Пароли (PBKDF2-HMAC-SHA256, 240k итераций) ---------------------------

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
    return f"pbkdf2_sha256$240000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                     bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:  # noqa: BLE001
        return False


# --- Токены ----------------------------------------------------------------

def create_access_token(subject: str, claims: dict, ttl_minutes: int | None = None) -> str:
    ttl = ttl_minutes or settings.ACCESS_TOKEN_TTL_MIN
    payload = {
        **claims,
        "sub": subject,
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=ttl),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_device_token(device_id: str, school_id: int, hardware_fingerprint: str) -> str:
    payload = {
        "sub": f"device:{device_id}",
        "typ": "device",
        "school_id": school_id,
        "hwfp": hardware_fingerprint,
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(days=settings.DEVICE_TOKEN_TTL_DAYS),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Срок действия токена истёк") from None
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Некорректный токен") from None


def fingerprint(*parts: str) -> str:
    """Стабильный отпечаток железа агента (MAC + серийник + CPU + ОС)."""
    return hashlib.sha256("|".join(p or "" for p in parts).encode()).hexdigest()


# --- Зависимости: пользователь --------------------------------------------

def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется авторизация")
    payload = decode_token(creds.credentials)
    if payload.get("typ") == "device":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Токен устройства не даёт доступ к веб-API")
    user = db.get(User, int(payload["sub"].split(":")[1]))
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пользователь неактивен")
    return user


def require_roles(*roles: str):
    """RBAC: допускает только перечисленные роли."""
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Роль «{user.role}» не имеет доступа к этому ресурсу",
            )
        return user
    return dependency


FULL_SCOPE_ROLES = ("admin", "operator")   # вся область


def scope_schools(query, user: User):
    """Изоляция выборки школ (запрос по School). Запрет по умолчанию: роль без своей
    области (школа без school_id, провайдер без имени, неизвестная роль) не видит ничего."""
    if user.role in FULL_SCOPE_ROLES:
        return query
    if user.role == "school" and user.school_id:
        return query.filter(School.id == user.school_id)
    if user.role == "provider" and user.provider_name:
        return query.filter(School.provider == user.provider_name)
    if user.role == "district" and user.district:
        return query.filter(School.region == user.district)
    return query.filter(false())


def scope_key(user: User) -> str:
    """Ключ кэша: одинаков только у пользователей с одинаковой областью видимости."""
    if user.role in FULL_SCOPE_ROLES:
        return "all"
    return f"{user.role}:{user.school_id or user.provider_name or user.district or '-'}"


def can_access_school(user: User, school) -> bool:
    if user.role in FULL_SCOPE_ROLES:
        return True
    if user.role == "school":
        return bool(user.school_id) and user.school_id == school.id
    if user.role == "provider":
        return bool(user.provider_name) and user.provider_name == school.provider
    if user.role == "district":
        return bool(user.district) and user.district == school.region
    return False


# --- Зависимость: устройство-агент ----------------------------------------

def current_device(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Device:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется токен агента")
    payload = decode_token(creds.credentials)
    if payload.get("typ") != "device":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Ожидается токен устройства")

    device_id = payload["sub"].split(":", 1)[1]
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device or device.revoked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Устройство отозвано или не зарегистрировано")

    # Токен выдан для другого отпечатка, чем записан у устройства (перерегистрация).
    # Не защищает от копирования токена вместе с состоянием агента — см. docstring модуля.
    if device.hardware_fingerprint and payload.get("hwfp") != device.hardware_fingerprint:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Токен выдан для другого оборудования — требуется перерегистрация")

    # School ID берём из БД, а не из тела запроса.
    if payload.get("school_id") != device.school_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Привязка School ID нарушена")

    if settings.REQUIRE_MTLS:
        presented = request.headers.get(settings.MTLS_HEADER)
        if not presented or presented != device.cert_fingerprint:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Клиентский сертификат mTLS не предъявлен или не совпадает")
    return device


# --- Неизменяемый аудит ----------------------------------------------------

def write_audit(db: Session, actor: str, role: str, action: str,
                target: str = "", ip: str = "", details: dict | None = None) -> AuditLog:
    """Append-only запись, сцепленная хэшем с предыдущей (tamper-evident)."""
    prev = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    prev_hash = prev.entry_hash if prev else "GENESIS"
    ts = datetime.now(UTC)
    body = json.dumps(
        {"ts": ts.isoformat(), "actor": actor, "role": role, "action": action,
         "target": target, "ip": ip, "details": details or {}},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    entry = AuditLog(
        timestamp=ts.replace(tzinfo=None), actor=actor, actor_role=role, action=action,
        target=target, ip_address=ip, details=body, prev_hash=prev_hash,
        entry_hash=hashlib.sha256((prev_hash + body).encode()).hexdigest(),
    )
    db.add(entry)
    db.commit()
    return entry


def verify_audit_chain(db: Session) -> dict:
    """Проверка целостности журнала: пересчитываем цепочку хэшей."""
    prev_hash = "GENESIS"
    checked = 0
    for entry in db.query(AuditLog).order_by(AuditLog.id.asc()).all():
        expected = hashlib.sha256((prev_hash + (entry.details or "")).encode()).hexdigest()
        if entry.prev_hash != prev_hash or entry.entry_hash != expected:
            return {"valid": False, "broken_at": entry.id, "checked": checked}
        prev_hash = entry.entry_hash
        checked += 1
    return {"valid": True, "broken_at": None, "checked": checked}
