# Научный клубок — карта знаний R&D (горно-металлургия)

Поисково-аналитическая система на графе знаний для горно-металлургических исследований:
связывает публикации, отчёты, эксперименты, материалы, процессы, оборудование, предприятия,
экспертов и выводы. Отвечает на вопросы вида *«что уже делали по материалу X в процессе Y
и какой был эффект на показатель Z»*, показывает связанные сущности, историю выводов
и пробелы в данных.

Архитектура — **GraphRAG**: граф знаний (скелет связей) + гибридный поиск в четыре канала
(вектор + BM25 + обход графа по сущностям вопроса + фильтр по измерениям и числовым
ограничениям) + LLM-слой для вопросов на естественном языке с цитатами.

Два инварианта:
1. **Provenance** — каждый факт хранит ссылку на источник (документ, страница/слайд,
   уверенность извлечения, модель): «нет источника — нет факта».
2. **Верификация** — сущности и выводы, впервые извлечённые из текстов, помечаются
   `needs_review` и попадают в очередь эксперта; seed-справочники и структурированные
   данные считаются верифицированными.

## Конвейер

```
Корпус (PDF/DOCX/PPTX/XLS/XLSX)         ~1 ГБ: доклады, журналы (2003–2026),
        │                                конференции, обзоры, статьи (RU/EN),
        │                                таблицы данных (котировки/балансы металлов)
        ▼  skg ingest   [SKG_OCR=1 — OCR сканов]
Document + Chunk в графе                метаданные из структуры папок,
        │                                текст с привязкой к странице/слайду/листу
        ├──▼  skg embed
        │  векторный индекс Neo4j       fastembed ONNX, мультиязычная модель,
        │  + полнотекстовый (BM25)       локально, без GPU и внешних API
        │
        └──▼  skg extract  (нужен LLM_API_KEY)
           сущности и связи             материалы/процессы/оборудование/предприятия,
           по онтологии                  численные результаты с условиями, выводы
        ▼
Слой запросов + веб-UI                  skg serve → http://localhost:8000
```

## Онтология

Метки и связи — на английском (кириллица в Cypher требует бэктиков и ломает инструменты),
данные — на русском с RU/EN-синонимами.

| Сущность | Метка | Ключ | Примечание |
|---|---|---|---|
| Материал/вещество | `Material` | `id` | `name`, `name_en`, `synonyms[]`, `kind`; «ПВП» ≡ «flash furnace» схлопываются |
| Процесс | `Process` | `id` | таксономия: выщелачивание, электроэкстракция… + `category` |
| Оборудование | `Equipment` | `id` | `type` |
| Предприятие/проект | `Facility` | `name` | `country` → фильтр «РФ/мир» |
| Документ | `Document` | `id` | `category`, `journal`/`conference`, `year`, `language`, `rel_path` |
| Фрагмент | `Chunk` | `id` | текст, страница/слайд, `embedding` (vector 384d) |
| Свойство/показатель | `Property` | `name` | канонические имена показателей |
| Измерение | `Measurement` | `id` | `value`, `unit`, `conditions` (JSON: параметр/оператор/диапазон) |
| Вывод | `Conclusion` | `id` | `text`, `confidence`, `geography`, `date`, `needs_review` |
| Эксперимент | `Experiment` | `id` | для структурированных протоколов (этап 1, загрузчики CSV/JSONL) |
| Режим | `Regime` | `key` | параметризованные условия, дедуп по хэшу |
| Сотрудник/эксперт | `Person` | `id` | экспертиза выводится по авторству и темам |
| Тег тематики | `TopicTag` | `name` | гидрометаллургия, экология… |

Ключевые связи: `(Document)-[:MENTIONS {count, source, confidence}]->(Material|Process|Equipment|Facility)`,
`(Document)-[:REPORTS]->(Measurement)-[:OF_PROPERTY]->(Property)`,
`(Conclusion)-[:DESCRIBED_IN {fragment}]->(Document)`, `(Conclusion)-[:ABOUT]->(…)`,
`(Conclusion)-[:SUPPORTS|CONTRADICTS]->(Conclusion)`, `(Document)-[:AUTHORED_BY]->(Person)`,
`(Document)-[:TAGGED]->(TopicTag)`, `(Document)-[:HAS_CHUNK]->(Chunk)`.
Для структурированных экспериментов: `(Experiment)-[:USES|APPLIES|ON_EQUIPMENT|YIELDS|LEADS_TO]->(…)`.

Схема: [schema/](schema) (уникальность ключей, диапазонные индексы, полнотекстовые
с русским анализатором, векторный индекс). Обязательность полей — в pydantic-моделях
(Neo4j Community не поддерживает NOT NULL).

## Быстрый старт (Windows, без Docker)

```powershell
Copy-Item .env.example .env               # заполнить NEO4J_PASSWORD (и LLM_* при наличии ключа)
powershell -File infra\neo4j-local\install.ps1
.neo4j\neo4j-community-2026.05.0\bin\neo4j.bat console    # в отдельном окне

py -m venv .venv
.venv\Scripts\pip install -e .

.venv\Scripts\skg schema                  # ограничения и индексы
.venv\Scripts\skg seed                    # канонические термины домена (нормализация)
.venv\Scripts\skg ingest data\local\corpus         # корпус: Document + Chunk
.venv\Scripts\skg embed                   # эмбеддинги (локально, первая загрузка модели ~0.5 ГБ)
.venv\Scripts\skg extract --limit 500     # LLM-извлечение (требует LLM_API_KEY в .env)
.venv\Scripts\skg serve                   # UI: http://localhost:8000
```

С Docker: `docker compose up -d`, дальше те же шаги начиная с venv.

### Данные кейса

Корпус (~4.5 ГБ) скачивается с Яндекс.Диска и распаковывается скриптом проекта —
не системным распаковщиком: имена файлов в zip записаны в CP866, `Expand-Archive`
даёт нечитаемые пути.

```powershell
# скачать zip всей публичной папки через API Яндекс.Диска
$pub = "https://disk.yandex.ru/d/npigiuw4Rbe9Pg"
$dl = Invoke-RestMethod ("https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key=" + [uri]::EscapeDataString($pub))
Invoke-WebRequest -Uri $dl.href -OutFile "data\local\case-corpus.zip"

# распаковать с исправлением кодировки имён (+ вложенные zip рекурсивно)
.venv\Scripts\python infra\unpack_corpus.py data\local\case-corpus.zip data\local\corpus
```

Настройка LLM в `.env` (Anthropic, Yandex AI Studio или OpenAI-совместимый API):
```
LLM_PROVIDER=anthropic            # или openai (openrouter, deepseek, vsegpt…), или yandex
LLM_MODEL=claude-haiku-4-5-20251001
LLM_API_KEY=sk-...
# LLM_BASE_URL=https://…          # для совместимых провайдеров
```
Для Yandex AI Studio — два равнозначных способа: OpenAI-совместимый эндпоинт
(см. .env.example) либо нативный провайдер:
```
LLM_PROVIDER=yandex
LLM_MODEL=yandexgpt-lite/latest
LLM_API_KEY=...
LLM_FOLDER_ID=...
```
Без ключа работают: ingest, embed, гибридный поиск, граф, статистика.
С ключом добавляются: извлечение сущностей/выводов, разбор вопроса в фильтры, сводка с цитатами.

## CLI

| Команда | Что делает |
|---|---|
| `skg ping / schema / stats` | подключение, применение схемы, счётчики |
| `skg seed [dir]` | канонические Material/Process/Equipment из data/seed |
| `skg ingest <dir> [--limit N] [--category X]` | корпус → Document + Chunk |
| `skg embed` | эмбеддинги чанков без них (возобновляемо) |
| `skg extract [--limit N] [--doc ID] [--force]` | LLM-извлечение из чанков (возобновляемо; `--force` — повторно, с версионированием выводов) |
| `skg search "запрос" [-k N] [--category …]` | гибридный поиск из терминала |
| `skg serve [--port 8000]` | веб-интерфейс |
| `skg load <kind> <file> / load-all <dir>` | структурированные источники (этап 1: CSV/JSONL) |
| `skg wipe --yes` | очистка данных графа (схема остаётся) |

## Структура проекта

```
schema/            ограничения и индексы (.cypher по порядку)
src/skg/
  ingest/          PDF/DOCX/PPTX/XLS(X) → текст → чанки (+OCR сканов); реестр корпуса из структуры папок
  extract/         LLM-клиент (Anthropic/OpenAI-совместимый), схемы, извлечение в граф
  query/           гибридный поиск (RRF), разбор вопроса, синтез ответа с цитатами
  web/             FastAPI + одностраничный UI (поиск, граф, пробелы, статистика)
  loaders/         seed-справочники, корпус, структурированные CSV/JSONL (этап 1)
data/seed/         канонические термины домена с RU/EN-синонимами
data/samples/      примеры структурированных форматов (этап 1)
queries/           демонстрационные Cypher-запросы
infra/neo4j-local/ развёртывание Neo4j без Docker (Windows)
```

## Дорожная карта

- [x] Скелет графа: схема, загрузчики структурированных источников
- [x] Корпус: извлечение текста, чанкинг, реестр документов
- [x] Форматы: PDF/DOCX/PPTX + таблицы XLS/XLSX (лист → юнит) + OCR сканов и
      картиночных страниц (Tesseract rus+eng, опционально `SKG_OCR=1`)
- [x] Семантический поиск: локальные эмбеддинги + BM25, RRF
- [x] LLM-извлечение по онтологии с нормализацией и очередью на проверку
- [x] Слой запросов: NL → фильтры → гибридный поиск → сводка с цитатами
- [x] Граф в поиске: сущности вопроса → документы по рёбрам MENTIONS (третий канал RRF)
- [x] Числовые ограничения: «сульфаты ≤ 300 мг/л» → интервальный фильтр по Measurement
- [x] UI: поиск с ответом, граф-эксплорер, тепловая карта пробелов, эксперты по теме
- [x] Версионирование фактов: изменившийся вывод при повторном извлечении
      (`skg extract --force`) архивируется в ConclusionVersion; API `/api/conclusion/history`
- [x] RBAC и аудит: 5 ролей (AUTH_TOKENS в .env), «внешний партнёр» не видит внутренние
      категории; журнал действий в logs/audit.jsonl, вкладка «Аудит» у администратора
- [ ] Связи SUPPORTS/CONTRADICTS между выводами из текстов (для структурированных — есть)
- [ ] Экспорт PDF/JSON-LD, уведомления, гриф на уровне документа — см. презентацию
