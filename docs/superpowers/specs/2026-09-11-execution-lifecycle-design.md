# Execution Lifecycle Design

> Version 0.1 · 2026-09-11 · First implementation slice

## Goal

Make every active Agent run have one authoritative lifecycle, one idempotent stop request, and one terminal outcome that can be consumed by Desktop and Terminal. The first slice covers the in-process WebSocket bridge and runaway continuation gap; it does not replace the Agent tool executor or redesign the model loop.

## Problem

The current runtime represents work in several independent flags: `_turn_busy`, `_runaway_chain_busy`, `cancel_event`, watchdog state, Agent `finish_reason`, and UI-local activity state. Each flag is useful, but their transitions are not one contract. A stop can therefore arrive between turns, after a queue notice, or while a worker is unwinding without one shared run identity or authoritative state.

## State Model

Each bridge owns at most one active execution record:

```text
idle -> running -> settled
idle -> queued -> running -> settled
running -> stopping -> settled
queued -> stopping -> settled
running -> failed
running -> paused
```

`settled`, `failed`, and `paused` are terminal snapshots for that run. `finish_reason` remains the detailed outcome (`completed`, `cancelled`, `timeout`, `error`, or a domain-specific reason). A stop request is represented by `state=stopping` and `cancel_requested=true`; it is never inferred from a UI timeout.

Every snapshot contains `run_id`, `mode`, a monotonic `sequence`, `cancel_requested`, and the final reason when available. `run_id` is stable across a queued runaway continuation becoming an executing turn.

## Ownership and Flow

- `ExecutionLifecycle` is a pure, lock-protected state machine. It does not call LLMs, processes, WebSockets, or UI code.
- `WsBridge` owns the lifecycle instance and translates state transitions into `execution.state` events.
- `_run_line` starts or activates a run, invokes the existing Agent, and finalizes it exactly once in `finally`.
- Runaway continuation paths create a `queued` record before waiting for cooldown/lock. A stop changes that record to `stopping`; the queue cannot activate after that transition.
- Existing `cancel_event`, Agent cancellation, confirmation unblocking, and watchdog behavior remain in place. The lifecycle is the authoritative coordination record, not a second cancellation mechanism.
- Desktop and Terminal treat `execution.state` as a shared observable fact. Existing `turn.start`/`turn.end` remain compatible and receive `run_id` when emitted through the bridge.

## Stop Contract

1. If no active record exists, return the existing no-op notice.
2. The first stop request transitions `queued` or `running` to `stopping`, sets the cancellation event, unblocks confirmation/input, and invokes the Agent cancellation callback once.
3. Repeated stop requests are idempotent: they do not invoke the callback or emit duplicate stop transitions.
4. The run must end in a final lifecycle snapshot even when a continuation is cancelled before a new model call begins.
5. The UI may recover its local watchdog, but it must not manufacture a successful completion or hide a still-active run.

## Compatibility

- Existing `turn.start`, `turn.notice`, `turn.end`, and `runaway_cancel_available` events remain available.
- Existing tests that construct `WsBridge` directly continue to work without injecting a lifecycle object.
- v1 and v2 runaway decision logic remains unchanged in this slice; only the execution ownership and cancellation visibility are unified.
- No persistent database or new third-party dependency is introduced.

## Acceptance Matrix

| Scenario | Required evidence |
|---|---|
| Ordinary run completes | `running -> settled`, one `turn.end`, matching `run_id` |
| Ordinary run is cancelled | first stop emits `stopping`, Agent callback runs once, final reason is `cancelled` |
| Duplicate stop | second request is accepted as idempotent/no-op and does not call Agent twice |
| Runaway continuation waits | `queued` is visible and stoppable before the next turn starts |
| Queued continuation is cancelled | it cannot activate, and it reaches a terminal lifecycle snapshot |
| Turn raises | `failed`/`error` state is emitted and cleanup still runs |
| Desktop/Terminal consumer | both keep a queued/stopping execution visible and clear it only on a final state |
| Existing terminal regressions | all current terminal, cancellation, runaway, and desktop build checks remain green |
