const fs = require('fs');
const path = require('path');
const { app, BrowserWindow } = require('electron');
const { buildBrowserTypeTargetScript } = require('../electron/browser-input');

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  await app.whenReady();
  const win = new BrowserWindow({
    width: 480,
    height: 320,
    show: false,
    webPreferences: { contextIsolation: true, sandbox: true },
  });
  win.webContents.on('console-message', (details) => {
    if (details.level === 'warning' || details.level === 'error') {
      console.error(`[renderer] ${details.message}`);
    }
  });
  const execute = async (label, source) => {
    try {
      return await win.webContents.executeJavaScript(source);
    } catch (error) {
      throw new Error(`${label} failed: ${error && error.stack || error}`);
    }
  };
  const reactRoot = path.join(__dirname, '..', 'src', 'webui', 'static', 'app');
  const reactSource = fs.readFileSync(path.join(reactRoot, 'react.production.min.js'), 'utf8');
  const reactDomSource = fs.readFileSync(path.join(reactRoot, 'react-dom.production.min.js'), 'utf8');

  await win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(
    '<div id="root"></div><div id="rich-editor" contenteditable="true">old editor text</div>'
  )}`);
  await execute('Loading React', reactSource);
  await execute('Loading ReactDOM', reactDomSource);
  await execute('Rendering controlled inputs', `
    (() => {
      window.cyreneInputChanges = { input: 0, textarea: 0 };
      function Fixture() {
        const [inputValue, setInputValue] = React.useState('');
        const [textareaValue, setTextareaValue] = React.useState('');
        const [, forceRender] = React.useState(0);
        React.useEffect(() => {
          const timer = setInterval(() => forceRender((value) => value + 1), 10);
          return () => clearInterval(timer);
        }, []);
        return React.createElement(
          React.Fragment,
          null,
          React.createElement('input', {
            id: 'controlled-input',
            value: inputValue,
            placeholder: 'input placeholder',
            onChange: (event) => {
              window.cyreneInputChanges.input += 1;
              setInputValue(event.target.value);
            },
          }),
          React.createElement('textarea', {
            id: 'controlled-textarea',
            value: textareaValue,
            placeholder: 'textarea placeholder',
            onChange: (event) => {
              window.cyreneInputChanges.textarea += 1;
              setTextareaValue(event.target.value);
            },
          })
        );
      }
      ReactDOM.createRoot(document.querySelector('#root')).render(React.createElement(Fixture));
    })()
  `);
  await wait(50);

  const findTargetSource = `(
    function () {
      return { ok: true, box: { x: 0, y: 0, w: 180, h: 40 } };
    }
  )`;
  const runTypeOperation = (label, selector, text, operation) => execute(
    label,
    buildBrowserTypeTargetScript(findTargetSource, {
      mode: 'selector',
      value: selector,
      text,
      operation,
    }),
  );
  const setResult = {
    input: await runTypeOperation(
      'Typing into controlled input',
      '#controlled-input',
      'React input works',
      'set-native',
    ),
    textarea: await runTypeOperation(
      'Typing into controlled textarea',
      '#controlled-textarea',
      '豆包输入正常',
      'set-native',
    ),
  };
  await wait(80);

  const richEditorNative = await runTypeOperation(
    'Routing rich editor to trusted input',
    '#rich-editor',
    '富文本输入正常',
    'set-native',
  );
  if (!richEditorNative.ok || !richEditorNative.needsTrustedInput) {
    throw new Error(`Rich editor did not request trusted input: ${JSON.stringify(richEditorNative)}`);
  }
  const richEditorPrepared = await runTypeOperation(
    'Selecting rich editor contents',
    '#rich-editor',
    '富文本输入正常',
    'prepare-trusted',
  );
  if (!richEditorPrepared.ok) {
    throw new Error(`Rich editor could not be prepared: ${JSON.stringify(richEditorPrepared)}`);
  }
  win.webContents.focus();
  await win.webContents.insertText('富文本输入正常');
  const richEditorVerified = await runTypeOperation(
    'Verifying rich editor contents',
    '#rich-editor',
    '富文本输入正常',
    'verify',
  );
  if (!richEditorVerified.ok || !richEditorVerified.persisted) {
    throw new Error(`Trusted rich-editor input did not persist: ${JSON.stringify(richEditorVerified)}`);
  }

  const observed = await execute('Inspecting controlled inputs', `
    (() => {
      const input = document.querySelector('#controlled-input');
      const textarea = document.querySelector('#controlled-textarea');
      return {
        inputValue: input.value,
        inputAttribute: input.getAttribute('value'),
        textareaValue: textarea.value,
        textareaAttribute: textarea.getAttribute('value'),
        richEditorText: document.querySelector('#rich-editor').textContent,
        changes: window.cyreneInputChanges,
      };
    })()
  `);

  if (!setResult.input.ok || !setResult.textarea.ok) {
    throw new Error(`Native setter returned failure: ${JSON.stringify(setResult)}`);
  }
  if (!setResult.input.persisted || !setResult.textarea.persisted) {
    throw new Error(`Native setter did not pass persistence verification: ${JSON.stringify(setResult)}`);
  }
  if (observed.inputValue !== 'React input works' || observed.textareaValue !== '豆包输入正常') {
    throw new Error(`Controlled values did not survive React render: ${JSON.stringify(observed)}`);
  }
  if (observed.richEditorText !== '富文本输入正常') {
    throw new Error(`Trusted rich-editor input was not preserved: ${JSON.stringify(observed)}`);
  }
  if (observed.changes.input !== 1 || observed.changes.textarea !== 1) {
    throw new Error(`React onChange did not fire exactly once: ${JSON.stringify(observed.changes)}`);
  }
  // React may mirror an <input> value to its attribute. A <textarea>'s live
  // value remains property-only, which is the snapshot bug this fixture guards.
  if (observed.textareaAttribute !== null) {
    throw new Error(`Textarea unexpectedly mirrored its live value to an attribute: ${JSON.stringify(observed)}`);
  }

  console.log(JSON.stringify({
    ok: true,
    setResult,
    richEditorNative,
    richEditorVerified,
    observed,
  }));
  win.destroy();
}

main()
  .then(() => app.quit())
  .catch((error) => {
    console.error(error && error.stack || error);
    app.exit(1);
  });
