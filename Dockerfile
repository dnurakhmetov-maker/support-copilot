# API service image. Owner: Infra / QA.
# Case data and secrets are NOT baked in: data/ is mounted and .env is passed by docker-compose.yml.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DATA_DIR=/app/data
WORKDIR /app

COPY ml/requirements.txt ml/requirements.txt
COPY service/ service/
RUN pip install -r ml/requirements.txt \
 && if [ -f service/requirements.txt ]; then pip install -r service/requirements.txt; fi

COPY contract/ contract/

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "service.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
