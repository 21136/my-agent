/**
 * Daily shell starfield — canvas 2D (T-904g1 idle + T-904g3 WS + T-904g4 recall/history + T-904g7 persistence).
 */

import type { ConstellationLink, ConstellationStar } from "./constellation";

export interface StarfieldOptions {
  /** Background dust particles (no turn links). */
  dustCount?: number;
  /** Recent pairs to highlight on recall (DAILY-SHELL §4.4). */
  recallPairs?: number;
  onStarPersist?: (star: ConstellationStar) => void;
  onLinkPersist?: (link: ConstellationLink) => void;
}

interface DustStar {
  x: number;
  y: number;
  radius: number;
  twinklePhase: number;
  twinklePeriod: number;
  /** 0 = far · 1 = near — drives parallax drift speed */
  depth: number;
  driftPhaseX: number;
  driftPhaseY: number;
  driftSpeed: number;
}

interface TurnStar {
  id: string;
  x: number;
  y: number;
  radius: number;
  twinklePhase: number;
  twinklePeriod: number;
  role: "user" | "assistant";
  turnIndex: number;
  sessionId: string;
  bornAt: number;
  createdAt: string;
  dedupeKey: string;
}

interface TurnLink {
  fromId: string;
  toId: string;
  sessionId: string;
  turnIndex: number;
  frozen: boolean;
  pulseUntil: number;
}

interface CameraState {
  panX: number;
  panY: number;
  scale: number;
}

interface CameraAnim extends CameraState {
  fromPanX: number;
  fromPanY: number;
  fromScale: number;
  start: number;
  duration: number;
}

export interface HistoryStarSeed {
  role: "user" | "assistant";
  turnIndex: number;
}

export interface StarfieldController {
  birthUserStar(turnIndex: number, sessionId?: string): string | null;
  birthAssistantStar(turnIndex: number, sessionId?: string): string | null;
  seedFromHistory(sessionId: string, items: HistoryStarSeed[]): void;
  restorePersisted(stars: ConstellationStar[], links: ConstellationLink[]): void;
  clearTurnStars(): void;
  hasStar(sessionId: string, turnIndex: number, role: "user" | "assistant"): boolean;
  focusRecall(k?: number): void;
  clearRecallFocus(): void;
  pulseTurnLink(sessionId: string, turnIndex: number): void;
  setConfirmFrozen(sessionId: string | null, turnIndex: number | null): void;
  setTurnBusy(busy: boolean): void;
  refreshLayout(): void;
  isReducedMotion(): boolean;
  destroy(): void;
}

const DRIFT_BASE_MS = 72_000;
const BIRTH_MS = 300;
const TWINKLE_MIN = 0.5;
const TWINKLE_MAX = 0.98;
const TURN_TWINKLE_MIN = 0.62;
const TURN_TWINKLE_MAX = 1;
const BUSY_DRIFT_MULT = 1.35;
const FROZEN_DRIFT_MULT = 0.2;
const RECALL_ZOOM = 1.22;
const CAMERA_MS = 850;
const DEFAULT_RECALL_PAIRS = 3;

function prefersReducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function readCssColor(el: HTMLElement, name: string, fallback: string): string {
  const value = getComputedStyle(el).getPropertyValue(name).trim();
  return value || fallback;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function dedupeKey(sessionId: string, turnIndex: number, role: "user" | "assistant"): string {
  return `${sessionId}:${turnIndex}:${role}`;
}

function accentGlow(accent: string, alpha: number): string {
  const hex = accent.trim();
  if (/^#[0-9a-fA-F]{6}$/.test(hex)) {
    const r = Number.parseInt(hex.slice(1, 3), 16);
    const g = Number.parseInt(hex.slice(3, 5), 16);
    const b = Number.parseInt(hex.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  return `rgba(212, 160, 106, ${alpha})`;
}

function seededUnit(turnIndex: number, salt: number): number {
  const raw = Math.sin(turnIndex * 127.1 + salt * 311.7) * 43758.5453;
  return raw - Math.floor(raw);
}

function buildDust(count: number): DustStar[] {
  const stars: DustStar[] = [];
  for (let i = 0; i < count; i += 1) {
    const depth = (i % 3) / 2;
    const depthScale = 0.72 + depth * 0.5;
    stars.push({
      x: Math.random(),
      y: Math.random(),
      radius: (1.1 + Math.random() * 1.6) * depthScale,
      twinklePhase: Math.random() * Math.PI * 2,
      twinklePeriod: 2800 + Math.random() * 4200,
      depth,
      driftPhaseX: Math.random() * Math.PI * 2,
      driftPhaseY: Math.random() * Math.PI * 2,
      driftSpeed: 0.55 + depth * 0.65 + Math.random() * 0.25,
    });
  }
  return stars;
}

function userStarPosition(turnIndex: number): { x: number; y: number } {
  const lane = (turnIndex - 1) % 8;
  return {
    x: clamp(0.1 + lane * 0.032 + (seededUnit(turnIndex, 1) - 0.5) * 0.04, 0.08, 0.42),
    y: clamp(0.12 + ((turnIndex * 0.11) % 0.66) + seededUnit(turnIndex, 2) * 0.06, 0.1, 0.84),
  };
}

function assistantStarPosition(turnIndex: number): { x: number; y: number } {
  const lane = (turnIndex - 1) % 8;
  return {
    x: clamp(0.56 + lane * 0.032 + (seededUnit(turnIndex, 3) - 0.5) * 0.04, 0.54, 0.92),
    y: clamp(0.16 + ((turnIndex * 0.13 + 0.04) % 0.62) + seededUnit(turnIndex, 4) * 0.06, 0.12, 0.86),
  };
}

function linkPulseAlpha(now: number, link: TurnLink, base: number): number {
  if (link.frozen) {
    return 0.55 + base * 0.15;
  }
  if (now >= link.pulseUntil) {
    return base;
  }
  const remaining = link.pulseUntil - now;
  const total = 900;
  const phase = 1 - remaining / total;
  const beats = Math.sin(phase * Math.PI * 4) * 0.5 + 0.5;
  return base + beats * 0.35;
}

export function createStarfield(
  canvas: HTMLCanvasElement,
  options: StarfieldOptions = {},
): StarfieldController {
  const host = canvas.parentElement ?? canvas;
  const ctx = canvas.getContext("2d")!;

  const recallPairs = options.recallPairs ?? DEFAULT_RECALL_PAIRS;
  const onStarPersist = options.onStarPersist;
  const onLinkPersist = options.onLinkPersist;
  const dust = buildDust(options.dustCount ?? 96);
  const turnStars: TurnStar[] = [];
  const turnLinks: TurnLink[] = [];
  let starIdSeq = 0;

  let width = 0;
  let height = 0;
  let dpr = 1;
  let raf = 0;
  let turnBusy = false;
  let frozenTurnIndex: number | null = null;
  let frozenSessionId: string | null = null;
  let highlightPairKeys = new Set<string>();
  let recallFocusActive = false;
  let reducedMotion = prefersReducedMotion();

  const camera: CameraState = { panX: 0, panY: 0, scale: 1 };
  let cameraAnim: CameraAnim | null = null;

  function twinkleAlpha(star: DustStar | TurnStar, now: number, isTurn = false): number {
    if (reducedMotion) {
      const min = isTurn ? TURN_TWINKLE_MIN : TWINKLE_MIN;
      const max = isTurn ? TURN_TWINKLE_MAX : TWINKLE_MAX;
      return (min + max) * 0.5;
    }
    const wave =
      0.5 +
      0.5 * Math.sin((now / star.twinklePeriod) * Math.PI * 2 + star.twinklePhase);
    const min = isTurn ? TURN_TWINKLE_MIN : TWINKLE_MIN;
    const max = isTurn ? TURN_TWINKLE_MAX : TWINKLE_MAX;
    return min + (max - min) * wave;
  }

  function birthGlow(star: TurnStar, now: number): number {
    if (reducedMotion || star.bornAt <= 0) return 1;
    const age = now - star.bornAt;
    if (age >= BIRTH_MS) return 1;
    return 0.55 + (age / BIRTH_MS) * 0.45;
  }

  function birthRadius(star: TurnStar, now: number): number {
    if (reducedMotion || star.bornAt <= 0) return star.radius;
    const age = now - star.bornAt;
    if (age >= BIRTH_MS) return star.radius;
    const t = age / BIRTH_MS;
    return star.radius * (0.4 + t * 0.6);
  }

  function recallPulse(now: number): number {
    if (reducedMotion) return 1;
    return 0.82 + Math.sin(now / 380) * 0.18;
  }

  function resize(): void {
    const nextW = host.clientWidth;
    const nextH = host.clientHeight;
    if (nextW < 2 || nextH < 2) return;

    dpr = Math.min(window.devicePixelRatio || 1, 2);
    width = nextW;
    height = nextH;
    canvas.width = Math.max(1, Math.floor(width * dpr));
    canvas.height = Math.max(1, Math.floor(height * dpr));
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function refreshLayout(): void {
    resize();
  }

  function starPixel(star: { x: number; y: number }): { x: number; y: number } {
    return { x: star.x * width, y: star.y * height };
  }

  function wrap01(value: number): number {
    return value - Math.floor(value);
  }

  function dustPixel(star: DustStar, now: number, driftMult: number): { x: number; y: number } {
    if (reducedMotion) {
      return starPixel(star);
    }
    const t = (now / DRIFT_BASE_MS) * driftMult;
    const amp = (0.022 + star.depth * 0.034) * driftMult;
    const x = wrap01(star.x + Math.sin(t * star.driftSpeed + star.driftPhaseX) * amp);
    const y = wrap01(star.y + Math.cos(t * star.driftSpeed * 0.83 + star.driftPhaseY) * amp * 0.72);
    return starPixel({ x, y });
  }

  function turnStarPixel(star: TurnStar, now: number): { x: number; y: number } {
    const base = starPixel(star);
    if (reducedMotion) return base;
    const breathe = Math.sin(now / 3600 + star.twinklePhase) * 2.2;
    const breatheY = Math.cos(now / 4400 + star.twinklePhase * 1.3) * 1.8;
    return { x: base.x + breathe, y: base.y + breatheY };
  }

  function applyCameraTransform(cam: CameraState): void {
    ctx.translate(width / 2 + cam.panX, height / 2 + cam.panY);
    ctx.scale(cam.scale, cam.scale);
    ctx.translate(-width / 2, -height / 2);
  }

  function findUserStar(sessionId: string, turnIndex: number): TurnStar | undefined {
    return turnStars.find(
      (s) => s.role === "user" && s.sessionId === sessionId && s.turnIndex === turnIndex,
    );
  }

  function findAssistantStar(sessionId: string, turnIndex: number): TurnStar | undefined {
    return turnStars.find(
      (s) => s.role === "assistant" && s.sessionId === sessionId && s.turnIndex === turnIndex,
    );
  }

  function findTurnLink(sessionId: string, turnIndex: number): TurnLink | undefined {
    return turnLinks.find((l) => l.sessionId === sessionId && l.turnIndex === turnIndex);
  }

  function hasStar(sessionId: string, turnIndex: number, role: "user" | "assistant"): boolean {
    const key = dedupeKey(sessionId, turnIndex, role);
    return turnStars.some((s) => s.dedupeKey === key);
  }

  function animateCamera(toPanX: number, toPanY: number, toScale: number, duration = CAMERA_MS): void {
    if (reducedMotion) {
      camera.panX = toPanX;
      camera.panY = toPanY;
      camera.scale = toScale;
      cameraAnim = null;
      return;
    }
    cameraAnim = {
      panX: toPanX,
      panY: toPanY,
      scale: toScale,
      fromPanX: camera.panX,
      fromPanY: camera.panY,
      fromScale: camera.scale,
      start: performance.now(),
      duration,
    };
  }

  function currentCamera(now: number): CameraState {
    if (reducedMotion) return camera;
    if (!cameraAnim) return camera;
    const t = clamp((now - cameraAnim.start) / cameraAnim.duration, 0, 1);
    const ease = t * (2 - t);
    const panX = cameraAnim.fromPanX + (cameraAnim.panX - cameraAnim.fromPanX) * ease;
    const panY = cameraAnim.fromPanY + (cameraAnim.panY - cameraAnim.fromPanY) * ease;
    const scale = cameraAnim.fromScale + (cameraAnim.scale - cameraAnim.fromScale) * ease;
    if (t >= 1) {
      camera.panX = cameraAnim.panX;
      camera.panY = cameraAnim.panY;
      camera.scale = cameraAnim.scale;
      cameraAnim = null;
    }
    return { panX, panY, scale };
  }

  function pairKey(sessionId: string, turnIndex: number): string {
    return `${sessionId}:${turnIndex}`;
  }

  function recentCompletePairs(k: number): Array<{ sessionId: string; turnIndex: number }> {
    const pairMap = new Map<string, { sessionId: string; turnIndex: number; latestAt: string }>();
    for (const star of turnStars) {
      const key = pairKey(star.sessionId, star.turnIndex);
      const existing = pairMap.get(key);
      const createdAt = star.createdAt;
      if (!existing) {
        pairMap.set(key, { sessionId: star.sessionId, turnIndex: star.turnIndex, latestAt: createdAt });
        continue;
      }
      if (createdAt > existing.latestAt) {
        existing.latestAt = createdAt;
      }
    }

    const complete: Array<{ sessionId: string; turnIndex: number; latestAt: string }> = [];
    for (const entry of pairMap.values()) {
      if (findUserStar(entry.sessionId, entry.turnIndex) && findAssistantStar(entry.sessionId, entry.turnIndex)) {
        complete.push(entry);
      }
    }
    complete.sort((a, b) => b.latestAt.localeCompare(a.latestAt) || b.turnIndex - a.turnIndex);
    return complete.slice(0, k).map(({ sessionId, turnIndex }) => ({ sessionId, turnIndex }));
  }

  function addTurnStar(
    sessionId: string,
    turnIndex: number,
    role: "user" | "assistant",
    silent = false,
    preset?: { id?: string; x?: number; y?: number; createdAt?: string },
  ): string | null {
    const key = dedupeKey(sessionId, turnIndex, role);
    if (turnStars.some((s) => s.dedupeKey === key)) return null;

    const pos =
      preset?.x !== undefined && preset?.y !== undefined
        ? { x: preset.x, y: preset.y }
        : role === "user"
          ? userStarPosition(turnIndex)
          : assistantStarPosition(turnIndex);
    const id = preset?.id ?? `star-${++starIdSeq}`;
    const star: TurnStar = {
      id,
      x: pos.x,
      y: pos.y,
      radius: role === "user" ? 4.5 + seededUnit(turnIndex, 5) * 2 : 5 + seededUnit(turnIndex, 6) * 2.2,
      twinklePhase: seededUnit(turnIndex, role === "user" ? 7 : 8) * Math.PI * 2,
      twinklePeriod: 3000 + seededUnit(turnIndex, role === "user" ? 9 : 10) * 3000,
      role,
      turnIndex,
      sessionId,
      bornAt: silent ? 0 : performance.now(),
      createdAt: preset?.createdAt ?? new Date().toISOString(),
      dedupeKey: key,
    };
    turnStars.push(star);

    if (!silent && onStarPersist) {
      onStarPersist({
        id: star.id,
        x: star.x,
        y: star.y,
        session_id: sessionId,
        turn_index: turnIndex,
        role,
        created_at: preset?.createdAt ?? new Date().toISOString(),
      });
    }

    if (role === "assistant") {
      const user = findUserStar(sessionId, turnIndex);
      if (user && !findTurnLink(sessionId, turnIndex)) {
        const link: TurnLink = {
          fromId: user.id,
          toId: id,
          sessionId,
          turnIndex,
          frozen: false,
          pulseUntil: 0,
        };
        turnLinks.push(link);
        if (!silent && onLinkPersist) {
          onLinkPersist({
            from: user.id,
            to: id,
            session_id: sessionId,
            turn_index: turnIndex,
          });
        }
      }
    }
    return id;
  }

  function birthUserStar(turnIndex: number, sessionId?: string): string | null {
    const sid = sessionId ?? "_local";
    if (!sessionId && findUserStar(sid, turnIndex)) return null;
    return addTurnStar(sid, turnIndex, "user", false);
  }

  function birthAssistantStar(turnIndex: number, sessionId?: string): string | null {
    const sid = sessionId ?? "_local";
    if (!sessionId && findAssistantStar(sid, turnIndex)) return null;
    return addTurnStar(sid, turnIndex, "assistant", false);
  }

  function seedFromHistory(sessionId: string, items: HistoryStarSeed[]): void {
    const sorted = [...items].sort((a, b) => a.turnIndex - b.turnIndex || (a.role === "user" ? -1 : 1));
    for (const item of sorted) {
      addTurnStar(sessionId, item.turnIndex, item.role, true);
    }
  }

  function restorePersisted(stars: ConstellationStar[], links: ConstellationLink[]): void {
    const sortedStars = [...stars].sort(
      (a, b) => a.created_at.localeCompare(b.created_at) || a.turn_index - b.turn_index,
    );
    for (const star of sortedStars) {
      addTurnStar(star.session_id, star.turn_index, star.role, true, {
        id: star.id,
        x: star.x,
        y: star.y,
        createdAt: star.created_at,
      });
    }
    for (const link of links) {
      if (findTurnLink(link.session_id, link.turn_index)) continue;
      const from = turnStars.find((s) => s.id === link.from);
      const to = turnStars.find((s) => s.id === link.to);
      if (!from || !to) continue;
      turnLinks.push({
        fromId: link.from,
        toId: link.to,
        sessionId: link.session_id,
        turnIndex: link.turn_index,
        frozen: false,
        pulseUntil: 0,
      });
    }
  }

  function clearTurnStars(): void {
    turnStars.length = 0;
    turnLinks.length = 0;
    highlightPairKeys = new Set();
    recallFocusActive = false;
    frozenTurnIndex = null;
    frozenSessionId = null;
    turnBusy = false;
    animateCamera(0, 0, 1);
  }

  function focusRecall(k = recallPairs): void {
    const pairs = recentCompletePairs(k);
    if (!pairs.length) return;

    recallFocusActive = true;
    highlightPairKeys = new Set(pairs.map((pair) => pairKey(pair.sessionId, pair.turnIndex)));
    for (const pair of pairs) {
      const link = findTurnLink(pair.sessionId, pair.turnIndex);
      if (link && !reducedMotion) link.pulseUntil = performance.now() + 2400;
    }

    const focusStars = turnStars.filter((s) => highlightPairKeys.has(pairKey(s.sessionId, s.turnIndex)));
    const cx = focusStars.reduce((sum, s) => sum + s.x, 0) / focusStars.length;
    const cy = focusStars.reduce((sum, s) => sum + s.y, 0) / focusStars.length;
    const px = cx * width;
    const py = cy * height;
    const targetScale = RECALL_ZOOM;
    animateCamera((width / 2 - px) * targetScale, (height / 2 - py) * targetScale, targetScale);
  }

  function clearRecallFocus(): void {
    recallFocusActive = false;
    highlightPairKeys = new Set();
    animateCamera(0, 0, 1, CAMERA_MS);
  }

  function pulseTurnLink(sessionId: string, turnIndex: number): void {
    if (reducedMotion) return;
    const link = findTurnLink(sessionId, turnIndex);
    if (!link || link.frozen) return;
    link.pulseUntil = performance.now() + 900;
  }

  function setConfirmFrozen(sessionId: string | null, turnIndex: number | null): void {
    frozenSessionId = sessionId;
    frozenTurnIndex = turnIndex;
    for (const link of turnLinks) {
      link.frozen =
        sessionId !== null &&
        turnIndex !== null &&
        link.sessionId === sessionId &&
        link.turnIndex === turnIndex;
    }
  }

  function setTurnBusy(busy: boolean): void {
    turnBusy = busy;
  }

  function driftMultiplier(): number {
    if (reducedMotion) return 0;
    if (frozenTurnIndex !== null) return FROZEN_DRIFT_MULT;
    if (recallFocusActive) return 0.35;
    if (turnBusy) return BUSY_DRIFT_MULT;
    return 1;
  }

  function drawNebula(now: number, accent: string): void {
    if (reducedMotion || width < 2 || height < 2) return;
    const t = now / 1000;
    const cx = width * (0.48 + Math.sin(t / 38) * 0.12);
    const cy = height * (0.42 + Math.cos(t / 44) * 0.1);
    const radius = Math.max(width, height) * 0.72;
    const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
    grad.addColorStop(0, accentGlow(accent, 0.14));
    grad.addColorStop(0.45, "rgba(40, 32, 24, 0.09)");
    grad.addColorStop(1, "transparent");
    ctx.fillStyle = grad;
    ctx.globalAlpha = 0.85 + Math.sin(t / 26) * 0.12;
    ctx.fillRect(0, 0, width, height);
    ctx.globalAlpha = 1;
  }

  function drawDust(now: number, driftMult: number, starColor: string): void {
    for (const star of dust) {
      const { x, y } = dustPixel(star, now, driftMult);
      const depthAlpha = 0.55 + star.depth * 0.42;
      ctx.globalAlpha = twinkleAlpha(star, now) * depthAlpha;
      ctx.fillStyle = starColor;
      ctx.beginPath();
      ctx.arc(x, y, star.radius, 0, Math.PI * 2);
      ctx.fill();
      if (star.depth > 0.55 && twinkleAlpha(star, now) > 0.82) {
        ctx.globalAlpha = twinkleAlpha(star, now) * 0.18;
        ctx.beginPath();
        ctx.arc(x, y, star.radius * 2.4, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  function drawTurnLayer(now: number, cam: CameraState, starColor: string, lineColor: string): void {
    ctx.save();
    applyCameraTransform(cam);

    ctx.lineWidth = 2;
    for (const link of turnLinks) {
      const a = turnStars.find((s) => s.id === link.fromId);
      const b = turnStars.find((s) => s.id === link.toId);
      if (!a || !b) continue;
      const pa = turnStarPixel(a, now);
      const pb = turnStarPixel(b, now);
      const midAlpha = (twinkleAlpha(a, now, true) + twinkleAlpha(b, now, true)) * 0.5;
      let base = 0.38 + midAlpha * 0.28;
      if (highlightPairKeys.has(pairKey(link.sessionId, link.turnIndex))) {
        base += reducedMotion ? 0.18 : recallPulse(now) * 0.25;
      }
      ctx.strokeStyle = lineColor;
      ctx.globalAlpha = reducedMotion ? base : linkPulseAlpha(now, link, base);
      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      ctx.lineTo(pb.x, pb.y);
      ctx.stroke();
    }

    for (const star of turnStars) {
      const { x, y } = turnStarPixel(star, now);
      const highlighted = highlightPairKeys.has(pairKey(star.sessionId, star.turnIndex));
      const glow = birthGlow(star, now) * (highlighted ? recallPulse(now) : 1);
      const isFrozen =
        frozenSessionId !== null &&
        frozenTurnIndex !== null &&
        star.sessionId === frozenSessionId &&
        star.turnIndex === frozenTurnIndex;
      const radius = birthRadius(star, now) * (highlighted ? 1.18 : 1);
      const alpha = twinkleAlpha(star, now, true) * glow * (isFrozen ? 1.15 : 1);

      ctx.globalAlpha = alpha * 0.22;
      ctx.fillStyle = starColor;
      ctx.beginPath();
      ctx.arc(x, y, radius * 3.2, 0, Math.PI * 2);
      ctx.fill();

      ctx.globalAlpha = alpha;
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fill();

      if (star.bornAt > 0 && now - star.bornAt < BIRTH_MS && !reducedMotion) {
        ctx.globalAlpha = (1 - (now - star.bornAt) / BIRTH_MS) * 0.45;
        ctx.beginPath();
        ctx.arc(x, y, radius * 2.8, 0, Math.PI * 2);
        ctx.fill();
      }
      if (highlighted) {
        ctx.globalAlpha = (reducedMotion ? 0.32 : recallPulse(now) * 0.28);
        ctx.beginPath();
        ctx.arc(x, y, radius * 3.4, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    ctx.restore();
  }

  function draw(now: number): void {
    if (width < 2 || height < 2) {
      resize();
      raf = requestAnimationFrame(draw);
      return;
    }

    const driftMult = driftMultiplier();
    const cam = currentCamera(now);
    const bg = readCssColor(host, "--daily-bg", "#0d0c0a");
    const starColor = readCssColor(host, "--daily-star", "#e6e2da");
    const lineColor = readCssColor(host, "--daily-line", "rgba(212,160,106,0.35)");
    const accent = readCssColor(host, "--daily-accent", "#d4a06a");

    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);

    drawNebula(now, accent);
    drawDust(now, driftMult, starColor);
    drawTurnLayer(now, cam, starColor, lineColor);

    ctx.globalAlpha = 1;
    raf = requestAnimationFrame(draw);
  }

  const onResize = (): void => {
    resize();
  };

  const resizeObserver = new ResizeObserver(() => {
    resize();
  });
  resizeObserver.observe(host);

  const shellHost = host.closest<HTMLElement>(".shell-host");
  const visibilityObserver =
    shellHost &&
    new MutationObserver(() => {
      if (!shellHost.hidden) {
        requestAnimationFrame(() => resize());
      }
    });
  if (shellHost && visibilityObserver) {
    visibilityObserver.observe(shellHost, { attributes: true, attributeFilter: ["hidden"] });
    if (!shellHost.hidden) {
      requestAnimationFrame(() => resize());
    }
  }

  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const onMotionChange = (): void => {
    reducedMotion = motionQuery.matches;
    if (reducedMotion) {
      cameraAnim = null;
      for (const link of turnLinks) {
        link.pulseUntil = 0;
      }
    }
  };

  resize();
  raf = requestAnimationFrame(draw);
  window.addEventListener("resize", onResize);
  motionQuery.addEventListener("change", onMotionChange);

  return {
    birthUserStar,
    birthAssistantStar,
    seedFromHistory,
    restorePersisted,
    clearTurnStars,
    hasStar,
    focusRecall,
    clearRecallFocus,
    pulseTurnLink,
    setConfirmFrozen,
    setTurnBusy,
    refreshLayout,
    isReducedMotion: () => reducedMotion,
    destroy: () => {
      cancelAnimationFrame(raf);
      resizeObserver.disconnect();
      visibilityObserver?.disconnect();
      window.removeEventListener("resize", onResize);
      motionQuery.removeEventListener("change", onMotionChange);
    },
  };
}
