from __future__ import annotations

import sqlite3
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import backtest, collector, repository, worker
from .clash import build_clash_controller
from .database import connect, initialize_database
from .notifier import Notifier, build_notifier
from .proxy_check import build_proxy_runtime_status, rotate_clash_proxy, run_proxy_check
from .proxy_pool import build_proxy_pool
from .request_profiles import build_request_profile_pool
from .settings import load_settings
from .time_utils import format_beijing
from .trends import (
    AVAILABLE_TIMEFRAMES,
    CONTEXT_TIMEFRAMES,
    DEFAULT_GEO,
    MONITORED_TIMEFRAMES,
    NOW_TIMEFRAMES,
    PytrendsProvider,
    SHORT_TIMEFRAME,
    TrendsProvider,
)

APP_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
templates.env.filters["bjtime"] = format_beijing
router = APIRouter()


def create_app(
    db_path: str | Path | None = None,
    trends_provider: TrendsProvider | None = None,
    notifier: Notifier | None = None,
    start_scheduler: bool | None = None,
    request_delay_seconds: float | None = None,
    retry_delay_seconds: int | None = None,
    max_attempts: int | None = None,
    public_base_url: str | None = None,
    p1_alert_cooldown_hours: int | None = None,
    p2_alert_cooldown_hours: int | None = None,
) -> FastAPI:
    settings = load_settings()
    resolved_db_path = Path(db_path) if db_path is not None else settings.db_path
    scheduler_enabled = settings.scheduler_enabled if start_scheduler is None else start_scheduler
    resolved_notifier = notifier or build_notifier(settings.feishu_webhook_url)
    resolved_request_delay = (
        settings.request_delay_seconds
        if request_delay_seconds is None
        else request_delay_seconds
    )
    resolved_retry_delay = (
        settings.retry_delay_seconds if retry_delay_seconds is None else retry_delay_seconds
    )
    resolved_max_attempts = settings.max_attempts if max_attempts is None else max_attempts
    resolved_public_base_url = (
        settings.public_base_url if public_base_url is None else public_base_url
    )
    resolved_p1_cooldown = (
        settings.p1_alert_cooldown_hours
        if p1_alert_cooldown_hours is None
        else p1_alert_cooldown_hours
    )
    resolved_p2_cooldown = (
        settings.p2_alert_cooldown_hours
        if p2_alert_cooldown_hours is None
        else p2_alert_cooldown_hours
    )
    clash_controller = build_runtime_clash_controller(settings)
    resolved_proxy_urls = settings.proxy_urls
    if resolved_proxy_urls is None and clash_controller is not None:
        resolved_proxy_urls = settings.clash_proxy_url
    proxy_pool = build_proxy_pool(
        proxy_urls=resolved_proxy_urls,
        subscription_url=settings.proxy_subscription_url,
        refresh_seconds=settings.proxy_refresh_seconds,
        auto_detect_proxy_url=(
            settings.clash_proxy_url if settings.proxy_auto_detect_local_clash else None
        ),
    )
    request_profile_pool = build_request_profile_pool(
        enabled=settings.request_profiles_enabled,
    )

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
        initialize_database(app_instance.state.db_path)
        with connect(app_instance.state.db_path) as conn:
            repository.delete_unmonitored_queued_jobs(conn)
        if scheduler_enabled:
            scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
            scheduler.add_job(
                collector.enqueue_enabled_jobs_for_path,
                trigger="cron",
                minute=5,
                id="hourly_short_trends_collection",
                replace_existing=True,
                kwargs={
                    "db_path": app_instance.state.db_path,
                    "source": "scheduled_short",
                    "max_attempts": app_instance.state.max_attempts,
                    "timeframes": NOW_TIMEFRAMES,
                },
            )
            scheduler.add_job(
                collector.enqueue_enabled_jobs_for_path,
                trigger="cron",
                hour=2,
                minute=0,
                id="daily_context_trends_collection",
                replace_existing=True,
                kwargs={
                    "db_path": app_instance.state.db_path,
                    "source": "scheduled_context",
                    "max_attempts": app_instance.state.max_attempts,
                    "timeframes": CONTEXT_TIMEFRAMES,
                },
            )
            scheduler.add_job(
                worker.start_worker,
                trigger="interval",
                minutes=1,
                id="collection_worker",
                replace_existing=True,
                kwargs={"app": app_instance},
            )
            scheduler.start()
            app_instance.state.scheduler = scheduler

        try:
            yield
        finally:
            scheduler = app_instance.state.scheduler
            if scheduler is not None:
                scheduler.shutdown(wait=False)

    app = FastAPI(title="Google Trends Monitor MVP", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
    app.state.db_path = resolved_db_path
    app.state.settings = settings
    app.state.proxy_pool = proxy_pool
    app.state.clash_controller = clash_controller
    app.state.request_profile_pool = request_profile_pool
    app.state.trends_provider = trends_provider or PytrendsProvider(
        proxy_pool=proxy_pool,
        request_profile_pool=request_profile_pool,
        clash_controller=clash_controller,
        clash_rotate_on_429=settings.clash_rotate_on_429,
        clash_rotate_on_error=settings.clash_rotate_on_error,
        clash_retry_after_rotate=settings.clash_retry_after_rotate,
    )
    app.state.notifier = resolved_notifier
    app.state.scheduler = None
    app.state.worker_lock = threading.Lock()
    app.state.request_delay_seconds = resolved_request_delay
    app.state.retry_delay_seconds = resolved_retry_delay
    app.state.max_attempts = resolved_max_attempts
    app.state.monitored_timeframes = MONITORED_TIMEFRAMES
    app.state.available_timeframes = AVAILABLE_TIMEFRAMES
    app.state.public_base_url = resolved_public_base_url
    app.state.p1_alert_cooldown_hours = resolved_p1_cooldown
    app.state.p2_alert_cooldown_hours = resolved_p2_cooldown

    app.include_router(router)
    return app


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def build_runtime_clash_controller(settings):
    if settings.clash_enabled:
        return build_clash_controller(
            enabled=True,
            controller_url=settings.clash_controller_url,
            secret=settings.clash_secret,
            proxy_group=settings.clash_proxy_group,
            config_path=settings.clash_config_path,
            skip_proxy_names=settings.clash_skip_proxy_names,
            allowed_proxy_name_keywords=settings.clash_allowed_proxy_name_keywords,
        )
    if not settings.proxy_auto_detect_local_clash:
        return None
    try:
        return build_clash_controller(
            enabled=True,
            controller_url=settings.clash_controller_url,
            secret=settings.clash_secret,
            proxy_group=settings.clash_proxy_group,
            config_path=settings.clash_config_path,
            skip_proxy_names=settings.clash_skip_proxy_names,
            allowed_proxy_name_keywords=settings.clash_allowed_proxy_name_keywords,
        )
    except Exception:
        return None


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/proxy-check", response_class=HTMLResponse)
def proxy_check_page(request: Request) -> HTMLResponse:
    return render_proxy_check(request)


@router.post("/proxy-check", response_class=HTMLResponse)
def run_proxy_check_page(request: Request) -> HTMLResponse:
    result = run_proxy_check(
        request.app.state.settings,
        request.app.state.proxy_pool,
    )
    return render_proxy_check(request, result=result)


@router.post("/proxy-check/rotate-clash", response_class=HTMLResponse)
def rotate_clash_proxy_page(request: Request) -> HTMLResponse:
    rotate_result = rotate_clash_proxy(
        request.app.state.settings,
        request.app.state.clash_controller,
    )
    return render_proxy_check(request, rotate_result=rotate_result)


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    error: str | None = None,
    message: str | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "keyword_items": build_keyword_list_items(conn, repository.list_keywords(conn)),
            "error": error,
            "message": message,
        },
    )


@router.post("/keywords")
def add_keyword(
    term: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    try:
        repository.create_keyword(conn, term)
    except ValueError as exc:
        return redirect("/", error=str(exc))
    except sqlite3.IntegrityError:
        return redirect("/", error="Keyword already exists.")
    return redirect("/", message="Keyword added.")


@router.post("/keywords/{keyword_id}/toggle")
def toggle_keyword(
    keyword_id: int,
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    ensure_keyword(conn, keyword_id)
    repository.toggle_keyword(conn, keyword_id)
    return redirect("/", message="Keyword updated.")


@router.post("/keywords/{keyword_id}/remark")
def update_keyword_remark(
    keyword_id: int,
    remark: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, str]:
    updated = repository.update_keyword_remark(conn, keyword_id, remark)
    if updated is None:
        raise HTTPException(status_code=404, detail="Keyword not found.")
    return {"remark": updated["remark"]}


@router.post("/keywords/{keyword_id}/timeframes")
async def update_keyword_timeframes(
    keyword_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    ensure_keyword(conn, keyword_id)
    form = await request.form()
    selected = [
        value
        for value in form.getlist("timeframes")
        if isinstance(value, str) and value in AVAILABLE_TIMEFRAMES
    ]
    if not selected:
        return redirect(f"/keywords/{keyword_id}", error="At least one timeframe must be selected.")
    repository.update_keyword_timeframes(conn, keyword_id, selected)
    next_url = form.get("next_url")
    target = (
        str(next_url)
        if isinstance(next_url, str) and next_url.startswith("/") and not next_url.startswith("//")
        else f"/keywords/{keyword_id}"
    )
    return redirect(target, message="Keyword timeframes updated.")


@router.post("/keywords/{keyword_id}/delete")
def delete_keyword(
    keyword_id: int,
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    ensure_keyword(conn, keyword_id)
    repository.delete_keyword(conn, keyword_id)
    return redirect("/", message="Keyword deleted.")


@router.get("/keywords/{keyword_id}", response_class=HTMLResponse)
def keyword_detail(
    keyword_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    timeframe: str = SHORT_TIMEFRAME,
) -> HTMLResponse:
    keyword = ensure_keyword(conn, keyword_id)
    if timeframe not in AVAILABLE_TIMEFRAMES:
        timeframe = SHORT_TIMEFRAME
    points = repository.list_trend_points(conn, keyword_id, timeframe=timeframe)
    chart = build_chart(points)
    runs = [
        run for run in repository.list_jobs(conn, limit=25) if run["keyword_id"] == keyword_id
    ][:10]
    return templates.TemplateResponse(
        request=request,
        name="keyword_detail.html",
        context={
            "request": request,
            "keyword": keyword,
            "points": points,
            "chart": chart,
            "runs": runs,
            "timeframe": timeframe,
            "timeframes": AVAILABLE_TIMEFRAMES,
            "selected_timeframes": repository.keyword_timeframes(keyword),
        },
    )


@router.post("/keywords/{keyword_id}/collect")
def collect_one(
    keyword_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    keyword = ensure_keyword(conn, keyword_id)
    selected_timeframes = repository.keyword_timeframes(keyword)
    jobs = collector.enqueue_keyword_jobs(
        conn,
        keyword_id=keyword_id,
        source="manual",
        max_attempts=request.app.state.max_attempts,
        timeframes=selected_timeframes,
    )
    worker.start_worker(request.app)
    return redirect(
        f"/keywords/{keyword_id}",
        message=(
            f"Queued {len(jobs)} collection jobs for: "
            f"{', '.join(selected_timeframes)}."
        ),
    )


@router.post("/collect/run-now")
def collect_now(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    jobs = collector.enqueue_enabled_jobs(
        conn,
        source="manual",
        max_attempts=request.app.state.max_attempts,
    )
    started = worker.start_worker(request.app)
    worker_status = "worker started" if started else "worker already running"
    return redirect(
        "/runs",
        message=f"Queued {len(jobs)} collection jobs; {worker_status}.",
    )


@router.get("/runs", response_class=HTMLResponse)
def runs(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    message: str | None = None,
    status: str | None = None,
) -> HTMLResponse:
    runs = repository.list_jobs(conn, status=status)
    attempts_by_job_id = {
        run["id"]: repository.list_collection_job_attempts(conn, run["id"])
        for run in runs
        if run["attempts"] > 1
    }
    return templates.TemplateResponse(
        request=request,
        name="runs.html",
        context={
            "request": request,
            "runs": runs,
            "attempts_by_job_id": attempts_by_job_id,
            "message": message,
            "status": status,
        },
    )


@router.get("/alerts", response_class=HTMLResponse)
def alerts(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="alerts.html",
        context={
            "request": request,
            "alerts": repository.list_alerts(conn),
        },
    )


@router.post("/alerts/{alert_id}/remark")
def update_alert_remark(
    alert_id: int,
    remark: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, str]:
    updated = repository.update_alert_remark(conn, alert_id, remark)
    if updated is None:
        raise HTTPException(status_code=404, detail="Alert not found.")
    return {"remark": updated["remark"]}


@router.get("/backtest", response_class=HTMLResponse)
def backtest_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
    keyword_id: int | None = None,
    timeframe: str = SHORT_TIMEFRAME,
) -> HTMLResponse:
    keywords = repository.list_keywords(conn)
    if timeframe not in AVAILABLE_TIMEFRAMES:
        timeframe = SHORT_TIMEFRAME

    selected_keyword_id = keyword_id
    if selected_keyword_id is None and keywords:
        selected_keyword_id = keywords[0]["id"]
    elif selected_keyword_id is not None and not any(
        keyword["id"] == selected_keyword_id for keyword in keywords
    ):
        raise HTTPException(status_code=404, detail="Keyword not found.")

    result = None
    if selected_keyword_id is not None:
        result = backtest.run_keyword_backtest(
            conn,
            keyword_id=selected_keyword_id,
            timeframe=timeframe,
            p1_alert_cooldown_hours=request.app.state.p1_alert_cooldown_hours,
            p2_alert_cooldown_hours=request.app.state.p2_alert_cooldown_hours,
        )

    return templates.TemplateResponse(
        request=request,
        name="backtest.html",
        context={
            "request": request,
            "keywords": keywords,
            "result": result,
            "selected_keyword_id": selected_keyword_id,
            "timeframe": timeframe,
            "timeframes": AVAILABLE_TIMEFRAMES,
        },
    )


def ensure_keyword(conn: sqlite3.Connection, keyword_id: int) -> sqlite3.Row:
    keyword = repository.get_keyword(conn, keyword_id)
    if keyword is None:
        raise HTTPException(status_code=404, detail="Keyword not found.")
    return keyword


def redirect(path: str, message: str | None = None, error: str | None = None) -> RedirectResponse:
    params = []
    if message:
        params.append(("message", message))
    if error:
        params.append(("error", error))
    if params:
        from urllib.parse import urlencode

        path = f"{path}?{urlencode(params)}"
    return RedirectResponse(path, status_code=303)


def render_proxy_check(
    request: Request,
    result: object | None = None,
    rotate_result: object | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="proxy_check.html",
        context={
            "request": request,
            "status": build_proxy_runtime_status(
                request.app.state.settings,
                request.app.state.proxy_pool,
                request.app.state.clash_controller,
            ),
            "result": result,
            "rotate_result": rotate_result,
        },
    )


def build_chart(points: list[sqlite3.Row]) -> dict[str, object]:
    return build_chart_with_size(points, width=900, height=280, padding_x=32, padding_y=20)


def build_chart_with_size(
    points: list[sqlite3.Row],
    width: int,
    height: int,
    padding_x: int,
    padding_y: int,
) -> dict[str, object]:
    if not points:
        return {
            "width": width,
            "height": height,
            "path": "",
            "points": [],
            "point_radius": 2.6,
        }

    usable_width = width - (padding_x * 2)
    usable_height = height - (padding_y * 2)
    max_index = max(len(points) - 1, 1)
    coords: list[dict[str, object]] = []
    for index, point in enumerate(points):
        value = int(point["value"])
        x = padding_x + (usable_width * index / max_index)
        y = padding_y + usable_height - (usable_height * value / 100)
        coords.append(
            {
                "x": round(x, 2),
                "y": round(y, 2),
                "value": value,
                "date": point["point_date"],
                "display_date": format_beijing(
                    point["point_date"], timeframe=point["timeframe"]
                ),
            }
        )
    path = " ".join(
        f"{'M' if index == 0 else 'L'} {coord['x']} {coord['y']}"
        for index, coord in enumerate(coords)
    )
    if height <= 100:
        point_radius = 1.5 if len(points) > 80 else 2.0
    else:
        point_radius = 2.4 if len(points) > 120 else 3.2
    return {
        "width": width,
        "height": height,
        "path": path,
        "points": coords,
        "point_radius": point_radius,
    }


def build_keyword_list_items(
    conn: sqlite3.Connection,
    keywords: list[sqlite3.Row],
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for keyword in keywords:
        timeframe, points = select_preview_points(conn, keyword)
        chart = build_chart_with_size(
            points,
            width=340,
            height=96,
            padding_x=10,
            padding_y=10,
        )
        items.append(
            {
                "keyword": keyword,
                "preview_timeframe": timeframe,
                "preview_points": points,
                "preview_latest": points[-1] if points else None,
                "chart": chart,
            }
        )
    return items


def select_preview_points(
    conn: sqlite3.Connection,
    keyword: sqlite3.Row,
) -> tuple[str, list[sqlite3.Row]]:
    selected = set(repository.keyword_timeframes(keyword))
    ordered_timeframes = [item for item in AVAILABLE_TIMEFRAMES if item in selected]
    if not ordered_timeframes:
        ordered_timeframes = list(MONITORED_TIMEFRAMES)

    timeframe = ordered_timeframes[0]
    points = repository.list_trend_points(
        conn,
        keyword["id"],
        limit=80,
        timeframe=timeframe,
    )
    return timeframe, points


app = create_app()
