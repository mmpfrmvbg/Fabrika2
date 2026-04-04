FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY factory/ ./factory/
COPY static/ ./static/

ENV FACTORY_DB=/data/factory.db

VOLUME ["/data"]

EXPOSE 8000

CMD ["uvicorn", "factory.api_server:app", "--host", "0.0.0.0", "--port", "8000"]
