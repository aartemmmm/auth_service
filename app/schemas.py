from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from typing import Optional, Literal

Role = Literal["user", "admin"]


class UserBase(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=50)


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserResponse(UserBase):
    id: int
    role: Role
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class UserListResponse(BaseModel):
    users: list[UserResponse]


class LoginRequest(BaseModel):
    username_or_email: str
    password: str


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class SessionResponse(BaseModel):
    session_id: str
    user_id: int
    refresh_expires_at: datetime
    revoked_at: Optional[datetime] = None

    class Config:
        from_attributes = True
