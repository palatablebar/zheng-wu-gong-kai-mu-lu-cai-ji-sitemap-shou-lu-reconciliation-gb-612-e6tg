from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.middlewares.rbac import READ_ROLES, require_roles
from src.services import repost_service
from src.utils.pagination import page_params

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("")
def list_documents(
    site_code: str | None = None,
    multi_site: bool | None = Query(default=None),
    q: str | None = None,
    limit: int = Query(20),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    page = page_params(limit, offset)
    return repost_service.list_documents(
        db,
        site_code=site_code,
        multi_site=multi_site,
        q=q,
        limit=page.limit,
        offset=page.offset,
    )
