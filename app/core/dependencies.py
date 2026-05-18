from typing import Annotated
import uuid

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.exceptions import UnauthorizedException, TenantIsolationException
from app.core.security import introspect_token, verify_internal_key
from app.db.mongodb import get_database
from app.schemas.common import TokenPayload

_bearer = HTTPBearer(auto_error=False)


async def get_db() -> AsyncIOMotorDatabase:
    """Return MongoDB database instance."""
    return get_database()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    x_organization_id: Annotated[str | None, Header()] = None,
) -> TokenPayload:
    """Validate JWT via be_core introspect. Returns TokenPayload with user and org context."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if x_organization_id is not None:
        try:
            uuid.UUID(x_organization_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid x-organization-id header (uuid is expected)",
            ) from exc
    try:
        payload = await introspect_token(credentials.credentials, x_organization_id)
        return payload
    except (UnauthorizedException, TenantIsolationException) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc.message),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def verify_internal_request(
    x_internal_key: Annotated[str | None, Header()] = None,
) -> None:
    """Verify that request comes from an internal service (iccp_be_core)."""
    if not x_internal_key or not verify_internal_key(x_internal_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing internal API key",
        )


async def require_system_admin(user: Annotated[TokenPayload, Depends(get_current_user)]) -> TokenPayload:
    roles = {role.strip().lower() for role in user.roles}
    if "system_admin" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System admin role required",
        )
    return user


# Typed dependency aliases
CurrentUser = Annotated[TokenPayload, Depends(get_current_user)]
DBSession = Annotated[AsyncIOMotorDatabase, Depends(get_db)]
InternalRequest = Annotated[None, Depends(verify_internal_request)]
SystemAdminUser = Annotated[TokenPayload, Depends(require_system_admin)]
