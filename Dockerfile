FROM mcr.microsoft.com/playwright/python:v1.62.0-jammy

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e .

COPY config.yaml config.example.yaml ./

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "monitor_cartas.worker"]
