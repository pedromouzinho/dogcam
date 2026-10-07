FROM python:3.12.12-slim-bookworm
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ app/
COPY tests/ tests/
COPY config.yaml /config/config.yaml
CMD ["python", "-u", "-m", "app.main"]
