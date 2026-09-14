# test · Runaway 进度
## 2026-09-07T12:23:58Z · verify · AC-PROJECT · failed
摘要：exit_code=1
下一焦点：AC-PROJECT
## 2026-09-07T12:24:13Z · verify · AC-PROJECT · failed
摘要：exit_code=1
下一焦点：AC-PROJECT
## 2026-09-07T12:24:28Z · verify · AC-PROJECT · failed
摘要：exit_code=1
下一焦点：AC-PROJECT directed
## 2026-09-07T20:24:30Z · verify · AC-PROJECT · blocked
摘要：本轮 0x567-flash 网络调用被 Windows WinError 10013 拒绝，tool_calls=0；未产生新的工具证据。既有 AC-PROJECT 验收命令仍为 exit_code=1。
下一焦点：恢复模型网络后继续 AC-PROJECT directed；不得把本轮网络失败记为验收通过
## 本地验收复核 · AC-PROJECT · blocked
摘要：Agent 验收通道返回 exit_code=1；直接执行 release-drill.py 时 Docker 子进程触发 Windows WinError 5（拒绝访问）。
下一焦点：恢复 Docker 执行权限后再重试；保持 AC-PROJECT failed，不进入 release_wait
## 体验脚本保护 · verified
摘要：当前 directed 状态下脚本在 turn 1 前识别 v2 human block 并停止，tool_calls=0；未继续消耗模型请求。
下一焦点：仅在 Docker 与 0x567 网络权限恢复后使用 directed resume 重试
## 本地 acceptance hook · release_wait · passed
摘要：Docker 权限放行后正式 v2 acceptance hook exit_code=0，AC-PROJECT 清单已 passed；项目保持 25/25 正式任务并进入 release_wait。
下一焦点：发布确认；若要验证有待办项的完整 0x567 狂奔链路，需获得外部模型网络授权
