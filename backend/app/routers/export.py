"""Выгрузка результатов измерений (ТЗ п.9): CSV и XLSX, построчно или сводкой по школам.

Выбор: школа, один или несколько ПК, период, статус, набор столбцов. Область видимости
роли применяется как везде. Время — UTC, так оно хранится.
"""
import csv
import io
import zipfile
from datetime import date, datetime, time, timedelta
from typing import Literal
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Device, Measurement, School, User
from ..security import can_access_school, current_user, write_audit
from ..services.status import BAD_STATUSES, STATUS_OFFLINE, classify
from .web import _guard, _limit, _school_ids

router = APIRouter(prefix="/api/web", tags=["Export"])

MAX_ROWS = 100_000   # больше — сузить период или выгружать сводкой

# ключ → (заголовок, функция(measurement, device, school, status))
FIELDS = {
    "school": ("Школа", lambda m, d, s, st: s.name),
    "school_id": ("School ID", lambda m, d, s, st: s.school_id_code),
    "district": ("Район/город", lambda m, d, s, st: s.region),
    "provider": ("Поставщик", lambda m, d, s, st: s.provider),
    "device_id": ("Device ID", lambda m, d, s, st: d.device_id),
    "device_name": ("Компьютер", lambda m, d, s, st: d.name),
    "room": ("Кабинет", lambda m, d, s, st: d.room),
    "role": ("Роль ПК", lambda m, d, s, st: "точка мониторинга" if d.line_id else "рабочее место"),
    "date": ("Дата (UTC)", lambda m, d, s, st: m.timestamp.date().isoformat()),
    "time": ("Время (UTC)", lambda m, d, s, st: m.timestamp.time().replace(microsecond=0).isoformat()),
    "download": ("Download, Мбит/с", lambda m, d, s, st: m.download_speed),
    "upload": ("Upload, Мбит/с", lambda m, d, s, st: m.upload_speed),
    "ping": ("Ping, мс", lambda m, d, s, st: m.ping),
    "jitter": ("Jitter, мс", lambda m, d, s, st: m.jitter),
    "packet_loss": ("Packet Loss, %", lambda m, d, s, st: m.packet_loss),
    "status": ("Статус соединения", lambda m, d, s, st: st),
    "thresholds": ("Версия порогов", lambda m, d, s, st: m.threshold_id or "пересчитано"),
}
DEFAULT_FIELDS = ["school", "school_id", "device_id", "room", "date", "time", "download",
                  "upload", "ping", "jitter", "packet_loss", "status"]
AGG_HEADERS = ["Школа", "School ID", "Район/город", "Поставщик", "Замеров", "Download средн., Мбит/с",
               "Download мин., Мбит/с", "Upload средн., Мбит/с", "Ping средн., мс",
               "Проблемных замеров", "Доля проблемных, %", "Случаев отсутствия интернета"]


def _status_of(m: Measurement, s: School) -> str:
    """Статус, сохранённый при приёме; у замеров до появления порогов — пересчёт по действующим."""
    return m.status or classify(m.download_speed or 0, m.ping or 0, m.packet_loss or 0,
                                s.contract_speed_down, bool(m.is_offline),
                                upload=m.upload_speed or 0, jitter=m.jitter or 0,
                                contract_up=s.contract_speed_up)


def _xlsx(header: list[str], rows: list[list]) -> bytes:
    """Минимальная книга XLSX без зависимостей: один лист, строки как inlineStr."""
    def col(n: int) -> str:
        name = ""
        while True:
            n, rem = divmod(n, 26)
            name = chr(65 + rem) + name
            if n == 0:
                return name
            n -= 1

    def cell(ref: str, value) -> str:
        if value is None or value == "":
            return ""
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{ref}"><v>{value}</v></c>'
        text = "".join(ch for ch in str(value) if ch >= " " or ch in "\t\n")   # XML запрещает управляющие
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>'

    body = "".join(
        f'<row r="{r}">' + "".join(cell(f"{col(c)}{r}", v) for c, v in enumerate(row)) + "</row>"
        for r, row in enumerate([header, *rows], start=1))
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    parts = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.'
            'openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType='
            '"application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" '
            'ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType='
            '"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://'
            'schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": f'<?xml version="1.0" encoding="UTF-8"?><workbook {ns} xmlns:r="http://'
            'schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet '
            'name="Измерения" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns='
            '"http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<?xml version="1.0" encoding="UTF-8"?><worksheet {ns}>'
            f'<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
            f'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            f'<sheetData>{body}</sheetData></worksheet>',
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, xml in parts.items():
            archive.writestr(name, xml)
    return buffer.getvalue()


@router.get("/export", summary="Выгрузка измерений: CSV или XLSX (построчно или сводка по школам)")
def export_measurements(
        request: Request, fmt: Literal["csv", "xlsx"] = Query("csv", alias="format"),
        school_id: int | None = None, device_id: list[str] | None = Query(None),
        date_from: date | None = None, date_to: date | None = None,
        status_filter: str | None = Query(None, alias="status"),
        fields: str | None = Query(None, description=f"через запятую: {', '.join(FIELDS)}"),
        aggregate: bool = False, db: Session = Depends(get_db),
        user: User = Depends(current_user)):
    _guard(user)
    keys = [k.strip() for k in fields.split(",") if k.strip()] if fields else DEFAULT_FIELDS
    unknown = [k for k in keys if k not in FIELDS]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Неизвестные поля: {', '.join(unknown)}")
    if school_id:
        school = db.get(School, school_id)
        if not school or not can_access_school(user, school):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа")

    query = (db.query(Measurement, Device, School)
             .join(Device, Device.device_id == Measurement.device_id)
             .join(School, School.id == Measurement.school_id))
    query = _limit(query, Measurement.school_id, _school_ids(db, user))
    if school_id:
        query = query.filter(Measurement.school_id == school_id)
    if device_id:
        query = query.filter(Measurement.device_id.in_(device_id))
    if date_from:
        query = query.filter(Measurement.timestamp >= datetime.combine(date_from, time.min))
    if date_to:
        query = query.filter(Measurement.timestamp < datetime.combine(date_to + timedelta(days=1), time.min))

    rows: list[list] = []
    agg: dict[int, dict] = {}
    for m, d, s in query.order_by(Measurement.timestamp).yield_per(2000):
        st = _status_of(m, s)
        if status_filter and st != status_filter:
            continue
        if aggregate:
            a = agg.setdefault(s.id, {"s": s, "n": 0, "down": 0.0, "min": None, "up": 0.0,
                                      "ping": 0.0, "bad": 0, "off": 0})
            a["n"] += 1
            a["down"] += m.download_speed or 0
            a["up"] += m.upload_speed or 0
            a["ping"] += m.ping or 0
            a["min"] = min(m.download_speed or 0, a["min"]) if a["min"] is not None else (m.download_speed or 0)
            a["bad"] += st in BAD_STATUSES
            a["off"] += st == STATUS_OFFLINE
        else:
            if len(rows) >= MAX_ROWS:
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                    f"Больше {MAX_ROWS} строк: сузьте период или выберите сводку")
            rows.append([FIELDS[k][1](m, d, s, st) for k in keys])

    if aggregate:
        header = AGG_HEADERS
        rows = [[a["s"].name, a["s"].school_id_code, a["s"].region, a["s"].provider, a["n"],
                 round(a["down"] / a["n"], 1), round(a["min"], 1), round(a["up"] / a["n"], 1),
                 round(a["ping"] / a["n"], 1), a["bad"], round(100 * a["bad"] / a["n"], 1), a["off"]]
                for a in sorted(agg.values(), key=lambda a: a["s"].name or "")]
    else:
        header = [FIELDS[k][0] for k in keys]

    write_audit(db, user.email, user.role, "export.created", fmt,
                request.client.host if request.client else "",
                {"rows": len(rows), "aggregate": aggregate, "school_id": school_id,
                 "devices": device_id, "from": str(date_from), "to": str(date_to),
                 "status": status_filter})
    name = f"measurements_{datetime.utcnow():%Y%m%d_%H%M}{'_summary' if aggregate else ''}.{fmt}"
    disposition = {"Content-Disposition": f'attachment; filename="{name}"'}
    if fmt == "xlsx":
        return Response(_xlsx(header, rows), headers=disposition,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows(rows)
    # BOM — чтобы Excel открыл кириллицу в UTF-8.
    return Response(("﻿" + out.getvalue()).encode("utf-8"), headers=disposition,
                    media_type="text/csv; charset=utf-8")
