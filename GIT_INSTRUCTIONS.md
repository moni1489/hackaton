# 🗂 Инструкция по работе с ветками

## Структура веток

```
main                  ← фронт + бэк (основная разработка, для жюри)
feature/tray-agent    ← трей-приложение для Windows/Linux (отдельная задача ТЗ)
```

---

## 1. Запушить трей-агент в отдельную ветку

```bash
# Переключиться на ветку трей-агента
git checkout feature/tray-agent

# Убедиться что всё закоммичено
git status

# Запушить ветку на GitHub
git push origin feature/tray-agent
```

После этого на GitHub появится ветка `feature/tray-agent` с файлами:
- `agent/tray_agent.py` — трей-агент (Windows + Linux)
- `agent/requirements-tray.txt`
- `agent/build_linux.sh`
- `agent/build_windows.bat`
- `agent/SAM-VKO-Agent.spec`

---

## 2. Запушить изменения фронта и бэка в main

```bash
# Переключиться на main
git checkout main

# Посмотреть что изменено (не закоммичено)
git status
git diff --stat

# Добавить изменения бэка
git add backend/hackathon.db
git add backend/import_vko_osm.py
git add backend/retry_failed_districts.py
git add backend/fix_db_names.py

# Добавить изменения фронта (если есть незакоммиченные)
git add frontend/src/

# Закоммитить
git commit -m "feat: real VKO schools from OSM, fixed names, Mapbox map, metrics charts"

# Запушить в main
git push origin main
```

---

## 3. Создать Pull Request (для красоты на GitHub)

Зайди на GitHub → вкладка **Pull requests** → **New pull request**:
- base: `main`
- compare: `feature/tray-agent`
- Title: `feat: Трей-агент мониторинга для Windows и Linux`

Это покажет жюри что работа ведётся профессионально с разделением по фичам.

---

## 4. Краткие команды (шпаргалка)

| Что сделать | Команда |
|---|---|
| Посмотреть ветки | `git branch -a` |
| Переключиться на main | `git checkout main` |
| Переключиться на трей | `git checkout feature/tray-agent` |
| Запушить текущую ветку | `git push origin HEAD` |
| Посмотреть историю | `git log --oneline -10` |
| Посмотреть отличия от origin | `git diff --stat origin/$(git branch --show-current)` |

---

## 5. Если нужно слить трей-агент в main (после хакатона)

```bash
git checkout main
git merge feature/tray-agent
git push origin main
```
