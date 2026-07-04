# syntax=docker/dockerfile:1
#
# Образ приложения «Научный клубок» (веб-UI + слой запросов).
# Neo4j поднимается отдельным сервисом (см. docker-compose.yml).
#
# Особенности:
#   * модель эмбеддингов (fastembed/ONNX) скачивается на этапе build и
#     запекается в образ → на рантайме сеть для неё не нужна;
#   * ключи (LLM_API_KEY и пр.) в образ НЕ кладутся — только env-переменными;
#   * приложение слушает 0.0.0.0 (в отличие от локального дефолта 127.0.0.1).

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000

# libgomp1 — рантайм OpenMP, нужен onnxruntime (fastembed)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) Зависимости и пакет ставим первым слоем — кэшируется, пока не менялись
#    исходники. README нужен сборщику как readme проекта не является — копируем
#    только необходимое для установки.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

# 2) Предзагрузка модели эмбеддингов в образ (кэш fastembed рядом с установкой
#    пакета — тот же путь читается на рантайме). Падение здесь ловим на build,
#    а не у жюри на первом запросе.
RUN python -c "from skg.embeddings import get_model; get_model(); print('embed model cached')" \
    && chmod -R a+rX /usr/local/lib/python3.12/.cache 2>/dev/null || true

# 3) Схемы, seed-справочники и демо-данные — чтобы инициализацию базы
#    (skg schema / skg seed / skg load-all) можно было запускать из контейнера.
COPY schema ./schema
COPY queries ./queries
COPY data/seed ./data/seed
COPY data/samples ./data/samples
COPY README.md ./

EXPOSE 8000

# Проверка живости: корневой эндпоинт отдаёт UI без обращения к БД
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4)" || exit 1

# PORT можно переопределить; host обязателен 0.0.0.0 для доступа снаружи контейнера
CMD ["sh", "-c", "exec skg serve --host 0.0.0.0 --port ${PORT:-8000}"]
