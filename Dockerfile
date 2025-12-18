# Use a lightweight Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the code
COPY . .

# Run the app using gunicorn (production-grade server)
# Cloud Run sets the PORT environment variable automatically
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 main:app

