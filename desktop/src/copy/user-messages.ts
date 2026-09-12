/** User-facing copy for desktop — plain Chinese, no Harness jargon. */

export const RUNAWAY_BLOCKER = {
  pausedImpact:
    "已停在当前阶段，没有跳过前置条件，也没有替你执行高风险操作。",
  pausedNextV2:
    "可点「继续狂奔」自动重试，或「定向再试」缩小修改范围；仍失败请在聊天说明要人工处理哪一项。",
  pausedNextLegacy:
    "点「继续狂奔」再试一轮；若仍失败，请在聊天说明要人工处理哪一项验收。",
  pausedNextOff:
    "处理上述问题后，再回到聊天继续当前项目。",
  resumeDocumentation: "继续补齐文档",
  resumeDefault: "继续狂奔",
} as const;

export const RUNAWAY_STATUS = {
  autoChain: "交付尚未完成，正在自动续接…",
  processing: "狂奔进行中",
  idle: "狂奔待命",
} as const;

export const RUNAWAY_STAGE = {
  preparingProject: "狂奔已开启，正在自动准备项目",
  preparingTasks: "狂奔已开启，正在自动准备实现任务",
  enteringImplementation: "狂奔已开启，正在进入实现",
  autoEnterImplementation: "狂奔已开启，自动进入实现",
} as const;
