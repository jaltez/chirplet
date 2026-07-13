FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY requirements.txt .
RUN uv pip install --system -r requirements.txt

COPY apps/ apps/

RUN useradd -r -M --uid 1000 app && chown -R app:app /app

RUN mkdir -p /app/data && chown -R app:app /app/data

VOLUME ["/app/data"]
ENV APP_HOST=127.0.0.1
ENV APP_PORT=8000
ENV DATABASE_PATH=data/chirplet.db

USER app

EXPOSE 8000

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
