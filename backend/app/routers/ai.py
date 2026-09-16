"""Integration / AI API — досудебные претензии и PDF-акты о нарушении SLA."""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Incident, School, User
from ..security import can_access_school, current_user, require_roles, write_audit
from ..services.predictive import analyze
from ..services.reports import sla_report
from ..routers.web import school_brief

log = logging.getLogger("ai")
router = APIRouter(prefix="/api/ai", tags=["Integration / AI API"])

_model = None


def _genai():
    """Ленивая инициализация Gemini — ключ только из окружения."""
    global _model
    if _model is not None:
        return _model or None
    token = settings.GENAI_TOKEN
    if not token:
        _model = False
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=token)
        _model = genai.GenerativeModel(settings.GENAI_MODEL)
    except Exception as exc:  # noqa: BLE001
        log.warning("Gemini недоступен: %s", exc)
        _model = False
    return _model or None


def _fallback_claim(incident: Incident, school: School, analysis: dict) -> str:
    """Детерминированный шаблон, если AI-ключ не задан — демо не ломается."""
    return f"""Руководителю {incident.provider}

от администрации {school.name}
{school.address}

ПРЕТЕНЗИЯ № {incident.incident_number}
от {datetime.now():%d.%m.%Y}

о ненадлежащем качестве услуг связи

Между {school.name} (идентификатор {school.school_id_code}) и {incident.provider} заключён
договор возмездного оказания услуг доступа к сети Интернет с гарантированной скоростью
{school.contract_speed_down} Мбит/с по линии типа «{school.connection_type}».

Системой автономного мониторинга организаций образования ВКО зафиксировано:

  • средняя фактическая скорость загрузки — {analysis['avg_speed']} Мбит/с
    ({round(100 * analysis['avg_speed'] / max(analysis['contract_speed'], 1))}% от договорной);
  • соответствие показателям SLA — {analysis['sla_compliance_pct']}% при нормативе 95%;
  • количество зафиксированных отклонений — {analysis['violations']} из {analysis['samples']} замеров;
  • обстоятельства инцидента — {incident.description};
  • дата и время фиксации — {incident.start_time:%d.%m.%Y %H:%M} (время сервера).

Выявленные повторяющиеся паттерны деградации:
{chr(10).join('  • ' + p['text'] for p in analysis['patterns']) or '  • устойчивых паттернов не выявлено'}

На основании изложенного и в соответствии с Законом Республики Казахстан «О связи»,
Правилами оказания услуг связи и условиями заключённого договора ТРЕБУЕМ:

  1. Устранить нарушение качества услуг в нормативный срок, не превышающий 4 (четырёх) часов
     с момента получения настоящей претензии.
  2. Предоставить официальный акт о причинах и сроках устранения аварии.
  3. Произвести перерасчёт абонентской платы за период предоставления услуг ненадлежащего
     качества.

В случае неисполнения указанных требований оставляем за собой право обратиться в
Инспекцию связи МЦРИАП РК и в судебном порядке взыскать причинённые убытки.

Приложение: акт инструментального контроля показателей качества связи на 1 л.

Ответственное лицо: {school.contact_name}, тел. {school.contact_phone}
"""


@router.post("/claim/{incident_id}", summary="Сгенерировать досудебную претензию")
def generate_claim(incident_id: int, request: Request, db: Session = Depends(get_db),
                   user: User = Depends(require_roles("admin", "operator", "school"))):
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Инцидент не найден")
    school = db.get(School, incident.school_id)
    if not school or not can_access_school(user, school):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа к инциденту")

    analysis = analyze(db, school.id, days=30)
    model = _genai()
    source = "gemini"

    if model:
        prompt = f"""Ты — ведущий юрисконсульт Управления образования Восточно-Казахстанской области.
Составь официальное досудебное письмо-претензию руководству интернет-провайдера {incident.provider}.

Реквизиты:
- Организация: {school.name} ({school.school_id_code}), {school.region}, {school.address}
- Договорная скорость: {school.contract_speed_down} Мбит/с, тип линии: {school.connection_type}
- Инцидент {incident.incident_number} от {incident.start_time}: {incident.description}
- Средняя фактическая скорость за 30 суток: {analysis['avg_speed']} Мбит/с
- Соответствие SLA: {analysis['sla_compliance_pct']}% (норматив 95%)
- Нарушений: {analysis['violations']} из {analysis['samples']} замеров
- Повторяющиеся паттерны: {'; '.join(p['text'] for p in analysis['patterns']) or 'не выявлены'}
- Ответственное лицо: {school.contact_name}, {school.contact_phone}

Структура: шапка (кому/от кого), исходящий номер и дата, ссылки на договор и
законодательство РК о связи, таблица зафиксированных отклонений, требование устранить
в срок не более 4 часов, требование перерасчёта, предупреждение о жалобе в Инспекцию
связи МЦРИАП РК. Стиль строго официальный, готовый к подписанию. Язык — русский."""
        try:
            claim_text = model.generate_content(prompt).text
        except Exception as exc:  # noqa: BLE001
            log.warning("Ошибка Gemini: %s", exc)
            claim_text, source = _fallback_claim(incident, school, analysis), "template"
    else:
        claim_text, source = _fallback_claim(incident, school, analysis), "template"

    incident.ai_claim_text = claim_text
    db.commit()
    write_audit(db, user.email, user.role, "ai.claim_generated", incident.incident_number,
                request.client.host if request.client else "", {"source": source})
    return {"claim_text": claim_text, "source": source, "analysis": analysis}


@router.get("/sla-report/{school_id}", summary="PDF-акт о нарушении SLA",
            response_class=Response)
def sla_pdf(school_id: int, days: int = 30, request: Request = None,
            db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Killer feature #2: доказательная база нарушения SLA одним файлом."""
    school = db.get(School, school_id)
    if not school:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Школа не найдена")
    if not can_access_school(user, school):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа")

    analysis = analyze(db, school_id, days=days)
    incidents = [{"incident_number": i.incident_number,
                  "start_time": i.start_time.isoformat() if i.start_time else "",
                  "status": i.status, "description": i.description}
                 for i in db.query(Incident).filter(Incident.school_id == school_id)
                 .order_by(Incident.id.desc()).limit(12).all()]
    payload = {**school_brief(school), "contact_name": school.contact_name,
               "contact_phone": school.contact_phone}
    pdf = sla_report(analysis, payload, incidents)

    write_audit(db, user.email, user.role, "ai.sla_report", school.school_id_code,
                request.client.host if request and request.client else "",
                {"days": days, "compliance": analysis["sla_compliance_pct"]})
    filename = f"SLA_{school.school_id_code}_{datetime.now():%Y%m%d}.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/status", summary="Состояние AI-интеграции")
def ai_status():
    return {"provider": "Google Gemini", "model": settings.GENAI_MODEL,
            "configured": bool(settings.GENAI_TOKEN),
            "fallback": "детерминированный юридический шаблон"}
