from __future__ import annotations

import logging
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
# Starlette's class, not FastAPI's subclass of it: a hand-parsed form yields the
# former, which is not an instance of the latter.
from starlette.datastructures import UploadFile

from ..awards import COLUMNS
from ..excel import build_workbook
from ..grid import Grid
from ..models import CellStatus
from ..pdf import build_pdf
from .. import theme
from .auth import PENDING_COOKIE, SESSION_COOKIE, Auth
from .db import Database
from .files import (
    ACCEPT_ATTRIBUTE,
    MAX_BYTES,
    TOO_LARGE,
    FileStore,
    RejectedUpload,
    clean_filename,
)
from .notify import EmailChannel, Message, Reminders
from .repository import (
    DuplicateMemberCode,
    ScanRepository,
    StaffRepository,
    grid_rows_to_member_rows,
    rows_from_scan,
)
from .scans import TIMEZONE, ScanRunner, verify_member_code, verify_red_cross_number
from .scheduler import Scheduler
from .settings import LOGIN_CODE_MINUTES, Settings, load_settings

logger = logging.getLogger(__name__)
TEMPLATES = Path(__file__).parent / "templates"


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or load_settings()
    database = database or Database(settings.database_path)
    staff_repo = StaffRepository(database)
    scan_repo = ScanRepository(database)
    auth = Auth(database, settings)
    runner = ScanRunner(staff_repo, scan_repo)
    store = FileStore(settings.uploads_path)
    email_channel = EmailChannel(settings)
    reminders = Reminders(database, settings, [email_channel])
    templates = Jinja2Templates(directory=str(TEMPLATES))
    scheduler = Scheduler(settings, runner, scan_repo, reminders, auth=auth)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        auth.purge_expired()
        abandoned = scan_repo.abandon_running()
        if abandoned:
            logger.warning("Marked %d scan(s) failed that a restart interrupted", abandoned)
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
    app.state.file_store = store
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
            request,
            template,
            {
                "columns": COLUMNS,
                "theme": theme,
                "accept": ACCEPT_ATTRIBUTE,
                "max_upload_mb": MAX_BYTES // (1024 * 1024),
                **context,
            },
        )

    # --- authentication ------------------------------------------------
    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, sent: bool = False, denied: bool = False, bad: bool = False):
        return render(request, "login.html", sent=sent, denied=denied, bad=bad)

    @app.post("/login")
    def login_submit(request: Request, email: str = Form(...)):
        client = request.client.host if request.client else "unknown"
        code = auth.issue_login_code(email, client=client)
        if code:
            try:
                email_channel.send(
                    Message(
                        to=email.strip(),
                        subject="Your certification dashboard sign-in code",
                        body=(
                            f"Your sign-in code is {code}\n\n"
                            f"It works once and expires in {LOGIN_CODE_MINUTES} minutes.\n"
                            "If you did not ask to sign in, ignore this message.\n"
                        ),
                    )
                )
            except Exception:  # noqa: BLE001 - never reveal delivery state to the caller
                logger.exception("Login code delivery failed")
        # Telling the caller their address is not approved is a deliberate trade of
        # security for clarity: it lets someone probe which addresses are managers.
        # The per-address and per-IP rate limits are what keep that probing slow.
        # Revert to a single response for both cases to close the oracle.
        if not settings.is_manager(email):
            logger.info("Rejected sign-in attempt for an address that is not a manager")
            return RedirectResponse("/login?denied=1", status_code=303)
        response = RedirectResponse("/login?sent=1", status_code=303)
        # The code form has to know which address the code went to. Carrying it in a
        # signed cookie keeps it out of the URL and means the reader does not retype it.
        response.set_cookie(
            PENDING_COOKIE,
            auth.create_pending(email),
            httponly=True,
            secure=settings.base_url.startswith("https"),
            samesite="lax",
            max_age=LOGIN_CODE_MINUTES * 60,
        )
        return response

    @app.post("/verify")
    def verify_code(request: Request, code: str = Form(...)):
        client = request.client.host if request.client else "unknown"
        email = auth.read_pending(request.cookies.get(PENDING_COOKIE))
        if email is None:
            # The pending cookie expired or was never set: start again from the address.
            return RedirectResponse("/login", status_code=303)
        signed_in = auth.redeem(email, code, client=client)
        if signed_in is None:
            return RedirectResponse("/login?sent=1&bad=1", status_code=303)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            auth.create_session(signed_in),
            httponly=True,
            secure=settings.base_url.startswith("https"),
            samesite="lax",
            max_age=30 * 86400,
        )
        response.delete_cookie(PENDING_COOKIE)
        return response

    @app.post("/logout")
    def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        response.delete_cookie(PENDING_COOKIE)
        return response

    # --- dashboard -----------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request, user: str = Depends(current_user)):
        scan_id = scan_repo.latest_complete_id()
        today = datetime.now(TIMEZONE).date()
        rows = rows_from_scan(database, scan_id, today) if scan_id else []
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
        roster = staff_repo.active()
        return render(
            request,
            "staff.html",
            user=user,
            staff=roster,
            # The edit panel is server-rendered per row, so its manual dates come
            # down with the page rather than through a second request.
            manual={member.id: staff_repo.manual_certs(member.id) for member in roster},
            documents={member.id: staff_repo.files(member.id) for member in roster},
            error=error,
        )

    @app.post("/staff")
    async def staff_add(request: Request, user: str = Depends(current_user)):
        form = await request.form()
        name = " ".join(str(form.get("name", "")).split())
        code = str(form.get("member_code", "")).strip().upper()
        red_cross = str(form.get("red_cross_number", "")).strip() or None

        if not name:
            return _staff_error("A name is required.")
        if not code.isalnum():
            return _staff_error("Member ID must be letters and digits only.")
        if red_cross and not red_cross.isdigit():
            return _staff_error("A Red Cross certificate number must be digits only.")
        try:
            manual = _manual_dates(form)
        except ValueError as exc:
            return _staff_error(str(exc))

        # Verify against the Society now, so a typo is caught at entry rather than
        # surfacing as an empty row after the next scan.
        result = verify_member_code(code, name)
        if not result.ok:
            return _staff_error(f"{code}: {result.error}")
        if red_cross:
            certificate = verify_red_cross_number(red_cross, name)
            if not certificate.ok:
                return _staff_error(f"Red Cross {red_cross}: {certificate.error}")
        try:
            member = staff_repo.add(
                name=name,
                member_code=code,
                society_name=result.society_name,
                email=str(form.get("email", "")).strip() or None,
                phone=str(form.get("phone", "")).strip() or None,
                red_cross_number=red_cross,
                away=bool(form.get("away")),
                actor=user,
            )
        except DuplicateMemberCode as exc:
            return _staff_error(str(exc))
        for column_code, certified in manual.items():
            staff_repo.set_manual_cert(member.id, column_code, certified, actor=user)
        return RedirectResponse("/staff", status_code=303)

    @app.post("/staff/{staff_id}/edit")
    async def staff_edit(staff_id: int, request: Request, user: str = Depends(current_user)):
        member = staff_repo.get(staff_id)
        if member is None:
            return _staff_error("That staff member is no longer on the roster.")
        form = await request.form()
        name = " ".join(str(form.get("name", "")).split()) or member.name
        code = str(form.get("member_code", "")).strip().upper() or member.member_code
        red_cross = str(form.get("red_cross_number", "")).strip() or None

        if not code.isalnum():
            return _staff_error("Member ID must be letters and digits only.")
        if red_cross and not red_cross.isdigit():
            return _staff_error("A Red Cross certificate number must be digits only.")
        # Everything is validated before anything is written, so a bad date at the
        # bottom of the panel cannot leave the details above it half-saved.
        try:
            manual = _manual_dates(form)
        except ValueError as exc:
            return _staff_error(str(exc))

        changes = {
            "name": name,
            "email": str(form.get("email", "")).strip() or None,
            "phone": str(form.get("phone", "")).strip() or None,
            "away": int(bool(form.get("away"))),
            "red_cross_number": red_cross,
        }
        # Only re-verify what actually changed: each check is a live request to an
        # outside service, and saving a phone number should not cost two lookups.
        if code != member.member_code:
            result = verify_member_code(code, name)
            if not result.ok:
                return _staff_error(f"{code}: {result.error}")
            changes["member_code"] = code
            changes["society_name"] = result.society_name
        if red_cross and red_cross != member.red_cross_number:
            certificate = verify_red_cross_number(red_cross, name)
            if not certificate.ok:
                return _staff_error(f"Red Cross {red_cross}: {certificate.error}")

        try:
            staff_repo.update(staff_id, actor=user, **changes)
        except DuplicateMemberCode as exc:
            return _staff_error(str(exc))
        for column_code, certified in manual.items():
            staff_repo.set_manual_cert(staff_id, column_code, certified, actor=user)
        return RedirectResponse("/staff", status_code=303)

    def _manual_dates(form) -> dict[str, date | None]:
        """The panel's hand-entered dates, one per column. ``None`` clears an entry."""
        dates: dict[str, date | None] = {}
        for column in COLUMNS:
            raw = str(form.get(f"manual_{column.code}", "")).strip()
            try:
                dates[column.code] = date.fromisoformat(raw) if raw else None
            except ValueError:
                raise ValueError(f"{column.code} manual date must be a real date.") from None
        return dates

    # --- stored copies of certificates ---------------------------------
    @app.post("/staff/{staff_id}/files")
    async def staff_file_add(staff_id: int, request: Request, user: str = Depends(current_user)):
        if staff_repo.get(staff_id) is None:
            return _staff_error("That staff member is no longer on the roster.")
        # A declared length is the caller's to understate, so save() counts the bytes
        # as well; checking it here only spares the disk from spooling an upload that
        # is already obviously too large. The slack covers the multipart envelope.
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BYTES + 8192:
            return _staff_error(TOO_LARGE)
        form = await request.form()
        upload = form.get("document")
        if not isinstance(upload, UploadFile) or not upload.filename:
            return _staff_error("Choose a file to attach.")
        # The bytes land on disk first: nothing is recorded for a file that was
        # refused, and nothing is stored that no row points at.
        try:
            stored = store.save(upload.file)
        except RejectedUpload as exc:
            return _staff_error(str(exc))
        try:
            staff_repo.add_file(
                staff_id,
                filename=clean_filename(upload.filename),
                stored_name=stored.stored_name,
                content_type=stored.kind.content_type,
                size_bytes=stored.size,
                actor=user,
            )
        except Exception:
            store.delete(stored.stored_name)
            raise
        return RedirectResponse("/staff", status_code=303)

    @app.get("/staff/{staff_id}/files/{file_id}")
    def staff_file_download(staff_id: int, file_id: int, user: str = Depends(current_user)):
        record = staff_repo.file(file_id)
        # The staff id has to match as well as the file id, so a guessed number
        # cannot be walked up under some other member's URL.
        if record is None or record.staff_id != staff_id:
            raise HTTPException(status_code=404, detail="No such copy.")
        path = store.path(record.stored_name)
        if not path.exists():
            raise HTTPException(status_code=404, detail="That copy is no longer on disk.")
        return FileResponse(
            path,
            media_type=record.content_type,
            headers={
                # Always an attachment, and never sniffed: a copy is uploaded by a
                # manager but served from the application's own origin, so it is not
                # given the chance to run there.
                "Content-Disposition": _attachment(record.filename),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/staff/{staff_id}/files/{file_id}/remove")
    def staff_file_remove(staff_id: int, file_id: int, user: str = Depends(current_user)):
        record = staff_repo.file(file_id)
        if record is not None and record.staff_id == staff_id:
            staff_repo.remove_file(file_id, actor=user)
            store.delete(record.stored_name)
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

    def _attachment(filename: str) -> str:
        """A Content-Disposition that survives a name with an accent or a quote in it."""
        from urllib.parse import quote

        ascii_name = filename.encode("ascii", "replace").decode("ascii").replace('"', "'")
        return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"

    def _staff_error(message: str) -> RedirectResponse:
        from urllib.parse import quote

        return RedirectResponse(f"/staff?error={quote(message)}", status_code=303)

    # --- exports -------------------------------------------------------
    def _grid() -> Grid:
        scan_id = scan_repo.latest_complete_id()
        if scan_id is None:
            raise HTTPException(status_code=404, detail="No completed scan yet.")
        scan = scan_repo.latest() or {}
        finished = scan.get("finished_at") or datetime.now(TIMEZONE).isoformat()
        generated = datetime.fromisoformat(finished)
        rows = grid_rows_to_member_rows(
            rows_from_scan(database, scan_id, generated.date()), COLUMNS
        )
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
