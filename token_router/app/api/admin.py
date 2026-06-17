from __future__ import annotations

from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from token_router.app.config import AppConfig
from token_router.app.router.quota import quota_date_for
from token_router.app.router.selector import NoAvailableModelError, RouteSelector
from token_router.app.usage import UsageManager


router = APIRouter(prefix="/admin")


def _get_config(request: Request) -> AppConfig:
    config = request.app.state.config
    if config is None:
        raise HTTPException(status_code=500, detail="router config is not loaded")
    return config


def _quota_date(request: Request, config: AppConfig) -> str:
    return quota_date_for(
        request.app.state.now_fn(),
        config.refresh.timezone,
        config.refresh.daily_reset_hour,
    )


@router.get("/models")
def list_models(request: Request) -> list[dict[str, Any]]:
    config = _get_config(request)
    usage_manager: UsageManager = request.app.state.usage_manager
    selector = RouteSelector(config, usage_manager)
    quota_date = _quota_date(request, config)
    return [route.to_dict() for route in selector.list_status(quota_date)]


@router.get("/usage", response_class=HTMLResponse)
def usage_page(request: Request) -> HTMLResponse:
    config = _get_config(request)
    usage_manager: UsageManager = request.app.state.usage_manager
    selector = RouteSelector(config, usage_manager)
    quota_date = _quota_date(request, config)
    routes = selector.list_status(quota_date)

    rows = []
    key_summaries: dict[tuple[str, str, str], dict[str, Any]] = {}
    for route in routes:
        usage = usage_manager.get_usage(
            route.provider,
            route.key_id,
            route.model_name,
            quota_date,
        )
        row = {
            "provider": route.provider,
            "endpoint": route.endpoint,
            "key_id": route.key_id,
            "model_name": route.model_name,
            "level": route.level,
            "priority": route.priority,
            "stage": route.stage,
            "daily_quota": route.daily_quota,
            "daily_request_quota": route.daily_request_quota,
            "usage_ratio": route.usage_ratio,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "request_count": usage.request_count,
            "used_requests": route.used_requests,
            "available": route.available,
        }
        rows.append(row)

        summary_key = (route.provider, route.endpoint, route.key_id)
        summary = key_summaries.setdefault(
            summary_key,
            {
                "provider": route.provider,
                "endpoint": route.endpoint,
                "key_id": route.key_id,
                "daily_quota": 0,
                "daily_request_quota": None,
                "best_priority": route.priority,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "request_count": 0,
                "model_count": 0,
                "available_count": 0,
            },
        )
        summary["daily_quota"] += route.daily_quota
        if route.daily_request_quota is not None:
            current_quota = summary["daily_request_quota"] or 0
            summary["daily_request_quota"] = max(current_quota, route.daily_request_quota)
        summary["best_priority"] = min(summary["best_priority"], route.priority)
        summary["prompt_tokens"] += usage.prompt_tokens
        summary["completion_tokens"] += usage.completion_tokens
        summary["total_tokens"] += usage.total_tokens
        summary["request_count"] += usage.request_count
        summary["model_count"] += 1
        if route.available:
            summary["available_count"] += 1

    html = _render_usage_page(
        quota_date=quota_date,
        timezone=config.refresh.timezone,
        reset_hour=config.refresh.daily_reset_hour,
        key_summaries=sorted(
            key_summaries.values(),
            key=lambda item: (item["provider"], item["endpoint"], item["key_id"]),
        ),
        rows=sorted(
            rows,
            key=lambda item: (
                item["provider"],
                item["endpoint"],
                item["key_id"],
                item["level"],
                item["model_name"],
            ),
        ),
    )
    return HTMLResponse(content=html)


@router.post("/route/preview")
def route_preview(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    config = _get_config(request)
    usage_manager: UsageManager = request.app.state.usage_manager
    selector = RouteSelector(config, usage_manager)
    quota_date = _quota_date(request, config)

    try:
        selected = selector.select(
            model=payload.get("model", "auto"),
            router=payload.get("router", {}),
            quota_date=quota_date,
            response_format_type=_response_format_type(payload),
        )
    except NoAvailableModelError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    return {
        "selected": selected.to_dict(),
        "reason": "selected by capability filters, level, stage, priority, and usage ratio",
    }


def _response_format_type(payload: dict[str, Any]) -> str | None:
    response_format = payload.get("response_format")
    if not isinstance(response_format, dict):
        return None
    response_format_type = response_format.get("type")
    if isinstance(response_format_type, str):
        return response_format_type
    return None


def _render_usage_page(
    quota_date: str,
    timezone: str,
    reset_hour: int,
    key_summaries: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> str:
    total_tokens = sum(item["total_tokens"] for item in key_summaries)
    total_quota = sum(item["daily_quota"] for item in key_summaries)
    total_requests = sum(item["request_count"] for item in key_summaries)
    total_models = sum(item["model_count"] for item in key_summaries)
    overall_ratio = _ratio(total_tokens, total_quota)

    key_rows = "\n".join(_render_key_row(item) for item in key_summaries)
    model_rows = "\n".join(_render_model_row(item) for item in rows)
    if not key_rows:
        key_rows = '<tr><td class="empty" colspan="10">No configured API keys.</td></tr>'
    if not model_rows:
        model_rows = '<tr><td class="empty" colspan="12">No configured models.</td></tr>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>API Key Usage</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f7fb;
      --surface: #ffffff;
      --surface-2: #eef3f8;
      --text: #18202b;
      --muted: #667085;
      --line: #d9e1ea;
      --accent: #0f766e;
      --accent-2: #b45309;
      --danger: #b42318;
      --ok-bg: #dcfce7;
      --ok-text: #166534;
      --warn-bg: #fef3c7;
      --warn-text: #92400e;
      --danger-bg: #fee2e2;
      --danger-text: #991b1b;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-size: 14px;
    }}
    .shell {{
      width: min(1440px, calc(100% - 48px));
      margin: 0 auto;
      padding: 32px 0 48px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 24px;
      align-items: flex-end;
      margin-bottom: 24px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 30px;
      font-weight: 750;
      letter-spacing: 0;
    }}
    .subtitle {{
      color: var(--muted);
      line-height: 1.5;
    }}
    .meta {{
      min-width: 260px;
      text-align: right;
      color: var(--muted);
      line-height: 1.6;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }}
    .stat {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
      box-shadow: 0 10px 26px rgba(15, 23, 42, 0.05);
    }}
    .label {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .value {{
      margin-top: 8px;
      font-size: 26px;
      font-weight: 760;
      letter-spacing: 0;
    }}
    section {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      margin-top: 18px;
      box-shadow: 0 10px 26px rgba(15, 23, 42, 0.05);
    }}
    .section-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(180deg, #ffffff, #f8fafc);
    }}
    h2 {{
      margin: 0;
      font-size: 16px;
      font-weight: 720;
      letter-spacing: 0;
    }}
    .table-wrap {{ overflow-x: auto; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 980px;
    }}
    th, td {{
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      white-space: nowrap;
      vertical-align: middle;
    }}
    th {{
      color: var(--muted);
      background: var(--surface-2);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .muted {{ color: var(--muted); }}
    .id {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 13px;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      height: 24px;
      padding: 0 9px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
    }}
    .ok {{ background: var(--ok-bg); color: var(--ok-text); }}
    .warn {{ background: var(--warn-bg); color: var(--warn-text); }}
    .danger {{ background: var(--danger-bg); color: var(--danger-text); }}
    .bar {{
      display: grid;
      grid-template-columns: minmax(120px, 1fr) 58px;
      align-items: center;
      gap: 10px;
    }}
    .track {{
      height: 9px;
      border-radius: 999px;
      background: #e5e7eb;
      overflow: hidden;
    }}
    .fill {{
      height: 100%;
      border-radius: 999px;
      background: var(--accent);
    }}
    .fill.warn {{ background: var(--accent-2); }}
    .fill.danger {{ background: var(--danger); }}
    .empty {{
      color: var(--muted);
      text-align: center;
      padding: 28px;
    }}
    @media (max-width: 880px) {{
      .shell {{ width: min(100% - 24px, 1440px); padding-top: 20px; }}
      header {{ display: block; }}
      .meta {{ text-align: left; margin-top: 14px; }}
      .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 560px) {{
      .stats {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 24px; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div>
        <h1>API Key Usage</h1>
        <div class="subtitle">Daily token usage by provider, endpoint, API key, and model.</div>
      </div>
      <div class="meta">
        <div>Quota date: <strong>{escape(quota_date)}</strong></div>
        <div>Reset: {escape(timezone)} at {reset_hour:02d}:00</div>
      </div>
    </header>

    <div class="stats">
      <div class="stat"><div class="label">Used Tokens</div><div class="value">{_fmt_int(total_tokens)}</div></div>
      <div class="stat"><div class="label">Daily Quota</div><div class="value">{_fmt_int(total_quota)}</div></div>
      <div class="stat"><div class="label">Usage Ratio</div><div class="value">{_fmt_pct(overall_ratio)}</div></div>
      <div class="stat"><div class="label">Requests / Models</div><div class="value">{_fmt_int(total_requests)} / {_fmt_int(total_models)}</div></div>
    </div>

    <section>
      <div class="section-head">
        <h2>API Key Summary</h2>
        <span class="muted">{len(key_summaries)} keys</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Provider</th>
              <th>Endpoint</th>
              <th>Key ID</th>
              <th class="num">Models</th>
              <th class="num">Priority</th>
              <th class="num">Requests / Quota</th>
              <th class="num">Prompt</th>
              <th class="num">Completion</th>
              <th class="num">Total / Quota</th>
              <th>Usage</th>
            </tr>
          </thead>
          <tbody>{key_rows}</tbody>
        </table>
      </div>
    </section>

    <section>
      <div class="section-head">
        <h2>Model Instances</h2>
        <span class="muted">{len(rows)} instances</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Provider</th>
              <th>Endpoint</th>
              <th>Key ID</th>
              <th>Model</th>
              <th class="num">Level</th>
              <th class="num">Priority</th>
              <th class="num">Stage</th>
              <th class="num">Requests / Quota</th>
              <th class="num">Prompt</th>
              <th class="num">Completion</th>
              <th class="num">Total / Quota</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>{model_rows}</tbody>
        </table>
      </div>
    </section>
  </main>
</body>
</html>"""


def _render_key_row(item: dict[str, Any]) -> str:
    ratio = _ratio(item["total_tokens"], item["daily_quota"])
    return f"""<tr>
  <td>{escape(item["provider"])}</td>
  <td>{escape(item["endpoint"])}</td>
  <td class="id">{escape(item["key_id"])}</td>
  <td class="num">{_fmt_int(item["available_count"])} / {_fmt_int(item["model_count"])}</td>
  <td class="num">{_fmt_int(item["best_priority"])}</td>
  <td class="num">{_fmt_request_usage(item["request_count"], item["daily_request_quota"])}</td>
  <td class="num">{_fmt_int(item["prompt_tokens"])}</td>
  <td class="num">{_fmt_int(item["completion_tokens"])}</td>
  <td class="num">{_fmt_int(item["total_tokens"])} / {_fmt_int(item["daily_quota"])}</td>
  <td>{_progress(ratio)}</td>
</tr>"""


def _render_model_row(item: dict[str, Any]) -> str:
    ratio = _ratio(item["total_tokens"], item["daily_quota"])
    status = '<span class="pill ok">Available</span>'
    if not item["available"]:
        status = '<span class="pill danger">Unavailable</span>'
    stage = item["stage"] if item["stage"] is not None else "Done"
    return f"""<tr>
  <td>{escape(item["provider"])}</td>
  <td>{escape(item["endpoint"])}</td>
  <td class="id">{escape(item["key_id"])}</td>
  <td>{escape(item["model_name"])}</td>
  <td class="num">{_fmt_int(item["level"])}</td>
  <td class="num">{_fmt_int(item["priority"])}</td>
  <td class="num">{escape(str(stage))}</td>
  <td class="num">{_fmt_request_usage(item["used_requests"], item["daily_request_quota"])}</td>
  <td class="num">{_fmt_int(item["prompt_tokens"])}</td>
  <td class="num">{_fmt_int(item["completion_tokens"])}</td>
  <td class="num">{_fmt_int(item["total_tokens"])} / {_fmt_int(item["daily_quota"])}</td>
  <td>{status}</td>
</tr>"""


def _progress(ratio: float) -> str:
    width = min(max(ratio, 0.0), 1.0) * 100
    tone = ""
    if ratio >= 0.9:
        tone = " danger"
    elif ratio >= 0.75:
        tone = " warn"
    return f"""<div class="bar">
  <div class="track"><div class="fill{tone}" style="width: {width:.2f}%"></div></div>
  <span class="num">{_fmt_pct(ratio)}</span>
</div>"""


def _ratio(used_tokens: int, daily_quota: int) -> float:
    if daily_quota <= 0:
        return 0.0
    return used_tokens / daily_quota


def _fmt_int(value: int) -> str:
    return f"{value:,}"


def _fmt_request_usage(used_requests: int, daily_request_quota: int | None) -> str:
    if daily_request_quota is None:
        return _fmt_int(used_requests)
    return f"{_fmt_int(used_requests)} / {_fmt_int(daily_request_quota)}"


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"
