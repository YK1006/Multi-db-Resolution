import asyncio
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import DATABASE_URL, UPLOAD_DIR
from app.db import get_connection
from app.ingest.inspect import inspect_source
from app.mapping.mapper import map_source
from app.matching.enrich import enrich_from_seed, merge_into_entity
from app.normalize.clean import normalize_email, normalize_phone, normalize_text
from app.stats import recompute_stats
from app.worker import process_batch_pipeline, resume_interrupted_batches

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    recovery = asyncio.create_task(resume_interrupted_batches()) if DATABASE_URL else None
    yield
    if recovery:
        recovery.cancel()
        with suppress(asyncio.CancelledError):
            await recovery


app = FastAPI(title="Multi-Database Entity Resolution", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


def _render(request: Request, template: str, **context):
    return templates.TemplateResponse(request=request, name=template, context=context)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not DATABASE_URL:
        return _render(request, "setup_required.html")
    with get_connection() as connection:
        sources = connection.execute("SELECT count(*) AS count FROM sources").fetchone()["count"]
        entities = connection.execute("SELECT count(*) AS count FROM entities").fetchone()["count"]
        records = connection.execute("SELECT count(*) AS count FROM raw_records").fetchone()["count"]
        recent = connection.execute(
            """SELECT b.id, s.name, b.status, b.progress, b.created_at FROM import_batches b
               JOIN sources s ON s.id = b.source_id ORDER BY b.created_at DESC LIMIT 6"""
        ).fetchall()
    return _render(request, "dashboard.html", source_count=sources, entity_count=entities, record_count=records, recent=recent)


@app.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request):
    with get_connection() as connection:
        rows = connection.execute(
            """SELECT s.id, s.name, s.type, s.uploaded_by, s.created_at, b.status, b.progress,
                      coalesce(rc.record_count, 0) AS record_count
               FROM sources s
               LEFT JOIN LATERAL (SELECT status, progress FROM import_batches WHERE source_id = s.id ORDER BY id DESC LIMIT 1) b ON true
               LEFT JOIN LATERAL (SELECT count(*) AS record_count FROM raw_records WHERE source_id = s.id) rc ON true
               ORDER BY s.created_at DESC"""
        ).fetchall()
    return _render(request, "sources.html", sources=rows)


@app.get("/sources/new", response_class=HTMLResponse)
def new_source(request: Request):
    return _render(request, "upload.html", error=None)


@app.post("/sources/upload", response_class=HTMLResponse)
async def upload_source(
    request: Request,
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    uploaded_by: str = Form(...),
    file: UploadFile = File(...),
    part_label: str = Form(default=""),
):
    filename = Path(file.filename or "").name
    extension = Path(filename).suffix.lower()
    if extension not in {".csv", ".sql"}:
        return _render(request, "upload.html", error="Choose a CSV file or SQL INSERT dump.")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_path = UPLOAD_DIR / f"{uuid.uuid4().hex}{extension}"
    with stored_path.open("wb") as destination:
        while chunk := await file.read(1024 * 1024):
            destination.write(chunk)
    source_type = "csv" if extension == ".csv" else "sql"
    try:
        with get_connection() as connection:
            source = connection.execute(
                "SELECT id FROM sources WHERE name = %s AND type = %s ORDER BY id LIMIT 1", (name.strip(), source_type)
            ).fetchone()
            if source:
                source_id = source["id"]
                connection.execute("UPDATE sources SET uploaded_by = %s WHERE id = %s", (uploaded_by.strip(), source_id))
            else:
                source_id = connection.execute(
                    "INSERT INTO sources(name, type, uploaded_by) VALUES (%s, %s, %s) RETURNING id",
                    (name.strip(), source_type, uploaded_by.strip()),
                ).fetchone()["id"]
            batch_id = connection.execute(
                """INSERT INTO import_batches(source_id, part_label, status, file_path)
                   VALUES (%s, %s, 'uploaded', %s) RETURNING id""",
                (source_id, part_label.strip() or None, str(stored_path)),
            ).fetchone()["id"]
            connection.execute("UPDATE import_batches SET status = 'inspecting' WHERE id = %s", (batch_id,))
        inspection = inspect_source(stored_path)
        map_source(source_id, inspection["tables"])
        with get_connection() as connection:
            connection.execute("UPDATE import_batches SET status = 'mapping' WHERE id = %s", (batch_id,))
        mappings = _mapping_rows(source_id)
        return _render(
            request, "mapping_review.html", source_id=source_id, batch_id=batch_id,
            source_name=name.strip(), tables=inspection["tables"], mappings=mappings,
        )
    except Exception as error:
        if "batch_id" in locals():
            with get_connection() as connection:
                connection.execute(
                    """UPDATE import_batches SET status = 'completed_with_errors', completed_at = now(),
                       processing_complete = true, error_log = concat_ws(E'\\n', error_log, %s) WHERE id = %s""",
                    (str(error), batch_id),
                )
        return _render(request, "upload.html", error=f"Could not inspect this file: {error}")


def _mapping_rows(source_id: int):
    with get_connection() as connection:
        return connection.execute(
            "SELECT id, table_name, original_column, canonical_field, confirmed FROM field_mappings WHERE source_id = %s ORDER BY table_name, id",
            (source_id,),
        ).fetchall()


@app.get("/sources/{source_id}/tables", response_class=HTMLResponse)
def source_tables(request: Request, source_id: int):
    with get_connection() as connection:
        source = connection.execute("SELECT * FROM sources WHERE id = %s", (source_id,)).fetchone()
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        mappings = _mapping_rows(source_id)
        tables = connection.execute(
            "SELECT table_name, count(*) AS record_count FROM raw_records WHERE source_id = %s GROUP BY table_name ORDER BY table_name",
            (source_id,),
        ).fetchall()
    return _render(request, "tables.html", source=source, tables=tables, mappings=mappings)


@app.post("/sources/{source_id}/confirm-mapping")
async def confirm_mapping(request: Request, source_id: int, background_tasks: BackgroundTasks):
    form = await request.form()
    try:
        batch_id = int(form.get("batch_id", ""))
    except ValueError as error:
        raise HTTPException(status_code=400, detail="A valid batch is required") from error
    with get_connection() as connection:
        batch = connection.execute(
            "SELECT id FROM import_batches WHERE id = %s AND source_id = %s AND processing_complete = false",
            (batch_id, source_id),
        ).fetchone()
        if not batch:
            raise HTTPException(status_code=404, detail="Import batch not found or already processed")
        rows = connection.execute("SELECT id FROM field_mappings WHERE source_id = %s", (source_id,)).fetchall()
        for row in rows:
            selected = str(form.get(f"mapping_{row['id']}", "ignore"))
            if selected not in {"email", "phone", "name", "username", "member_id", "company", "address", "ignore"}:
                selected = "ignore"
            connection.execute(
                "UPDATE field_mappings SET canonical_field = %s, confirmed = true WHERE id = %s",
                (selected, row["id"]),
            )
        connection.execute("UPDATE import_batches SET status = 'cleaning' WHERE id = %s", (batch_id,))
    background_tasks.add_task(process_batch_pipeline, batch_id)
    return RedirectResponse(url=f"/jobs/{batch_id}", status_code=303)


@app.get("/jobs/{batch_id}", response_class=HTMLResponse)
def job_page(request: Request, batch_id: int):
    return _render(request, "job_status.html", batch_id=batch_id)


@app.get("/jobs/{batch_id}/status")
def job_status(batch_id: int):
    with get_connection() as connection:
        batch = connection.execute(
            "SELECT id, status, progress, total_rows, last_processed_row, error_log, completed_at FROM import_batches WHERE id = %s",
            (batch_id,),
        ).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = ""):
    results = []
    if q.strip():
        candidates = []
        email = normalize_email(q)
        phone = normalize_phone(q)
        username = normalize_text(q)
        if email:
            candidates.append(("email", email))
        if phone:
            candidates.append(("phone", phone))
        if username:
            candidates.append(("username", username.lower()))
        resolved: set[int] = set()
        for identifier_type, value in candidates:
            resolution = enrich_from_seed(identifier_type, value)
            if resolution["matched_record_ids"]:
                entity_id = merge_into_entity(resolution["matched_record_ids"], resolution["trail"])
                if entity_id:
                    resolved.add(entity_id)
        with get_connection() as connection:
            if resolved:
                results = connection.execute(
                    """SELECT e.id, e.is_new, count(DISTINCT er.raw_record_id) AS record_count,
                              count(DISTINCT r.source_id) AS source_count
                       FROM entities e JOIN entity_records er ON er.entity_id = e.id
                       JOIN raw_records r ON r.id = er.raw_record_id WHERE e.id = ANY(%s)
                       GROUP BY e.id ORDER BY e.id""",
                    (list(resolved),),
                ).fetchall()
    return _render(request, "search_results.html", query=q, results=results)


@app.get("/entities/{entity_id}", response_class=HTMLResponse)
def entity_detail(request: Request, entity_id: int):
    with get_connection() as connection:
        entity = connection.execute("SELECT * FROM entities WHERE id = %s", (entity_id,)).fetchone()
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")
        fields = connection.execute(
            """SELECT ef.*, r.table_name, r.original_data, r.source_id FROM entity_fields ef
               LEFT JOIN raw_records r ON r.id = ef.source_raw_record_id
               WHERE ef.entity_id = %s ORDER BY ef.field_name""",
            (entity_id,),
        ).fetchall()
        sources = connection.execute(
            """SELECT DISTINCT s.name, s.type, r.table_name FROM entity_records er
               JOIN raw_records r ON r.id = er.raw_record_id JOIN sources s ON s.id = r.source_id
               WHERE er.entity_id = %s ORDER BY s.name, r.table_name""",
            (entity_id,),
        ).fetchall()
        trail = connection.execute(
            "SELECT * FROM entity_enrichment_trail WHERE entity_id = %s ORDER BY hop_number, id", (entity_id,)
        ).fetchall()
    return _render(request, "entity_detail.html", entity=entity, fields=fields, sources=sources, trail=trail)


@app.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request):
    recompute_stats()
    with get_connection() as connection:
        stats = connection.execute("SELECT metric_name, metric_value, updated_at FROM stats ORDER BY metric_name").fetchall()
    return _render(request, "stats.html", stats=stats)