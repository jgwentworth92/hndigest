FROM python:3.12-slim

WORKDIR /app

# Install system deps for lxml/trafilatura
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python deps
COPY pyproject.toml .
COPY src/ src/
COPY config/ config/
COPY db/ db/

RUN pip install --no-cache-dir .

# Create output directory
RUN mkdir -p /app/output/digests

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# Default: server mode
CMD ["python", "-m", "hndigest", "start", "--mode", "server", "--host", "0.0.0.0", "--port", "8000"]
