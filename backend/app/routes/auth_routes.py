from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.user_account import UserAccount
from app.schemas.auth import (
    LoginRequest,
    TokenResponse,
    UserCreate,
    UserResponse,
    UserUpdate,
)
from app.services.auth_service import (
    AuthenticatedUser,
    authenticate_user,
    create_access_token,
    get_current_user,
    hash_password,
    require_roles,
)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, data.username, data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token, expires_in = create_access_token(user)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.get(
    "/users",
    response_model=list[UserResponse],
    dependencies=[Depends(require_roles("admin"))],
)
def list_users(db: Session = Depends(get_db)):
    return db.query(UserAccount).order_by(UserAccount.id.asc()).all()


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_user(
    data: UserCreate,
    db: Session = Depends(get_db),
    _: AuthenticatedUser = Depends(require_roles("admin")),
):
    user = UserAccount(
        username=data.username,
        password_hash=hash_password(data.password),
        role=data.role,
        is_active=True,
    )
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists",
        ) from error
    return user


@router.patch(
    "/users/{user_id}",
    response_model=UserResponse,
)
def update_user(
    user_id: int,
    data: UserUpdate,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_roles("admin")),
):
    user = db.query(UserAccount).filter(UserAccount.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    next_role = data.role or user.role
    next_active = user.is_active if data.is_active is None else data.is_active
    if user.role == "admin" and user.is_active and (
        next_role != "admin" or not next_active
    ):
        other_admin = db.query(UserAccount.id).filter(
            UserAccount.id != user.id,
            UserAccount.role == "admin",
            UserAccount.is_active.is_(True),
        ).first()
        if not other_admin:
            raise HTTPException(
                status_code=409,
                detail="At least one active administrator must remain",
            )
    if current_user.id == user.id and not next_active:
        raise HTTPException(
            status_code=400,
            detail="You cannot deactivate your own account",
        )

    if data.role is not None:
        user.role = data.role
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.password is not None:
        user.password_hash = hash_password(data.password)
    db.commit()
    db.refresh(user)
    return user


@router.get("/me", response_model=UserResponse)
def get_me(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.id == 0:
        return UserResponse(
            id=0,
            username=current_user.username,
            role=current_user.role,
            is_active=True,
            created_at=None,
        )
    return db.query(UserAccount).filter(
        UserAccount.id == current_user.id
    ).one()
