# syntax=docker/dockerfile:1
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=secret,id=proxy_ca_bundle \
    if [ -f /run/secrets/proxy_ca_bundle ]; then PIP_CERT=/run/secrets/proxy_ca_bundle pip install --no-cache-dir -r requirements.txt; else pip install --no-cache-dir -r requirements.txt; fi
COPY . .
RUN DJANGO_SECRET_KEY=build-only-static-files-not-a-runtime-secret DEBUG=1 python manage.py collectstatic --noinput \
    && useradd --uid 10001 --create-home crm \
    && mkdir -p /data && chown -R crm:crm /app /data
USER crm
ENV CRM_DATA_DIR=/data DEBUG=0
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]
