FROM python:3.11-slim

# ffmpeg + libass (legendas) + fontes. libGL não entra: usamos opencv headless.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-dejavu-core \
        fonts-noto-core \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cutclips/ ./cutclips/
COPY api/ ./api/
COPY web/ ./web/

ENV PYTHONUNBUFFERED=1 \
    CUTCLIPS_STORAGE=/data \
    HF_HOME=/models

RUN mkdir -p /data /models

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
