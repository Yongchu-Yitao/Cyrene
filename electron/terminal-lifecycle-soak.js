const fs = require('fs');
const net = require('net');
const path = require('path');

function readConnection(userDataDir) {
  const connectionPath = path.join(userDataDir, 'terminal-daemon', 'connection.json');
  const connection = JSON.parse(fs.readFileSync(connectionPath, 'utf8'));
  if (
    !Number.isInteger(Number(connection.pid))
    || !Number.isInteger(Number(connection.port))
    || !Number.isInteger(Number(connection.version))
    || !Number.isInteger(Number(connection.lifecycleVersion))
    || !String(connection.token || '')
  ) {
    throw new Error('Terminal Daemon published an invalid connection record');
  }
  return connection;
}

function daemonRequest(connection, action, payload = {}, timeoutMs = 5000) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let response = '';
    const socket = net.createConnection({
      host: '127.0.0.1',
      port: Number(connection.port),
    });
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      if (error) reject(error);
      else resolve(value);
    };
    socket.setTimeout(timeoutMs);
    socket.setEncoding('utf8');
    socket.on('connect', () => {
      socket.write(`${JSON.stringify({
        version: Number(connection.version),
        token: String(connection.token),
        action,
        ...payload,
      })}\n`);
    });
    socket.on('data', (chunk) => {
      response += chunk;
      const newline = response.indexOf('\n');
      if (newline < 0) return;
      try {
        const parsed = JSON.parse(response.slice(0, newline));
        if (!parsed.ok) {
          finish(new Error(parsed.error || `Terminal Daemon ${action} failed`));
          return;
        }
        finish(null, parsed);
      } catch (error) {
        finish(error);
      }
    });
    socket.on('timeout', () => finish(new Error(`Terminal Daemon ${action} timed out`)));
    socket.on('error', (error) => finish(error));
    socket.on('end', () => {
      if (!settled) finish(new Error(`Terminal Daemon ${action} closed without a response`));
    });
  });
}

async function waitForDaemon(userDataDir, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const connection = readConnection(userDataDir);
      await daemonRequest(connection, 'ping');
      return connection;
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  throw lastError || new Error('Terminal Daemon did not become ready');
}

async function waitForBackendReady(getBackendPid, requestBackendJson, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    if (Number(getBackendPid() || 0) > 0) {
      try {
        await requestBackendJson('GET', '/api/status');
        return;
      } catch (error) {
        lastError = error;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw lastError || new Error('Backend did not become ready');
}

async function waitForBackendRestart(
  previousPid,
  projectId,
  getBackendPid,
  requestBackendJson,
  timeoutMs = 45000,
) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    const currentPid = Number(getBackendPid() || 0);
    if (currentPid > 0 && currentPid !== previousPid) {
      try {
        const listed = await requestBackendJson(
          'GET',
          `/api/terminals?projectId=${encodeURIComponent(projectId)}`,
        );
        return { backendPid: currentPid, listed };
      } catch (error) {
        lastError = error;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw lastError || new Error(`Backend did not restart after PID ${previousPid}`);
}

async function waitForMarker(connection, terminalId, marker, cursor, input) {
  await daemonRequest(connection, 'input', {
    terminalId,
    data: input || `echo ${marker}\r`,
    actor: 'user',
  }, 10000);
  const deadline = Date.now() + 20000;
  let position = Math.max(0, Number(cursor || 0));
  let tail = Buffer.alloc(0);
  while (Date.now() < deadline) {
    const page = await daemonRequest(connection, 'scrollback', {
      terminalId,
      cursor: position,
      maxBytes: 512 * 1024,
    }, 10000);
    const chunk = Buffer.from(String(page.data || ''), 'base64');
    if (chunk.length) {
      tail = Buffer.concat([tail, chunk]);
      if (tail.includes(Buffer.from(marker, 'utf8'))) {
        return Math.max(Number(page.endSeq || 0), Number(page.nextSeq || 0));
      }
      if (tail.length > 8192) tail = tail.subarray(tail.length - 8192);
    }
    const endSeq = Number(page.endSeq || position);
    const nextSeq = Number(page.nextSeq || endSeq);
    position = Math.max(position, endSeq);
    if (!chunk.length || endSeq >= nextSeq) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  throw new Error(`Terminal output did not contain ${marker}`);
}

async function runTerminalLifecycleSoak(options) {
  const {
    cycles,
    getBackendPid,
    requestBackendJson,
    restartBackend,
    tempDir,
    userDataDir,
  } = options;
  const workspace = path.join(tempDir, 'terminal-lifecycle-workspace');
  fs.mkdirSync(workspace, { recursive: true });
  let projectId = '';
  let terminalId = '';
  let connection = null;
  let successMessage = '';
  try {
    await waitForBackendReady(getBackendPid, requestBackendJson);
    const createdProject = await requestBackendJson('POST', '/api/projects', {
      name: 'Windows terminal lifecycle soak',
      workspacePath: workspace,
    });
    projectId = String(createdProject.project && createdProject.project.id || '');
    if (!projectId) throw new Error('Lifecycle soak project creation returned no project ID');

    const createdTerminal = await requestBackendJson('POST', '/api/terminals', {
      projectId,
      title: 'Lifecycle soak',
      cwd: '',
      cols: 100,
      rows: 30,
    });
    const terminal = createdTerminal.terminal || {};
    terminalId = String(terminal.id || '');
    const terminalPid = Number(terminal.pid || 0);
    if (!terminalId || terminalPid <= 0 || terminal.status !== 'running') {
      throw new Error(`Lifecycle soak terminal did not start: ${JSON.stringify(terminal)}`);
    }

    connection = await waitForDaemon(userDataDir);
    const daemonPid = Number(connection.pid);
    const daemonPort = Number(connection.port);
    const daemonToken = String(connection.token);
    const lifecycleVersion = Number(connection.lifecycleVersion);
    let cursor = await waitForMarker(
      connection,
      terminalId,
      'CYRENE_TERMINAL_SOAK_READY',
      Number(terminal.nextSeq || 0),
    );

    const burstMarker = 'CYRENE_TERMINAL_SOAK_BURST_COMPLETE';
    const burst = Array.from(
      { length: 1024 },
      (_value, index) => `echo CYRENE_TERMINAL_SOAK_BURST_${String(index).padStart(4, '0')}\r`,
    ).join('') + `echo ${burstMarker}\r`;
    cursor = await waitForMarker(connection, terminalId, burstMarker, cursor, burst);

    for (let cycle = 1; cycle <= cycles; cycle += 1) {
      const previousBackendPid = Number(getBackendPid() || 0);
      if (previousBackendPid <= 0) throw new Error(`Backend unavailable before cycle ${cycle}`);
      console.log(`TERMINAL_LIFECYCLE_SOAK=running cycle=${cycle}/${cycles} backendPid=${previousBackendPid}`);
      restartBackend();
      const restarted = await waitForBackendRestart(
        previousBackendPid,
        projectId,
        getBackendPid,
        requestBackendJson,
      );
      const currentConnection = await waitForDaemon(userDataDir);
      if (
        Number(currentConnection.pid) !== daemonPid
        || Number(currentConnection.port) !== daemonPort
        || String(currentConnection.token) !== daemonToken
        || Number(currentConnection.lifecycleVersion) !== lifecycleVersion
      ) {
        throw new Error(`Terminal Daemon was replaced during lifecycle cycle ${cycle}`);
      }
      connection = currentConnection;
      const listedTerminal = (restarted.listed.terminals || []).find(
        (candidate) => String(candidate.id || '') === terminalId,
      );
      if (
        !listedTerminal
        || listedTerminal.status !== 'running'
        || Number(listedTerminal.pid || 0) !== terminalPid
      ) {
        throw new Error(`Terminal process did not survive lifecycle cycle ${cycle}`);
      }
      const marker = `CYRENE_TERMINAL_SOAK_CYCLE_${String(cycle).padStart(2, '0')}`;
      cursor = await waitForMarker(connection, terminalId, marker, cursor);
      console.log(`TERMINAL_LIFECYCLE_SOAK=progress cycle=${cycle}/${cycles} backendPid=${restarted.backendPid}`);
    }
    successMessage = `CYRENE_WINDOWS_TERMINAL_LIFECYCLE_SOAK=ok cycles=${cycles} daemonPid=${daemonPid} terminalPid=${terminalPid}`;
  } finally {
    let cleanupConnection = connection;
    try {
      cleanupConnection = readConnection(userDataDir);
    } catch (_) {}
    if (terminalId && cleanupConnection) {
      try {
        await daemonRequest(cleanupConnection, 'delete', { terminalId }, 10000);
      } catch (_) {}
    }
    if (projectId) {
      try {
        await requestBackendJson('DELETE', `/api/projects/${encodeURIComponent(projectId)}`);
      } catch (_) {}
    }
    if (cleanupConnection) {
      try {
        await daemonRequest(cleanupConnection, 'shutdown', {}, 10000);
      } catch (_) {}
    }
  }
  return successMessage;
}

module.exports = { runTerminalLifecycleSoak };
