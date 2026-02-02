FROM python:3.12-alpine

RUN sed -i 's#https\?://dl-cdn.alpinelinux.org/alpine#https://mirrors.tuna.tsinghua.edu.cn/alpine#g' /etc/apk/repositories
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
    # Java
    jdtls \
    bash

# Install rust-analyzer
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH=/root/.cargo/bin:$PATH
RUN rustup component add rust-analyzer rust-src

# Configure pip mirror
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

# Install Python LSP
RUN pip install --no-cache-dir pyright

# Install TypeScript LSP
RUN npm install -g typescript-language-server

WORKDIR /workspace

CMD ["/bin/sh"]
