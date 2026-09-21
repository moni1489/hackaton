"""Генерация PDF-акта о нарушении SLA (доказательная база для претензии)."""
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_FONT = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"

# Кириллица: подключаем системный DejaVu, если он есть в образе.
for path, name, bold in (
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVu", False),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVu-Bold", True),
):
    try:
        pdfmetrics.registerFont(TTFont(name, path))
        if bold:
            _FONT_BOLD = name
        else:
            _FONT = name
    except Exception:  # noqa: BLE001 — шрифта нет, остаёмся на Helvetica
        pass


def sla_report(analysis: dict, school: dict, incidents: list[dict],
               verdict: dict | None = None, banner: str | None = None) -> bytes:
    """banner — заметная плашка вверху первой страницы (демонстрационные документы)."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=18 * mm, bottomMargin=18 * mm,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            title=f"SLA-отчёт {school.get('school_id_code', '')}")
    base = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=base["Heading1"], fontName=_FONT_BOLD, fontSize=15,
                        alignment=TA_CENTER, spaceAfter=4)
    sub = ParagraphStyle("sub", parent=base["Normal"], fontName=_FONT, fontSize=9,
                         alignment=TA_CENTER, textColor=colors.HexColor("#64748B"))
    h2 = ParagraphStyle("h2", parent=base["Heading2"], fontName=_FONT_BOLD, fontSize=11,
                        spaceBefore=12, spaceAfter=6, textColor=colors.HexColor("#0F172A"))
    body = ParagraphStyle("body", parent=base["Normal"], fontName=_FONT, fontSize=9.5, leading=14)

    story = []
    if banner:
        warn = ParagraphStyle("banner", parent=base["Normal"], fontName=_FONT_BOLD, fontSize=9,
                              leading=12, alignment=TA_CENTER, textColor=colors.HexColor("#B42318"),
                              backColor=colors.HexColor("#FEF3F2"), borderPadding=6,
                              borderColor=colors.HexColor("#FDA29B"), borderWidth=0.8,
                              spaceAfter=10)
        story.append(Paragraph(banner, warn))
    story += [
        Paragraph("АКТ ФИКСАЦИИ НАРУШЕНИЯ ПОКАЗАТЕЛЕЙ КАЧЕСТВА СВЯЗИ (SLA)", h1),
        Paragraph("Система автономного мониторинга организаций образования ВКО · "
                  f"сформировано {datetime.now():%d.%m.%Y %H:%M}", sub),
        Spacer(1, 10),
        Paragraph("1. Сведения об организации образования", h2),
    ]

    def table(rows, widths):
        t = Table(rows, colWidths=widths, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _FONT),
            ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2F7")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        return t

    story.append(table([
        ["Параметр", "Значение"],
        ["Наименование", school.get("name", "—")],
        ["Идентификатор", school.get("school_id_code", "—")],
        ["Район / город", school.get("region", "—")],
        ["Адрес", school.get("address", "—")],
        ["Поставщик услуг", school.get("provider", "—")],
        ["Тип подключения", school.get("connection_type", "—")],
        ["Договорная скорость", f"{school.get('contract_speed_down', '—')} Мбит/с"],
    ], [55 * mm, 119 * mm]))

    story += [Paragraph("2. Результаты инструментального контроля", h2),
              table([
                  ["Показатель", "Факт", "Норматив", "Оценка"],
                  ["Средняя скорость загрузки", f"{analysis['avg_speed']} Мбит/с",
                   f"≥ {analysis['speed_floor']} Мбит/с",
                   "нарушение" if analysis["avg_speed"] < analysis["speed_floor"] else "норма"],
                  ["Доля замеров в пределах порогов", f"{analysis['sla_compliance_pct']}%", "≥ 95%",
                   "нарушение" if analysis["sla_compliance_pct"] < 95 else "норма"],
                  ["Доступность соединения", f"{analysis['availability_pct']}%",
                   f"≥ {analysis['availability_norm_pct']:g}%",
                   "норма" if analysis["availability_ok"] else "нарушение"],
                  ["Замеров ниже договорной доли", f"{analysis['below_contract_pct']}%",
                   f"< {analysis['sla_threshold_pct']}% договора", "—"],
                  ["Замеров за период", str(analysis["samples"]),
                   f"{analysis['window_days']} сут.", "—"],
                  ["Зафиксировано отклонений", str(analysis["violations"]), "0", "—"],
                  ["Стабильность канала", f"{analysis['stability']}%", "≥ 90%",
                   "нарушение" if analysis["stability"] < 90 else "норма"],
                  ["Оценка риска", f"{analysis['risk_score']}/100 ({analysis['risk_level']})",
                   "< 25", "—"],
              ], [58 * mm, 38 * mm, 40 * mm, 38 * mm])]

    story.append(Paragraph("3. Выявленные повторяющиеся паттерны деградации", h2))
    if analysis["patterns"]:
        story.append(table(
            [["Тип", "Период", "Средняя скорость", "Отклонение"]] +
            [[{"weekday": "День недели", "hour": "Часы"}.get(p["type"], p["type"]),
              p["key"], f"{p['avg_speed']} Мбит/с", f"−{p['drop_pct']}%"]
             for p in analysis["patterns"]],
            [40 * mm, 40 * mm, 47 * mm, 47 * mm]))
    else:
        story.append(Paragraph("Устойчивых повторяющихся паттернов не выявлено.", body))

    # --- Предполагаемый источник: гипотеза, а не установленный факт --------
    story.append(Paragraph("4. Предполагаемый источник нарушения", h2))
    if verdict and verdict.get("cause") and verdict["cause"] not in ("none", "no_data", "undetermined"):
        evidence = verdict.get("evidence", {})
        quality = verdict.get("data_quality", {})
        story.append(table([
            ["Предполагаемый источник", verdict.get("cause_label", "—")],
            ["Зона ответственности", verdict.get("responsible", "—")],
            ["Оценка модели", f"{round(float(verdict.get('confidence', 0)) * 100)}% "
                              "(не является доказанной вероятностью)"],
            ["Достаточность данных", quality.get("label", "—")],
            ["ПК организации в отклонении",
             f"{evidence.get('devices_affected', 0)} из {evidence.get('devices_total', 0)}"],
            ["Сопоставимые школы того же провайдера в районе",
             f"{evidence.get('peers_same_provider_district_affected', 0)} из "
             f"{evidence.get('peers_same_provider_district', 0)} в отклонении"],
            ["Сопоставимые школы других провайдеров в районе",
             f"{evidence.get('peers_other_providers_district_affected_pct', 0)}% в отклонении "
             f"({evidence.get('peers_other_providers_district', 0)} школ)"],
            ["Средняя просадка к норме канала", f"{evidence.get('avg_depth_pct', 0)}%"],
            ["Модель атрибуции", verdict.get("model_version", "—")],
        ], [70 * mm, 104 * mm]))
        limits = "; ".join(quality.get("reasons", []))
        story += [Spacer(1, 6), Paragraph(verdict.get("narrative", ""), body), Spacer(1, 4),
                  Paragraph("Вывод основан на сопоставлении показателей агентов в других "
                            "организациях образования. Совпадение границы отклонения сужает круг "
                            "возможных причин, но не доказывает конкретную; окончательный вывод "
                            "делается после проверки на месте."
                            + (f" Ограничения данных: {limits}." if limits else ""), body)]
    else:
        story.append(Paragraph("Источник нарушения автоматической атрибуцией не установлен"
                               + (": " + verdict["narrative"] if verdict and verdict.get("narrative")
                                  else ": данных недостаточно") + ".", body))

    story += [Paragraph("5. Прогноз системы предиктивной аналитики", h2),
              Paragraph(analysis["forecast"] or "—", body)]
    if verdict and verdict.get("forecast_text"):
        story.append(Paragraph(verdict["forecast_text"], body))

    story.append(Paragraph("6. Зарегистрированные инциденты", h2))
    if incidents:
        story.append(table(
            [["Номер", "Начало", "Статус", "Описание"]] +
            [[i.get("incident_number", "—"), (i.get("start_time") or "—")[:16].replace("T", " "),
              i.get("status", "—"), Paragraph(str(i.get("description", ""))[:160],
                                              ParagraphStyle("c", fontName=_FONT, fontSize=7.5,
                                                             leading=10))]
             for i in incidents[:12]],
            [32 * mm, 30 * mm, 32 * mm, 80 * mm]))
    else:
        story.append(Paragraph("Открытых инцидентов за период не зарегистрировано.", body))

    story += [
        Spacer(1, 14),
        Paragraph("Акт сформирован автоматически на основании данных инструментального "
                  "мониторинга. Замеры выполнены сертифицированными агентами, установленными "
                  "в организации образования, и хранятся в неизменяемом виде. Документ "
                  "является приложением к досудебной претензии поставщику услуг связи.", body),
        Spacer(1, 18),
        table([["Ответственное лицо организации", school.get("contact_name", "—")],
               ["Контактный телефон", school.get("contact_phone", "—")],
               ["Подпись", " " * 40]], [70 * mm, 104 * mm]),
    ]

    doc.build(story)
    return buffer.getvalue()
