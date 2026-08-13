import React, {memo} from 'react';
import {StatusBar} from '../../components/StatusBar.js';

type Props = {
  model: string;
  root: string;
  working?: boolean;
  activeTool?: string;
  activeToolStartedAt?: number;
  planStatus?: string;
};

export const StatusPane = memo(function StatusPane({
  model,
  root,
  working,
  activeTool,
  activeToolStartedAt,
  planStatus,
}: Props) {
  return (
    <StatusBar
      model={model}
      root={root}
      working={working}
      toolName={activeTool}
      toolStartedAt={activeToolStartedAt}
      planStatus={planStatus}
    />
  );
});
