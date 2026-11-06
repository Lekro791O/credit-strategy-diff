FROM python:3.13-slim
WORKDIR /app
COPY mart/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY mart /app/mart
COPY checkrunner /app/checkrunner
ENV PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65534:65534
ENTRYPOINT ["python", "-m", "checkrunner.container_entry"]
