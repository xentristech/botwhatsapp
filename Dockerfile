# PLATIM Agent — imagen de produccion
FROM python:3.11-slim

# Evita prompts y buffers; logs en tiempo real
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencias primero (mejor cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Codigo de la app
COPY agent/ ./agent/
COPY dashboard/ ./dashboard/

# Carpeta de datos (SQLite). En produccion monta un volumen aqui para
# conservar cotizaciones, citas y conversaciones entre despliegues.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

# El puerto lo define la plataforma via $PORT (default 8088)
ENV PORT=8088
EXPOSE 8088

# host 0.0.0.0 para ser accesible desde fuera del contenedor
CMD ["sh", "-c", "uvicorn agent.main:app --host 0.0.0.0 --port ${PORT}"]
