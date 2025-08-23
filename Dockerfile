# Multi-stage Dockerfile optimized for Raspberry Pi Zero W 2
FROM python:3.11-slim-bullseye AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    python3-dev \
    libfreetype6-dev \
    libjpeg-dev \
    libopenjp2-7-dev \
    libtiff5-dev \
    libwebp-dev \
    libffi-dev \
    cython3 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source files needed for Cython build
COPY fast_dither.pyx .

# Build Cython fast dithering module
RUN pip install Cython && \
    python3 -c "from distutils.core import setup; from Cython.Build import cythonize; import numpy; setup(ext_modules=cythonize('fast_dither.pyx'), include_dirs=[numpy.get_include()])" build_ext --inplace

# Production stage
FROM python:3.11-slim-bullseye

# Install runtime dependencies for Pi GPIO and SPI
RUN apt-get update && apt-get install -y \
    libfreetype6 \
    libjpeg62-turbo \
    libopenjp2-7 \
    libtiff5 \
    libwebp6 \
    fonts-dejavu-core \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create app user for security
RUN useradd --create-home --shell /bin/bash pokemon
USER pokemon
WORKDIR /home/pokemon/app

# Copy application files
COPY --chown=pokemon:pokemon . .

# Copy built Cython module from builder
COPY --from=builder --chown=pokemon:pokemon fast_dither*.so ./

# Create cache directory
RUN mkdir -p pokemon_cache earliest_pokemon_sprites

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Expose web server port
EXPOSE 8000

# Default configuration for remote deployment
ENV PYTHONUNBUFFERED=1
ENV POKEMON_CONFIG_FILE=/home/pokemon/app/config.json
ENV POKEMON_CACHE_DIR=/home/pokemon/app/pokemon_cache
ENV POKEMON_WEB_HOST=0.0.0.0
ENV POKEMON_WEB_PORT=8000

# Entry point with automatic dependency installation
COPY --chown=pokemon:pokemon docker-entrypoint.sh /home/pokemon/
RUN chmod +x /home/pokemon/docker-entrypoint.sh

ENTRYPOINT ["/home/pokemon/docker-entrypoint.sh"]
CMD ["python3", "pokemon_eink_calendar.py", "--web-server"]