FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY app/ ./app/

# Data directory (persisted via volume in compose)
RUN mkdir -p /data

ENV DB_PATH=/data/autocs.db
ENV LOG_LEVEL=INFO
ENV DEBUG=false

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
