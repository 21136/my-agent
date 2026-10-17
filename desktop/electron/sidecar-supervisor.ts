export type SidecarStartResult =
  | { ok: true; host: string; port: number }
  | { ok: false; message?: string };

export type SidecarLifecycleState = "idle" | "stopping" | "starting" | "ready" | "candidate_starting" | "candidate_health" | "promoting" | "failed" | "rolled_back";

export type SidecarController = {
  stop: () => void | Promise<void>;
  start: () => Promise<SidecarStartResult>;
};

export type CandidateSwitchOptions = {
  startCandidate: () => Promise<SidecarStartResult>;
  healthCandidate: (candidate: Extract<SidecarStartResult, { ok: true }>) => Promise<boolean>;
  stopCandidate: () => void | Promise<void>;
  stopCurrent: () => void | Promise<void>;
  promoteCandidate: (candidate: Extract<SidecarStartResult, { ok: true }>) => Promise<SidecarStartResult>;
  recoverCurrent: () => Promise<SidecarStartResult>;
};

export type SidecarOperationResult = {
  ok: boolean;
  state: SidecarLifecycleState;
  recovered: boolean;
  error?: string;
};

export class SidecarSupervisor {
  private _state: SidecarLifecycleState = "idle";
  private _busy = false;

  get state(): SidecarLifecycleState {
    return this._state;
  }

  async restart(controller: SidecarController): Promise<SidecarOperationResult> {
    if (this._busy) {
      return { ok: false, state: this._state, recovered: false, error: "sidecar operation already in progress" };
    }
    this._busy = true;
    try {
      this._state = "stopping";
      await controller.stop();
      this._state = "starting";
      const started = await controller.start();
      if (!started.ok) {
        this._state = "failed";
        return { ok: false, state: this._state, recovered: false, error: started.message ?? "sidecar start failed" };
      }
      this._state = "ready";
      return { ok: true, state: this._state, recovered: false };
    } catch (error) {
      this._state = "failed";
      return { ok: false, state: this._state, recovered: false, error: error instanceof Error ? error.message : String(error) };
    } finally {
      this._busy = false;
    }
  }

  async switchCandidate(options: CandidateSwitchOptions): Promise<SidecarOperationResult> {
    if (this._busy) {
      return { ok: false, state: this._state, recovered: false, error: "sidecar operation already in progress" };
    }
    this._busy = true;
    let candidate: Extract<SidecarStartResult, { ok: true }> | null = null;
    try {
      this._state = "candidate_starting";
      const started = await options.startCandidate();
      if (!started.ok) {
        this._state = "failed";
        return { ok: false, state: this._state, recovered: false, error: started.message ?? "candidate start failed" };
      }
      candidate = started;
      this._state = "candidate_health";
      if (!(await options.healthCandidate(candidate))) {
        await options.stopCandidate();
        const recovered = await options.recoverCurrent();
        const recoveredOk = recovered.ok;
        this._state = recoveredOk ? "rolled_back" : "failed";
        return {
          ok: false,
          state: this._state,
          recovered: recoveredOk,
          error: "candidate health check failed",
        };
      }
      this._state = "stopping";
      await options.stopCurrent();
      this._state = "promoting";
      const promoted = await options.promoteCandidate(candidate);
      if (promoted.ok) {
        this._state = "ready";
        return { ok: true, state: this._state, recovered: false };
      }
      await options.stopCandidate();
      const recovered = await options.recoverCurrent();
      const recoveredOk = recovered.ok;
      this._state = recoveredOk ? "rolled_back" : "failed";
      return {
        ok: false,
        state: this._state,
        recovered: recoveredOk,
        error: promoted.message ?? "candidate promotion failed",
      };
    } catch (error) {
      if (candidate) {
        try {
          await options.stopCandidate();
        } catch {
          // Preserve original failure; recovery is attempted below.
        }
      }
      let recovered = false;
      try {
        recovered = (await options.recoverCurrent()).ok;
      } catch {
        recovered = false;
      }
      this._state = recovered ? "rolled_back" : "failed";
      return {
        ok: false,
        state: this._state,
        recovered,
        error: error instanceof Error ? error.message : String(error),
      };
    } finally {
      this._busy = false;
    }
  }
}
