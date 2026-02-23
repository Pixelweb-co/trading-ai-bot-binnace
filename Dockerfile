# Use official Python lightweight image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (build-essential might be needed for some talib-like wheels)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Environment variables (can be overridden by docker-compose or .env)
ENV PYTHONUNBUFFERED=1

# Command to run the bot
CMD ["python", "run_refactored.py"]
