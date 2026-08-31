from __future__ import annotations

import logging
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..awards import COLUMNS
from ..excel import build_workbook
from ..grid import Grid
from ..models import CellStatus
from ..pdf import build_pdf
from .. import theme
from .auth import SESSION_COOKIE, Auth
from .db import Database
from .notify import EmailChannel, Message, Reminders
from .repository import (
    DuplicateMemberCode,
    ScanRepository,
    StaffRepository,
    grid_rows_to_member_rows,
    rows_from_scan,
)
from .scans import TIMEZONE, ScanRunner, verify_member_code
from .scheduler import Scheduler
from .settings import Settings, load_settings

logger = logging.getLogger(__name__)
TEMPLATES = Path(__file__).parent / "templates"


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or load_settings()
    database = database or Database(settings.database_path)
    staff_repo = StaffRepository(database)
    scan_repo = ScanRepository(database)
    auth = Auth(database, settings)
    runner = ScanRunner(staff_repo, scan_repo)
    email_channel = EmailChannel(settings)
    reminders = Reminders(database, settings, [email_channel])
    templates = Jinja2Templates(directory=str(TEMPLATES))
    scheduler = Scheduler(settings, runner, scan_repo, reminders, auth=auth)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        auth.purge_expired()
        if os.environ.get("DISABLE_SCHEDULER", "").strip().casefold() not in {"1", "true", "yes"}:
            scheduler.start()
        yield
        scheduler.stop()

    app = FastAPI(
        title="Aquatic Centre Certifications", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.scheduler = scheduler
    app.state.settings = settings
    app.state.database = database
    app.state.staff_repo = staff_repo
    app.state.scan_repo = scan_repo
    app.state.runner = runner
    app.state.reminders = reminders
    app.state.auth = auth

    for warning in settings.warnings:
        logger.warning(warning)

    def current_user(request: Request) -> str:
        email = auth.read_session(request.cookies.get(SESSION_COOKIE))
        if email is None:
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        return email

    def render(request: Request, template: str, **context) -> HTMLResponse:
        return templates.TemplateResponse(
            request, template, {"columns": COLUMNS, "theme": theme, **context}
        )

    # --- authentication ------------------------------------------------
    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, sent: bool = False, denied: bool = False):
        return render(request, "login.html", sent=sent, denied=denied)

    @app.post("/login")
    def login_submit(request: Request, email: str = Form(...)):
        client = request.client.host if request.client else "unknown"
        token = auth.issue_login_token(email, client=client)
        if token:
            link = f"{settings.base_url}/auth?token={token}"
            try:
                email_channel.send(
                    Message(
                        to=email.strip(),
                        subject="Your certification dashboard sign-in link",
                        body=f"Sign in here (valid 15 minutes, single use):\n\n{link}\n",
                    )
                )
            except Exception:  # noqa: BLE001 - never reveal delivery state to the caller
                logger.exception("Login link delivery failed")
        # Telling the caller their address is not approved is a deliberate trade of
        # security for clarity: it lets someone probe which addresses are managers.
        # The per-address and per-IP rate limits are what keep that probing slow.
        # Revert to a single response for both cases to close the oracle.
        if not settings.is_manager(email):
            logger.info("Rejected sign-in attempt for an address that is not a manager")
            return RedirectResponse("/login?denied=1", status_code=303)
        return RedirectResponse("/login?sent=1", status_code=303)

    @app.get("/auth")
    def auth_callback(token: str = ""):
        email = auth.redeem(token)
        if email is None:
            return RedirectResponse("/login", status_code=303)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            auth.create_session(email),
            httponly=True,
            secure=settings.base_url.startswith("https"),
            samesite="lax",
            max_age=30 * 86400,
        )
        return response

    @app.post("/logout")
    def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        return response

    # --- dashboard -----------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, user: str = Depends(current_user)):
        scan_id = scan_repo.latest_complete_id()
        rows = rows_from_scan(database, scan_id) if scan_id else []
        return render(
            request,
            "dashboard.html",
            user=user,
            rows=rows,
            latest=scan_repo.latest(),
            running=runner.running,
            statuses=CellStatus,
            has_scan=scan_id is not None,
        )

    @app.post("/scan")
    def start_scan(user: str = Depends(current_user)):
        runner.start(triggered_by=user)
        return RedirectResponse("/", status_code=303)

    @app.get("/scan/status")
    def scan_status(user: str = Depends(current_user)):
        return {"running": runner.running, "latest": scan_repo.latest()}

    # --- roster --------------------------------------------------------
    @app.get("/staff", response_class=HTMLResponse)
    def staff_list(request: Request, user: str = Depends(current_user), error: str = ""):
        return render(request, "staff.html", user=user, staff=staff_repo.active(), error=error)

    @app.post("/staff")
    def staff_add(
        user: str = Depends(current_user),
        name: str = Form(...),
        member_code: str = Form(...),
        email: str = Form(""),
        phone: str = Form(""),
        away: bool = Form(False),
    ):
        code = member_code.strip().upper()
        if not code.isalnum():
            return _staff_error("Member ID must be letters and digits only.")
        # Verify against the Society now, so a typo is caught at entry rather than
        # surfacing as an empty row after the next scan.
        result = verify_member_code(code, name)
        if not result.ok:
            return _staff_error(f"{code}: {result.error}")
        try:
            staff_repo.add(
                name=name,
                member_code=code,
                society_name=result.society_name,
                email=email.strip() or None,
                phone=phone.strip() or None,
                away=away,
                actor=user,
            )
        except DuplicateMemberCode as exc:
            return _staff_error(str(exc))
        return RedirectResponse("/staff", status_code=303)

    @app.post("/staff/{staff_id}/adopt-name")
    def staff_adopt_name(staff_id: int, user: str = Depends(current_user)):
        staff = staff_repo.get(staff_id)
        if staff and staff.society_name:
            staff_repo.update(staff_id, actor=user, name=staff.society_name)
        return RedirectResponse("/staff", status_code=303)

    @app.post("/staff/{staff_id}/remove")
    def staff_remove(staff_id: int, user: str = Depends(current_user)):
        staff_repo.remove(staff_id, actor=user)
        return RedirectResponse("/staff", status_code=303)

    def _staff_error(message: str) -> RedirectResponse:
        from urllib.parse import quote

        return RedirectResponse(f"/staff?error={quote(message)}", status_code=303)

    # --- exports -------------------------------------------------------
    def _grid() -> Grid:
        scan_id = scan_repo.latest_complete_id()
        if scan_id is None:
            raise HTTPException(status_code=404, detail="No completed scan yet.")
        rows = grid_rows_to_member_rows(rows_from_scan(database, scan_id), COLUMNS)
        scan = scan_repo.latest() or {}
        finished = scan.get("finished_at") or datetime.now(TIMEZONE).isoformat()
        generated = datetime.fromisoformat(finished)
        notes = scan_repo.notes(scan_id)
        return Grid(
            as_of=generated.date(),
            generated_at=generated,
            rows=tuple(rows),
            unmapped_awards=tuple(n["detail"] for n in notes if n["kind"] == "unmapped"),
            disagreements=tuple(n["detail"] for n in notes if n["kind"] == "disagreement"),
        )

    @app.get("/export.xlsx")
    def export_excel(user: str = Depends(current_user)):
        return _download(build_workbook, "xlsx", _grid())

    @app.get("/export.pdf")
    def export_pdf(user: str = Depends(current_user)):
        return _download(build_pdf, "pdf", _grid())

    def _download(builder, suffix: str, grid: Grid) -> Response:
        media = {
            "pdf": "application/pdf",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }[suffix]
        with tempfile.TemporaryDirectory(prefix="lss-export-") as directory:
            path = Path(directory) / f"certifications.{suffix}"
            builder(grid, path)
            payload = path.read_bytes()
        filename = f"certifications-{grid.as_of.isoformat()}.{suffix}"
        return Response(
            payload,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # --- diagnostics and reminders -------------------------------------
    @app.get("/diagnostics", response_class=HTMLResponse)
    def diagnostics(request: Request, user: str = Depends(current_user)):
        scan_id = scan_repo.latest_complete_id()
        return render(
            request,
            "diagnostics.html",
            user=user,
            notes=scan_repo.notes(scan_id) if scan_id else [],
        )

    @app.get("/reminders", response_class=HTMLResponse)
    def reminders_page(request: Request, user: str = Depends(current_user)):
        scan_id = scan_repo.latest_complete_id()
        today = datetime.now(TIMEZONE).date()
        return render(
            request,
            "reminders.html",
            user=user,
            upcoming=scan_repo.upcoming(scan_id, settings.reminder_days, today)
            if scan_id
            else [],
            history=scan_repo.history(),
            thresholds=settings.reminder_days,
            reminder_hour=settings.reminder_hour,
            has_scan=scan_id is not None,
        )

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app
