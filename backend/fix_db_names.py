"""
fix_db_names.py
================
1. Переименовывает «Школа #N» в реалистичные казахстанские названия.
2. Делает дополнительные запросы к OSM Overpass API для поиска
   пропущенных школ по расширенному набору bbox/тегов.
3. Пишет лог что изменилось.
"""
from __future__ import annotations
import random, sqlite3, time, json, ssl, urllib.request, urllib.parse
from pathlib import Path

DB = Path(__file__).parent / "hackathon.db"

# ──────────────────────────────────────────────────────────────────────────────
# 1. РЕАЛИСТИЧНЫЕ КАЗАХСТАНСКИЕ НАЗВАНИЯ ШКОЛ
# ──────────────────────────────────────────────────────────────────────────────

# Шаблоны настоящих официальных названий по ТЗ (КГУ, ГУ, ГККП)
TEMPLATES_BY_REGION = {
    "г. Усть-Каменогорск": [
        "КГУ «Средняя общеобразовательная школа №{n}» ГУ «ОО г. Усть-Каменогорска»",
        "КГУ «Гимназия №{n}» ГУ «ОО г. Усть-Каменогорска»",
        "КГУ «Лицей №{n}» ГУ «ОО г. Усть-Каменогорска»",
        "КГУ «Школа-гимназия №{n}» ГУ «ОО г. Усть-Каменогорска»",
    ],
    "г. Семей": [
        "КГУ «СОШ №{n}» ГУ «Отдел образования г. Семей»",
        "КГУ «Общеобразовательная школа №{n}» ГУ «ОО г. Семей»",
        "КГУ «Гимназия №{n}» ГУ «ОО г. Семей»",
    ],
    "г. Риддер": [
        "КГУ «Средняя школа №{n}» ГУ «ОО г. Риддер»",
        "КГУ «СОШ №{n}» ГУ «Отдел образования г. Риддер»",
    ],
    "г. Курчатов": [
        "КГУ «СОШ №{n}» ГУ «ОО г. Курчатов»",
    ],
    "DEFAULT": [
        "КГУ «Средняя общеобразовательная школа с. {village} №{n}»",
        "КГУ «СОШ №{n}» ГУ «ОО {region}»",
        "ГККП «Школа №{n}» акимата {region}",
    ],
}

VILLAGES_BY_REGION = {
    "Зайсанский район":       ["Зайсан", "Акжар", "Жанаул", "Коктал", "Сарыбел"],
    "Катон-Карагайский район":["Катон-Карагай", "Урыль", "Белое", "Карагайлы", "Медведка"],
    "Курчумский район":       ["Курчум", "Теректы", "Буран", "Маральды", "Сарыпшал"],
    "Тарбагатайский район":   ["Аксуат", "Маканчи", "Казанка", "Николаевка", "Дулат"],
    "Уланский район":         ["Улан", "Молодёжное", "Тарханка", "Гагаринское", "Крестовка"],
    "Глубоковский район":     ["Глубокое", "Черемшанка", "Прапорщиково", "Усть-Таловка", "Берёзовка"],
    "Шемонаихинский район":   ["Шемонаиха", "Выдриха", "Предгорное", "Первомайское", "Луговое"],
    "Самарский район":        ["Самарское", "Чалдай", "Барлык", "Кулуджун", "Аблакетка"],
    "Район Алтай":            ["Алтай", "Путинцево", "Лесное", "Таврическое", "Новая Шульба"],
}

def fix_school_name(school_id: int, current_name: str, region: str) -> str | None:
    """Возвращает новое имя или None если переименовывать не нужно."""
    bad = (
        current_name.startswith("Школа #")
        or current_name == ""
        or current_name is None
    )
    if not bad:
        return None

    # Извлекаем порядковый номер из «Школа #145» → 145
    n = current_name.replace("Школа #", "").strip() if current_name else str(school_id)

    templates = TEMPLATES_BY_REGION.get(region, TEMPLATES_BY_REGION["DEFAULT"])
    tmpl = random.choice(templates)

    villages = VILLAGES_BY_REGION.get(region, [""])
    village = random.choice(villages)

    return tmpl.format(n=n, region=region, village=village)


def fix_all_names(conn: sqlite3.Connection) -> int:
    schools = conn.execute(
        "SELECT id, name, region FROM schools"
    ).fetchall()
    changed = 0
    for sid, name, region in schools:
        new_name = fix_school_name(sid, name or "", region or "")
        if new_name:
            conn.execute("UPDATE schools SET name=? WHERE id=?", (new_name, sid))
            print(f"  [{sid}] '{name}' → '{new_name}'")
            changed += 1
    conn.commit()
    return changed


# ──────────────────────────────────────────────────────────────────────────────
# 2. ДОПОЛНИТЕЛЬНЫЙ ПОИСК ШКОЛ В OSM (расширенный)
# ──────────────────────────────────────────────────────────────────────────────

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# Дополнительные теги и bbox'ы которые мы пропустили
EXTRA_QUERIES = [
    # Усть-Каменогорск север (который таймаутился)
    {"code": "UK",  "region": "г. Усть-Каменогорск", "bbox": "50.00,82.55,50.10,82.80",
     "tag": 'amenity"="school'},
    # Semey восток
    {"code": "SEM", "region": "г. Семей", "bbox": "50.37,80.30,50.52,80.55",
     "tag": 'amenity"="school'},
    # Поиск по тегу education (некоторые школы помечены иначе)
    {"code": "UK",  "region": "г. Усть-Каменогорск", "bbox": "49.88,82.45,50.10,82.80",
     "tag": 'building"="school'},
    {"code": "SEM", "region": "г. Семей", "bbox": "50.33,80.00,50.52,80.50",
     "tag": 'building"="school'},
    # Районы которые дали 0 результатов
    {"code": "KRC", "region": "Курчумский район", "bbox": "48.40,83.60,48.65,84.20",
     "tag": 'amenity"="school'},
    {"code": "TRB", "region": "Тарбагатайский район", "bbox": "47.70,81.30,48.00,82.10",
     "tag": 'amenity"="school'},
    # Восточные части ВКО
    {"code": "ZYS", "region": "Зайсанский район", "bbox": "47.10,84.00,47.55,85.50",
     "tag": 'amenity"="school'},
    # г. Аягоз
    {"code": "AYG", "region": "Аягозский район", "bbox": "47.90,80.30,48.10,80.60",
     "tag": 'amenity"="school'},
    # Бескарагайский район
    {"code": "BSK", "region": "Бескарагайский район", "bbox": "51.00,81.50,51.40,82.50",
     "tag": 'amenity"="school'},
    # Абайский район
    {"code": "ABY", "region": "Абайский район", "bbox": "49.60,80.20,50.00,80.80",
     "tag": 'amenity"="school'},
]

from datetime import UTC, datetime, timedelta
import os, platform, random as _rand

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

def osm_query(bbox: str, tag: str, limit: int = 100, retries: int = 2):
    q = f'[out:json][timeout:25];(node["{tag}]({bbox});way["{tag}]({bbox}););out center {limit};'
    data = urllib.parse.urlencode({"data": q}).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                "https://overpass-api.de/api/interpreter", data=data,
                headers={"User-Agent": "VKO-Monitor/2.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if attempt < retries - 1:
                print(f"    retry {attempt+1} ({e})")
                time.sleep(8)
            else:
                raise
    return {"elements": []}

def insert_school(conn, sid, code, region, el, tag_field):
    tags = el.get("tags", {})
    center = el.get("center") or {"lat": el.get("lat", 0), "lon": el.get("lon", 0)}
    name = (tags.get("name:ru") or tags.get("name") or "").strip()

    # Фильтр не-школ
    skip = ["университет","институт","колледж","факультет","академия","техникум","вуз","детский сад","ясли"]
    if any(w in name.lower() for w in skip):
        return False

    if not name:
        n = str(sid)
        villages = VILLAGES_BY_REGION.get(region, [""])
        village = random.choice(villages)
        templates = TEMPLATES_BY_REGION.get(region, TEMPLATES_BY_REGION["DEFAULT"])
        name = random.choice(templates).format(n=n, region=region, village=village)

    prov, prov_phone = pick_provider()
    conn_type = random.choice(CONN_TYPES)
    contract = random.choice([50.0, 100.0, 100.0, 200.0])
    now = datetime.now()

    r_stat = random.random()
    if r_stat < 0.87:
        status, dl = "Норма", round(contract * random.uniform(0.75, 1.05), 1)
        ul = round(dl * random.uniform(0.7, 1.0), 1)
        ping = round(random.uniform(12, 45), 1); jitter = round(random.uniform(1.5, 6), 1)
        loss = round(random.uniform(0, 0.5), 1); offline = False
    elif r_stat < 0.94:
        status, dl = "Нестабильно", round(random.uniform(13, 19.5), 1)
        ul = round(random.uniform(10, 18), 1)
        ping = round(random.uniform(70, 120), 1); jitter = round(random.uniform(15, 35), 1)
        loss = round(random.uniform(1.2, 3.5), 1); offline = False
    elif r_stat < 0.98:
        status, dl = "Критично", round(random.uniform(2, 12), 1)
        ul = round(random.uniform(1, 8), 1)
        ping = round(random.uniform(150, 380), 1); jitter = round(random.uniform(40, 95), 1)
        loss = round(random.uniform(5, 18), 1); offline = False
    else:
        status = "Нет соединения"; dl = ul = ping = jitter = 0.0; loss = 100.0; offline = True

    addr = tags.get("addr:street", tags.get("addr:full", region))
    last_m = now - timedelta(minutes=random.randint(3, 90))
    dev_id = f"DEV-{code}-PC1"

    conn.execute("""INSERT INTO schools (
        id, school_id_code, name, region, address, lat, lng,
        provider, connection_type, contract_speed_down, contract_speed_up,
        contact_name, contact_phone, contact_email, provider_phone,
        status, current_download, current_upload, current_ping,
        current_jitter, current_packet_loss, last_measurement
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        sid, f"VKO-{code}-X{sid}", name, region, addr,
        round(center["lat"], 6), round(center["lon"], 6),
        prov, conn_type, contract, contract,
        f"{random.choice(LAST_NAMES)} {random.choice(FIRST_NAMES)}",
        f"+7 (777) {random.randint(100,999)}-{random.randint(10,99)}-{random.randint(10,99)}",
        f"admin.vko{sid}@vko.edu.kz", prov_phone,
        status, dl, ul, ping, jitter, loss, last_m,
    ))
    conn.execute("""INSERT OR IGNORE INTO devices (
        device_id, school_id, name, room, ip_address, status, last_seen
    ) VALUES (?,?,?,?,?,?,?)""", (
        dev_id, sid, "Монитор-агент", "Серверная",
        f"192.168.{random.randint(1,20)}.{random.randint(10,200)}",
        "offline" if offline else ("warning" if status != "Норма" else "online"),
        last_m,
    ))
    for h in range(1, 6):
        m_dl = dl * random.uniform(0.9, 1.1) if not offline else 0.0
        conn.execute("""INSERT INTO measurements (
            device_id, school_id, timestamp,
            download_speed, upload_speed, ping, jitter, packet_loss, is_offline
        ) VALUES (?,?,?,?,?,?,?,?,?)""", (
            dev_id, sid, now - timedelta(hours=h * 4),
            round(m_dl, 1), round(m_dl * 0.9, 1), ping, jitter, loss, offline,
        ))
    return True


def find_more_schools(conn):
    from sqlalchemy import text
    existing_latlon = {
        (r[0], r[1])
        for r in conn.execute("SELECT ROUND(lat,4), ROUND(lng,4) FROM schools").fetchall()
    }
    max_id = conn.execute("SELECT MAX(id) FROM schools").fetchone()[0] or 174
    sid = max_id
    added = 0

    print(f"\nПоиск дополнительных школ через OSM ({len(EXTRA_QUERIES)} запросов)...")
    for q in EXTRA_QUERIES:
        tag = q["tag"]
        print(f"  [{q['code']}] {q['region']} tag={tag[:20]}...", end=" ", flush=True)
        try:
            result = osm_query(q["bbox"], tag)
            elements = [
                el for el in result.get("elements", [])
                if el.get("tags", {}).get("amenity") in ("school", None)
                   or el.get("tags", {}).get("building") == "school"
            ]
            new_cnt = 0
            for el in elements:
                center = el.get("center") or {"lat": el.get("lat", 0), "lon": el.get("lon", 0)}
                key = (round(center["lat"], 4), round(center["lon"], 4))
                if key in existing_latlon:
                    continue
                existing_latlon.add(key)
                sid += 1
                ok = insert_school(conn, sid, q["code"], q["region"], el, tag)
                if ok:
                    added += 1
                    new_cnt += 1
                else:
                    sid -= 1
            print(f"{len(elements)} raw → {new_cnt} new")
        except Exception as e:
            print(f"FAIL: {e}")
        time.sleep(5)

    conn.commit()
    return added


# ──────────────────────────────────────────────────────────────────────────────
# 3. ЗАПУСК
# ──────────────────────────────────────────────────────────────────────────────

def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    print("=== Шаг 1: Исправление имён школ ===")
    changed = fix_all_names(conn)
    print(f"✅ Переименовано: {changed} школ\n")

    print("=== Шаг 2: Поиск дополнительных школ в OSM ===")
    added = find_more_schools(conn)
    total = conn.execute("SELECT COUNT(*) FROM schools").fetchone()[0]
    print(f"\n✅ Добавлено новых: {added}")
    print(f"✅ Итого школ в БД: {total}")

    conn.close()


if __name__ == "__main__":
    main()
