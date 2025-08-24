# Single-stage Dockerfile for Pi Zero 2 (Bookworm)
FROM --platform=linux/arm/v7 python:3.11-slim-bookworm

# Build + runtime deps (Pillow, SPI, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ python3-dev \
    libfreetype6-dev libjpeg-dev libopenjp2-7-dev libtiff6-dev libwebp-dev libffi-dev \
    libfreetype6 libjpeg62-turbo libopenjp2-7 libtiff6 libwebp7 \
    python3-spidev \
    fonts-dejavu-core curl git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for layer caching
COPY requirements.txt fast_dither.pyx ./
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
 && pip install --no-cache-dir -r requirements.txt \
    pigpio gpiozero spidev \
 && pip install --no-cache-dir Cython numpy \
 && python3 -c "from setuptools import setup; from Cython.Build import cythonize; import numpy; setup(ext_modules=cythonize('fast_dither.pyx'), include_dirs=[numpy.get_include()])" build_ext --inplace

# Waveshare e-Paper lib (pin master; adjust if you want a specific commit)
ARG EPD_REF=master
RUN pip install --no-cache-dir "git+https://github.com/waveshareteam/e-Paper.git@${EPD_REF}#subdirectory=RaspberryPi_JetsonNano/python"

# App files
COPY . .

# Healthcheck (only if your app serves /health)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

ENV PYTHONUNBUFFERED=1 \
    POKEMON_CONFIG_FILE=/app/config.json \
    POKEMON_CACHE_DIR=/app/pokemon_cache \
    POKEMON_WEB_HOST=0.0.0.0 \
    POKEMON_WEB_PORT=8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python3", "pokemon_eink_calendar.py", "--web-server"]