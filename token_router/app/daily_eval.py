from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from token_router.app.config import ApiKeyConfig, AppConfig, EndpointConfig
from token_router.app.providers.openai_compatible import OpenAICompatibleProvider
from token_router.app.router.quota import quota_date_for
from token_router.app.usage import UsageManager


DEFAULT_REPORTS_DIR = Path("reports/daily-model-eval")
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
DEFAULT_DAILY_EVAL_SCHEDULE_HOUR = 0
DEFAULT_DAILY_EVAL_MAX_TOKENS = 100000
DEFAULT_DAILY_EVAL_CONCURRENCY = 4
DAILY_EVAL_EXCLUDED_PROVIDERS = frozenset({"volcengine_ark"})

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HotspotQuery:
    name: str
    title: str
    query: str
    topic: str = "news"


HOTSPOT_QUERIES = [
    HotspotQuery(
        name="general",
        title="综合新闻",
        query=(
            "latest daily general news headlines including major global events, "
            "technology, AI, public policy, health, and society"
        ),
        topic="general",
    ),
    HotspotQuery(
        name="news",
        title="国际新闻",
        query=(
            "latest daily international news covering geopolitics, diplomacy, "
            "conflicts, security, and global affairs"
        ),
        topic="news",
    ),
    HotspotQuery(
        name="finance",
        title="财经新闻",
        query=(
            "latest daily finance news covering global economy, markets, central "
            "banks, inflation, commodities, stocks, and AI business"
        ),
        topic="finance",
    ),
]


@dataclass(frozen=True)
class EvalTarget:
    provider: str
    endpoint: str
    key_id: str
    model_name: str
    upstream_model_name: str
    level: int
    daily_quota: int
    daily_request_quota: int | None


@dataclass(frozen=True)
class ModelEvalResult:
    provider: str
    endpoint: str
    key_id: str
    model_name: str
    level: int
    status: str
    latency_ms: int | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    usage_missing: bool
    summary: str
    error_message: str | None


@dataclass(frozen=True)
class DailyEvalResult:
    run_date: str
    quota_date: str
    generated_at: str
    tavily_results: dict[str, Any]
    model_results: list[ModelEvalResult]

    @property
    def ok_count(self) -> int:
        return sum(1 for result in self.model_results if result.status == "ok")

    @property
    def error_count(self) -> int:
        return sum(1 for result in self.model_results if result.status != "ok")

    @property
    def total_tokens(self) -> int:
        return sum(result.total_tokens for result in self.model_results)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ok_count"] = self.ok_count
        data["error_count"] = self.error_count
        data["total_tokens"] = self.total_tokens
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DailyEvalResult:
        return cls(
            run_date=data["run_date"],
            quota_date=data["quota_date"],
            generated_at=data["generated_at"],
            tavily_results=data.get("tavily_results", {}),
            model_results=[
                ModelEvalResult(**item) for item in data.get("model_results", [])
            ],
        )


def reports_dir_from_env() -> Path:
    return Path(os.environ.get("TOKEN_ROUTER_REPORTS_DIR", str(DEFAULT_REPORTS_DIR)))


def expand_eval_targets(config: AppConfig) -> list[EvalTarget]:
    targets: list[EvalTarget] = []
    for instance in config.model_instances:
        if not instance.enabled:
            continue
        if instance.provider in DAILY_EVAL_EXCLUDED_PROVIDERS:
            continue
        for key_config in instance.iter_key_configs():
            if not key_config.enabled:
                continue
            targets.append(
                EvalTarget(
                    provider=instance.provider,
                    endpoint=instance.endpoint,
                    key_id=key_config.key_id,
                    model_name=instance.name,
                    upstream_model_name=instance.upstream_model_name,
                    level=instance.level,
                    daily_quota=key_config.daily_quota,
                    daily_request_quota=key_config.daily_request_quota,
                )
            )
    return targets


def find_api_key(endpoint_config: EndpointConfig, key_id: str) -> ApiKeyConfig:
    for api_key in endpoint_config.keys:
        if api_key.id == key_id:
            return api_key
    raise KeyError(f"unknown API key {key_id!r}")


async def fetch_tavily_hotspots(
    api_key: str,
    queries: list[HotspotQuery] | None = None,
) -> dict[str, Any]:
    query_set = queries or HOTSPOT_QUERIES
    headers = build_tavily_headers(api_key)
    async with httpx.AsyncClient(timeout=60) as client:
        results: dict[str, Any] = {}
        for query in query_set:
            response = await client.post(
                TAVILY_SEARCH_URL,
                headers=headers,
                json=build_tavily_search_payload(query),
            )
            response.raise_for_status()
            data = response.json()
            data["title"] = query.title
            results[query.name] = data
        return results


def build_tavily_search_payload(query: HotspotQuery) -> dict[str, Any]:
    return {
        "query": query.query,
        "topic": query.topic,
        "include_answer": "advanced",
        "search_depth": "advanced",
        "max_results": 10,
        "time_range": "day",
    }


def build_tavily_headers(api_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


def format_hotspot_context(tavily_results: dict[str, Any]) -> str:
    sections: list[str] = []
    for query in HOTSPOT_QUERIES:
        data = tavily_results.get(query.name)
        if not data:
            continue
        sections.append(f"## {query.title}")
        sections.append(f"Query: {data.get('query', query.query)}")
        answer = data.get("answer")
        if answer:
            sections.append(f"Tavily answer: {answer}")
        for index, item in enumerate(data.get("results", []), start=1):
            title = item.get("title", "Untitled")
            url = item.get("url", "")
            content = item.get("content", "")
            sections.append(f"{index}. {title}\nURL: {url}\n摘要: {content}")
    return "\n\n".join(sections)


def build_eval_payload(model_name: str, hotspot_context: str, max_tokens: int) -> dict[str, Any]:
    return {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是严谨的中文时事分析助手。只根据用户提供的 Tavily "
                    "搜索材料总结，不编造来源之外的信息。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请基于以下每日热点材料，分别总结国际形势、经济、AI 三类"
                    "相关要点，并给出 3 条需要持续关注的风险或机会。请用中文，"
                    "结构清晰，避免空话。\n\n"
                    f"{hotspot_context}"
                ),
            },
        ],
        "temperature": 0.2,
        # "max_tokens": max_tokens,
    }


async def run_model_eval(
    config: AppConfig,
    usage_manager: UsageManager,
    provider: OpenAICompatibleProvider,
    target: EvalTarget,
    quota_date: str,
    hotspot_context: str,
    max_tokens: int,
) -> ModelEvalResult:
    endpoint_config = config.providers[target.provider].get_endpoint(target.endpoint)
    api_key = find_api_key(endpoint_config, target.key_id)
    payload = build_eval_payload(
        target.upstream_model_name, hotspot_context, max_tokens
    )
    started_at = perf_counter()

    try:
        response = await provider.chat_completion(endpoint_config, api_key, payload)
    except Exception as exc:
        latency_ms = int((perf_counter() - started_at) * 1000)
        usage_manager.log_request(
            request_id=f"daily-eval-{uuid4()}",
            provider=target.provider,
            key_id=target.key_id,
            model_name=target.model_name,
            level=target.level,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            status="error",
            error_message=str(exc),
            latency_ms=latency_ms,
        )
        return ModelEvalResult(
            provider=target.provider,
            endpoint=target.endpoint,
            key_id=target.key_id,
            model_name=target.model_name,
            level=target.level,
            status="error",
            latency_ms=latency_ms,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            usage_missing=False,
            summary="",
            error_message=str(exc),
        )

    latency_ms = int((perf_counter() - started_at) * 1000)
    prompt_tokens, completion_tokens, total_tokens, usage_missing = _extract_usage(
        response.get("usage")
    )
    usage_manager.record_usage(
        provider=target.provider,
        key_id=target.key_id,
        model_name=target.model_name,
        quota_date=quota_date,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    usage_manager.log_request(
        request_id=str(response.get("id") or f"daily-eval-{uuid4()}"),
        provider=target.provider,
        key_id=target.key_id,
        model_name=target.model_name,
        level=target.level,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        status="ok",
        error_message=None,
        latency_ms=latency_ms,
    )
    return ModelEvalResult(
        provider=target.provider,
        endpoint=target.endpoint,
        key_id=target.key_id,
        model_name=target.model_name,
        level=target.level,
        status="ok",
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        usage_missing=usage_missing,
        summary=_extract_summary(response),
        error_message=None,
    )


async def run_model_evals(
    config: AppConfig,
    usage_manager: UsageManager,
    provider: OpenAICompatibleProvider,
    targets: list[EvalTarget],
    quota_date: str,
    hotspot_context: str,
    max_tokens: int,
    concurrency: int,
) -> list[ModelEvalResult]:
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run_one(target: EvalTarget) -> ModelEvalResult:
        async with semaphore:
            return await run_model_eval(
                config=config,
                usage_manager=usage_manager,
                provider=provider,
                target=target,
                quota_date=quota_date,
                hotspot_context=hotspot_context,
                max_tokens=max_tokens,
            )

    return list(await asyncio.gather(*(run_one(target) for target in targets)))


async def run_daily_eval(
    config: AppConfig,
    usage_manager: UsageManager,
    tavily_api_key: str,
    reports_dir: Path | None = None,
    now: datetime | None = None,
    max_tokens: int = 800,
    concurrency: int = 4,
) -> DailyEvalResult:
    current_time = now or datetime.now().astimezone()
    quota_date = quota_date_for(
        current_time,
        config.refresh.timezone,
        config.refresh.daily_reset_hour,
    )
    tavily_results = await fetch_tavily_hotspots(tavily_api_key)
    hotspot_context = format_hotspot_context(tavily_results)
    provider = OpenAICompatibleProvider()
    model_results = await run_model_evals(
        config=config,
        usage_manager=usage_manager,
        provider=provider,
        targets=expand_eval_targets(config),
        quota_date=quota_date,
        hotspot_context=hotspot_context,
        max_tokens=max_tokens,
        concurrency=concurrency,
    )
    result = DailyEvalResult(
        run_date=current_time.date().isoformat(),
        quota_date=quota_date,
        generated_at=current_time.isoformat(),
        tavily_results=tavily_results,
        model_results=model_results,
    )
    write_daily_eval_report(reports_dir or reports_dir_from_env(), result)
    return result


def next_daily_eval_run(
    now: datetime,
    timezone: str,
    schedule_hour: int = DEFAULT_DAILY_EVAL_SCHEDULE_HOUR,
) -> datetime:
    tz = ZoneInfo(timezone)
    local_now = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
    next_run = local_now.replace(
        hour=schedule_hour,
        minute=0,
        second=0,
        microsecond=0,
    )
    if next_run <= local_now:
        next_run += timedelta(days=1)
    return next_run


async def run_daily_eval_scheduler(
    config: AppConfig,
    usage_manager: UsageManager,
    tavily_api_key: str,
    reports_dir: Path | None = None,
    max_tokens: int = DEFAULT_DAILY_EVAL_MAX_TOKENS,
    concurrency: int = DEFAULT_DAILY_EVAL_CONCURRENCY,
    schedule_hour: int = DEFAULT_DAILY_EVAL_SCHEDULE_HOUR,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now_fn: Callable[[], datetime] | None = None,
    run_eval: Callable[..., Awaitable[DailyEvalResult]] = run_daily_eval,
) -> None:
    timezone = config.refresh.timezone
    current_time = now_fn or (lambda: datetime.now(ZoneInfo(timezone)))

    while True:
        now = current_time()
        next_run = next_daily_eval_run(now, timezone, schedule_hour)
        local_now = (
            now.astimezone(next_run.tzinfo)
            if now.tzinfo
            else now.replace(tzinfo=next_run.tzinfo)
        )
        delay_seconds = max(0.0, (next_run - local_now).total_seconds())
        logger.info("daily model evaluation scheduled for %s", next_run.isoformat())
        await sleep_fn(delay_seconds)

        try:
            run_time = current_time()
            local_run_time = (
                run_time.astimezone(next_run.tzinfo)
                if run_time.tzinfo
                else run_time.replace(tzinfo=next_run.tzinfo)
            )
            effective_run_time = next_run if local_run_time < next_run else local_run_time
            await run_eval(
                config=config,
                usage_manager=usage_manager,
                tavily_api_key=tavily_api_key,
                reports_dir=reports_dir,
                now=effective_run_time,
                max_tokens=max_tokens,
                concurrency=concurrency,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("daily model evaluation failed")


def start_daily_eval_scheduler(
    *,
    config: AppConfig,
    usage_manager: UsageManager,
) -> asyncio.Task[None] | None:
    tavily_api_key = os.environ.get("TAVILY_API_KEY")
    if not tavily_api_key:
        logger.warning("TAVILY_API_KEY is not set; daily model evaluation is disabled")
        return None

    return asyncio.create_task(
        run_daily_eval_scheduler(
            config=config,
            usage_manager=usage_manager,
            tavily_api_key=tavily_api_key,
            reports_dir=reports_dir_from_env(),
            max_tokens=_int_from_env(
                "DAILY_EVAL_MAX_TOKENS",
                DEFAULT_DAILY_EVAL_MAX_TOKENS,
            ),
            concurrency=_int_from_env(
                "DAILY_EVAL_CONCURRENCY",
                DEFAULT_DAILY_EVAL_CONCURRENCY,
            ),
        ),
        name="daily-model-eval-scheduler",
    )


def _int_from_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("%s must be an integer; using %s", name, default)
        return default
    return max(1, value)


def write_daily_eval_report(reports_dir: str | Path, result: DailyEvalResult) -> None:
    base_dir = Path(reports_dir)
    run_dir = base_dir / result.run_date
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "tavily.json").write_text(
        json.dumps(result.tavily_results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "results.jsonl").write_text(
        "\n".join(
            json.dumps(asdict(item), ensure_ascii=False) for item in result.model_results
        )
        + ("\n" if result.model_results else ""),
        encoding="utf-8",
    )
    (run_dir / "report.md").write_text(_render_markdown_report(result), encoding="utf-8")
    (base_dir / "latest.json").write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_latest_report(reports_dir: str | Path | None = None) -> DailyEvalResult | None:
    path = Path(reports_dir or reports_dir_from_env()) / "latest.json"
    if not path.exists():
        return None
    return DailyEvalResult.from_dict(json.loads(path.read_text(encoding="utf-8")))


def list_report_history(reports_dir: str | Path | None = None) -> list[str]:
    base_dir = Path(reports_dir or reports_dir_from_env())
    if not base_dir.exists():
        return []
    return sorted(
        [item.name for item in base_dir.iterdir() if item.is_dir()],
        reverse=True,
    )


def render_daily_eval_home(
    result: DailyEvalResult | None,
    history: list[str],
) -> str:
    if result is None:
        body = """
        <section class="empty">
          <h2>暂无每日模型质量评测结果</h2>
          <p>运行 <code>python scripts/daily_model_eval.py</code> 后，这里会显示最新日报。</p>
        </section>
        """
    else:
        body = _render_result_html(result, history)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>每日模型质量评测</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1f2933; background: #f7f8fa; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px 20px 48px; }}
    h1 {{ margin: 0 0 6px; font-size: 28px; }}
    h2 {{ margin-top: 28px; font-size: 20px; }}
    a {{ color: #155eef; }}
    .meta {{ color: #5c6670; margin-bottom: 18px; }}
    .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 18px 0; }}
    .stat {{ background: white; border: 1px solid #d8dee4; border-radius: 6px; padding: 14px; }}
    .stat b {{ display: block; font-size: 22px; margin-bottom: 4px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #d8dee4; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid #e5e9ee; text-align: left; vertical-align: top; }}
    th {{ background: #eef2f6; font-size: 13px; }}
    td {{ font-size: 14px; }}
    .ok {{ color: #0f7b3d; font-weight: 600; }}
    .error {{ color: #b42318; font-weight: 600; }}
    .section {{ background: white; border: 1px solid #d8dee4; border-radius: 6px; padding: 16px; margin: 12px 0; }}
    .summary {{ max-width: 420px; white-space: pre-wrap; }}
    code {{ background: #eef2f6; padding: 2px 5px; border-radius: 4px; }}
  </style>
</head>
<body>
  <main>
    <h1>每日模型质量评测</h1>
    <div class="meta"><a href="/admin/usage">查看 Model Instances 用量</a></div>
    {body}
  </main>
</body>
</html>"""


def _extract_usage(usage: Any) -> tuple[int, int, int, bool]:
    if not isinstance(usage, dict):
        return 0, 0, 0, True
    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion_tokens = int(
        usage.get("completion_tokens") or usage.get("output_tokens") or 0
    )
    total_tokens = int(usage.get("total_tokens") or prompt_tokens + completion_tokens)
    if total_tokens and prompt_tokens == 0 and completion_tokens == 0:
        prompt_tokens = total_tokens
    return prompt_tokens, completion_tokens, total_tokens, False


def _extract_summary(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    return ""


def _render_markdown_report(result: DailyEvalResult) -> str:
    lines = [
        f"# 每日模型质量评测 - {result.run_date}",
        "",
        f"- 生成时间：{result.generated_at}",
        f"- 计入额度日期：{result.quota_date}",
        f"- 成功：{result.ok_count}",
        f"- 失败：{result.error_count}",
        f"- 总 token：{result.total_tokens}",
        "",
        "## Tavily 热点",
    ]
    for name, data in result.tavily_results.items():
        title = data.get("title") or name
        lines.extend(
            [
                "",
                f"### {title}",
                "",
                f"- Query：`{data.get('query', '')}`",
                f"- Answer：{data.get('answer', '')}",
            ]
        )
    lines.extend(["", "## 模型结果", ""])
    lines.append(
        "| Provider | Endpoint | Key | Model | Status | Latency | Tokens |"
    )
    lines.append("| --- | --- | --- | --- | --- | ---: | ---: |")
    for item in result.model_results:
        lines.append(
            "| "
            f"{item.provider} | {item.endpoint} | {item.key_id} | "
            f"{item.model_name} | {item.status} | {item.latency_ms or 0} | "
            f"{item.total_tokens} |"
        )
    for item in result.model_results:
        detail = item.error_message or item.summary
        if detail:
            lines.extend(["", f"### {item.provider} / {item.model_name} / {item.key_id}", ""])
            lines.append(detail)
    return "\n".join(lines) + "\n"


def _render_result_html(result: DailyEvalResult, history: list[str]) -> str:
    hotspots = []
    for name, data in result.tavily_results.items():
        title = escape(str(data.get("title") or name))
        answer = escape(str(data.get("answer") or ""))
        query = escape(str(data.get("query") or ""))
        hotspots.append(
            f'<div class="section"><h3>{title}</h3><p><code>{query}</code></p><p>{answer}</p></div>'
        )
    rows = []
    for item in result.model_results:
        status_class = "ok" if item.status == "ok" else "error"
        rows.append(
            "<tr>"
            f"<td>{escape(item.provider)}</td>"
            f"<td>{escape(item.endpoint)}</td>"
            f"<td>{escape(item.key_id)}</td>"
            f"<td>{escape(item.model_name)}</td>"
            f'<td class="{status_class}">{escape(item.status)}</td>'
            f"<td>{item.latency_ms or ''}</td>"
            f"<td>{item.total_tokens}</td>"
            f'<td class="summary">{escape(item.error_message or item.summary)}</td>'
            "</tr>"
        )
    history_links = " ".join(f"<code>{escape(day)}</code>" for day in history[:14])
    return f"""
<div class="meta">生成时间：{escape(result.generated_at)}；计入额度日期：{escape(result.quota_date)}</div>
<div class="stats">
  <div class="stat"><b>{result.ok_count}</b>成功模型 key</div>
  <div class="stat"><b>{result.error_count}</b>失败模型 key</div>
  <div class="stat"><b>{result.total_tokens}</b>总 token</div>
</div>
<h2>热点摘要</h2>
{''.join(hotspots)}
<h2>模型对比</h2>
<table>
  <thead>
    <tr><th>Provider</th><th>Endpoint</th><th>Key</th><th>Model</th><th>Status</th><th>Latency ms</th><th>Tokens</th><th>摘要 / 错误</th></tr>
  </thead>
  <tbody>{''.join(rows)}</tbody>
</table>
<h2>历史报告</h2>
<div>{history_links}</div>
"""
