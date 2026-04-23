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

WORKDIR /workspace

RUN mkdir -p /workspace/.config/gcloud /workspace/.claude \
    && touch /workspace/.config/gcloud/application_default_credentials.json \
    && chmod -R g+rwX /workspace \
    && chgrp -R 0 /workspace

ENV HOME=/workspace

ENTRYPOINT ["/bin/bash"]
