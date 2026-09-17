"""Подготовка БД: миграция схемы, учётные записи, парк ПК-агентов, история замеров.

Запуск:  python bootstrap.py
Идемпотентно — можно выполнять повторно.
"""
import random
from datetime import datetime, timedelta

from sqlalchemy import inspect, text

from app.database import Base, SessionLocal, engine
from app.models import Device, FaultEvent, Incident, Measurement, School, User
from app.security import fingerprint, hash_password
from app.services.ml.train import train_models
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


# --- Симулятор отказов -----------------------------------------------------
#
# Ключевая идея: авария рождается на одном из четырёх уровней и «спускается»
# на все замеры, которые под неё попадают. Именно это порождает синхронность,
# по которой модель атрибуции потом восстанавливает виновника.
#
#   regional       магистраль / энергоснабжение района — страдают ВСЕ провайдеры
#   provider_node  узел провайдера в районе — страдают школы одного провайдера
#   school_lan     шлюз или ЛВС школы — страдают все ПК одной школы
#   device         Wi-Fi или сетевая карта ПК — страдает один ПК
#
# rate — среднее число аварий на сущность за окно; sev — множитель скорости
# (0.0 — полный обрыв связи).
FAULT_SPECS = {
    "regional":      {"rate": 0.4, "dur": (1.0, 5.0),  "sev": (0.00, 0.30)},
    "provider_node": {"rate": 1.0, "dur": (1.0, 8.0),  "sev": (0.00, 0.45)},
    "school_lan":    {"rate": 1.4, "dur": (0.5, 6.0),  "sev": (0.05, 0.55)},
    "device":        {"rate": 1.7, "dur": (0.5, 12.0), "sev": (0.30, 0.80)},
}
SLOT_MINUTES = 30


def _draw_events(kind: str, count_scale: float, window_h: float, now, **tags) -> list[dict]:
    """Пуассоновский розыгрыш аварий одного уровня для одной сущности."""
    spec = FAULT_SPECS[kind]
    events = []
    for _ in range(_poisson(spec["rate"] * count_scale)):
        duration = random.uniform(*spec["dur"])
        start_h = random.uniform(0, window_h - duration)
        events.append({
            "cause": kind,
            "start": now - timedelta(hours=window_h - start_h),
            "end": now - timedelta(hours=window_h - start_h - duration),
            "severity": round(random.uniform(*spec["sev"]), 3),
            **tags,
        })
    return events


def _poisson(lam: float) -> int:
    """Обратное преобразование — без numpy."""
    import math
    limit, k, product = math.exp(-lam), 0, random.random()
    while product > limit:
        k += 1
        product *= random.random()
    return k


def _make_active(event: dict, now, hours: float) -> dict:
    """Продлевает аварию до текущего момента — «горит» прямо сейчас в демо."""
    event["start"] = now - timedelta(hours=hours)
    event["end"] = now + timedelta(hours=random.uniform(1, 6))
    return event


def seed_faults(db, devices: list[Device], days: int = 14) -> tuple[list[dict], datetime]:
    """Генерирует размеченные аварии. Идемпотентно: при наличии — читает из БД."""
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    if db.query(FaultEvent).count():
        rows = [{"cause": f.cause, "district": f.district, "provider": f.provider,
                 "school_id": f.school_id, "device_id": f.device_id,
                 "start": f.start_time, "end": f.end_time, "severity": f.severity}
                for f in db.query(FaultEvent).all()]
        print(f"  журнал аварий уже наполнен: {len(rows)}")
        return rows, now

    schools = db.query(School).order_by(School.id).all()
    window_h = days * 24.0
    events: list[dict] = []

    districts = sorted({s.region for s in schools})
    for district in districts:
        events += _draw_events("regional", 1.0, window_h, now, district=district)

    pairs = {}
    for school in schools:
        pairs.setdefault((school.provider, school.region), []).append(school)
    for (provider, district), members in pairs.items():
        if len(members) < 2:          # одиночная школа — это не «узел провайдера»
            continue
        events += _draw_events("provider_node", 1.0, window_h, now,
                               provider=provider, district=district)

    for school in schools:
        events += _draw_events("school_lan", 1.0, window_h, now,
                               school_id=school.id, district=school.region,
                               provider=school.provider)

    by_school = {s.id: s for s in schools}
    for device in devices:
        school = by_school[device.school_id]
        # у Wi-Fi-линков локальные отказы объективно чаще
        scale = 1.6 if "Wi-Fi" in (device.link_mode or "") else 0.7
        events += _draw_events("device", scale, window_h, now, device_id=device.device_id,
                               school_id=school.id, district=school.region,
                               provider=school.provider)

    # --- Гарантированная витрина: аварии, горящие на момент демо ----------
    showcase = []
    big_pair = max(pairs.items(), key=lambda kv: len(kv[1]))
    showcase.append(_make_active(
        {"cause": "provider_node", "provider": big_pair[0][0], "district": big_pair[0][1],
         "severity": 0.12}, now, hours=5.5))
    for school in random.sample(schools, 3):
        showcase.append(_make_active(
            {"cause": "school_lan", "school_id": school.id, "district": school.region,
             "provider": school.provider, "severity": 0.22}, now, hours=3.0))
    for device in random.sample([d for d in devices if "Wi-Fi" in (d.link_mode or "")], 4):
        school = by_school[device.school_id]
        showcase.append(_make_active(
            {"cause": "device", "device_id": device.device_id, "school_id": school.id,
             "district": school.region, "provider": school.provider, "severity": 0.45},
            now, hours=4.0))
    events += showcase

    db.bulk_insert_mappings(FaultEvent, [
        {"cause": e["cause"], "district": e.get("district"), "provider": e.get("provider"),
         "school_id": e.get("school_id"), "device_id": e.get("device_id"),
         "start_time": e["start"], "end_time": e["end"], "severity": e["severity"],
         "origin": "simulator"} for e in events])
    db.commit()
    by_cause = {}
    for e in events:
        by_cause[e["cause"]] = by_cause.get(e["cause"], 0) + 1
    print(f"  аварий сгенерировано: {len(events)} — " +
          ", ".join(f"{k}: {v}" for k, v in sorted(by_cause.items())))
    return events, now


def seed_measurements(db, devices: list[Device], days: int = 14) -> None:
    """История замеров: базовая линия школы × сезонность × активные аварии.

    Сетка 30 минут — вдвое разреженнее штатного интервала агента (INTERVAL_NORMAL_SEC
    = 15 мин), чтобы демо-база осталась в разумном объёме. Этого уже достаточно для
    скользящих окон и прогноза на 6 часов вперёд.
    """
    if db.query(Measurement).filter(Measurement.device_id.like("PC-%")).count() > 1000:
        print("  история замеров уже наполнена")
        return

    events, now = seed_faults(db, devices, days=days)
    schools = {s.id: s for s in db.query(School).all()}

    # Разложение аварий по адресатам — чтобы не перебирать весь список на каждый замер.
    ev_district: dict[str, list] = {}
    ev_pair: dict[tuple, list] = {}
    ev_school: dict[int, list] = {}
    ev_device: dict[str, list] = {}
    for e in events:
        if e["cause"] == "regional":
            ev_district.setdefault(e["district"], []).append(e)
        elif e["cause"] == "provider_node":
            ev_pair.setdefault((e["provider"], e["district"]), []).append(e)
        elif e["cause"] == "school_lan":
            ev_school.setdefault(e["school_id"], []).append(e)
        else:
            ev_device.setdefault(e["device_id"], []).append(e)

    # Базовое качество линии школы: у ~22% организаций канал объективно слабый.
    school_grade, busy_hour = {}, {}
    for school_id in schools:
        roll = random.random()
        school_grade[school_id] = (random.uniform(0.25, 0.45) if roll < 0.09
                                   else random.uniform(0.45, 0.78) if roll < 0.26
                                   else random.uniform(0.85, 1.05))
        # Часы пиковой нагрузки — свойство ШКОЛЫ: все её ПК за одним шлюзом.
        busy_hour[school_id] = random.choice([10, 11, 12]) if random.random() < 0.45 else None
    # Регламентные работы провайдера — свойство ПРОВАЙДЕРА, общее для его школ.
    providers = sorted({s.provider for s in schools.values()})
    bad_weekday = {p: (random.choice([0, 1, 2, 3, 4]) if random.random() < 0.4 else None)
                   for p in providers}

    slots = int(days * 24 * 60 / SLOT_MINUTES)
    rows, device_state = [], {}

    for device in devices:
        school = schools[device.school_id]
        contract = school.contract_speed_down or 100.0
        base_ratio = school_grade[school.id] * (1.0 if device.device_type == "Шлюз"
                                                else random.uniform(0.90, 1.0))
        if "Wi-Fi" in (device.link_mode or ""):
            base_ratio *= random.uniform(0.75, 0.9)
        if device.link_mode == "Ethernet 100 Мбит/с":
            base_ratio = min(base_ratio, 95 / contract)

        affecting = (ev_district.get(school.region, []) + ev_school.get(school.id, [])
                     + ev_pair.get((school.provider, school.region), [])
                     + ev_device.get(device.device_id, []))
        school_busy = busy_hour[school.id]
        prov_bad_day = bad_weekday[school.provider]

        samples = []
        for slot in range(slots, -1, -1):
            ts = now - timedelta(minutes=slot * SLOT_MINUTES)
            ratio = base_ratio * random.uniform(0.93, 1.05)
            if prov_bad_day is not None and ts.weekday() == prov_bad_day and 8 <= ts.hour <= 18:
                ratio *= random.uniform(0.55, 0.75)
            if school_busy is not None and ts.hour in (school_busy, school_busy + 1):
                ratio *= random.uniform(0.55, 0.75)

            # Узкое место определяет самая тяжёлая из активных аварий.
            active = [e for e in affecting if e["start"] <= ts <= e["end"]]
            if active:
                worst = min(active, key=lambda e: e["severity"])
                ratio *= worst["severity"]

            offline = ratio < 0.02
            down = 0.0 if offline else round(max(0.3, contract * ratio), 1)
            up = 0.0 if offline else round(down * random.uniform(0.45, 0.95), 1)
            ping = 0.0 if offline else round(
                random.uniform(9, 26) / max(ratio, 0.12) * random.uniform(0.85, 1.25), 1)
            jitter = 0.0 if offline else round(random.uniform(1.2, 6) / max(ratio, 0.2), 1)
            # Потери растут только по мере деградации канала: на здоровой линии
            # это доли процента, иначе формально «нарушение SLA» горело бы всегда.
            loss = 100.0 if offline else round(
                min(35.0, max(0.0, random.gauss(0.15, 0.25)
                              + max(0.0, (0.55 - ratio) * 7))), 2)

            samples.append({"device_id": device.device_id, "school_id": device.school_id,
                            "timestamp": ts, "download_speed": down, "upload_speed": up,
                            "ping": ping, "jitter": jitter, "packet_loss": loss,
                            "is_offline": offline,
                            "source": "backfill" if offline or random.random() < 0.03 else "live"})
        rows.extend(samples)
        device_state[device.device_id] = samples[-1]

    db.bulk_insert_mappings(Measurement, rows)
    db.commit()
    print(f"  записано замеров: {len(rows)} (шаг {SLOT_MINUTES} мин, окно {days} сут)")

    # --- текущие метрики ПК + SLA-показатели ------------------------------
    per_device: dict[str, list] = {}
    for row in rows:
        per_device.setdefault(row["device_id"], []).append(row)
    for device in devices:
        last = device_state[device.device_id]
        school = schools[device.school_id]
        own = per_device[device.device_id]
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
        # Статус организации — по усреднённому каналу; проблемы отдельного ПК
        # видны на ПК-уровне и не «красят» всю школу.
        school.status = classify(school.current_download, school.current_ping,
                                 school.current_packet_loss, school.contract_speed_down,
                                 all(d.status == "offline" for d in devices))
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


def attribute_incidents(db) -> None:
    """Прогоняет модель по открытым инцидентам — вердикт виден сразу в ленте."""
    from app.services.ml import attribution
    attribution.reload_model()
    open_incidents = db.query(Incident).filter(
        Incident.status.in_(["Новый", "В работе", "Передан поставщику"])).all()
    counts: dict[str, int] = {}
    for incident in open_incidents:
        verdict = attribution.diagnose_incident(db, incident)
        cause = verdict.get("cause", "none")
        counts[cause] = counts.get(cause, 0) + 1
    print(f"  вердикт вынесен по {len(open_incidents)} инцидентам — " +
          ", ".join(f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))


def reset_history(db) -> None:
    """Удаляет сгенерированную историю ПК-агентов (для повторного наполнения)."""
    deleted = db.query(Measurement).filter(Measurement.device_id.like("PC-%")).delete(
        synchronize_session=False)
    db.query(Incident).filter(Incident.incident_number.like("INC-2026-%")).delete(
        synchronize_session=False)
    faults = db.query(FaultEvent).delete(synchronize_session=False)
    db.commit()
    print(f"  удалено замеров: {deleted}, аварий: {faults}")


def drop_orphan_measurements(db) -> None:
    """Замеры от несуществующих устройств (хвост сидера v1) ломают JOIN по ПК."""
    known = {d.device_id for d in db.query(Device.device_id).all()}
    orphans = [row[0] for row in db.query(Measurement.device_id).distinct().all()
               if row[0] not in known]
    if not orphans:
        return
    removed = db.query(Measurement).filter(Measurement.device_id.in_(orphans)).delete(
        synchronize_session=False)
    db.commit()
    print(f"  удалено осиротевших замеров: {removed} ({len(orphans)} ID)")


def main() -> None:
    import sys
    print("Миграция схемы:")
    migrate_schema()
    db = SessionLocal()
    try:
        if "--reset" in sys.argv:
            print("Сброс истории:")
            reset_history(db)
        print("Учётные записи:")
        seed_users(db)
        print("ПК-агенты:")
        devices = seed_devices(db)
        print("История замеров:")
        seed_measurements(db, devices)
        drop_orphan_measurements(db)
        print("Агрегация:")
        recompute_schools(db)
        refresh_incidents(db)
        print("Обучение моделей:")
        train_models(db)
        print("Атрибуция инцидентов:")
        attribute_incidents(db)
        print("\nГотово. Демо-доступы:")
        print("  admin@vko.edu.kz / admin123        — полный доступ")
        print("  operator@vko.edu.kz / operator123  — оператор мониторинга")
        print("  school@vko.edu.kz / school123      — только своя школа")
        print("  provider@kaztelecom.kz / provider123 — только линии провайдера")
    finally:
        db.close()


if __name__ == "__main__":
    main()
