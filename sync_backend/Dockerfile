FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app.py /app/app.py
COPY templates /app/templates
COPY static /app/static

ENV SYNC_DB_PATH=/data/sync.db
ENV SYNC_BACKUP_DIR=/data/backups
ENV SYNC_SESSION_TTL_SECONDS=45
ENV SYNC_BACKUP_RETENTION_DAYS=14

VOLUME ["/data"]
EXPOSE 8095

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8095"]
