import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';

test('Retain quotes across reconnect and distinguish recovery from an empty search', async () => {
  const html = await readFile(new URL('../wwwroot/index.html', import.meta.url), 'utf8');
  const elements = new Map();
  function element() {
    return { value: '', textContent: '', style: {}, children: [],
      appendChild(child) { this.children.push(child); this.firstChild ??= child; },
      replaceChildren(...children) { this.children = children; } };
  }
  function get(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); }
  let events;
  let render;
  vm.runInNewContext(html.split('<script>')[1].split('</script>')[0], {
    document: { getElementById: get, createElement: element },
    EventSource: class { constructor() { events = this; } },
    performance, Date, Intl, Map,
    setInterval(callback) { render = callback; },
  });
  const row = { symbol: 'BTCUSD', connection: 1, bid: '100.01', ask: '100.02', last: '100.01', readiness: 'ready', observedAt: new Date().toISOString(), receivedAt: new Date().toISOString(), maximumAge: 60000, sourceAge: 0, updates: 1, raw: '{}' };
  const state = { state: 'connected', connections: 1, rows: [row], symbols: 1, ready: 1, withData: 1, messages: 1, renewals: 0, error: null };
  const emit = data => { events.onmessage({ data: JSON.stringify(data) }); render(); };
  get('search').value = 'BTC';
  get('filter').value = 'ready';
  emit(state);
  assert.equal(get('quotes').children.length, 1);
  emit({ ...state, rows: [], renewing: true });
  assert.equal(get('quotes').children.length, 1);
  emit({ ...state, state: 'disconnected', rows: [] });
  assert.match(get('empty').textContent, /连接恢复中，已保留最后报价/);
  get('filter').value = 'all'; render();
  assert.equal(get('quotes').children.length, 1);
  assert.equal(get('quotes').children[0].children[4].firstChild.textContent, '已过期');
  emit({ ...state, connections: 2, rows: [] });
  assert.equal(get('quotes').children.length, 1);
  assert.equal(get('quotes').children[0].children[4].firstChild.textContent, '已过期');
  get('filter').value = 'ready'; render();
  assert.match(get('empty').textContent, /暂无实时行情/);
  emit({ ...state, connections: 2, rows: [{ ...row, connection: 2 }] });
  assert.equal(get('quotes').children.length, 1);
  get('search').value = 'NO-SUCH-SYMBOL'; render();
  assert.equal(get('empty').textContent, '没有匹配的品种');
});
