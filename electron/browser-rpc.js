// This is a closed protocol table, not unrestricted method reflection.
const commands = new Map();
for (const name of ['setBounds', 'setChatOverlay', 'setTabPicker', 'inspect', 'visibleLinkMatches', 'navigationGuard',
  'click', 'clickRef', 'clickText', 'clickAt', 'type', 'typeRef', 'waitFor', 'networkLog', 'screenshot',
  'prepareUpload', 'setInputFiles', 'reload', 'setMuted', 'scroll']) {
  commands.set(name, (manager, args) => manager[name](args || {}));
}
for (const name of ['state', 'goBack', 'goForward']) {
  commands.set(name, manager => manager[name]());
}
for (const name of ['activateTab', 'closeTab']) {
  commands.set(name, (manager, args) => manager[name](args && args.tabId));
}
for (const name of ['openLocalFile', 'navigate']) {
  commands.set(name, (manager, args, ownerRoundId) => manager[name]({ ...(args || {}), agentOwnerRoundId: ownerRoundId }));
}
commands.set('snapshot', (manager, args) => manager.pageSnapshot(args && args.tabId, args && args.maxChars));
commands.set('setObscured', (manager, args, ownerRoundId, setObscured) => setObscured(args && args.obscured));
commands.set('createTab', async (manager, args, ownerRoundId) => {
  await manager.createTab({ ...(args || {}), agentOwnerRoundId: ownerRoundId });
  return manager.state();
});

function dispatchBrowserCommand(manager, method, args, ownerRoundId, setObscured) {
  const command = commands.get(method);
  return command ? command(manager, args, ownerRoundId, setObscured)
    : { ok: false, error: `Unknown browser RPC method: ${method}` };
}
module.exports = { dispatchBrowserCommand };
