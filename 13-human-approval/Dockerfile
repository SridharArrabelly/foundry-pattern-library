FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY approval_store.py mcp_server.py ./

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data
USER appuser

ENV PORT=8080
ENV APPROVAL_DB_PATH=/data/change-control.sqlite3
EXPOSE 8080

CMD ["python", "mcp_server.py"]
