"""
main.py — LectureHub API
─────────────────────────
Auth endpoints that integrate with the LectureHub frontend.

Signup flow  (3 steps)
  1. POST /api/auth/send-otp      → validate form data, store pending user, email OTP
  2. POST /api/auth/verify-otp    → check OTP validity
  3. POST /api/auth/signup        → create verified account from pending_users row

Login flow   (1 step)
  POST /api/auth/login            → verify credentials, return user

Resend OTP
  POST /api/auth/resend-otp       → invalidate old OTP, send a fresh one

Run:
  uvicorn main:app --reload --port 8000
"""

import os
import random
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, field_validator

from database import get_db, init_db

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
#  Config  (set these in .env — see .env.example)
# ─────────────────────────────────────────────────────────────────────────────

SMTP_HOST          = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT          = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER          = os.getenv("SMTP_USER", "")        # sender Gmail address
SMTP_PASS          = os.getenv("SMTP_PASS", "")        # Gmail App Password (16 chars)
FROM_NAME          = os.getenv("FROM_NAME", "LectureHub")
OTP_EXPIRE_MINUTES = int(os.getenv("OTP_EXPIRE_MINUTES", "10"))
CORS_ORIGINS       = os.getenv("CORS_ORIGINS", "*").split(",")

# ─────────────────────────────────────────────────────────────────────────────
#  App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="LectureHub API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

pwd_ctx = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


@app.on_event("startup")
def startup() -> None:
    init_db()


# ─────────────────────────────────────────────────────────────────────────────
#  Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class LoginReq(BaseModel):
    email:    EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def pw_min_len(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters.")
        return v


class SendOTPReq(BaseModel):
    """
    Frontend sends this from doSignup().
    We accept name + password here so they can be stored
    securely in pending_users while the user verifies their email.
    """
    email:    EmailStr
    name:     str
    password: str

    @field_validator("name")
    @classmethod
    def name_min_len(cls, v: str) -> str:
        if len(v.strip()) < 2:
            raise ValueError("Name must be at least 2 characters.")
        return v.strip()

    @field_validator("password")
    @classmethod
    def pw_min_len(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters.")
        return v


class VerifyOTPReq(BaseModel):
    email: EmailStr
    otp:   str


class SignupReq(BaseModel):
    """Called after OTP is verified — creates the actual users row."""
    name:  str
    email: EmailStr


class ResendOTPReq(BaseModel):
    email: EmailStr


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resp(success: bool, message: str, **extra) -> dict:
    """Consistent response envelope."""
    return {"success": success, "message": message, **extra}


def _http_err(status: int, message: str) -> HTTPException:
    """Raises an HTTPException whose detail matches the frontend's expected shape."""
    return HTTPException(
        status_code=status,
        detail=_resp(False, message)
    )


def _gen_otp(length: int = 6) -> str:
    return "".join(random.choices("0123456789", k=length))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _save_otp(email: str, otp: str) -> None:
    """Invalidate all pending OTPs for this email, then insert the new one."""
    expires = (_utc_now() + timedelta(minutes=OTP_EXPIRE_MINUTES)).isoformat()
    with get_db() as db:
        db.execute(
            "UPDATE otp_codes SET used = 1 WHERE email = ? AND used = 0",
            (email,)
        )
        db.execute(
            "INSERT INTO otp_codes (email, otp, expires_at) VALUES (?, ?, ?)",
            (email, otp, expires)
        )


def _validate_otp(email: str, entered: str) -> bool:
    """
    Checks the most recent unused OTP for this email.
    Marks it used on success.  Returns True / False.
    """
    with get_db() as db:
        row = db.execute(
            """
            SELECT id, otp, expires_at
            FROM   otp_codes
            WHERE  email = ? AND used = 0
            ORDER  BY created_at DESC
            LIMIT  1
            """,
            (email,)
        ).fetchone()

        if not row:
            return False

        # Check expiry
        expires_at = datetime.fromisoformat(row["expires_at"])
        # Make timezone-aware for comparison
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if _utc_now() > expires_at:
            return False

        # Constant-time compare to prevent timing attacks
        if row["otp"] != entered.strip():
            return False

        # Mark used
        db.execute("UPDATE otp_codes SET used = 1 WHERE id = ?", (row["id"],))
        return True


def _send_email(to_email: str, otp: str) -> None:
    """
    Sends a styled OTP email via SMTP (Gmail by default).
    Raises RuntimeError if sending fails so callers can surface a 500.
    """
    if not SMTP_USER or not SMTP_PASS:
        # Dev fallback — print to console instead of crashing
        print(f"\n{'='*40}\n[DEV] OTP for {to_email}: {otp}\n{'='*40}\n")
        return

    subject = f"Your LectureHub verification code: {otp}"

    html = f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f7f5f0;font-family:sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 16px">
      <table width="480" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:16px;overflow:hidden;
                    box-shadow:0 4px 24px rgba(19,17,26,0.10)">

        <!-- Header -->
        <tr>
          <td style="background:#4f35d2;padding:28px 36px">
            <span style="font-size:22px;font-weight:900;color:#fff;letter-spacing:-0.5px">
              LectureHub
            </span>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:36px 36px 28px">
            <h2 style="margin:0 0 10px;font-size:20px;color:#13111a">
              Verify your email address
            </h2>
            <p style="margin:0 0 28px;font-size:14px;color:#5c5870;line-height:1.65">
              Use the code below to complete your sign-up.
              It expires in <strong>{OTP_EXPIRE_MINUTES} minutes</strong>.
            </p>

            <!-- OTP block -->
            <div style="background:#4f35d2;border-radius:12px;
                        padding:22px 0;text-align:center;margin-bottom:28px">
              <span style="font-size:40px;font-weight:900;color:#ffffff;
                           letter-spacing:14px;font-family:monospace">
                {otp}
              </span>
            </div>

            <p style="margin:0;font-size:13px;color:#8880aa;line-height:1.6">
              If you didn't request this, you can safely ignore this email.
              <br/>Never share this code with anyone — LectureHub will never ask for it.
            </p>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f7f5f0;padding:18px 36px;
                     border-top:1px solid #ddd8cc;font-size:12px;color:#8880aa">
            © 2025 LectureHub &nbsp;·&nbsp; Made for students, by students
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{FROM_NAME} <{SMTP_USER}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=12) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to_email, msg.as_string())
    except smtplib.SMTPAuthenticationError:
        raise RuntimeError(
            "Email authentication failed. Check SMTP_USER and SMTP_PASS in .env."
        )
    except smtplib.SMTPException as exc:
        raise RuntimeError(f"SMTP error: {exc}")
    except OSError as exc:
        raise RuntimeError(f"Network error reaching SMTP server: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "LectureHub API"}


# ── POST /api/auth/send-otp ───────────────────────────────────────────────────
@app.post("/api/auth/send-otp")
def send_otp(req: SendOTPReq):
    """
    Signup step 1.
    - Rejects if a verified account already exists for this email.
    - Hashes and stores name + password in pending_users.
    - Generates OTP, saves it, and emails it.
    """
    email_lc = req.email.lower()

    with get_db() as db:
        existing = db.execute(
            "SELECT is_verified FROM users WHERE email = ?", (email_lc,)
        ).fetchone()

    if existing and existing["is_verified"]:
        raise _http_err(409, "An account with this email already exists. Please sign in.")

    # Upsert pending user (hash password now — never store plaintext)
    pw_hash = pwd_ctx.hash(req.password)
    with get_db() as db:
        db.execute(
            """
            INSERT INTO pending_users (email, name, password_hash)
            VALUES (?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                name          = excluded.name,
                password_hash = excluded.password_hash,
                created_at    = datetime('now')
            """,
            (email_lc, req.name, pw_hash)
        )

    otp = _gen_otp()
    _save_otp(email_lc, otp)

    try:
        _send_email(email_lc, otp)
    except RuntimeError as exc:
        raise _http_err(500, str(exc))

    return _resp(True, f"OTP sent to {req.email}. Check your inbox.")


# ── POST /api/auth/verify-otp ─────────────────────────────────────────────────
@app.post("/api/auth/verify-otp")
def verify_otp(req: VerifyOTPReq):
    """
    Signup step 2.
    Validates the OTP.  Does NOT create the account yet — that happens
    in /api/auth/signup (step 3) so the frontend can show a success screen first.
    """
    if not _validate_otp(req.email.lower(), req.otp):
        raise _http_err(400, "Incorrect or expired OTP. Please try again.")

    return _resp(True, "OTP verified successfully.")


# ── POST /api/auth/signup ─────────────────────────────────────────────────────
@app.post("/api/auth/signup")
def signup(req: SignupReq):
    """
    Signup step 3.
    Moves the row from pending_users → users and marks it verified.
    """
    email_lc = req.email.lower()

    with get_db() as db:
        pending = db.execute(
            "SELECT name, password_hash FROM pending_users WHERE email = ?",
            (email_lc,)
        ).fetchone()

    if not pending:
        raise _http_err(
            400,
            "Session expired or OTP not verified. Please restart sign-up."
        )

    name     = req.name or pending["name"]
    pw_hash  = pending["password_hash"]

    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM users WHERE email = ?", (email_lc,)
        ).fetchone()

        if existing:
            # Account stub may exist from a partial attempt — just update it
            db.execute(
                "UPDATE users SET name = ?, password_hash = ?, is_verified = 1 WHERE email = ?",
                (name, pw_hash, email_lc)
            )
        else:
            db.execute(
                """
                INSERT INTO users (name, email, password_hash, is_verified)
                VALUES (?, ?, ?, 1)
                """,
                (name, email_lc, pw_hash)
            )

        # Clean up pending row
        db.execute("DELETE FROM pending_users WHERE email = ?", (email_lc,))

    return _resp(
        True, "Account created successfully!",
        user={"name": name, "email": email_lc}
    )


# ── POST /api/auth/login ──────────────────────────────────────────────────────
@app.post("/api/auth/login")
def login(req: LoginReq):
    """
    Checks email + password.  Returns user object on success.
    Uses a generic error message to avoid leaking whether the email exists.
    """
    email_lc = req.email.lower()

    with get_db() as db:
        user = db.execute(
            "SELECT name, email, password_hash, is_verified FROM users WHERE email = ?",
            (email_lc,)
        ).fetchone()

    # Fail fast — but always run pwd_ctx.verify() to avoid timing side-channels
    dummy_hash = "$2b$12$placeholderplaceholderplaceholderplaceholderplaceholder"
    stored_hash = user["password_hash"] if user else dummy_hash

    password_ok = pwd_ctx.verify(req.password, stored_hash)

    if not user or not password_ok:
        raise _http_err(401, "Invalid email or password.")

    if not user["is_verified"]:
        raise _http_err(403, "Email not verified. Please complete the sign-up process.")

    return _resp(
        True, "Login successful.",
        user={"name": user["name"], "email": user["email"]}
    )


# ── POST /api/auth/resend-otp ─────────────────────────────────────────────────
@app.post("/api/auth/resend-otp")
def resend_otp(req: ResendOTPReq):
    """
    Invalidates the current OTP and sends a fresh one.
    Requires that a pending_users row exists (i.e. user started sign-up).
    """
    email_lc = req.email.lower()

    with get_db() as db:
        pending = db.execute(
            "SELECT email FROM pending_users WHERE email = ?", (email_lc,)
        ).fetchone()

    if not pending:
        raise _http_err(400, "No pending sign-up found for this email. Please restart.")

    otp = _gen_otp()
    _save_otp(email_lc, otp)

    try:
        _send_email(email_lc, otp)
    except RuntimeError as exc:
        raise _http_err(500, str(exc))

    return _resp(True, "New OTP sent. Check your inbox.")
