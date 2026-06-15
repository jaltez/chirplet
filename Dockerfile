FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY requirements.txt .
RUN uv pip install --system -r requirements.txt

COPY apps/ apps/

VOLUME ["/app/data"]
ENV APP_HOST=0.0.0.0
ENV APP_PORT=8000
ENV DATABASE_PATH=data/chirplet.db

EXPOSE 8000

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
