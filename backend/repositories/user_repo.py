from sqlalchemy.orm import Session
from typing import Optional
from backend.database.models import User, UserAuthSession
import bcrypt
import secrets
import datetime


# 密码哈希:直接使用 bcrypt 包(而非 passlib 的 CryptContext)。
# 原因:passlib 1.7.x 的后端探测依赖 ``bcrypt.__about__.__version__``,而 bcrypt
# >=4.1 移除了 ``__about__`` 并改动行为,导致 passlib 在 hash/verify 时抛
# AttributeError + "password cannot be longer than 72 bytes" 的 detect_wrap_bug
# 误报(即使密码只有几字节)。直接用 bcrypt 包的 hashpw/checkpw 可绕开这套探测,
# 对 bcrypt 4.x / 5.x 都稳定。哈希格式仍是标准 $2b$,与历史 passlib 产物互相兼容。
#
# 注意:存储字段是 user.password_hash(非 user.password,后者是已不存在的死代码)。


def create_user(
    db: Session,
    username: str,
    email: str = None,
    password: str = None
) -> User:
    """Create a new user"""
    user = User(
        username=username,
        email=email,
        password_hash=_hash_password(password) if password else None,
        is_active="true"
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_or_create_user(
    db: Session, 
    username: str = "default",
    email: str = None,
    password: str = None
) -> User:
    """Get or create user for default mode
    
    Note: For default/simulation mode, creates user without authentication.
    """
    user = db.query(User).filter(User.username == username).first()
    if user:
        return user
    
    # Create default user without password requirement
    return create_user(db, username, email, password)


def get_user(db: Session, user_id: int) -> Optional[User]:
    """Get user by ID"""
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    """Get user by username"""
    return db.query(User).filter(User.username == username).first()


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Get user by email"""
    return db.query(User).filter(User.email == email).first()


def update_user(
    db: Session,
    user_id: int,
    username: str = None,
    email: str = None
) -> Optional[User]:
    """Update user information"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return None
    
    if username is not None:
        user.username = username
    if email is not None:
        user.email = email
    
    db.commit()
    db.refresh(user)
    return user


def _hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt (returns standard $2b$ hash)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash.

    Returns False when no hash is stored (empty/None) so callers can treat
    "password not set" uniformly as a failed verification.
    """
    if not password_hash:
        return False
    try:
        # bcrypt.checkpw 要求 bytes;hash 通常是 str(从 DB 读出)。
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # hash 格式不对 / 不是合法 bcrypt 串 —— 视为校验失败而非抛错。
        return False


def set_user_password(db: Session, user_id: int, password: str) -> Optional[User]:
    """Set or update user login password (bcrypt-hashed into password_hash)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return None

    user.password_hash = _hash_password(password)
    db.commit()
    db.refresh(user)
    return user


def verify_user_password(db: Session, user_id: int, password: str) -> bool:
    """Verify user login password against the stored bcrypt hash."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False

    return _verify_password(password, user.password_hash)


def user_has_password(db: Session, user_id: int) -> bool:
    """Check if user has set a login password (password_hash present)."""
    user = db.query(User).filter(User.id == user_id).first()
    return user is not None and bool(user.password_hash) and user.password_hash.strip() != ""


def create_auth_session(db: Session, user_id: int) -> Optional[UserAuthSession]:
    """Create a new authentication session for user (180 days expiry)"""
    # Clean up expired sessions for this user
    cleanup_expired_sessions(db, user_id)
    
    # Generate session token
    session_token = secrets.token_urlsafe(32)
    
    # Set expiry to 180 days from now
    expires_at = datetime.datetime.now(timezone.utc) + datetime.timedelta(days=180)
    
    # Create session
    session = UserAuthSession(
        user_id=user_id,
        session_token=session_token,
        expires_at=expires_at
    )
    
    db.add(session)
    db.commit()
    db.refresh(session)
    
    return session


def verify_auth_session(db: Session, session_token: str) -> Optional[int]:
    """Verify session token and return user_id if valid"""
    session = db.query(UserAuthSession).filter(
        UserAuthSession.session_token == session_token,
        UserAuthSession.expires_at > datetime.datetime.now(timezone.utc)
    ).first()
    
    return session.user_id if session else None


def cleanup_expired_sessions(db: Session, user_id: int = None) -> int:
    """Clean up expired sessions. If user_id provided, clean only for that user"""
    query = db.query(UserAuthSession).filter(
        UserAuthSession.expires_at <= datetime.datetime.now(timezone.utc)
    )
    
    if user_id:
        query = query.filter(UserAuthSession.user_id == user_id)
    
    deleted_count = query.count()
    query.delete()
    db.commit()
    
    return deleted_count


def revoke_auth_session(db: Session, session_token: str) -> bool:
    """Revoke a specific session token"""
    session = db.query(UserAuthSession).filter(
        UserAuthSession.session_token == session_token
    ).first()
    
    if session:
        db.delete(session)
        db.commit()
        return True
    
    return False


def revoke_all_user_sessions(db: Session, user_id: int) -> int:
    """Revoke all sessions for a user"""
    deleted_count = db.query(UserAuthSession).filter(
        UserAuthSession.user_id == user_id
    ).count()
    
    db.query(UserAuthSession).filter(
        UserAuthSession.user_id == user_id
    ).delete()
    
    db.commit()
    return deleted_count
