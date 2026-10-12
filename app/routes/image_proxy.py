"""外部图片代理接口：/mail-image?u=<地址>&s=<签名>（需要登录）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from ..services import image_proxy

router = APIRouter(tags=["image-proxy"])


# 普通 def：下载图片是阻塞调用，放进线程池执行
@router.get(image_proxy.PROXY_PATH, name="image_proxy.get", include_in_schema=False)
def get_image(u: str = Query(..., max_length=8192), s: str = Query(..., max_length=64)):
    url = image_proxy.verify(u, s)
    if url is None:
        raise HTTPException(status_code=403, detail="Invalid signature")
    try:
        data, ctype = image_proxy.get(url)
    except image_proxy.ImageProxyError:
        raise HTTPException(status_code=404, detail="Image not available") from None
    return Response(
        content=data,
        media_type=ctype,
        headers={
            "Cache-Control": "private, max-age=604800",
            # 即使对方返回的内容不是图片，也不能在本站域名下被当作页面执行
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )
