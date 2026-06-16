FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Setup local repos directory
RUN mkdir -p /app/repos

ENV PYTHONUNBUFFERED=1
ENV REPOS_DIR=/app/repos
ENV DATABASE_URL=sqlite+aiosqlite:////app/repomind.db

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
