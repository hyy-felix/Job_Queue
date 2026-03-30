FROM python:3.12-slim

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create jobs directory
RUN mkdir -p /app/jobs

EXPOSE 8080

# Default: run the server
CMD ["python3", "server.py"]
