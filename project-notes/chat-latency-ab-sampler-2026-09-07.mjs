export default async function measureBatch(page, specs) {
  const samples = [];
  for (const spec of specs) {
    await page.goto(spec.url);
    const field = page.getByRole('combobox', {name:'给 Cyrene 发送消息...'});
    await field.waitFor({state:'visible'});
    await page.getByText(spec.previous, {exact:true}).last().waitFor({state:'visible'});
    await field.fill('This is an isolated timing check. Do not use any tools. Reply with exactly ' + spec.token);
    const button = page.getByRole('button', {name:'发送', exact:true});
    await button.evaluate((button, spec) => {
      const result = window.__latencySample = {...spec, visibility:document.visibilityState};
      const initial = [...document.querySelectorAll('.wbc-msg.assistant .wbc-msg-body')];
      const initialTexts = new Set(initial.map(node => node.textContent.trim()));
      let started = null;
      const observer = new MutationObserver(() => {
        if (started === null) return;
        const bodies = [...document.querySelectorAll('.wbc-msg.assistant .wbc-msg-body')];
        const body = bodies.at(-1);
        if (!body) return;
        const text = body.textContent.trim();
        if (!text || initialTexts.has(text) || !spec.token.startsWith(text)) return;
        if (result.first_dom_ms === undefined) {
          result.first_dom_ms = performance.now() - started;
          requestAnimationFrame(() => requestAnimationFrame(() => {
            result.first_dom_two_raf_ms = performance.now() - started;
          }));
        }
        if (body.textContent.trim() === spec.token && result.complete_dom_ms === undefined) {
          result.complete_dom_ms = performance.now() - started;
          requestAnimationFrame(() => requestAnimationFrame(() => {
            result.complete_two_raf_ms = performance.now() - started;
            result.complete = true;
            observer.disconnect();
          }));
        }
      });
      observer.observe(document.body, {subtree:true, childList:true, characterData:true});
      button.addEventListener('click', () => {
        started = performance.now();
        result.click_performance_ms = started;
        result.click_epoch_ms = performance.timeOrigin + started;
        result.chat_id = document.querySelector('.wbc-page')?.getAttribute('data-active-chat-id');
      }, {capture:true, once:true});
    }, spec);
    await button.click();
    await page.waitForFunction(() => window.__latencySample?.complete === true, null, {timeout:45000});
    await page.waitForFunction(() => {
      const id = document.querySelector('.wbc-page')?.getAttribute('data-active-chat-id');
      return id && !window.CyreneUI.require('chat').Runtimes.isRunning(id);
    }, null, {timeout:45000});
    await page.waitForFunction(() => performance.getEntriesByType('resource').some(e => e.name.endsWith('/messages') && e.startTime >= window.__latencySample.click_performance_ms));
    samples.push(await page.evaluate(() => {
      const r = window.__latencySample;
      const resource = performance.getEntriesByType('resource').find(e => e.name.endsWith('/messages') && e.startTime >= r.click_performance_ms);
      if (resource) {
        r.fetch_start_ms = resource.startTime - r.click_performance_ms;
        r.fetch_headers_ms = resource.responseStart - r.click_performance_ms;
        r.fetch_end_ms = resource.responseEnd - r.click_performance_ms;
        r.http_status = resource.responseStatus;
      }
      if (!resource || r.http_status !== 200 || r.visibility !== 'visible'
          || !(r.fetch_headers_ms <= r.first_dom_ms && r.first_dom_ms <= r.complete_dom_ms
            && r.complete_dom_ms <= r.complete_two_raf_ms)) {
        throw new Error('Incomplete or inconsistent browser timing sample');
      }
      return r;
    }));
  }
  await page.evaluate(rows => window.__batchResults = rows, samples);
}
