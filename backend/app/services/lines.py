"""Линии связи (ТЗ п.10, п.14) и стартовые справочники.

Статус школы = статус её ОСНОВНОЙ линии, измеренной точкой мониторинга (Device с line_id).
Резервная линия ведётся отдельно и школу не «красит». Рабочие места оцениваются отдельно.
"""
from sqlalchemy import select

from ..models import Device, Line, School, Threshold
from .status import Thresholds, classify

ROLES = ("main", "backup", "disabled")


def set_state(target, row: dict, status: str) -> None:
    """Текущие показатели и статус — у School и Line поля называются одинаково."""
    target.current_download = row["download_speed"]
    target.current_upload = row["upload_speed"]
    target.current_ping = row["ping"]
    target.current_jitter = row["jitter"]
    target.current_packet_loss = row["packet_loss"]
    target.last_measurement = row["timestamp"]
    target.status = status


def contracts(db, device: Device) -> tuple[float | None, float | None]:
    """Договорные Download/Upload линии, которую измеряет устройство (иначе — школы)."""
    line = db.get(Line, device.line_id) if device.line_id else None
    if line and line.contract_speed_down:
        return line.contract_speed_down, line.contract_speed_up
    school = db.get(School, device.school_id)
    return (school.contract_speed_down, school.contract_speed_up) if school else (None, None)


def main_monitors():
    """Подзапрос: device_id точек мониторинга ОСНОВНЫХ линий — по ним судят о канале школы."""
    return (select(Device.device_id).join(Line, Line.id == Device.line_id)
            .where(Line.role == "main"))


def main_line(db, school_id: int) -> Line | None:
    return db.query(Line).filter(Line.school_id == school_id, Line.role == "main").first()


def sync_from_monitors(db, line: Line, school: School) -> None:
    """Состояние линии (и школы, если линия основная) — по её точке мониторинга.
    Раньше школа считалась средним по всем ПК; теперь рабочие места в оценку канала не входят."""
    latest = db.query(Device).filter(Device.line_id == line.id, Device.last_measured.isnot(None)) \
        .order_by(Device.last_measured.desc()).first()
    if not latest:
        return
    row = {"download_speed": latest.current_download or 0.0,
           "upload_speed": latest.current_upload or 0.0,
           "ping": latest.current_ping or 0.0, "jitter": latest.current_jitter or 0.0,
           "packet_loss": latest.current_packet_loss or 0.0, "timestamp": latest.last_measured}
    status = classify(row["download_speed"], row["ping"], row["packet_loss"],
                      line.contract_speed_down, latest.status == "offline",
                      upload=row["upload_speed"], jitter=row["jitter"],
                      contract_up=line.contract_speed_up)
    set_state(line, row, status)
    if line.role == "main":
        set_state(school, row, status)


def ensure_defaults(db) -> None:
    """Стартовая версия порогов и основная линия у каждой школы. Идемпотентно."""
    if not db.query(Threshold.id).first():
        t = Thresholds.from_settings()
        db.add(Threshold(created_by="system", down_min=t.down_min, up_min=t.up_min,
                         ping_max=t.ping_max, jitter_max=t.jitter_max, loss_max=t.loss_max,
                         availability_min=t.availability_min, contract_ratio=t.contract_ratio,
                         incident_after=t.incident_after, stale_after_min=t.stale_after_min))
    db.query(Device).filter(Device.last_measured.is_(None), Device.last_seen.isnot(None)) \
        .update({Device.last_measured: Device.last_seen}, synchronize_session=False)

    for school in db.query(School).all():
        if main_line(db, school.id):
            continue
        line = Line(school_id=school.id, code=f"{school.school_id_code}-L1", role="main",
                    provider=school.provider, connection_type=school.connection_type,
                    contract_speed_down=school.contract_speed_down,
                    contract_speed_up=school.contract_speed_up)
        db.add(line)
        db.flush()
        for gateway in db.query(Device).filter(Device.school_id == school.id,
                                               Device.device_type == "Шлюз"):
            gateway.line_id = line.id
        db.flush()
        sync_from_monitors(db, line, school)
    db.commit()
