"""
import_vko_osm.py
Импортирует реальные школы Восточно-Казахстанской области из OpenStreetMap (Overpass API).
Запрашивает данные по частям (по районам), чтобы не превысить таймаут.
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

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# VKO districts with bounding boxes and city names
DISTRICTS = [
    {"code": "UK",  "name": "г. Усть-Каменогорск",     "bbox": "49.88,82.45,50.10,82.80"},
    {"code": "RID", "name": "г. Риддер",                 "bbox": "50.30,83.38,50.45,83.60"},
    {"code": "SEM", "name": "г. Семей",                  "bbox": "50.35,80.10,50.50,80.40"},
    {"code": "KRT", "name": "г. Курчатов",               "bbox": "50.70,78.50,50.80,78.70"},
    {"code": "ZYS", "name": "Зайсанский район",           "bbox": "47.30,84.50,47.75,85.10"},
    {"code": "KTN", "name": "Катон-Карагайский район",   "bbox": "49.00,85.00,49.50,86.50"},
    {"code": "KRC", "name": "Курчумский район",           "bbox": "48.30,83.00,48.75,84.50"},
    {"code": "TRB", "name": "Тарбагатайский район",      "bbox": "47.50,81.00,48.10,82.00"},
    {"code": "ULN", "name": "Уланский район",             "bbox": "49.60,82.10,50.00,82.60"},
    {"code": "GLB", "name": "Глубоковский район",         "bbox": "49.90,82.00,50.30,82.60"},
    {"code": "SHM", "name": "Шемонаихинский район",      "bbox": "50.40,81.70,50.80,82.20"},
    {"code": "SMR", "name": "Самарский район",            "bbox": "48.80,82.80,49.20,83.50"},
    {"code": "ALT", "name": "Район Алтай",               "bbox": "49.50,84.00,50.00,84.50"},
]

PROVIDERS = [
    ("АО «Казахтелеком»",  "+7 (800) 080-50-00", 0.65),
    ("Beeline Казахстан",   "+7 (777) 000-99-99", 0.15),
    ("Kcell / Activ",       "+7 (727) 258-83-00", 0.10),
    ("АО «Транстелеком»",  "+7 (800) 080-88-88", 0.07),
    ("Starlink / KazSat",  "+7 (717) 279-55-55", 0.03),
]

CONN_TYPES = ["ВОЛС (Оптика)", "ADSL / Медь", "LTE 4G Роутер", "Radromost РРЛ", "Starlink Спутник"]

FIRST_NAMES = ["Нурлан", "Айдос", "Ерлан", "Серик", "Канат", "Гульнара", "Алия", "Данияр"]
LAST_NAMES  = ["Ахметов", "Смагулов", "Омаров", "Касымов", "Садыков", "Калиев", "Байжанов"]

def pick_provider():
    r = random.random(); cum = 0
    for p, ph, w in PROVIDERS:
        cum += w
        if r <= cum: return p, ph
    return PROVIDERS[0][0], PROVIDERS[0][1]

def query_osm(bbox):
    q = f'[out:json][timeout:30];(node["amenity"="school"]({bbox});way["amenity"="school"]({bbox}););out center 200;'
    data = urllib.parse.urlencode({'data': q}).encode()
    req = urllib.request.Request(
        'https://overpass-api.de/api/interpreter', data=data,
        headers={'User-Agent': 'VKO-Monitor-Hackathon/1.0', 'Accept': 'application/json'}
    )
    with urllib.request.urlopen(req, context=ctx, timeout=35) as resp:
        return json.loads(resp.read().decode('utf-8'))

def main():
    db = SessionLocal()
    school_id = 0
    device_id_counter = 0
    incident_counter = 0
    now = datetime.now()

    all_schools = []

    print("Fetching real school data from OpenStreetMap for VKO districts...")

    for dist in DISTRICTS:
        print(f"  → {dist['name']} ({dist['bbox']})...", end=' ')
        try:
            result = query_osm(dist['bbox'])
            elements = result.get('elements', [])
            # Filter only school-type amenities
            schools = [
                el for el in elements
                if el.get('tags', {}).get('amenity') == 'school'
                and (el.get('lat') or el.get('center'))
            ]
            print(f"{len(schools)} found")
            for el in schools:
                all_schools.append((dist, el))
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(2)  # Be polite to Overpass

    print(f"\nTotal real schools from OSM: {len(all_schools)}")
    print("Enriching with realistic metrics and saving to DB...\n")

    for i, (dist, el) in enumerate(all_schools):
        school_id += 1
        tags = el.get('tags', {})
        center = el.get('center') or {'lat': el.get('lat', 0), 'lon': el.get('lon', 0)}

        name_ru = tags.get('name:ru') or tags.get('name') or f"Школа #{school_id}"
        # Filter out non-school things (universities, etc.) if name contains them
        skip_words = ['университет', 'институт', 'колледж', 'вуз', 'факультет', 'академия', 'техникум']
        if any(w in name_ru.lower() for w in skip_words):
            school_id -= 1
            continue

        code = f"VKO-{dist['code']}-{str(i+1).zfill(3)}"
        prov, prov_phone = pick_provider()
        conn = random.choice(CONN_TYPES)
        contract = random.choice([50.0, 100.0, 100.0, 200.0])

        # Realistic status distribution
        r_stat = random.random()
        if r_stat < 0.87:
            status = "Норма"
            dl = round(contract * random.uniform(0.75, 1.05), 1)
            ul = round(dl * random.uniform(0.7, 1.0), 1)
            ping = round(random.uniform(12, 45), 1)
            jitter = round(random.uniform(1.5, 6), 1)
            loss = round(random.uniform(0, 0.5), 1)
            offline = False
        elif r_stat < 0.94:
            status = "Нестабильно"
            dl = round(random.uniform(13, 19.5), 1)
            ul = round(random.uniform(10, 18), 1)
            ping = round(random.uniform(70, 120), 1)
            jitter = round(random.uniform(15, 35), 1)
            loss = round(random.uniform(1.2, 3.5), 1)
            offline = False
        elif r_stat < 0.98:
            status = "Критично"
            dl = round(random.uniform(2, 12), 1)
            ul = round(random.uniform(1, 8), 1)
            ping = round(random.uniform(150, 380), 1)
            jitter = round(random.uniform(40, 95), 1)
            loss = round(random.uniform(5, 18), 1)
            offline = False
        else:
            status = "Нет соединения"
            dl = ul = ping = jitter = 0.0
            loss = 100.0
            offline = True

        addr = tags.get('addr:full') or tags.get('addr:street', dist['name'])
        if tags.get('addr:housenumber'):
            addr += ', ' + tags['addr:housenumber']

        contact = f"{random.choice(LAST_NAMES)} {random.choice(FIRST_NAMES)}"
        phone   = f"+7 (777) {random.randint(100,999)}-{random.randint(10,99)}-{random.randint(10,99)}"
        email   = f"admin.{code.lower()}@vko.edu.kz"
        last_m  = now - timedelta(minutes=random.randint(3, 90))

        school = School(
            id=school_id, school_id_code=code, name=name_ru,
            region=dist['name'], address=addr,
            lat=round(center['lat'], 6), lng=round(center['lon'], 6),
            provider=prov, connection_type=conn,
            contract_speed_down=contract, contract_speed_up=contract,
            contact_name=contact, contact_phone=phone,
            contact_email=email, provider_phone=prov_phone,
            status=status, current_download=dl, current_upload=ul,
            current_ping=ping, current_jitter=jitter,
            current_packet_loss=loss, last_measurement=last_m
        )
        db.add(school)

        # Primary device
        device_id_counter += 1
        dev_id = f"DEV-{code}-PC1"
        db.add(Device(
            device_id=dev_id, school_id=school_id,
            name="Основной монитор-агент",
            room="Серверная / Шлюз",
            ip_address=f"192.168.{random.randint(1,20)}.{random.randint(10,200)}",
            status="offline" if offline else ("warning" if status != "Норма" else "online"),
            last_seen=last_m
        ))

        # Measurement history (5 points)
        for h in range(1, 6):
            m_time = now - timedelta(hours=h * 4)
            m_dl = dl * random.uniform(0.9, 1.1) if not offline else 0.0
            db.add(Measurement(
                device_id=dev_id, school_id=school_id,
                timestamp=m_time,
                download_speed=round(m_dl, 1),
                upload_speed=round(m_dl * 0.9, 1),
                ping=ping, jitter=jitter, packet_loss=loss,
                is_offline=offline
            ))

        # Create incident for problem schools
        if status in ("Критично", "Нет соединения", "Нестабильно"):
            incident_counter += 1
            db.add(Incident(
                incident_number=f"INC-2026-{str(incident_counter).zfill(4)}",
                school_id=school_id, device_id=dev_id,
                provider=prov, status=random.choice(["Новый","В работе","Передан поставщику"]),
                start_time=last_m,
                description=f"Фиксация нарушения качества связи в \"{name_ru}\". Download: {dl} Мбит/с (по договору: {contract} Мбит/с), Ping: {ping} мс, Loss: {loss}%."
            ))

    db.commit()
    db.close()

    print(f"✅ Saved {school_id} REAL schools from OSM!")
    print(f"   Devices: {device_id_counter}")
    print(f"   Incidents: {incident_counter}")
    print(f"   DB path: {DB_PATH}")

if __name__ == "__main__":
    main()
