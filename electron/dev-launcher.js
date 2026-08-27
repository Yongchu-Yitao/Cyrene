const { spawn } = require('child_process');

const electronPath = require('electron');

const child = spawn(electronPath, ['.'], {
  cwd: __dirname,
  env: {
    ...process.env,
    ELECTRON_DEV: '1',
  },
  stdio: 'inherit',
});

child.on('error', (error) => {
  console.error(`[electron:dev] Failed to launch Electron: ${error.message}`);
  process.exitCode = 1;
});

child.on('exit', (code, signal) => {
  if (signal) {
    process.exitCode = signal === 'SIGINT' ? 130 : 1;
    return;
  }
  process.exitCode = code ?? 1;
});
