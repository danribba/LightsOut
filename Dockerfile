FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ ./src/
COPY main.py .
COPY config.yaml .

# Create data and logs directories
RUN mkdir -p data logs

# Run as non-root user
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

VOLUME ["/app/data", "/app/logs"]

CMD ["python", "main.py"]
