"""Подготовка БД: миграция схемы, учётные записи, парк ПК-агентов, история замеров.

Запуск:  python bootstrap.py
Идемпотентно — можно выполнять повторно.
"""
import random
from datetime import datetime, timedelta

from sqlalchemy import inspect, text

from app.database import Base, SessionLocal, engine
from app.models import Device, Incident, Measurement, School, User
from app.security import fingerprint, hash_password
from app.services.status import classify

random.seed(20260916)

ROOMS = [
    ("Серверная / Шлюз", "Шлюз"), ("Кабинет информатики №1", "Рабочая станция"),
    ("Кабинет информатики №2", "Рабочая станция"), ("Библиотека / медиацентр", "Рабочая станция"),
    ("Учительская", "Ноутбук"), ("Кабинет директора", "Ноутбук"),
    ("Лаборатория робототехники", "Рабочая станция"),
]
OS_LIST = ["Windows 11 Pro 23H2", "Windows 10 Pro 22H2", "Astra Linux SE 1.7", "Ubuntu 24.04 LTS"]
CPU_LIST = ["Intel Core i5-10400", "Intel Core i3-12100", "AMD Ryzen 5 5600G",
            "Intel Celeron J4125", "Intel Core i7-11700"]
LINKS = ["Ethernet 1 Гбит/с", "Ethernet 100 Мбит/с", "Wi-Fi 5 (802.11ac)", "Wi-Fi 6 (802.11ax)"]


def migrate_schema() -> None:
    """Добавляет колонки, появившиеся в модели, в уже существующие таблицы."""
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    type_map = {"INTEGER": "INTEGER", "VARCHAR": "VARCHAR", "FLOAT": "FLOAT",
                "BOOLEAN": "BOOLEAN", "DATETIME": "DATETIME", "TEXT": "TEXT"}
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in inspector.get_table_names():
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                sql_type = type_map.get(str(column.type).split("(")[0].upper(), "VARCHAR")
                conn.execute(text(
                    f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {sql_type}'))
                print(f"  + {table.name}.{column.name}")


def seed_users(db) -> None:
    if db.query(User).count():
        print("  учётные записи уже созданы")
        return
    first_school = db.query(School).order_by(School.id).first()
    accounts = [
        ("admin@vko.edu.kz", "admin123", "Администратор УО ВКО", "admin", None, None),
        ("operator@vko.edu.kz", "operator123", "Оператор мониторинга", "operator", None, None),
        ("school@vko.edu.kz", "school123",
         f"Ответственный: {first_school.name if first_school else 'школа'}", "school",
         first_school.id if first_school else None, None),
        ("provider@kaztelecom.kz", "provider123", "АО «Казахтелеком» · SLA-контроль",
         "provider", None, "АО «Казахтелеком»"),
    ]
    for email, password, name, role, school_id, provider in accounts:
        db.add(User(email=email, password_hash=hash_password(password), full_name=name,
                    role=role, school_id=school_id, provider_name=provider))
    db.commit()
    print(f"  создано учётных записей: {len(accounts)}")


def seed_devices(db) -> list[Device]:
    """Каждой школе — 2–5 ПК-агентов с инвентарными данными и отпечатком железа."""
    devices: list[Device] = []
    for school in db.query(School).order_by(School.id).all():
        existing = db.query(Device).filter(Device.school_id == school.id).all()
        target = random.randint(2, 5)
        for index in range(target):
            room, dev_type = ROOMS[index % len(ROOMS)]
            device_id = f"PC-{school.school_id_code}-{index + 1:02d}"
            device = next((d for d in existing if d.device_id == device_id), None)
            if device is None and index < len(existing):
                device = existing[index]           # переиспользуем запись агента v1
                device.device_id = device_id
            if device is None:
                device = Device(device_id=device_id, school_id=school.id)
                db.add(device)

            mac = ":".join(f"{random.randint(0, 255):02X}" for _ in range(6))
            link = "Ethernet 1 Гбит/с" if index == 0 else random.choice(LINKS)
            device.school_id = school.id
            device.name = "Шлюз-агент (основной)" if index == 0 else f"ПК-агент №{index}"
            device.room = room
            device.device_type = dev_type
            device.ip_address = f"192.168.{school.id % 250}.{10 + index}"
            device.mac_address = mac
            device.os_name = random.choice(OS_LIST)
            device.cpu_model = random.choice(CPU_LIST)
            device.ram_gb = random.choice([4, 8, 8, 16])
            device.agent_version = random.choice(["2.0.0", "2.0.0", "1.9.4"])
            device.link_mode = link
            device.wifi_signal_dbm = round(random.uniform(-72, -42), 1) if "Wi-Fi" in link else None
            device.uptime_hours = round(random.uniform(4, 900), 1)
            device.hardware_fingerprint = fingerprint(mac, device_id,
                                                      device.cpu_model, device.os_name)
            device.cert_fingerprint = fingerprint("cert", device_id)[:40]
            device.enrolled_at = datetime.utcnow() - timedelta(days=random.randint(30, 400))
            device.revoked = False
            devices.append(device)
    db.commit()
    print(f"  ПК-агентов в системе: {len(devices)}")
    return devices


def seed_measurements(db, devices: list[Device], days: int = 21, per_day: int = 6) -> None:
    """История по каждому ПК с воспроизводимыми паттернами деградации."""
    if db.query(Measurement).filter(Measurement.device_id.like("PC-%")).count() > 1000:
        print("  история замеров уже наполнена")
        return

    schools = {s.id: s for s in db.query(School).all()}
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    rows, device_state = [], {}

    for device in devices:
        school = schools[device.school_id]
        contract = school.contract_speed_down or 100.0
        # базовое качество линии конкретного ПК
        base_ratio = {"Шлюз": 0.92}.get(device.device_type, random.uniform(0.55, 0.95))
        if "Wi-Fi" in (device.link_mode or ""):
            base_ratio *= random.uniform(0.55, 0.8)
        if device.link_mode == "Ethernet 100 Мбит/с":
            base_ratio = min(base_ratio, 95 / contract)

        bad_weekday = random.choice([0, 1, 2, 3, 4]) if random.random() < 0.35 else None
        busy_hour = random.choice([10, 11, 12]) if random.random() < 0.45 else None
        flaky = random.random() < 0.12

        samples = []
        for day in range(days, -1, -1):
            for slot in range(per_day):
                ts = now - timedelta(days=day, hours=(per_day - slot) * (14 // per_day) + 1)
                ratio = base_ratio * random.uniform(0.9, 1.06)
                if bad_weekday is not None and ts.weekday() == bad_weekday:
                    ratio *= random.uniform(0.45, 0.65)      # недельный паттерн
                if busy_hour is not None and ts.hour in (busy_hour, busy_hour + 1):
                    ratio *= random.uniform(0.5, 0.7)        # часы пиковой нагрузки
                offline = flaky and random.random() < 0.05

                down = 0.0 if offline else round(max(0.5, contract * ratio), 1)
                up = 0.0 if offline else round(down * random.uniform(0.45, 0.95), 1)
                ping = 0.0 if offline else round(
                    random.uniform(9, 26) / max(ratio, 0.15) * random.uniform(0.8, 1.3), 1)
                jitter = 0.0 if offline else round(random.uniform(1.2, 14), 1)
                loss = 100.0 if offline else round(max(0.0, random.gauss(0.5, 1.4)), 2)

                samples.append({"device_id": device.device_id, "school_id": device.school_id,
                                "timestamp": ts, "download_speed": down, "upload_speed": up,
                                "ping": ping, "jitter": jitter, "packet_loss": loss,
                                "is_offline": offline,
                                "source": "backfill" if offline or random.random() < 0.04 else "live"})
        rows.extend(samples)
        device_state[device.device_id] = samples[-1]

    db.bulk_insert_mappings(Measurement, rows)
    db.commit()
    print(f"  записано замеров: {len(rows)}")

    # --- текущие метрики ПК + SLA-показатели ------------------------------
    for device in devices:
        last = device_state[device.device_id]
        school = schools[device.school_id]
        own = [r for r in rows if r["device_id"] == device.device_id]
        ok = sum(1 for r in own
                 if classify(r["download_speed"], r["ping"], r["packet_loss"],
                             school.contract_speed_down, r["is_offline"]) == "Норма")
        device.current_download = last["download_speed"]
        device.current_upload = last["upload_speed"]
        device.current_ping = last["ping"]
        device.current_jitter = last["jitter"]
        device.current_packet_loss = last["packet_loss"]
        device.last_seen = last["timestamp"]
        device.availability_pct = round(
            100 * (1 - sum(1 for r in own if r["is_offline"]) / len(own)), 1)
        device.sla_compliance_pct = round(100 * ok / len(own), 1)
        status = classify(last["download_speed"], last["ping"], last["packet_loss"],
                          school.contract_speed_down, last["is_offline"])
        device.status = ("offline" if status == "Нет соединения"
                         else "online" if status == "Норма" else "warning")
        interval = {"Норма": 900, "Нестабильно": 180}.get(status, 60)
        device.test_interval_sec = interval
        device.diagnostic_mode = "standard" if interval == 900 else "deep"
    db.commit()


def recompute_schools(db) -> None:
    """Статус школы = состояние основного шлюза, метрики — среднее по её ПК."""
    order = {"Норма": 0, "Нестабильно": 1, "Критично": 2, "Нет соединения": 3}
    for school in db.query(School).all():
        devices = db.query(Device).filter(Device.school_id == school.id).all()
        if not devices:
            continue
        school.current_download = round(sum(d.current_download or 0 for d in devices) / len(devices), 1)
        school.current_upload = round(sum(d.current_upload or 0 for d in devices) / len(devices), 1)
        school.current_ping = round(sum(d.current_ping or 0 for d in devices) / len(devices), 1)
        school.current_jitter = round(sum(d.current_jitter or 0 for d in devices) / len(devices), 1)
        school.current_packet_loss = round(
            sum(d.current_packet_loss or 0 for d in devices) / len(devices), 2)
        school.last_measurement = max((d.last_seen for d in devices if d.last_seen),
                                      default=datetime.utcnow())
        statuses = [classify(d.current_download or 0, d.current_ping or 0,
                             d.current_packet_loss or 0, school.contract_speed_down,
                             d.status == "offline") for d in devices]
        school.status = max(statuses, key=lambda s: order[s])
    db.commit()
    print("  статусы школ пересчитаны по ПК-агентам")


def refresh_incidents(db) -> None:
    """Инциденты для школ, вышедших за SLA (без дублей по открытым)."""
    created = 0
    counter = db.query(Incident).count()
    for school in db.query(School).filter(School.status.in_(["Критично", "Нет соединения"])).all():
        open_inc = db.query(Incident).filter(
            Incident.school_id == school.id,
            Incident.status.in_(["Новый", "В работе", "Передан поставщику"])).first()
        if open_inc:
            continue
        worst = (db.query(Device).filter(Device.school_id == school.id)
                 .order_by(Device.sla_compliance_pct.asc()).first())
        counter += 1
        created += 1
        db.add(Incident(
            incident_number=f"INC-2026-{counter:04d}", school_id=school.id,
            device_id=worst.device_id if worst else None, provider=school.provider,
            status=random.choice(["Новый", "В работе", "Передан поставщику"]),
            severity="critical" if school.status == "Нет соединения" else "major",
            start_time=school.last_measurement or datetime.utcnow(),
            description=(f"Скорость {school.current_download} Мбит/с при договорных "
                         f"{school.contract_speed_down} Мбит/с · Ping {school.current_ping} мс · "
                         f"Потери {school.current_packet_loss}%"),
        ))
    db.commit()
    print(f"  инцидентов создано: {created}")


def main() -> None:
    print("Миграция схемы:")
    migrate_schema()
    db = SessionLocal()
    try:
        print("Учётные записи:")
        seed_users(db)
        print("ПК-агенты:")
        devices = seed_devices(db)
        print("История замеров:")
        seed_measurements(db, devices)
        print("Агрегация:")
        recompute_schools(db)
        refresh_incidents(db)
        print("\nГотово. Демо-доступы:")
        print("  admin@vko.edu.kz / admin123        — полный доступ")
        print("  operator@vko.edu.kz / operator123  — оператор мониторинга")
        print("  school@vko.edu.kz / school123      — только своя школа")
        print("  provider@kaztelecom.kz / provider123 — только линии провайдера")
    finally:
        db.close()


if __name__ == "__main__":
    main()
