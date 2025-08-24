# Single-stage Dockerfile optimized for Raspberry Pi Zero W 2
FROM python:3.11-slim-bookworm

# Install build dependencies and runtime libraries
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    python3-dev \
    cython3 \
    git \
    libfreetype6-dev \
    libjpeg-dev \
    libopenjp2-7-dev \
    libtiff5-dev \
    libwebp-dev \
    libffi-dev \
    libfreetype6 \
    libjpeg62-turbo \
    libopenjp2-7 \
    libtiff6 \
    libwebp7 \
    python3-spidev \
    fonts-dejavu-core \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory early
WORKDIR /app

# Copy source files needed for Cython build first
COPY fast_dither.pyx requirements.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install RPi.GPIO for GPIO access (not available via apt)
RUN pip install --no-cache-dir RPi.GPIO

# Install Waveshare e-Paper library from GitHub
RUN git clone --depth 1 https://github.com/waveshareteam/e-Paper.git && \
    cd e-Paper/RaspberryPi_JetsonNano/python && \
    pip install . && \
    cd ../../.. && \
    rm -rf e-Paper

# Build Cython fast dithering module
RUN pip install Cython && \
    python3 -c "from distutils.core import setup; from Cython.Build import cythonize; import numpy; setup(ext_modules=cythonize('fast_dither.pyx'), include_dirs=[numpy.get_include()])" build_ext --inplace

# Copy remaining application files
COPY . .

# Create cache directory
RUN mkdir -p pokemon_cache earliest_pokemon_sprites

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Expose web server port
EXPOSE 8000

# Default configuration for remote deployment
ENV PYTHONUNBUFFERED=1
ENV POKEMON_CONFIG_FILE=/app/config.json
ENV POKEMON_CACHE_DIR=/app/pokemon_cache
ENV POKEMON_WEB_HOST=0.0.0.0
ENV POKEMON_WEB_PORT=8000

# Entry point with automatic dependency installation  
COPY docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python3", "pokemon_eink_calendar.py", "--web-server"]