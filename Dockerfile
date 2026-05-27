FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libdrm2 libgbm1 libxkbcommon0 libasound2 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libcups2 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN pip install --no-cache-dir -e .
RUN playwright install --with-deps chromium

EXPOSE 8000
ENV PYTHONUNBUFFERED=1
ENV IN_DOCKER=true

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
