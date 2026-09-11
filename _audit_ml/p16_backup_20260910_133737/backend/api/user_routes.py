"""
User authentication API routes
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from sqlalchemy.orm import Session
from typing import List, Optional
import logging

from backend.database.connection import SessionLocal
from backend.database.models import User, UserExchangeConfig, UserSubscription
from backend.repositories.user_repo import (
    create_user, get_user, get_user_by_username,
    update_user, create_auth_session, verify_auth_session
)
from datetime import datetime
from pydantic import BaseModel
from backend.schemas.user import (
    UserCreate, UserUpdate, UserOut, UserLogin, UserAuthResponse
)
from backend.core.request_identity import require_user_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["users"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Registration endpoint disabled for security - single user mode
# @router.post("/register", response_model=UserOut)
# async def register_user(user_data: UserCreate, db: Session = Depends(get_db)):
#     raise HTTPException(status_code=403, detail="Registration disabled - single user mode")


@router.post("/login", response_model=UserAuthResponse)
def login_user(login_data: UserLogin, db: Session = Depends(get_db)):
    try:
        # For now, just verify username exists and create session
        # Password verification can be implemented later
        user = get_user_by_username(db, login_data.username)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        # Create auth session
        session = create_auth_session(db, user.id)
        if not session:
            raise HTTPException(status_code=500, detail="Failed to create session")
        
        return UserAuthResponse(
            user=UserOut(
                id=user.id,
                username=user.username,
                email=user.email,
                is_active=user.is_active == "true"
            ),
            session_token=session.session_token,
            expires_at=session.expires_at.isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"User login failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"User login failed: {str(e)}")


@router.get("/profile", response_model=UserOut)
def get_user_profile(session_token: str, db: Session = Depends(get_db)):
    try:
        user_id = verify_auth_session(db, session_token)
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        
        user = get_user(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        return UserOut(
            id=user.id,
            username=user.username,
            email=user.email,
            is_active=user.is_active == "true"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get user profile: {str(e)}")


@router.put("/profile", response_model=UserOut)
def update_user_profile(
    session_token: str, 
    user_data: UserUpdate, 
    db: Session = Depends(get_db)
):
    try:
        user_id = verify_auth_session(db, session_token)
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid or expired session")
        
        # Check if new username is taken (if provided)
        if user_data.username:
            existing = get_user_by_username(db, user_data.username)
            if existing and existing.id != user_id:
                raise HTTPException(status_code=400, detail="Username already exists")
        
        user = update_user(
            db=db,
            user_id=user_id,
            username=user_data.username,
            email=user_data.email
        )
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        return UserOut(
            id=user.id,
            username=user.username,
            email=user.email,
            is_active=user.is_active == "true"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update user profile: {str(e)}")


@router.get("/", response_model=List[UserOut])
def list_users(db: Session = Depends(get_db)):
    try:
        users = db.query(User).filter(User.is_active == "true").order_by(User.username).all()
        return [
            UserOut(
                id=user.id,
                username=user.username,
                email=user.email,
                is_active=user.is_active == "true"
            )
            for user in users
        ]
        
    except Exception as e:
        logger.error(f"Failed to list users: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list users: {str(e)}")


@router.get("/exchange-config")
def get_exchange_config(request: Request, db: Session = Depends(get_db)):
    """当前登录用户选择的交易所偏好（每账户独立）。"""
    uid, _tid = require_user_tenant(request)
    try:
        config = db.query(UserExchangeConfig).filter(UserExchangeConfig.user_id == uid).first()
        if not config:
            from config import settings
            return {"selected_exchange": getattr(settings, "DEFAULT_EXCHANGE", "asterdex"), "user_id": uid}
        return {"selected_exchange": config.selected_exchange, "user_id": uid}
    except Exception as e:
        logger.error(f"Failed to get exchange config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get exchange config: {str(e)}")


@router.post("/exchange-config")
def set_exchange_config(exchange_data: dict, request: Request, db: Session = Depends(get_db)):
    """设置当前登录用户的交易所偏好。"""
    uid, _tid = require_user_tenant(request)
    try:
        selected_exchange = exchange_data.get("selected_exchange")
        from backend.config.settings import SUPPORTED_EXCHANGES_LIST
        if not selected_exchange or selected_exchange not in SUPPORTED_EXCHANGES_LIST:
            raise HTTPException(status_code=400, detail=f"Invalid exchange selection: {selected_exchange}")

        config = db.query(UserExchangeConfig).filter(UserExchangeConfig.user_id == uid).first()
        if config:
            config.selected_exchange = selected_exchange
        else:
            config = UserExchangeConfig(user_id=uid, selected_exchange=selected_exchange)
            db.add(config)

        db.commit()
        return {"selected_exchange": selected_exchange, "status": "success", "user_id": uid}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to set exchange config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to set exchange config: {str(e)}")


class MembershipSyncRequest(BaseModel):
    """Request model for syncing membership info from www.akooi.com"""
    username: str
    status: Optional[str]  # "ACTIVE" or None
    current_period_end: Optional[str]  # ISO datetime string


@router.post("/sync-membership")
def sync_membership_info(
    sync_data: MembershipSyncRequest,
    db: Session = Depends(get_db)
):
    """
    Sync membership information from www.akooi.com to local database

    This endpoint is called by frontend after successfully fetching membership
    info from www.akooi.com/api/membership/me. It updates the local UserSubscription
    table to keep it in sync, preventing accidental usage of stale local data.

    Important: This clears all non-default user subscriptions before updating,
    ensuring only the current logged-in user's subscription is active.
    """
    try:
        # Step 1: Delete all subscriptions for non-default users
        non_default_users = db.query(User).filter(User.username != "default").all()
        for u in non_default_users:
            db.query(UserSubscription).filter(UserSubscription.user_id == u.id).delete()
        logger.info(f"Cleared subscriptions for {len(non_default_users)} non-default users")

        # Step 2: Find or create user
        user = db.query(User).filter(User.username == sync_data.username).first()
        if not user:
            user = User(
                username=sync_data.username,
                email=f"{sync_data.username}@external.user",
                is_active="true"
            )
            db.add(user)
            db.flush()
            logger.info(f"Created new user: {sync_data.username} (ID: {user.id})")

        # Step 3: Determine subscription type based on status
        subscription_type = "premium" if sync_data.status == "ACTIVE" else "free"

        # Parse expiry date if provided
        expires_at = None
        if sync_data.current_period_end:
            try:
                expires_at = datetime.fromisoformat(sync_data.current_period_end.replace('Z', '+00:00'))
            except Exception as e:
                logger.warning(f"Failed to parse expiry date: {e}")

        # Step 4: Create subscription for current user
        subscription = UserSubscription(
            user_id=user.id,
            subscription_type=subscription_type,
            expires_at=expires_at,
            max_sampling_depth=60 if subscription_type == "premium" else 10
        )
        db.add(subscription)
        logger.info(f"Created subscription for user {sync_data.username}: {subscription_type}")

        db.commit()

        return {
            "status": "success",
            "message": f"Membership synced for {sync_data.username}",
            "subscription_type": subscription_type,
            "max_sampling_depth": subscription.max_sampling_depth
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Failed to sync membership info: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to sync membership info: {str(e)}"
        )