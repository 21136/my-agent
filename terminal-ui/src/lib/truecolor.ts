/** Enable 24-bit ANSI before Ink/chalk initialize (Windows stderr · WT). */
export function enableTrueColor(stream: NodeJS.WriteStream = process.stderr): void {
  if (process.env.FORCE_COLOR === undefined) process.env.FORCE_COLOR = '3';
  if (process.env.COLORTERM === undefined) process.env.COLORTERM = 'truecolor';
  const tty = stream as NodeJS.WriteStream & {getColorDepth?: () => number};
  if (stream.isTTY && typeof tty.getColorDepth === 'function' && tty.getColorDepth() < 8) {
    tty.getColorDepth = () => 32;
  }
}
