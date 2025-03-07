FROM python:3.10-slim

# Install required packages
RUN apt-get update && apt-get install -y \
    iputils-ping \
    net-tools \
    curl \
    docker.io

# Install Python dependencies
COPY requirements-dynamics.txt /app/
RUN pip install -r /app/requirements-dynamics.txt

# Copy application code
COPY emulation /app/emulation/

# Set working directory
WORKDIR /app

# Create necessary directories
RUN mkdir -p /app/data /app/logs

CMD ["python3", "-m", "emulation.dynamics_service"]
