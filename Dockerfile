FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e . \
    && playwright install --with-deps --only-shell chromium

COPY config.yaml config.example.yaml ./

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "monitor_cartas.worker"]
