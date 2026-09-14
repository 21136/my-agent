export type ModelOption = {
  id: string;
  name: string;
  tier?: string;
};

export function parseModelOptions(value: unknown): ModelOption[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
    .map((item) => ({
      id: typeof item.id === 'string' ? item.id : '',
      name: typeof item.name === 'string' ? item.name : '',
      tier: typeof item.tier === 'string' ? item.tier : undefined,
    }))
    .filter((item) => item.id.length > 0 && item.name.length > 0);
}

/** True when Composer text is bare ``/model`` (no model id argument). */
export function isModelPickerTrigger(text: string): boolean {
  return text.trim().toLowerCase() === '/model';
}

export function modelPickerStartIndex(models: readonly ModelOption[], currentModel: string): number {
  const normalized = currentModel.trim().toLowerCase();
  const index = models.findIndex((model) => model.id.toLowerCase() === normalized);
  return index >= 0 ? index : 0;
}

export const MODEL_PICKER_MAX_VISIBLE = 6;

export function modelPickerVisibleWindow(
  count: number,
  selectedIndex: number,
  maxVisible: number = MODEL_PICKER_MAX_VISIBLE,
): {start: number; end: number} {
  if (count <= 0) return {start: 0, end: 0};
  if (count <= maxVisible) return {start: 0, end: count};
  let start = selectedIndex - Math.floor(maxVisible / 2);
  if (start < 0) start = 0;
  if (start + maxVisible > count) start = count - maxVisible;
  return {start, end: start + maxVisible};
}

export function nextModelPickerIndex(
  current: number,
  direction: -1 | 1,
  count: number,
): number {
  if (count <= 0) return 0;
  return (current + direction + count) % count;
}
