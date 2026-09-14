FROM python:3.12-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

# Copy application code
COPY . .

# Create non-root user
RUN useradd -m -u 1000 gaiapulse && chown -R gaiapulse:gaiapulse /app
USER gaiapulse

# Expose port
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/auth/me')"

# Un solo worker, y no por capacidad: cada worker de uvicorn es un proceso Python que
# importa `app.main` y por lo tanto arranca su propio APScheduler. Con dos, los ocho jobs
# corrían dos veces por día — dos notificaciones de stock bajo, dos ausencias por la misma
# tarjeta, dos tandas de sugerencias— y nada en el código lo notaba, porque cada proceso
# veía su propia corrida como la única. No se arregló con un índice único sobre las señales
# porque eso pide una migración y la 0003 ya está gastada; se arregla acá, que es donde
# está la causa. Para escalar, un worker por contenedor y `ENABLE_BACKGROUND_JOBS=false` en
# todos menos uno.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
