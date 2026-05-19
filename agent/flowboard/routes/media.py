"""Media cache routes.

`GET /media/:media_id` streams bytes (cache hit → immediate; miss → one-shot
fetch from GCS then cache). `GET /api/media/:media_id/status` exposes cache
state for the frontend to poll while it waits for a URL to arrive.
`POST /api/media/:media_id/upscale` triggers Flow upscale for non-default
resolutions.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import Asset, Node
from flowboard.services import media as media_service
from flowboard.services.flow_client import flow_client
from flowboard.services.flow_sdk import get_flow_sdk

logger = logging.getLogger(__name__)

bytes_router = APIRouter(tags=["media"])
api_router = APIRouter(prefix="/api/media", tags=["media"])


def _derive_project_id(asset: Asset) -> str:
    """Try to find the project_id for an upscale request.

    First checks the asset's linked node for a projectId in node.data,
    then falls back to the board's project_id field.
    """
    if asset.node_id:
        with get_session() as s:
            node = s.get(Node, asset.node_id)
            if node:
                project_id = node.data.get("projectId") or node.data.get("project_id")
                if project_id:
                    return project_id
    return "default"


@bytes_router.get("/media/{media_id:path}")
async def get_media_bytes(media_id: str):
    media_id = media_service.normalize_media_id(media_id)
    if not media_service.is_valid_media_id(media_id):
        raise HTTPException(status_code=400, detail="invalid media_id")

    cached = media_service.cached_path(media_id)
    if cached is not None:
        return FileResponse(
            path=str(cached),
            media_type=media_service._mime_from_ext(cached.suffix),
        )

    # Cache miss — try one fetch through the stored URL.
    result = await media_service.fetch_and_cache(media_id)
    if result is None:
        status = media_service.status(media_id)
        return JSONResponse(status_code=404, content=status)
    _bytes, mime, path = result
    return FileResponse(path=str(path), media_type=mime)


@api_router.get("/{media_id}/status")
def get_media_status(media_id: str):
    media_id = media_service.normalize_media_id(media_id)
    if not media_service.is_valid_media_id(media_id):
        return JSONResponse(
            status_code=400,
            content={"available": False, "has_url": False, "reason": "invalid_id"},
        )
    return media_service.status(media_id)


class UpscaleRequest(BaseModel):
    resolution: str  # e.g. "2K", "4K", "1080", "4K"
    captcha_token: str = ""  # Optional; frontend injects one from the extension


@api_router.post("/{media_id}/upscale")
async def upscale_media(media_id: str, body: UpscaleRequest):
    """Trigger Flow upscale and return immediately.

    The caller should poll GET /api/media/{media_id}/status until the
    upscaled asset is available (the url field will be populated).
    """
    media_id = media_service.normalize_media_id(media_id)
    if not media_service.is_valid_media_id(media_id):
        raise HTTPException(400, "invalid media_id")

    with get_session() as s:
        asset = s.exec(
            select(Asset).where(Asset.uuid_media_id == media_id)
        ).first()
        if not asset:
            raise HTTPException(404, "asset not found")

    sdk = get_flow_sdk()

    # Determine project_id from board/node context. We store the project_id
    # on the Node.data. If we can't find it, derive from the asset's node.
    project_id = _derive_project_id(asset)

    paygate_tier = flow_client.paygate_tier or "PAYGATE_TIER_ONE"
    captcha_token = body.captcha_token or ""

    kind = asset.kind or "image"

    if kind == "image":
        flow_res = media_service.IMAGE_UPSCALE_RESOLUTIONS.get(body.resolution)
        if not flow_res:
            raise HTTPException(400, f"unsupported image resolution: {body.resolution}")

        result = await sdk.upscale_image(
            media_id=media_id,
            target_resolution=flow_res,
            project_id=project_id,
            paygate_tier=paygate_tier,
            captcha_token=captcha_token,
        )
    elif kind == "video":
        flow_res = media_service.VIDEO_UPSCALE_RESOLUTIONS.get(body.resolution)
        if not flow_res:
            raise HTTPException(400, f"unsupported video resolution: {body.resolution}")

        result = await sdk.upscale_video(
            media_id=media_id,
            resolution=flow_res,
            aspect_ratio="VIDEO_ASPECT_RATIO_LANDSCAPE",
            project_id=project_id,
            paygate_tier=paygate_tier,
            captcha_token=captcha_token,
        )
    else:
        raise HTTPException(400, f"upscale not supported for kind: {kind}")

    if result.get("error"):
        raise HTTPException(502, f"Flow API error: {result['error']}")

    return {"status": "pending", "raw": result}


@api_router.get("/_debug/assets")
def debug_assets():
    """Dev-only dump of every Asset row so we can see what URLs the extension
    has actually pushed to the agent. Remove once media flow is stable.
    """
    from sqlmodel import select as _select

    from flowboard.db import get_session
    from flowboard.db.models import Asset

    with get_session() as s:
        rows = s.exec(_select(Asset)).all()
        return {
            "count": len(rows),
            "rows": [
                {
                    "id": r.id,
                    "media_id": r.uuid_media_id,
                    "has_url": bool(r.url),
                    "url_head": (r.url or "")[:80] if r.url else None,
                    "mime": r.mime,
                    "cached": bool(r.local_path),
                    "node_id": r.node_id,
                }
                for r in rows
            ],
        }
