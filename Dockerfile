FROM node:20-bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates python3 openssh-client \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

# Claude Code refuses --dangerously-skip-permissions as root, so run as a normal user
RUN useradd -m -s /bin/bash claude
USER claude
WORKDIR /home/claude

CMD ["sleep", "infinity"]
