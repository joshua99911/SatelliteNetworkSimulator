FROM ubuntu:22.04

# Set noninteractive installation
ENV DEBIAN_FRONTEND=noninteractive

# Install required packages
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-dev \
    iputils-ping \
    net-tools \
    iproute2 \
    tcpdump \
    traceroute \
    frr \
    supervisor

# Install Python dependencies
COPY requirements-node.txt /app/
RUN pip3 install -r /app/requirements-node.txt

# Configure FRR
RUN sed -i 's/bgpd=no/bgpd=yes/g' /etc/frr/daemons && \
    sed -i 's/ospfd=no/ospfd=yes/g' /etc/frr/daemons && \
    sed -i 's/zebra=no/zebra=yes/g' /etc/frr/daemons && \
    sed -i 's/staticd=no/staticd=yes/g' /etc/frr/daemons

# Copy application code
COPY emulation/node_agent.py /app/emulation/
COPY emulation/common /app/emulation/common/

# Create necessary directories
RUN mkdir -p /app/data /app/logs /app/configs

# Set working directory
WORKDIR /app

# Copy supervisor configuration for services
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose ports
EXPOSE 8080 179 2601 2604 2605 5000

CMD ["/usr/bin/supervisord"]
