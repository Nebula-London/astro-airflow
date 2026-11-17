FROM mcr.microsoft.com/devcontainers/base:ubuntu-22.04

# Install Astro CLI
RUN curl -fsSL install.astronomer.io | ASTRO_INSTALL_DIR=/usr/local/bin bash -s

# Install OpenCode CLI for use inside the container
RUN mkdir -p /tmp/opencode && \
    curl -fsSL -o /tmp/opencode/install.sh https://opencode.ai/v2/install && \
    HOME=/opt/opencode bash /tmp/opencode/install.sh --no-modify-path && \
    ln -s /opt/opencode/.opencode/bin/opencode /usr/local/bin/opencode && \
    rm -rf /tmp/opencode

# Install Python and pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Install the OCI CLI independently of Airflow's Python environment
RUN curl -fsSL -o /tmp/install-oci.sh https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh && \
    bash /tmp/install-oci.sh --accept-all-defaults --install-dir /opt/oci-cli --exec-dir /usr/local/bin --script-dir /usr/local/bin --no-tty && \
    rm /tmp/install-oci.sh

# Set Airflow environment and version variables
ENV AIRFLOW_HOME=/opt/airflow
ARG AIRFLOW_VERSION=3.3.2

# Install Apache Airflow with official constraints
RUN PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" && \
    CONSTRAINT_URL="https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt" && \
    pip3 install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" --constraint "${CONSTRAINT_URL}"
