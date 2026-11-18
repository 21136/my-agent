FROM node:22.14.0-bookworm-slim AS node-verifier
RUN npm install --prefix /opt --ignore-scripts --no-package-lock --no-audit --no-fund typescript@5.7.2

FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
COPY --from=node-verifier /usr/local/bin/node /usr/local/bin/node
COPY --from=node-verifier /opt/node_modules /opt/node_modules
RUN /usr/local/bin/python -m pip install --no-cache-dir --disable-pip-version-check \
    pytest==8.3.5 iniconfig==2.0.0 packaging==24.2 pluggy==1.5.0 \
    httpx==0.28.1 httpcore==1.0.9 h11==0.16.0 anyio==4.9.0 \
    certifi==2026.1.4 idna==3.10 sniffio==1.3.1 typing-extensions==4.13.2 \
    websockets==15.0.1 rich==13.9.4 prompt_toolkit==3.0.51
