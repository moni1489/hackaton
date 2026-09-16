import random
import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker

DB_PATH = os.path.join(os.path.dirname(__file__), 'hackathon.db')
engine = create_engine(f'sqlite:///{DB_PATH}', connect_args={'check_same_thread': False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class School(Base):
    __tablename__ = "schools"
    id = Column(Integer, primary_key=True, index=True)
    school_id_code = Column(String, index=True) # e.g. VKO-UK-001
    name = Column(String)
    region = Column(String)
    address = Column(String)
    lat = Column(Float)
    lng = Column(Float)
    provider = Column(String)
    connection_type = Column(String)
    contract_speed_down = Column(Float)
    contract_speed_up = Column(Float)
    contact_name = Column(String)
    contact_phone = Column(String)
    contact_email = Column(String)
    provider_phone = Column(String)
    status = Column(String) # 'Норма', 'Нестабильно', 'Критично', 'Нет соединения'
    current_download = Column(Float)
    current_upload = Column(Float)
    current_ping = Column(Float)
    current_jitter = Column(Float)
    current_packet_loss = Column(Float)
    last_measurement = Column(DateTime)

class Device(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, unique=True, index=True)
    school_id = Column(Integer)
    name = Column(String)
    room = Column(String)
    ip_address = Column(String)
    status = Column(String)
    last_seen = Column(DateTime)

class Measurement(Base):
    __tablename__ = "measurements"
    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String)
    school_id = Column(Integer)
    timestamp = Column(DateTime)
    download_speed = Column(Float)
    upload_speed = Column(Float)
    ping = Column(Float)
    jitter = Column(Float)
    packet_loss = Column(Float)
    is_offline = Column(Boolean)

class Incident(Base):
    __tablename__ = "incidents"
    id = Column(Integer, primary_key=True, index=True)
    incident_number = Column(String) # e.g. INC-2026-0012
    school_id = Column(Integer)
    device_id = Column(String)
    provider = Column(String)
    status = Column(String) # 'Новый', 'Передан поставщику', 'В работе', 'Ожидает информации', 'Устранен', 'Закрыт'
    start_time = Column(DateTime)
    resolved_time = Column(DateTime, nullable=True)
    description = Column(String)
    ai_claim_text = Column(Text, nullable=True)

# Drop & recreate tables to match updated schema
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

DISTRICTS = [
    {
        "name": "г. Усть-Каменогорск",
        "code": "UK",
        "lat_range": (49.92, 50.02),
        "lng_range": (82.55, 82.72),
        "school_count": 52
    },
    {
        "name": "г. Риддер",
        "code": "RID",
        "lat_range": (50.32, 50.40),
        "lng_range": (83.45, 83.58),
        "school_count": 18
    },
    {
        "name": "Район Алтай",
        "code": "ALT",
        "lat_range": (49.70, 49.85),
        "lng_range": (84.20, 84.35),
        "school_count": 36
    },
    {
        "name": "Глубоковский район",
        "code": "GLB",
        "lat_range": (50.10, 50.25),
        "lng_range": (82.25, 82.45),
        "school_count": 31
    },
    {
        "name": "Зайсанский район",
        "code": "ZYS",
        "lat_range": (47.45, 47.60),
        "lng_range": (84.80, 85.05),
        "school_count": 27
    },
    {
        "name": "Катон-Карагайский район",
        "code": "KTN",
        "lat_range": (49.15, 49.30),
        "lng_range": (85.55, 85.75),
        "school_count": 28
    },
    {
        "name": "Курчумский район",
        "code": "KRC",
        "lat_range": (48.50, 48.65),
        "lng_range": (83.60, 83.80),
        "school_count": 29
    },
    {
        "name": "Самарский район",
        "code": "SMR",
        "lat_range": (49.00, 49.15),
        "lng_range": (83.25, 83.45),
        "school_count": 22
    },
    {
        "name": "Тарбагатайский район",
        "code": "TRB",
        "lat_range": (47.65, 47.85),
        "lng_range": (81.50, 81.80),
        "school_count": 38
    },
    {
        "name": "Уланский район",
        "code": "ULN",
        "lat_range": (49.80, 49.95),
        "lng_range": (82.40, 82.60),
        "school_count": 33
    },
    {
        "name": "Шемонаихинский район",
        "code": "SHM",
        "lat_range": (50.55, 50.70),
        "lng_range": (81.85, 82.05),
        "school_count": 28
    },
]

PROVIDERS = [
    ("АО «Казахтелеком»", "+7 (800) 080-50-00", 0.65),
    ("Beeline Казахстан", "+7 (777) 000-99-99", 0.15),
    ("Kcell / Activ", "+7 (727) 258-83-00", 0.10),
    ("АО «Транстелеком»", "+7 (800) 080-88-88", 0.07),
    ("Starlink / KazSat", "+7 (717) 279-55-55", 0.03)
]

CONN_TYPES = ["ВОЛС (Оптика)", "ADSL / Медь", "Starlink Спутник", "Радиомост РРЛ", "LTE 4G Роутер"]

FIRST_NAMES = ["Нурлан", "Айдос", "Ерлан", "Серик", "Канат", "Гульнара", "Алия", "Бахыт", "Данияр", "Асхат", "Динара", "Марат"]
LAST_NAMES = ["Ахметов", "Смагулов", "Омаров", "Касымов", "Сулейменов", "Садыков", "Ибраев", "Токтаров", "Базарбаев", "Калиев"]

def choose_weighted_provider():
    r = random.random()
    cum = 0
    for prov, phone, weight in PROVIDERS:
        cum += weight
        if r <= cum:
            return prov, phone
    return PROVIDERS[0][0], PROVIDERS[0][1]

def seed_database():
    db = SessionLocal()
    school_counter = 0
    device_counter = 0
    incident_counter = 0
    
    total_target_schools = sum(d["school_count"] for d in DISTRICTS) # exactly 342!
    print(f"Generating realistic dataset for {total_target_schools} schools in East Kazakhstan Region (ВКО)...")

    now = datetime.now()

    for d in DISTRICTS:
        for i in range(1, d["school_count"] + 1):
            school_counter += 1
            code = f"VKO-{d['code']}-{str(i).zfill(3)}"
            
            # School type
            r_type = random.random()
            if r_type < 0.15:
                s_type = "Школа-лицей"
            elif r_type < 0.25:
                s_type = "Школа-гимназия"
            elif r_type < 0.40:
                s_type = "Основная средняя школа"
            else:
                s_type = "Средняя общеобразовательная школа"
                
            name = f"{s_type} №{i} ({d['name']})"
            address = f"{d['name']}, ул. Абая, {random.randint(1, 140)}"

            lat = round(random.uniform(d["lat_range"][0], d["lat_range"][1]), 5)
            lng = round(random.uniform(d["lng_range"][0], d["lng_range"][1]), 5)

            prov, prov_phone = choose_weighted_provider()
            conn_type = random.choice(CONN_TYPES)
            contract_down = random.choice([50.0, 100.0, 200.0, 50.0, 100.0])
            contract_up = contract_down

            # Determine status realistically:
            # 88% Normal, 7% Unstable, 3% Critical, 2% Offline
            stat_rand = random.random()
            if stat_rand < 0.88:
                status = "Норма"
                curr_down = round(contract_down * random.uniform(0.75, 1.05), 1)
                curr_up = round(contract_up * random.uniform(0.70, 1.0), 1)
                curr_ping = round(random.uniform(12, 45), 1)
                curr_jitter = round(random.uniform(1.5, 6.0), 1)
                curr_loss = round(random.uniform(0.0, 0.5), 1)
                is_off = False
            elif stat_rand < 0.95:
                status = "Нестабильно"
                curr_down = round(random.uniform(14.0, 19.5), 1)
                curr_up = round(random.uniform(10.0, 18.0), 1)
                curr_ping = round(random.uniform(70, 120), 1)
                curr_jitter = round(random.uniform(15.0, 35.0), 1)
                curr_loss = round(random.uniform(1.2, 3.5), 1)
                is_off = False
            elif stat_rand < 0.98:
                status = "Критично"
                curr_down = round(random.uniform(2.0, 12.0), 1)
                curr_up = round(random.uniform(1.0, 8.0), 1)
                curr_ping = round(random.uniform(150, 380), 1)
                curr_jitter = round(random.uniform(40.0, 95.0), 1)
                curr_loss = round(random.uniform(5.0, 18.0), 1)
                is_off = False
            else:
                status = "Нет соединения"
                curr_down = 0.0
                curr_up = 0.0
                curr_ping = 0.0
                curr_jitter = 0.0
                curr_loss = 100.0
                is_off = True

            contact_n = f"{random.choice(LAST_NAMES)} {random.choice(FIRST_NAMES)}"
            contact_p = f"+7 (777) {random.randint(100, 999)}-{random.randint(10, 99)}-{random.randint(10, 99)}"
            contact_e = f"admin.{code.lower()}@vko.edu.kz"

            last_m_time = now - timedelta(minutes=random.randint(2, 85))

            school = School(
                id=school_counter,
                school_id_code=code,
                name=name,
                region=d["name"],
                address=address,
                lat=lat,
                lng=lng,
                provider=prov,
                connection_type=conn_type,
                contract_speed_down=contract_down,
                contract_speed_up=contract_up,
                contact_name=contact_n,
                contact_phone=contact_p,
                contact_email=contact_e,
                provider_phone=prov_phone,
                status=status,
                current_download=curr_down,
                current_upload=curr_up,
                current_ping=curr_ping,
                current_jitter=curr_jitter,
                current_packet_loss=curr_loss,
                last_measurement=last_m_time
            )
            db.add(school)

            # Generate 3 devices per school on average (342 * 3 = 1026, close to 1024)
            num_devs = random.choice([2, 3, 3, 4])
            primary_dev_id = None
            for dev_idx in range(1, num_devs + 1):
                device_counter += 1
                dev_id = f"DEV-{code}-PC{dev_idx}"
                if dev_idx == 1:
                    primary_dev_id = dev_id
                    room = "Серверная (Шлюз/Агент №1)"
                    d_name = "Основной монитор-агент"
                elif dev_idx == 2:
                    room = "Кабинет информатики №1"
                    d_name = "Учительский ПК"
                else:
                    room = f"Кабинет №{dev_idx*10}"
                    d_name = f"Рабочая станция {dev_idx}"

                d_status = "offline" if is_off else ("warning" if status != "Норма" else "online")
                dev = Device(
                    device_id=dev_id,
                    school_id=school_counter,
                    name=d_name,
                    room=room,
                    ip_address=f"192.168.{random.randint(1, 20)}.{random.randint(10, 200)}",
                    status=d_status,
                    last_seen=last_m_time
                )
                db.add(dev)

                # Add some measurements history for this primary device
                if dev_idx == 1:
                    for h in range(1, 6):
                        m_time = now - timedelta(hours=h*4)
                        m_down = curr_down * random.uniform(0.9, 1.1) if not is_off else 0.0
                        db.add(Measurement(
                            device_id=dev_id,
                            school_id=school_counter,
                            timestamp=m_time,
                            download_speed=round(m_down, 1),
                            upload_speed=round(m_down * 0.9, 1),
                            ping=curr_ping,
                            jitter=curr_jitter,
                            packet_loss=curr_loss,
                            is_offline=is_off
                        ))

            # If Critical or No Connection or Unstable, create an incident!
            if status in ["Критично", "Нет соединения", "Нестабильно"]:
                incident_counter += 1
                inc_num = f"INC-2026-{str(incident_counter).zfill(4)}"
                inc_status = random.choice(["Новый", "Передан поставщику", "В работе", "Ожидает информации"])
                db.add(Incident(
                    incident_number=inc_num,
                    school_id=school_counter,
                    device_id=primary_dev_id or f"DEV-{code}-PC1",
                    provider=prov,
                    status=inc_status,
                    start_time=last_m_time,
                    description=f"Фиксация устойчивого падения качества связи в {name}. Скорость: {curr_down} Мбит/с (договор: {contract_down} Мбит/с), Ping: {curr_ping} мс, Потеря пакетов: {curr_loss}%."
                ))

    db.commit()
    print(f"Successfully generated {school_counter} schools, {device_counter} devices, and {incident_counter} incidents in SQLite!")
    db.close()

if __name__ == "__main__":
    seed_database()
