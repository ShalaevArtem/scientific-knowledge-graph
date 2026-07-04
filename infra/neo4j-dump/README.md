# Дамп заполненной базы Neo4j для стенда

Контейнерный Neo4j поднимается **пустым**. Чтобы стенд сразу работал с данными
(граф, чанки, эмбеддинги), сюда кладётся дамп заполненной базы `neo4j.dump`.

Сам файл (~1–2 ГБ) в git не хранится — он раздаётся через облачный диск или
GitHub Release. Положите его в этот каталог: `infra/neo4j-dump/neo4j.dump`.

## Как снять дамп (с локальной машины разработки)

```powershell
powershell -File infra\dump.ps1
```

Скрипт останавливает локальный Neo4j, снимает оффлайн-дамп в
`infra/neo4j-dump/neo4j.dump` и поднимает сервер обратно.

## Как восстановить на стенде (Docker)

Каталог смонтирован в контейнер Neo4j как `/dump` (см. `docker-compose.yml`).
Загрузка — до старта сервера, штатным `neo4j-admin`:

```bash
# 1) создать/инициализировать том (сервер сам выставит права на /data), затем остановить
docker compose up -d neo4j
docker compose stop neo4j

# 2) загрузить дамп в базу 'neo4j' (сервер не запущен)
docker compose run --rm --entrypoint bash neo4j -lc \
  "neo4j-admin database load neo4j --from-path=/dump --overwrite-destination=true"

# 3) поднять весь стек с данными
docker compose up -d
```

Версия дампа и целевого образа должны совпадать (`neo4j:2026.05.0-community`).
