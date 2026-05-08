import os

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import StreamingResponse
from sqlbot_xpack.file_utils import SQLBotFileUtils

from apps.system.crud.parameter_manage import get_groups, save_parameter_args
from apps.system.schemas.permission import SqlbotPermission, require_permissions
from common.core.deps import SessionDep

router = APIRouter(tags=["system/appearance"], prefix="/system/appearance", include_in_schema=False)


@router.get("/ui")
async def get_ui(session: SessionDep):
    return await get_groups(session, "appearance")


@router.post("")
@require_permissions(permission=SqlbotPermission(role=["admin"]))
async def save_ui(session: SessionDep, request: Request):
    return await save_parameter_args(session=session, request=request)


@router.get("/picture/{file_id}")
async def picture(file_id: str = Path(description="file_id")):
    file_path = SQLBotFileUtils.get_file_path(file_id=file_id)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    media_type = "image/svg+xml" if file_id.lower().endswith(".svg") else "image/jpeg"

    def iterfile():
        with open(file_path, mode="rb") as f:
            yield from f

    return StreamingResponse(iterfile(), media_type=media_type)
