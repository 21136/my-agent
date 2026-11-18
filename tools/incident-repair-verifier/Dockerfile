# Same pinned Linux CPython base as the self-evolution verifier.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
RUN /usr/local/bin/python -m pip install --no-cache-dir --disable-pip-version-check \
  pytest==8.3.5 iniconfig==2.0.0 packaging==24.2 pluggy==1.5.0 \
  httpx==0.28.1 httpcore==1.0.9 h11==0.16.0 anyio==4.9.0 \
  certifi==2026.1.4 idna==3.10 sniffio==1.3.1 typing-extensions==4.13.2
