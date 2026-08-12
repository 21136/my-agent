/** Must be the first import in cli/demo — ESM hoists imports before other module code. */
process.env.FORCE_COLOR ??= '3';
process.env.COLORTERM ??= 'truecolor';
