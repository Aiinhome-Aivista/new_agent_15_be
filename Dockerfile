FROM python:3.12-slim

WORKDIR /app

# Install system dependencies if any are needed for Python packages
# RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .


# Expose port 3030
EXPOSE 3050

# Command to run the application
CMD ["python", "run.py"]
