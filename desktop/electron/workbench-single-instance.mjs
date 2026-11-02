export function activateWorkbenchWindow(window, sendSessionControl) {
  if (!window || window.isDestroyed()) return false;
  const needsResume = !window.isVisible();
  if (window.isMinimized()) window.restore();
  if (needsResume) sendSessionControl(window, "resume");
  window.show();
  window.focus();
  return true;
}

export function installWorkbenchSingleInstance({ app, profile, processType, getWindow, activateWindow }) {
  const untracked = { ownsInstance: true, windowReady() {}, windowUnavailable() {} };
  if (profile.kind !== "normal" || (processType && processType !== "browser")) return untracked;

  const ownsInstance = app.requestSingleInstanceLock({
    profile: profile.kind,
    userDataPath: profile.userDataPath,
  });
  if (!ownsInstance) return { ...untracked, ownsInstance: false };

  let activationPending = false;
  let windowReady = false;
  const activateWhenReady = () => {
    const window = windowReady ? getWindow() : null;
    if (!window || window.isDestroyed()) {
      windowReady = false;
      activationPending = true;
      return;
    }
    activationPending = false;
    activateWindow(window);
  };

  app.on("second-instance", activateWhenReady);
  return {
    ownsInstance: true,
    windowReady() {
      windowReady = true;
      if (activationPending) activateWhenReady();
    },
    windowUnavailable() { windowReady = false; },
  };
}
