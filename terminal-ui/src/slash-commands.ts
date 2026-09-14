export type SlashCommand = {
  name: string;
  description: string;
  suffix?: string;
};

export const SLASH_COMMANDS: readonly SlashCommand[] = [
  {name: '/model', description: '查看或切换模型', suffix: '模型名'},
  {name: '/clear', description: '清空当前 transcript'},
  {name: '/compact', description: '压缩当前会话上下文'},
];

export function slashCommandCandidates(text: string): SlashCommand[] {
  if (!/^\/[^\s]*$/.test(text)) return [];
  const normalized = text.toLowerCase();
  if (SLASH_COMMANDS.some((command) => command.name === normalized)) return [];
  return SLASH_COMMANDS.filter((command) => command.name.startsWith(normalized)).slice(0, 4);
}

export function nextSlashCommandIndex(
  current: number,
  direction: -1 | 1,
  count: number,
): number {
  if (count <= 0) return 0;
  return (current + direction + count) % count;
}

export function completeSlashCommand(command: SlashCommand): string {
  return `${command.name}${command.suffix ? ' ' : ''}`;
}
