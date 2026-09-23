FROM python:3.11-slim

# ffmpeg + libass (legendas) + fontes. libGL não entra: usamos opencv headless.
RUN apt-get update && apt-get install -y --no-install-recommends         ffmpeg         fonts-dejavu-core         fonts-noto-core         libgomp1     && rm -rf /var/lib/apt/lists/*

# O repositório inteiro é o pacote `cutclips`, então ele entra numa pasta com esse nome.
WORKDIR /app/cutclips

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1     CUTCLIPS_STORAGE=/data     CUTCLIPS_HOST=0.0.0.0     HF_HOME=/models

RUN mkdir -p /data /models

EXPOSE 8000
# Pelo launch.py, e não por uvicorn direto: com a raiz no sys.path, o select.py do
# projeto sombrearia o módulo `select` da biblioteca padrão.
CMD ["python", "-P", "scripts/launch.py", "api", "8000"]
