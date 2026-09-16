"""
retry_failed_districts.py — повторный запрос для 6 районов, которые упали в основном импорте
"""
import urllib.request, urllib.parse, json, ssl, time, os, random
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker

DB_PATH = os.path.join(os.path.dirname(__file__), 'hackathon.db')
engine = create_engine(f'sqlite:///{DB_PATH}', connect_args={'check_same_thread': False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class School(Base):
    __tablename__ = "schools"
    id = Column(Integer, primary_key=True)
    school_id_code = Column(String, index=True)
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
    status = Column(String)
    current_download = Column(Float)
    current_upload = Column(Float)
    current_ping = Column(Float)
    current_jitter = Column(Float)
    current_packet_loss = Column(Float)
    last_measurement = Column(DateTime)

class Device(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True)
    device_id = Column(String, unique=True)
    school_id = Column(Integer)
    name = Column(String)
    room = Column(String)
    ip_address = Column(String)
    status = Column(String)
    last_seen = Column(DateTime)

class Measurement(Base):
    __tablename__ = "measurements"
    id = Column(Integer, primary_key=True)
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
    id = Column(Integer, primary_key=True)
    incident_number = Column(String)
    school_id = Column(Integer)
    device_id = Column(String)
    provider = Column(String)
    status = Column(String)
    start_time = Column(DateTime)
    resolved_time = Column(DateTime, nullable=True)
    description = Column(String)
    ai_claim_text = Column(Text, nullable=True)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# Пропущенные районы — разбиты на меньшие bbox чтобы не таймаутиться
RETRY_DISTRICTS = [
    # Усть-Каменогорск — делим на 2 части
    {"code": "UK",  "name": "г. Усть-Каменогорск", "bbox": "49.88,82.45,49.99,82.65"},
    {"code": "UK",  "name": "г. Усть-Каменогорск", "bbox": "49.99,82.60,50.10,82.80"},
    # Катон-Карагайский
    {"code": "KTN", "name": "Катон-Карагайский район", "bbox": "49.00,85.00,49.25,86.00"},
    {"code": "KTN", "name": "Катон-Карагайский район", "bbox": "49.25,85.50,49.50,86.50"},
    # Курчумский
    {"code": "KRC", "name": "Курчумский район", "bbox": "48.30,83.00,48.55,84.00"},
    {"code": "KRC", "name": "Курчумский район", "bbox": "48.55,83.50,48.75,84.50"},
    # Тарбагатайский
    {"code": "TRB", "name": "Тарбагатайский район", "bbox": "47.50,81.00,47.80,81.50"},
    {"code": "TRB", "name": "Тарбагатайский район", "bbox": "47.80,81.20,48.10,82.00"},
    # Глубоковский
    {"code": "GLB", "name": "Глубоковский район", "bbox": "49.90,82.00,50.10,82.40"},
    {"code": "GLB", "name": "Глубоковский район", "bbox": "50.10,82.10,50.30,82.60"},
    # Район Алтай
    {"code": "ALT", "name": "Район Алтай", "bbox": "49.50,84.00,49.75,84.50"},
    {"code": "ALT", "name": "Район Алтай", "bbox": "49.75,83.80,50.00,84.50"},
]

PROVIDERS = [
    ("АО «Казахтелеком»",  "+7 (800) 080-50-00", 0.65),
    ("Beeline Казахстан",   "+7 (777) 000-99-99", 0.15),
    ("Kcell / Activ",       "+7 (727) 258-83-00", 0.10),
    ("АО «Транстелеком»",  "+7 (800) 080-88-88", 0.07),
    ("Starlink / KazSat",  "+7 (717) 279-55-55", 0.03),
]
CONN_TYPES  = ["ВОЛС (Оптика)", "ADSL / Медь", "LTE 4G Роутер", "Radromost РРЛ", "Starlink Спутник"]
FIRST_NAMES = ["Нурлан", "Айдос", "Ерлан", "Серик", "Канат", "Гульнара", "Алия", "Данияр"]
LAST_NAMES  = ["Ахметов", "Смагулов", "Омаров", "Касымов", "Садыков", "Калиев", "Байжанов"]

def pick_provider():
    r = random.random(); cum = 0
    for p, ph, w in PROVIDERS:
        cum += w
        if r <= cum: return p, ph
    return PROVIDERS[0][0], PROVIDERS[0][1]

def query_osm(bbox, retries=3):
    q = f'[out:json][timeout:25];(node["amenity"="school"]({bbox});way["amenity"="school"]({bbox}););out center 150;'
    data = urllib.parse.urlencode({'data': q}).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                'https://overpass-api.de/api/interpreter', data=data,
                headers={'User-Agent': 'VKO-Monitor/1.0', 'Accept': 'application/json'}
            )
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            if attempt < retries - 1:
                wait = 10 * (attempt + 1)
                print(f"    Retry {attempt+1} in {wait}s ({e})")
                time.sleep(wait)
            else:
                raise
    return {'elements': []}

def add_school(db, school_id, dist, el, incident_counter):
    tags = el.get('tags', {})
    center = el.get('center') or {'lat': el.get('lat', 0), 'lon': el.get('lon', 0)}
    name_ru = tags.get('name:ru') or tags.get('name') or f"Школа #{school_id}"

    skip_words = ['университет', 'институт', 'колледж', 'факультет', 'академия', 'техникум', 'вуз']
    if any(w in name_ru.lower() for w in skip_words):
        return False, incident_counter

    code = f"VKO-{dist['code']}-R{school_id}"
    prov, prov_phone = pick_provider()
    conn = random.choice(CONN_TYPES)
    contract = random.choice([50.0, 100.0, 100.0, 200.0])
    now = datetime.now()

    r_stat = random.random()
    if r_stat < 0.87:
        status, dl = "Норма",          round(contract * random.uniform(0.75, 1.05), 1)
        ul   = round(dl * random.uniform(0.7, 1.0), 1)
        ping = round(random.uniform(12, 45), 1); jitter = round(random.uniform(1.5, 6), 1)
        loss = round(random.uniform(0, 0.5), 1); offline = False
    elif r_stat < 0.94:
        status, dl = "Нестабильно",    round(random.uniform(13, 19.5), 1)
        ul   = round(random.uniform(10, 18), 1)
        ping = round(random.uniform(70, 120), 1); jitter = round(random.uniform(15, 35), 1)
        loss = round(random.uniform(1.2, 3.5), 1); offline = False
    elif r_stat < 0.98:
        status, dl = "Критично",       round(random.uniform(2, 12), 1)
        ul   = round(random.uniform(1, 8), 1)
        ping = round(random.uniform(150, 380), 1); jitter = round(random.uniform(40, 95), 1)
        loss = round(random.uniform(5, 18), 1); offline = False
    else:
        status = "Нет соединения"; dl = ul = ping = jitter = 0.0; loss = 100.0; offline = True

    addr   = tags.get('addr:street', dist['name'])
    last_m = now - timedelta(minutes=random.randint(3, 90))

    db.add(School(
        id=school_id, school_id_code=code, name=name_ru,
        region=dist['name'], address=addr,
        lat=round(center['lat'], 6), lng=round(center['lon'], 6),
        provider=prov, connection_type=conn,
        contract_speed_down=contract, contract_speed_up=contract,
        contact_name=f"{random.choice(LAST_NAMES)} {random.choice(FIRST_NAMES)}",
        contact_phone=f"+7 (777) {random.randint(100,999)}-{random.randint(10,99)}-{random.randint(10,99)}",
        contact_email=f"admin.{code.lower()}@vko.edu.kz",
        provider_phone=prov_phone, status=status,
        current_download=dl, current_upload=ul,
        current_ping=ping, current_jitter=jitter,
        current_packet_loss=loss, last_measurement=last_m
    ))

    dev_id = f"DEV-{code}-PC1"
    db.add(Device(
        device_id=dev_id, school_id=school_id, name="Монитор-агент",
        room="Серверная", ip_address=f"192.168.{random.randint(1,20)}.{random.randint(10,200)}",
        status="offline" if offline else ("warning" if status != "Норма" else "online"),
        last_seen=last_m
    ))

    for h in range(1, 6):
        m_dl = dl * random.uniform(0.9, 1.1) if not offline else 0.0
        db.add(Measurement(
            device_id=dev_id, school_id=school_id,
            timestamp=now - timedelta(hours=h * 4),
            download_speed=round(m_dl, 1), upload_speed=round(m_dl * 0.9, 1),
            ping=ping, jitter=jitter, packet_loss=loss, is_offline=offline
        ))

    if status in ("Критично", "Нет соединения", "Нестабильно"):
        incident_counter += 1
        db.add(Incident(
            incident_number=f"INC-2026-R{str(incident_counter).zfill(3)}",
            school_id=school_id, device_id=dev_id, provider=prov,
            status=random.choice(["Новый", "В работе", "Передан поставщику"]),
            start_time=last_m,
            description=f"Нарушение качества связи: {name_ru}. Download: {dl} Мбит/с (договор: {contract} Мбит/с), Ping: {ping} мс, Loss: {loss}%."
        ))

    return True, incident_counter

def main():
    db = SessionLocal()

    # Find current max ID
    from sqlalchemy import func
    max_id = db.query(func.max(School.id)).scalar() or 0
    max_inc = db.query(func.max(Incident.id)).scalar() or 0
    school_id = max_id
    incident_counter = max_inc

    # Track already-saved lat/lng to avoid duplicates
    existing = {(s.lat, s.lng) for s in db.query(School.lat, School.lng).all()}

    added = 0
    seen_latlon = set()

    print(f"Current DB: {max_id} schools, {max_inc} incidents")
    print(f"Retrying {len(RETRY_DISTRICTS)} district sub-queries...\n")

    for dist in RETRY_DISTRICTS:
        print(f"  → {dist['name']} [{dist['bbox']}]...", end=' ', flush=True)
        try:
            result = query_osm(dist['bbox'])
            elements = [
                el for el in result.get('elements', [])
                if el.get('tags', {}).get('amenity') == 'school'
                and (el.get('lat') or el.get('center'))
            ]
            new_count = 0
            for el in elements:
                center = el.get('center') or {'lat': el.get('lat', 0), 'lon': el.get('lon', 0)}
                key = (round(center['lat'], 5), round(center['lon'], 5))
                if key in existing or key in seen_latlon:
                    continue
                seen_latlon.add(key)
                school_id += 1
                ok, incident_counter = add_school(db, school_id, dist, el, incident_counter)
                if ok:
                    added += 1
                    new_count += 1
                else:
                    school_id -= 1
            print(f"{len(elements)} raw → {new_count} new added")
        except Exception as e:
            print(f"FAILED: {e}")
        time.sleep(5)

    db.commit()
    db.close()

    print(f"\n✅ Added {added} more real schools!")
    print(f"   Total schools in DB: {max_id + added}")

if __name__ == "__main__":
    main()
