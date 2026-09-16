from flask import Flask, request, jsonify
from flask_cors import CORS
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, Text, func
from sqlalchemy.orm import declarative_base, sessionmaker
import os
import google.generativeai as genai
from datetime import datetime

app = Flask(__name__)
CORS(app)

DB_PATH = os.path.join(os.path.dirname(__file__), 'hackathon.db')
engine = create_engine(f'sqlite:///{DB_PATH}', connect_args={'check_same_thread': False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class School(Base):
    __tablename__ = "schools"
    id = Column(Integer, primary_key=True, index=True)
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
    incident_number = Column(String)
    school_id = Column(Integer)
    device_id = Column(String)
    provider = Column(String)
    status = Column(String)
    start_time = Column(DateTime)
    resolved_time = Column(DateTime, nullable=True)
    description = Column(String)
    ai_claim_text = Column(Text, nullable=True)

# Setup Gemini API
GENAI_TOKEN = os.environ.get("GENAI_TOKEN", "AQ.Ab8RN6JSvLVuyizpeOFFb_D0PrYuS3A9qWoYy2cnq0sYvBxCGA")
try:
    genai.configure(api_key=GENAI_TOKEN)
    model = genai.GenerativeModel('gemini-1.5-pro')
except Exception as e:
    print(f"Warning: could not configure Gemini: {e}")
    model = None

@app.route("/")
def read_root():
    return jsonify({"status": "ok", "message": "VKO Schools Internet Monitoring API"})

@app.route("/api/schools", methods=["GET"])
def get_schools():
    db = SessionLocal()
    # Support query params
    region = request.args.get("region")
    provider = request.args.get("provider")
    status = request.args.get("status")
    conn_type = request.args.get("connection_type")

    query = db.query(School)
    if region and region != "Все районы":
        query = query.filter(School.region == region)
    if provider and provider != "Все провайдеры":
        query = query.filter(School.provider == provider)
    if status and status != "Все статусы":
        query = query.filter(School.status == status)
    if conn_type and conn_type != "Все типы":
        query = query.filter(School.connection_type == conn_type)

    schools = query.all()
    res = [{
        "id": s.id,
        "school_id_code": s.school_id_code,
        "name": s.name,
        "region": s.region,
        "address": s.address,
        "lat": s.lat,
        "lng": s.lng,
        "provider": s.provider,
        "connection_type": s.connection_type,
        "contract_speed_down": s.contract_speed_down,
        "contract_speed_up": s.contract_speed_up,
        "contact_name": s.contact_name,
        "contact_phone": s.contact_phone,
        "contact_email": s.contact_email,
        "provider_phone": s.provider_phone,
        "status": s.status,
        "current_download": s.current_download,
        "current_upload": s.current_upload,
        "current_ping": s.current_ping,
        "current_jitter": s.current_jitter,
        "current_packet_loss": s.current_packet_loss,
        "last_measurement": s.last_measurement.isoformat() if s.last_measurement else None
    } for s in schools]
    db.close()
    return jsonify(res)

@app.route("/api/schools/<int:school_id>", methods=["GET"])
def get_school_detail(school_id):
    db = SessionLocal()
    school = db.query(School).filter(School.id == school_id).first()
    if not school:
        db.close()
        return jsonify({"detail": "School not found"}), 404
        
    devices = db.query(Device).filter(Device.school_id == school_id).all()
    incidents = db.query(Incident).filter(Incident.school_id == school_id).order_by(Incident.id.desc()).all()
    
    res = {
        "id": school.id,
        "school_id_code": school.school_id_code,
        "name": school.name,
        "region": school.region,
        "address": school.address,
        "lat": school.lat,
        "lng": school.lng,
        "provider": school.provider,
        "connection_type": school.connection_type,
        "contract_speed_down": school.contract_speed_down,
        "contract_speed_up": school.contract_speed_up,
        "contact_name": school.contact_name,
        "contact_phone": school.contact_phone,
        "contact_email": school.contact_email,
        "provider_phone": school.provider_phone,
        "status": school.status,
        "current_download": school.current_download,
        "current_upload": school.current_upload,
        "current_ping": school.current_ping,
        "current_jitter": school.current_jitter,
        "current_packet_loss": school.current_packet_loss,
        "last_measurement": school.last_measurement.isoformat() if school.last_measurement else None,
        "devices": [{
            "id": d.id,
            "device_id": d.device_id,
            "name": d.name,
            "room": d.room,
            "ip_address": d.ip_address,
            "status": d.status,
            "last_seen": d.last_seen.isoformat() if d.last_seen else None
        } for d in devices],
        "incidents": [{
            "id": i.id,
            "incident_number": i.incident_number,
            "status": i.status,
            "start_time": i.start_time.isoformat() if i.start_time else None,
            "description": i.description
        } for i in incidents]
    }
    db.close()
    return jsonify(res)

@app.route("/api/schools/<int:school_id>/measurements", methods=["GET"])
def get_school_measurements(school_id):
    db = SessionLocal()
    measurements = db.query(Measurement).filter(Measurement.school_id == school_id).order_by(Measurement.timestamp.asc()).all()
    res = [{
        "timestamp": m.timestamp.isoformat() if m.timestamp else None,
        "download_speed": m.download_speed,
        "upload_speed": m.upload_speed,
        "ping": m.ping,
        "jitter": m.jitter,
        "packet_loss": m.packet_loss,
        "is_offline": m.is_offline
    } for m in measurements]
    db.close()
    return jsonify(res)

@app.route("/api/incidents", methods=["GET"])
def get_incidents():
    db = SessionLocal()
    incidents = db.query(Incident).order_by(Incident.id.desc()).limit(100).all()
    schools_dict = {s.id: s.name for s in db.query(School).all()}
    res = [{
        "id": i.id,
        "incident_number": i.incident_number,
        "school_id": i.school_id,
        "school_name": schools_dict.get(i.school_id, f"Школа #{i.school_id}"),
        "device_id": i.device_id,
        "provider": i.provider,
        "status": i.status,
        "start_time": i.start_time.isoformat() if i.start_time else None,
        "description": i.description,
        "ai_claim_text": i.ai_claim_text
    } for i in incidents]
    db.close()
    return jsonify(res)

@app.route("/api/analytics", methods=["GET"])
def get_analytics():
    db = SessionLocal()
    total_schools = db.query(School).count()
    total_devices = db.query(Device).count()
    total_incidents = db.query(Incident).filter(Incident.status.in_(["Новый", "В работе", "Передан поставщику"])).count()
    
    avg_down = db.query(func.avg(School.current_download)).filter(School.status != "Нет соединения").scalar() or 0.0
    avg_up = db.query(func.avg(School.current_upload)).filter(School.status != "Нет соединения").scalar() or 0.0
    avg_ping = db.query(func.avg(School.current_ping)).filter(School.status != "Нет соединения").scalar() or 0.0
    
    normal_count = db.query(School).filter(School.status == "Норма").count()
    unstable_count = db.query(School).filter(School.status == "Нестабильно").count()
    critical_count = db.query(School).filter(School.status == "Критично").count()
    offline_count = db.query(School).filter(School.status == "Нет соединения").count()

    regions = [r[0] for r in db.query(School.region).distinct().all()]
    providers = [p[0] for p in db.query(School.provider).distinct().all()]
    conn_types = [c[0] for c in db.query(School.connection_type).distinct().all()]

    db.close()
    return jsonify({
        "total_schools": total_schools,
        "total_devices": total_devices,
        "active_incidents": total_incidents,
        "avg_download": round(avg_down, 1),
        "avg_upload": round(avg_up, 1),
        "avg_ping": round(avg_ping, 1),
        "status_counts": {
            "normal": normal_count,
            "unstable": unstable_count,
            "critical": critical_count,
            "offline": offline_count
        },
        "filters": {
            "regions": ["Все районы"] + regions,
            "providers": ["Все провайдеры"] + providers,
            "connection_types": ["Все типы"] + conn_types,
            "statuses": ["Все статусы", "Норма", "Нестабильно", "Критично", "Нет соединения"]
        }
    })

@app.route("/api/generate-claim/<int:incident_id>", methods=["POST"])
def generate_claim(incident_id):
    db = SessionLocal()
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        db.close()
        return jsonify({"detail": "Incident not found"}), 404
        
    school = db.query(School).filter(School.id == incident.school_id).first()
    
    prompt = f"""
    Ты — ведущий юрисконсульт и технический эксперт Управления образования Восточно-Казахстанской области (ВКО).
    Составь официальное досудебное письмо-претензию руководству интернет-провайдера {incident.provider} 
    по факту нарушения условий публичного договора оказания услуг связи для образовательного учреждения:
    
    Реквизиты школы:
    - Наименование: {school.name if school else 'Школа ВКО'}
    - Идентификатор (School ID): {school.school_id_code if school else 'Н/Д'}
    - Район / Населенный пункт: {school.region if school else 'ВКО'}
    - Адрес: {school.address if school else 'Н/Д'}
    - Договорная гарантированная скорость: {school.contract_speed_down if school else 100} Мбит/с
    - Фактические параметры инцидента: {incident.description}
    - Дата и время фиксации сбоя: {incident.start_time}
    - Номер зарегистрированного инцидента: {incident.incident_number}
    - Ответственное лицо школы: {school.contact_name if school else 'Администрация'}, тел: {school.contact_phone if school else 'Н/Д'}

    Письмо должно содержать:
    1. Шапку (Куда: Руководству {incident.provider}, От кого: Администрация {school.name if school else 'школы'})
    2. Исходящий номер и дату
    3. Ссылки на договор и законодательство Республики Казахстан о связи и стандарты качества услуг
    4. Таблицу или четкий перечень зафиксированных параметров несоответствия (Down/Up/Ping/Loss)
    5. Требование устранить аварию в нормативный срок (не более 4 часов) и предоставить официальный акт
    6. Предупреждение о перерасчете абонентской платы и направлении жалобы в Инспекцию связи МЦРИАП РК
    
    Стиль: Строго официальный, юридически выверенный, готовый к подписанию и отправке.
    """
    
    try:
        if model:
            response = model.generate_content(prompt)
            claim_text = response.text
        else:
            claim_text = "AI модель не сконфигурирована."
            
        incident.ai_claim_text = claim_text
        db.commit()
        db.close()
        return jsonify({"claim_text": claim_text})
    except Exception as e:
        db.close()
        return jsonify({"detail": str(e)}), 500

@app.route("/api/measurements", methods=["POST"])
def add_measurement():
    data = request.json
    db = SessionLocal()
    
    ts_str = data.get("timestamp")
    try:
        ts = datetime.fromisoformat(ts_str) if ts_str else datetime.now()
    except ValueError:
        ts = datetime.now()

    down = float(data.get("download_speed", 0))
    up = float(data.get("upload_speed", 0))
    ping = float(data.get("ping", 0))
    jitter = float(data.get("jitter", 0))
    loss = float(data.get("packet_loss", 0))
    is_offline = data.get("is_offline", False)
    school_id = data.get("school_id", 1)
    device_id = data.get("device_id", "AGENT-01")

    # Update school current metrics
    school = db.query(School).filter(School.id == school_id).first()
    if school:
        school.current_download = down
        school.current_upload = up
        school.current_ping = ping
        school.current_jitter = jitter
        school.current_packet_loss = loss
        school.last_measurement = ts
        if is_offline or down == 0:
            school.status = "Нет соединения"
        elif down < 15 or ping > 120 or loss > 5:
            school.status = "Критично"
        elif down < 20 or ping > 80 or loss > 2:
            school.status = "Нестабильно"
        else:
            school.status = "Норма"

    measurement = Measurement(
        device_id=device_id,
        school_id=school_id,
        timestamp=ts,
        download_speed=down,
        upload_speed=up,
        ping=ping,
        jitter=jitter,
        packet_loss=loss,
        is_offline=is_offline
    )
    db.add(measurement)

    if (down < 20 or is_offline) and school:
        existing = db.query(Incident).filter(
            Incident.school_id == school_id, 
            Incident.status.in_(["Новый", "В работе"])
        ).first()
        if not existing:
            inc_count = db.query(Incident).count() + 1
            inc = Incident(
                incident_number=f"INC-2026-{str(inc_count).zfill(4)}",
                school_id=school_id,
                device_id=device_id,
                provider=school.provider,
                status="Новый",
                start_time=ts,
                description=f"Низкая скорость: {down} Мбит/с (договор: {school.contract_speed_down} Мбит/с), Ping: {ping} мс, Loss: {loss}%."
            )
            db.add(inc)

    db.commit()
    db.close()
    return jsonify({"status": "success"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
