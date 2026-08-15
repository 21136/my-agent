# Repository Agent Instructions

## Python runtime

- On Windows, prefer `.venv\Scripts\python.exe` whenever that file exists.
- Before running Python-dependent validation, verify `sys.executable` and import the required packages with that interpreter.
- Do not rely on packages installed only in the user site directory; the Codex execution sandbox may not be able to read that directory.
- If `.venv` does not exist, use the system `python` and report any missing dependency instead of assuming a user-site installation is visible.
