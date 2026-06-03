#!/usr/bin/env python
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from token_router.app.config import load_config  # noqa: E402
from token_router.app.daily_eval import run_daily_eval  # noqa: E402
from token_router.app.database import init_db  # noqa: E402
from token_router.app.usage import UsageManager  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the daily Tavily-backed model quality evaluation."
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("TOKEN_ROUTER_CONFIG", "config.yaml"),
        help="Router config path. Defaults to TOKEN_ROUTER_CONFIG or config.yaml.",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("TOKEN_ROUTER_DB", "token_router.sqlite3"),
        help="SQLite usage DB path. Defaults to TOKEN_ROUTER_DB or token_router.sqlite3.",
    )
    parser.add_argument(
        "--reports-dir",
        default=os.environ.get(
            "TOKEN_ROUTER_REPORTS_DIR", "reports/daily-model-eval"
        ),
        help=(
            "Report output directory. Defaults to TOKEN_ROUTER_REPORTS_DIR "
            "or reports/daily-model-eval."
        ),
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=int(os.environ.get("DAILY_EVAL_MAX_TOKENS", "100000")),
        help="Maximum completion tokens per model/key evaluation request.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    tavily_api_key = os.environ.get("TAVILY_API_KEY")
    if not tavily_api_key:
        raise SystemExit("Missing TAVILY_API_KEY in environment or .env.")

    init_db(args.db)
    usage_manager = UsageManager(args.db)
    result = asyncio.run(
        run_daily_eval(
            config=config,
            usage_manager=usage_manager,
            tavily_api_key=tavily_api_key,
            reports_dir=Path(args.reports_dir),
            max_tokens=args.max_tokens,
        )
    )
    print(
        json.dumps(
            {
                "run_date": result.run_date,
                "quota_date": result.quota_date,
                "ok_count": result.ok_count,
                "error_count": result.error_count,
                "total_tokens": result.total_tokens,
                "reports_dir": str(Path(args.reports_dir)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
