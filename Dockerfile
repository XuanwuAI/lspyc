FROM python:3.12-alpine

ARG USE_MIRROR=true

RUN if [ "$USE_MIRROR" = "true" ]; then \
      sed -i 's#https\?://dl-cdn.alpinelinux.org/alpine#https://mirrors.tuna.tsinghua.edu.cn/alpine#g' /etc/apk/repositories; \
    fi
RUN apk update

# Install build dependencies and tools
RUN apk add --no-cache \
    # Build tools
    build-base \
    curl \
    # Node.js and npm
    nodejs \
    npm \
    # Go
    go gopls \
    # C/C++
    clang clang-extra-tools \
    # Java (kotlin server can not run with jdk-25)
    openjdk21 jdtls \
    bash

# Install rust-analyzer
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH=/root/.cargo/bin:$PATH
RUN rustup component add rust-analyzer rust-src

# Install TypeScript LSP
RUN npm install -g typescript-language-server typescript
RUN npm install -g pyright

# Install Kotlin LSP
RUN wget -O /tmp/kotlin-lsp.zip https://github.com/fwcd/kotlin-language-server/releases/download/1.3.13/server.zip
RUN unzip /tmp/kotlin-lsp.zip -d /usr/local/share/kotlin
RUN chmod +x /usr/local/share/kotlin/server/bin/kotlin-language-server
ENV PATH=/usr/local/share/kotlin/server/bin:$PATH

# Install uv
RUN if [ "$USE_MIRROR" = "true" ]; then \
      pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn; \
    else \
      pip install uv; \
    fi

# Copy project to /app directory
WORKDIR /app
ADD pyproject.toml .
ADD .python-version .
ADD uv.lock .
RUN uv sync --no-dev --no-install-project

ADD . .
RUN uv sync --no-dev

ENV PATH=/app/.venv/bin:$PATH


WORKDIR /workspace
CMD ["/bin/bash"]
