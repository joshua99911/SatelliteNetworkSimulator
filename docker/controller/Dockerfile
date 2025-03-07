FROM python:3.10-slim

# Install required packages
RUN apt-get update && apt-get install -y \
    iputils-ping \
    net-tools \
    curl \
    docker.io \
    nodejs \
    npm

# Install Python dependencies
COPY requirements-controller.txt /app/
RUN pip install -r /app/requirements-controller.txt

# Copy application code
COPY emulation /app/emulation/

# Set up the web interface
WORKDIR /app/emulation/mnet/static/js
RUN npm install && npm run build

# Set working directory back to /app
WORKDIR /app

# Create necessary directories
RUN mkdir -p /app/data /app/logs

# Expose the web interface port
EXPOSE 8000

CMD ["python3", "-m", "emulation.controller"]
