# One image, two roles: the "api" (Fastify server) and the "worker" (job runner)
# started with different commands from docker-compose.yml. Mirrors scripts/setup.sh:
# Python venv for bcn, pinned Marp toolchain, pinned Chrome for Testing, ffmpeg 7.1,
# and the built ui/server + ui/app.
#
# Build with:  docker build -t beacon-ui .
# Node images already ship Python? No — base on node:22, add Python 3.11+ ourselves.

FROM node:22-bookworm-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip \
      ca-certificates curl xz-utils \
      # Chrome for Testing (headless) runtime libraries
      fonts-liberation libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 \
      libcups2 libdbus-1-3 libdrm2 libgbm1 libgtk-3-0 libnspr4 libnss3 \
      libxcomposite1 libxdamage1 libxfixes3 libxkbcommon0 libxrandr2 \
      xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# -- ffmpeg 7.1, built from source ---------------------------------------------------
# No prebuilt static binary is reliably pinned to exactly 7.1 (johnvansickle.com's
# "release" build floats to the latest version, and its old-releases archive does not
# go back to 7.x), and bcn's tools.py strictly requires "7.1" or "7.1.x". Building from
# the official ffmpeg.org source tarball is slower but exactly reproducible.
# bcn's default audio codec name is plain "aac" (config.py's [delivery] default), which
# ffmpeg's own built-in native AAC encoder satisfies — no need for the non-free libfdk-aac,
# which also isn't in Debian's default apt sources (it's in non-free, not enabled here).
FROM base AS ffmpeg-build
ARG FFMPEG_VERSION=7.1
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential yasm nasm pkg-config \
      libx264-dev libx265-dev libvpx-dev libmp3lame-dev libopus-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSL -o ffmpeg.tar.xz "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" \
    && tar -xJf ffmpeg.tar.xz --strip-components=1 \
    && ./configure --prefix=/opt/ffmpeg --disable-debug --disable-doc \
         --enable-gpl \
         --enable-libx264 --enable-libx265 --enable-libvpx \
         --enable-libmp3lame --enable-libopus \
    && make -j"$(nproc)" \
    && make install \
    && /opt/ffmpeg/bin/ffmpeg -version | head -1 | grep -q "ffmpeg version ${FFMPEG_VERSION}"

FROM base AS ffmpeg
COPY --from=ffmpeg-build /opt/ffmpeg /opt/ffmpeg
# Runtime shared libs the --enable-lib* flags above link against. Exact soname-versioned
# package names (e.g. libx264-164) vary between Debian releases; the -dev packages'
# corresponding runtime packages are pulled in as apt dependencies already during the
# ffmpeg-build stage, so re-declaring the *-dev packages here (without build-essential)
# is the simplest way to get matching runtime libs without guessing exact package names.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libx264-dev libx265-dev libvpx-dev libmp3lame-dev libopus-dev \
    && rm -rf /var/lib/apt/lists/*

# -- tooling: Python venv + pinned Marp toolchain + Chrome for Testing ------------------
FROM base AS tooling
WORKDIR /app
COPY tooling/pyproject.toml tooling/pyproject.toml
COPY tooling/bcn tooling/bcn
RUN python3 -m venv tooling/.venv \
    && tooling/.venv/bin/pip install --no-cache-dir -q -e tooling

COPY tooling/node/package.json tooling/node/package-lock.json tooling/node/
RUN cd tooling/node && npm ci --no-audit --no-fund

# CHROME_VERSION is pinned in tooling/bcn/tools.py; installed by the same
# @puppeteer/browsers tool scripts/setup.sh uses, to the same path tools.py expects.
RUN CHROME_VERSION=$(python3 -c "import re;print(re.search(r'CHROME_VERSION = \"([^\"]+)\"', open('tooling/bcn/tools.py').read()).group(1))") \
    && cd tooling/node \
    && npx --no-install @puppeteer/browsers install "chrome@${CHROME_VERSION}" --path /app/tooling/vendor/chrome

COPY tooling/themes tooling/themes

# -- ui: build the Fastify server and the Quasar SPA ------------------------------------
FROM base AS ui-build
WORKDIR /app/ui
COPY ui/package.json ui/package-lock.json ./
COPY ui/shared/package.json shared/
COPY ui/server/package.json server/
COPY ui/app/package.json app/
RUN npm ci --no-audit --no-fund
COPY ui/shared shared
COPY ui/server server
COPY ui/app app
RUN npm run build

# -- final image -------------------------------------------------------------------------
FROM base AS final
WORKDIR /app

COPY --from=ffmpeg /opt/ffmpeg/bin/ffmpeg /opt/ffmpeg/bin/ffprobe /usr/local/bin/
COPY --from=tooling /app/tooling /app/tooling
COPY --from=ui-build /app/ui/server/dist ui/server/dist
COPY --from=ui-build /app/ui/server/package.json ui/server/package.json
COPY --from=ui-build /app/ui/server/bin ui/server/bin
COPY --from=ui-build /app/ui/server/node_modules ui/server/node_modules
COPY --from=ui-build /app/ui/app/dist/spa ui/app/dist/spa

ENV BCN=/app/tooling/.venv/bin/bcn
ENV NODE_ENV=production

EXPOSE 8420
ENTRYPOINT ["node", "/app/ui/server/bin/beacon-ui"]
