from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from token_router.app.daily_eval import (
    list_report_history,
    load_latest_report,
    render_daily_eval_home,
)


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def daily_eval_home() -> HTMLResponse:
    result = load_latest_report()
    history = list_report_history()
    return HTMLResponse(render_daily_eval_home(result, history))
