const { spawn } = require('child_process');

function doctorArgs(backendArgs) {
  return (backendArgs[0] === 'run' ? ['run', 'cyrene'] : []).concat(['doctor', '--offline', '--json']);
}

function runOfflineDoctor(command, args, options = {}, spawnChild = spawn) {
  return new Promise(resolve => {
    let finished = false;
    let output = '';
    let timer;
    let child;
    const finish = result => { if (!finished) { finished = true; clearTimeout(timer); resolve(result); } };
    try {
      child = spawnChild(command, doctorArgs(args), { ...options, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
      timer = setTimeout(() => { child.kill(); finish({ status: 'unavailable', reason: 'diagnostic_timeout' }); }, 15000);
      child.stdout.on('data', chunk => {
        output += String(chunk);
        if (output.length > 1000000) { child.kill(); finish({ status: 'unavailable', reason: 'diagnostic_output_limit' }); }
      });
      child.stderr.on('data', () => {});
      child.on('error', () => finish({ status: 'unavailable', reason: 'python_unavailable' }));
      child.on('exit', code => {
        try {
          const report = JSON.parse(output);
          if (!Array.isArray(report.findings)) throw new Error('Invalid report');
          finish({ status: 'completed', report });
        } catch (_) { finish({ status: 'unavailable', reason: 'diagnostic_failed', exitCode: code }); }
      });
    } catch (_) { finish({ status: 'unavailable', reason: 'python_unavailable' }); }
  });
}

module.exports = { doctorArgs, runOfflineDoctor };
