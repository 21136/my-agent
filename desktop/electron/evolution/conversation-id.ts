const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const LEGACY_CONVERSATION_ID = /^([0-9]{4})([0-9]{2})([0-9]{2})-([0-9a-f]{8})$/;

export function isEvolutionConversationId(value: unknown): value is string {
  if (typeof value !== "string") return false;
  if (value.length === 36 && UUID.test(value)) return true;
  if (value.length !== 17) return false;
  const match = LEGACY_CONVERSATION_ID.exec(value);
  if (!match || match[0] !== value) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (year < 1 || month < 1 || month > 12) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day >= 1 && day <= daysInMonth[month - 1];
}
