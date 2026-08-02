# 浏览器打开（BROWSER）

> 版本 **0.1.0** · 2026-08-02 · Phase **33 F1** · 关联：[CURSOR-ALIGN.md](./CURSOR-ALIGN.md) §4.F · [http_request](./PROJECT-DEV-TOOLS.md)

## 心智

| 需求 | 工具 |
|------|------|
| 探活 / 读响应体 | `http_request` |
| 给人看页面 | **`browser_open`**（系统默认浏览器） |
| 无头截图 / DOM 断言 | **F2 defer**（Playwright 等） |

## F1 规则

- 仅 `http://` / `https://`；禁 `file:` / `javascript:` / URL userinfo
- **loopback**（localhost / 127.0.0.1 / ::1）可免确认
- **外网**永远确认
- `dry_run` 只校验不打开

## 回归

- **IT-150** · 手工 **S-150**：起本地服 → `browser_open http://127.0.0.1:…`
