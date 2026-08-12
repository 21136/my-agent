#!/usr/bin/env node
import './bootstrap.js';
import {enableTrueColor} from './lib/truecolor.js';
enableTrueColor();

import React from 'react';
import {render} from 'ink';
import {Repl} from './repl.js';
import {MASCOT_LABEL, MASCOT_LINES} from './theme/welcomeMascotData.js';

render(
  <Repl
    greetSub="打工仔在这，今天继续搞 my-agent。"
    mascotLines={[...MASCOT_LINES]}
    mascotLabel={MASCOT_LABEL}
  />,
);
