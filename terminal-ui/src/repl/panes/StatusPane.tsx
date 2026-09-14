import React, {memo} from 'react';
import {StatusBar} from '../../components/StatusBar.js';
import type {TerminalResult} from '../../types.js';

type Props = {
  model: string;
  root: string;
  working?: boolean;
  activeTool?: string;
  activeToolStartedAt?: number;
  planStatus?: string;
  result?: TerminalResult;
  columns?: number;
};

export const StatusPane = memo(function StatusPane({
  model,
  root,
  working,
  activeTool,
  activeToolStartedAt,
  planStatus,
  result,
  columns,
}: Props) {
  return (
    <StatusBar
      model={model}
      root={root}
      working={working}
      toolName={activeTool}
      toolStartedAt={activeToolStartedAt}
      planStatus={planStatus}
      result={result}
      columns={columns}
    />
  );
});
