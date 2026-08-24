#!/usr/bin/env python3
"""Generate a configurable Music Dreamer microservice project skeleton."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DEFAULT_MODULES = ["auth", "user", "song", "playlist", "recommendation", "favorite", "history", "notification", "admin"]
SERVICE_NAMES = {
    "auth": "music-auth",
    "user": "music-user",
    "song": "music-song",
    "playlist": "music-playlist",
    "recommendation": "music-recommendation",
    "favorite": "music-favorite",
    "history": "music-history",
    "notification": "music-notification",
    "admin": "music-admin",
}


def fail(message: str) -> dict:
    return {"ok": False, "error": message}


def validate(args: dict) -> tuple[dict | None, str | None]:
    if not isinstance(args, dict):
        return None, "input must be a JSON object"
    for key in ("project_name", "target_dir"):
        if not isinstance(args.get(key), str) or not args[key].strip():
            return None, f"{key} is required and must be a non-empty string"
    project_name = args["project_name"].strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,63}", project_name):
        return None, "project_name must start with a letter and contain 2-64 letters, digits, '_' or '-'"
    modules = args.get("modules", DEFAULT_MODULES)
    if not isinstance(modules, list) or not modules or any(not isinstance(x, str) for x in modules):
        return None, "modules must be a non-empty array of strings"
    unknown = sorted(set(modules) - set(DEFAULT_MODULES))
    if unknown:
        return None, f"unsupported modules: {', '.join(unknown)}"
    if not isinstance(args.get("include_frontend", True), bool) or not isinstance(args.get("include_database_schema", True), bool):
        return None, "include_frontend and include_database_schema must be booleans"
    if not isinstance(args.get("dry_run", False), bool):
        return None, "dry_run must be boolean"
    return {
        "project_name": project_name,
        "target_dir": args["target_dir"].strip(),
        "group_id": args.get("group_id", "com.musicdreamer"),
        "java_version": str(args.get("java_version", "17")),
        "service_registry": args.get("service_registry", "Nacos"),
        "database": args.get("database", "MySQL"),
        "modules": list(dict.fromkeys(modules)),
        "include_frontend": args.get("include_frontend", True),
        "include_database_schema": args.get("include_database_schema", True),
        "dry_run": args.get("dry_run", False),
    }, None


def files_for(cfg: dict) -> list[str]:
    root = Path(cfg["target_dir"]) / cfg["project_name"]
    files = ["pom.xml", "README.md", "infrastructure/gateway/README.md", "infrastructure/nacos/README.md"]
    files += [f"services/{SERVICE_NAMES[m]}/README.md" for m in cfg["modules"]]
    if cfg["include_frontend"]:
        files += ["frontend/README.md", "frontend/package.json"]
    if cfg["include_database_schema"]:
        files += ["database/schema.sql"]
    return [str(root / f) for f in files]


def render(cfg: dict, relative: str) -> str:
    if relative == "README.md":
        return f"# {cfg['project_name']}\n\nSpring Cloud Alibaba + Spring Boot + VueJS 音乐平台骨架。\n\n模块：{', '.join(cfg['modules'])}\n注册配置中心：{cfg['service_registry']}；数据库：{cfg['database']}。\n"
    if relative == "pom.xml":
        modules = "\n".join(f"    <module>services/{SERVICE_NAMES[m]}</module>" for m in cfg["modules"])
        return f"""<project xmlns=\"http://maven.apache.org/POM/4.0.0\"><modelVersion>4.0.0</modelVersion>
  <groupId>{cfg['group_id']}</groupId><artifactId>{cfg['project_name']}</artifactId><version>0.1.0-SNAPSHOT</version>
  <packaging>pom</packaging><properties><java.version>{cfg['java_version']}</java.version></properties>
  <modules>{modules}</modules>
</project>
"""
    if relative == "frontend/package.json":
        return '{\n  "name": "music-dreamer-web",\n  "private": true,\n  "scripts": {"dev": "vite", "build": "vite build"},\n  "dependencies": {"vue": "latest"},\n  "devDependencies": {"vite": "latest", "@vitejs/plugin-vue": "latest"}\n}\n'
    if relative == "database/schema.sql":
        return "-- Music Dreamer initial schema\nCREATE TABLE IF NOT EXISTS users (id BIGINT PRIMARY KEY, username VARCHAR(64) NOT NULL UNIQUE);\nCREATE TABLE IF NOT EXISTS songs (id BIGINT PRIMARY KEY, title VARCHAR(255) NOT NULL, artist_id BIGINT, status VARCHAR(32));\nCREATE TABLE IF NOT EXISTS playlists (id BIGINT PRIMARY KEY, name VARCHAR(255) NOT NULL, owner_id BIGINT);\n"
    if relative.startswith("services/"):
        service = relative.split("/")[1]
        return f"# {service}\n\n独立 Spring Boot 微服务边界；请在此补充 controller、application、domain、repository 与测试。\n"
    if relative.startswith("infrastructure/"):
        return "# Infrastructure\n\n此目录用于网关、Nacos 注册/配置与部署配置。\n"
    if relative == "frontend/README.md":
        return "# Frontend\n\nVueJS 用户端与后台管理端入口。\n"
    return ""


def run(args: dict) -> dict:
    cfg, error = validate(args)
    if error:
        return fail(error)
    paths = files_for(cfg)
    if cfg["dry_run"]:
        return {"ok": True, "dry_run": True, "project": cfg["project_name"], "modules": cfg["modules"], "files": paths}
    root = Path(cfg["target_dir"]) / cfg["project_name"]
    if root.exists() and any(root.iterdir()):
        return fail(f"target project already exists and is not empty: {root}")
    created = []
    for full in paths:
        path = Path(full)
        relative = str(path.relative_to(root)).replace("\\", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(cfg, relative), encoding="utf-8")
        created.append(str(path))
    return {"ok": True, "project": str(root), "modules": cfg["modules"], "created": created}


if __name__ == "__main__":
    try:
        payload = json.load(sys.stdin)
        print(json.dumps(run(payload), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps(fail(str(exc)), ensure_ascii=False))
