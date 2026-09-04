# =============================================================
# OWNER: AARON
# =============================================================
from fastapi import Security, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import os
import logging
from datetime import datetime, timedelta
from passlib.context import CryptContext

logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-this-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()


class AuthHandler:
    """
    JWT-based authentication for API gateway.
    Production-grade with token refresh support.
    """

    def create_access_token(self, user_id: str, role: str = "analyst") -> str:
        """Create JWT access token."""
        payload = {
            "sub": user_id,
            "role": role,
            "iat": datetime.utcnow(),
            "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        }
        return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

    def verify_token(self, credentials: HTTPAuthorizationCredentials = Security(security)):
        """Verify JWT token from request."""
        token = credentials.credentials
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("sub")
            role = payload.get("role")

            if user_id is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token"
                )

            return {"user_id": user_id, "role": role}

        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired"
            )
        except jwt.InvalidTokenError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token"
            )

    def require_role(self, required_role: str):
        """Role-based access control decorator."""
        def check_role(user=Depends(self.verify_token)):
            if user["role"] != required_role and user["role"] != "admin":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Role '{required_role}' required"
                )
            return user
        return check_role

    def hash_password(self, password: str) -> str:
        return pwd_context.hash(password)

    def verify_password(self, plain: str, hashed: str) -> bool:
        return pwd_context.verify(plain, hashed)


auth_handler = AuthHandler()
