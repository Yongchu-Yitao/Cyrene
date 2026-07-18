const { app, BrowserWindow } = require('electron');

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  await app.whenReady();
  const win = new BrowserWindow({
    width: 420,
    height: 420,
    show: false,
    webPreferences: { contextIsolation: true, sandbox: true },
  });
  const html = `<!doctype html>
    <style>
      html, body { height: 100%; margin: 0; overflow: hidden; }
      #modal { position: fixed; inset: 40px; overflow-y: auto; background: white; }
      #content { height: 1600px; background: linear-gradient(#fff, #ddd); }
    </style>
    <div id="modal"><div id="content">Nested scroll fixture</div></div>`;
  await win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
  win.show();
  win.focus();
  win.webContents.focus();
  await wait(100);

  win.webContents.sendInputEvent({ type: 'mouseMove', x: 210, y: 210 });
  win.webContents.sendInputEvent({
    type: 'mouseWheel',
    x: 210,
    y: 210,
    deltaY: -240,
    hasPreciseScrollingDeltas: true,
    canScroll: true,
  });
  await wait(200);
  const down = await win.webContents.executeJavaScript(`({
    nested: document.querySelector('#modal').scrollTop,
    root: window.scrollY,
  })`);
  if (!(down.nested > 0) || down.root !== 0) {
    throw new Error(`Nested scroll failed: ${JSON.stringify(down)}`);
  }

  win.webContents.sendInputEvent({
    type: 'mouseWheel',
    x: 210,
    y: 210,
    deltaY: 120,
    hasPreciseScrollingDeltas: true,
    canScroll: true,
  });
  await wait(200);
  const up = await win.webContents.executeJavaScript(`({
    nested: document.querySelector('#modal').scrollTop,
    root: window.scrollY,
  })`);
  if (!(up.nested < down.nested) || up.root !== 0) {
    throw new Error(`Reverse nested scroll failed: ${JSON.stringify({ down, up })}`);
  }
  console.log(JSON.stringify({ ok: true, down, up }));
  win.destroy();
}

main()
  .then(() => app.quit())
  .catch((error) => {
    console.error(error && error.stack || error);
    app.exit(1);
  });
