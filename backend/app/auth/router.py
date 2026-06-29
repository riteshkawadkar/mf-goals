"""Email-OTP authentication. Issues a bearer JWT on successful verification."""
import hashlib
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.db import User
from app.schemas.api import OtpRequest, OtpVerify, TokenResponse
from app.auth.jwt_utils import create_access_token

router = APIRouter(prefix="/auth", tags=["Auth"])


def _hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode()).hexdigest()


def _send_otp_email(to_email: str, otp: str) -> None:
    settings = get_settings()
    if not settings.smtp_host:
        # Development: just print
        print(f"[DEV] OTP for {to_email}: {otp}")
        return
    msg = MIMEText(f"Your Goal Tracker OTP is: {otp}\n\nValid for 10 minutes.")
    msg["Subject"] = "Your Goal Tracker sign-in code"
    msg["From"] = settings.from_email
    msg["To"] = to_email
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.starttls()
        server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)


@router.post("/otp/request", status_code=204)
def request_otp(body: OtpRequest, db: Session = Depends(get_db)):
    settings = get_settings()
    user = db.query(User).filter(User.email == body.email).first()
    if not user:
        user = User(email=body.email)
        db.add(user)

    otp = f"{secrets.randbelow(1_000_000):06d}"
    user.otp_hash = _hash_otp(otp)
    user.otp_expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.otp_valid_seconds)
    db.commit()

    _send_otp_email(body.email, otp)


@router.post("/otp/verify", response_model=TokenResponse)
def verify_otp(body: OtpVerify, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not user.otp_hash or not user.otp_expires_at:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No OTP pending for this email")

    if datetime.now(timezone.utc) > user.otp_expires_at:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="OTP expired")

    if user.otp_hash != _hash_otp(body.otp):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OTP")

    user.otp_hash = None
    user.otp_expires_at = None
    db.commit()

    # Seed default assumptions if first login
    from app.seeds.default_assumptions import seed_defaults
    seed_defaults(user.id, db)

    return TokenResponse(access_token=create_access_token(user.id))
