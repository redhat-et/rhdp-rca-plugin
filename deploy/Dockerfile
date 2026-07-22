FROM node:20-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    python3 \
    python3-venv \
    python3-pip \
    openssh-client \
    rsync \
    jq \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

WORKDIR /opt/app

COPY skills/ /opt/app/skills/
COPY deploy/batch-rca-automation/ /opt/app/batch/
RUN pip install --no-cache-dir --break-system-packages -r /opt/app/batch/requirements.txt
COPY .claude-plugin/ /opt/app/plugins-cache/

WORKDIR /workspace

RUN mkdir -p /workspace/.config/gcloud /workspace/.claude \
    && touch /workspace/.config/gcloud/application_default_credentials.json \
    && chmod -R g+rwX /workspace \
    && chgrp -R 0 /workspace

ENV HOME=/workspace

ENTRYPOINT ["/bin/bash"]
