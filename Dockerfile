FROM python:3.12-slim

WORKDIR /app

COPY api/requirements.txt api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt

COPY api api
COPY frontend frontend
COPY task task
COPY README.md .

ENV CSD_DATA_DIR=/app/data
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
