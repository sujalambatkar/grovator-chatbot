from fastapi import APIRouter, Depends, Request

from app.content.catalog import CATEGORY_LABEL
from app.core.abuse import limited_principal
from app.core.auth import Principal

router = APIRouter(tags=["content"])


@router.get("/content")
async def content(request: Request, p: Principal = Depends(limited_principal)) -> dict:
    """The documents this user may read: rendered by the page, and the same ones the chatbot answers from."""
    cat = request.app.state.catalog
    canned = getattr(request.app.state.service, "canned", None)
    return {
        "categories": CATEGORY_LABEL,
        "docs": [{"source": d.source, "title": d.title, "category": d.category, "order": d.order, "body": d.body,
                  # suggested questions that are answered by pre-written answers (0 tokens); empty = the page uses its defaults
                  "suggestions": canned.suggestions(d.source) if canned else []}
                 for d in cat.visible(p)],
    }
