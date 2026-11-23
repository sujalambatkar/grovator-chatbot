from fastapi import APIRouter, Depends, Request

from app.content.catalog import CATEGORY_LABEL
from app.core.abuse import limited_principal
from app.core.auth import Principal

router = APIRouter(tags=["content"])


@router.get("/content")
async def content(request: Request, p: Principal = Depends(limited_principal)) -> dict:
    """The documents this user may read: rendered by the page, and the same ones the chatbot answers from."""
    cat = request.app.state.catalog
    return {
        "categories": CATEGORY_LABEL,
        "docs": [{"source": d.source, "title": d.title, "category": d.category, "order": d.order, "body": d.body}
                 for d in cat.visible(p)],
    }
