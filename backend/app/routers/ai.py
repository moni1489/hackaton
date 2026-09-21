"""Integration / AI API — досудебные претензии и PDF-акты о нарушении SLA."""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Incident, School, User
from ..security import can_access_school, current_user, require_roles, write_audit
from ..services.ml import attribution, forecast as ml_forecast
from ..services.predictive import analyze
from ..services.reports import sla_report
from ..services.status import naive_utc, thresholds
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


def _verdict_block(verdict: dict | None) -> str:
    """Предполагаемый источник — то, чем претензия отличается от жалобы «медленно».
    Гипотеза автоматической атрибуции: в тексте она не выдаётся за установленный факт."""
    if not verdict or verdict.get("cause") in (None, "none", "no_data", "undetermined"):
        return ""
    evidence = verdict.get("evidence", {})
    quality = verdict.get("data_quality", {})
    limits = "; ".join(quality.get("reasons", [])) or "ограничений нет"
    return f"""
ПРЕДПОЛАГАЕМЫЙ ИСТОЧНИК НАРУШЕНИЯ (автоматическая атрибуция, требует подтверждения):

  • предполагаемый источник — {verdict['cause_label']};
  • зона ответственности — {verdict['responsible']};
  • оценка модели — {round(verdict['confidence'] * 100)}%, достаточность данных — {quality.get('label', '—')};
  • ПК организации в отклонении — {evidence.get('devices_affected', 0)} из {evidence.get('devices_total', 0)};
  • сопоставимые школы того же провайдера в районе в отклонении — {evidence.get('peers_same_provider_district_affected', 0)} из {evidence.get('peers_same_provider_district', 0)};
  • сопоставимые школы иных провайдеров в районе в отклонении — {evidence.get('peers_other_providers_district_affected_pct', 0)}% ({evidence.get('peers_other_providers_district', 0)} школ);
  • ограничения данных — {limits}.

{verdict['narrative']}
"""


def _limits_text() -> str:
    th = thresholds()
    return (f"Download ≥ {th.down_min:g}, Upload ≥ {th.up_min:g} Мбит/с, Ping ≤ {th.ping_max:g} мс, "
            f"Jitter ≤ {th.jitter_max:g} мс, потери ≤ {th.loss_max:g}%")


def _fallback_claim(incident: Incident, school: School, analysis: dict,
                    verdict: dict | None = None) -> str:
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
  • доля замеров в пределах установленных порогов ({_limits_text()}) — {analysis['sla_compliance_pct']}%;
  • доступность соединения — {analysis['availability_pct']}% при нормативе {analysis['availability_norm_pct']:g}%;
  • количество замеров с отклонениями — {analysis['violations']} из {analysis['samples']};
  • обстоятельства инцидента — {incident.description};
  • дата и время фиксации — {incident.start_time:%d.%m.%Y %H:%M} (время сервера).

Выявленные повторяющиеся паттерны деградации:
{chr(10).join('  • ' + p['text'] for p in analysis['patterns']) or '  • устойчивых паттернов не выявлено'}
{_verdict_block(verdict)}
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

    # Окно анализа — вокруг инцидента, а не «от сегодня»: претензия о том, что было.
    analysis = analyze(db, school.id, days=30,
                       now=incident.start_time + timedelta(days=1) if incident.start_time else None)
    if not analysis["samples"]:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Нет замеров основной линии за период инцидента — претензию "
                            "формировать не на чем")
    verdict = attribution.diagnose_incident(db, incident)
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
- Доля замеров в пределах порогов ({_limits_text()}): {analysis['sla_compliance_pct']}%
- Доступность: {analysis['availability_pct']}% (норматив {analysis['availability_norm_pct']:g}%)
- Замеров с отклонениями: {analysis['violations']} из {analysis['samples']}
- Повторяющиеся паттерны: {'; '.join(p['text'] for p in analysis['patterns']) or 'не выявлены'}
- Ответственное лицо: {school.contact_name}, {school.contact_phone}

ВЫВОД СИСТЕМЫ ОБ ИСТОЧНИКЕ НАРУШЕНИЯ — ЭТО ПРЕДПОЛОЖЕНИЕ, а не установленный факт.
Излагай его именно как предполагаемый источник, с теми же оговорками и ограничениями
данных, не усиливай и не превращай в утверждение о вине:
{verdict.get('narrative', 'источник не установлен')}
Предполагаемый источник: {verdict.get('cause_label', '—')}.
Зона ответственности: {verdict.get('responsible', '—')}.

Структура: шапка (кому/от кого), исходящий номер и дата, ссылки на договор и
законодательство РК о связи, таблица зафиксированных отклонений, отдельный раздел
«Предполагаемый источник нарушения» с описанием границы зоны отказа и ограничений данных, требование
устранить в срок не более 4 часов, требование перерасчёта, предупреждение о жалобе
в Инспекцию связи МЦРИАП РК. Стиль строго официальный, готовый к подписанию.
Язык — русский."""
        try:
            claim_text = model.generate_content(prompt).text
        except Exception as exc:  # noqa: BLE001
            log.warning("Ошибка Gemini: %s", exc)
            claim_text, source = _fallback_claim(incident, school, analysis, verdict), "template"
    else:
        claim_text, source = _fallback_claim(incident, school, analysis, verdict), "template"

    incident.ai_claim_text = claim_text
    db.commit()
    write_audit(db, user.email, user.role, "ai.claim_generated", incident.incident_number,
                request.client.host if request.client else "", {"source": source})
    # Претензия обоснована только при достаточных данных и предполагаемой стороне провайдера.
    # Уровень школы (ЛВС, роутер, ИНДИВИДУАЛЬНАЯ ЛИНИЯ) провайдера не исключает, поэтому
    # прямого запрета нет — есть условие: сначала исключить оборудование школы.
    cause = verdict.get("cause")
    advised = bool(verdict.get("actionable"))
    if advised:
        advisory = ("Данные указывают на сторону провайдера; вывод предположительный — "
                    "провайдер вправе его оспорить, приложите акт с замерами.")
    elif cause == "school_lan":
        advisory = ("ВНИМАНИЕ: предполагаемый источник — уровень школы (ЛВС, роутер или "
                    "индивидуальная линия провайдера). Перед направлением претензии исключите "
                    "неисправность оборудования школы; при его исправности допустимо просить "
                    "провайдера о диагностике линии.")
    elif cause == "device":
        advisory = ("ВНИМАНИЕ: отклонение локализовано на отдельном ПК — претензия провайдеру "
                    "преждевременна, проверьте ПК и его подключение.")
    elif cause in ("provider_node", "regional"):
        advisory = ("Предполагается сторона провайдера, но данных недостаточно для уверенного "
                    "вывода: " + "; ".join(verdict.get("data_quality", {}).get("reasons", [])) +
                    ". Уточните у провайдера плановые работы и аварии по району.")
    else:
        advisory = ("Источник нарушения не установлен — данных недостаточно; не указывайте "
                    "источник в претензии, приложите только акт с замерами.")
    return {"claim_text": claim_text, "source": source, "analysis": analysis,
            "verdict": verdict, "claim_advised": advised, "advisory": advisory}


@router.get("/sla-report/{school_id}", summary="PDF-акт о нарушении SLA",
            response_class=Response)
def sla_pdf(school_id: int, days: int = 30, as_of: datetime | None = None, request: Request = None,
            db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Killer feature #2: доказательная база нарушения SLA одним файлом."""
    school = db.get(School, school_id)
    if not school:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Школа не найдена")
    if not can_access_school(user, school):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет доступа")

    now = naive_utc(as_of) if as_of else None
    analysis = analyze(db, school_id, days=days, now=now)
    if not analysis["samples"]:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Нет замеров основной линии за период — акт не формируется")
    incidents = [{"incident_number": i.incident_number,
                  "start_time": i.start_time.isoformat() if i.start_time else "",
                  "status": i.status, "description": i.description}
                 for i in db.query(Incident).filter(Incident.school_id == school_id)
                 .order_by(Incident.id.desc()).limit(12).all()]
    payload = {**school_brief(school, now), "contact_name": school.contact_name,
               "contact_phone": school.contact_phone}
    verdict = attribution.diagnose(db, school_id, at=now)
    prediction = ml_forecast.predict(db, school_id, at=now)
    if prediction.get("probability") is not None:
        verdict["forecast_text"] = (
            f"Вероятность выхода за SLA в ближайшие {prediction['horizon_hours']} ч — "
            f"{round(prediction['probability'] * 100)}% ({prediction['band']}). "
            f"{prediction['recommendation']}")
    pdf = sla_report(analysis, payload, incidents, verdict=verdict)

    write_audit(db, user.email, user.role, "ai.sla_report", school.school_id_code,
                request.client.host if request and request.client else "",
                {"days": days, "compliance": analysis["sla_compliance_pct"],
                 "root_cause": verdict.get("cause")})
    filename = f"SLA_{school.school_id_code}_{datetime.now():%Y%m%d}.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/status", summary="Состояние AI-интеграции")
def ai_status():
    return {"provider": "Google Gemini", "model": settings.GENAI_MODEL,
            "configured": bool(settings.GENAI_TOKEN),
            "fallback": "детерминированный юридический шаблон"}
