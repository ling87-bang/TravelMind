const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const { configureStore } = require('@reduxjs/toolkit');

// Run the actual TypeScript reducer/thunk without a browser or a second test framework.
const root = path.resolve(__dirname, '..');
const modules = new Map();
function loadTs(filename) {
  if (modules.has(filename)) return modules.get(filename).exports;
  const mod = { exports: {} };
  modules.set(filename, mod);
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const localRequire = (id) => {
    if (id.startsWith('@/') || id.startsWith('.')) {
      const base = id.startsWith('@/') ? path.join(root, id.slice(2))
        : path.resolve(path.dirname(filename), id);
      const target = [base, `${base}.ts`, path.join(base, 'index.ts')].find(p => fs.existsSync(p) && fs.statSync(p).isFile());
      if (target) return loadTs(target);
    }
    return require(id);
  };
  new Function('require', 'module', 'exports', code)(localRequire, mod, mod.exports);
  return mod.exports;
}

global.requestAnimationFrame = (callback) => { callback(); return 1; };
const chat = loadTs(path.join(root, 'store/slices/chatSlice.ts'));
const result = loadTs(path.join(root, 'lib/stream-result.ts'));
const createStore = () => configureStore({ reducer: {
  chat: chat.default, auth: () => ({ token: 'test' }), ui: () => ({}),
} });
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const frame = (role, content, code) => new TextEncoder().encode(
  `data: ${JSON.stringify({ role, content, code })}\n\n`);

test('a structured error is appended to the partial answer', () => {
  assert.equal(result.streamResultText('已收到需求。', result.streamErrorMessage({
    type: 'model_rate_limit', message: '模型限流，请稍后重试。',
  })), '已收到需求。\n\n模型限流，请稍后重试。');
});

test('switching away and back retains the active message and subsequent chunks', () => {
  const store = createStore();
  store.dispatch(chat.setCurrentConversationId('A'));
  store.dispatch(chat.startStreaming('reply-A'));
  store.dispatch(chat.updateStreamContent({ id: 'reply-A', content: '第一段' }));
  store.dispatch(chat.setCurrentConversationId('B'));
  store.dispatch(chat.updateStreamContent({ id: 'reply-A', content: '第一段第二段' }));
  assert.deepEqual(store.getState().chat.messages, []);
  store.dispatch(chat.setCurrentConversationId('A'));
  assert.equal(store.getState().chat.messages[0].content, '第一段第二段');
  store.dispatch(chat.commitStreamMessage({ id: 'reply-A', content: '完成' }));
  assert.equal(store.getState().chat.messages[0].content, '完成');
  assert.equal(store.getState().chat.isStreaming, false);
});

test('a completed background reply does not overwrite another conversation', () => {
  const store = createStore();
  store.dispatch(chat.setCurrentConversationId('A'));
  store.dispatch(chat.startStreaming('reply-A'));
  store.dispatch(chat.setCurrentConversationId('B'));
  store.dispatch(chat.appendMessage({ id: 'B-history', role: 'user', content: 'B', kind: 'normal', createdAt: 0 }));
  store.dispatch(chat.commitStreamMessage({ id: 'reply-A', content: 'A completed' }));
  assert.equal(store.getState().chat.messages[0].content, 'B');
  assert.equal(store.getState().chat.messages.length, 1);
});

test('real SSE consumer shows chunks before done and preserves them on model failure', async () => {
  let controller;
  const response = new Response(new ReadableStream({ start(c) { controller = c; } }), {
    headers: { 'Content-Type': 'text/event-stream' },
  });
  global.fetch = async (url) => String(url).includes('/messages') ? response
    : new Response('[]', { headers: { 'Content-Type': 'application/json' } });
  const store = createStore();
  store.dispatch(chat.setCurrentConversationId('A'));
  const task = store.dispatch(chat.sendMessageThunk('正常提问'));
  controller.enqueue(frame('assistant', '第一段'));
  await pause(70);
  assert.equal(store.getState().chat.messages[1].content, '第一段');
  assert.equal(store.getState().chat.isStreaming, true);
  store.dispatch(chat.setCurrentConversationId('B'));
  controller.enqueue(frame('assistant', '第二段'));
  await pause(70);
  store.dispatch(chat.setCurrentConversationId('A'));
  assert.equal(store.getState().chat.messages[1].content, '第一段第二段');
  controller.enqueue(frame('error', { type: 'model_rate_limit', message: '模型限流，请稍后重试。' }));
  controller.enqueue(frame('done', 'finish', 500));
  controller.close();
  await task;
  const state = store.getState().chat;
  assert.equal(state.messages[1].content, '第一段第二段\n\n模型限流，请稍后重试。');
  assert.equal(state.isLoading, false);
  assert.equal(state.isStreaming, false);
});

test('an interrupted connection preserves text and releases the input lock', async () => {
  let controller;
  global.fetch = async () => new Response(new ReadableStream({ start(c) { controller = c; } }), {
    headers: { 'Content-Type': 'text/event-stream' },
  });
  const store = createStore();
  store.dispatch(chat.setCurrentConversationId('A'));
  const task = store.dispatch(chat.sendMessageThunk('你好'));
  await pause(0);
  controller.enqueue(frame('assistant', '保留这段'));
  await pause(70);
  controller.error(new Error('connection reset'));
  await task;
  assert.match(store.getState().chat.messages[1].content, /^保留这段\n\n连接中断/);
  assert.equal(store.getState().chat.isLoading, false);
});

test('EOF without done is reported as incomplete instead of silent success', async () => {
  global.fetch = async (url) => String(url).includes('/messages')
    ? new Response(new ReadableStream({ start(c) {
        c.enqueue(frame('assistant', '部分正文'));
        c.close();
      } }), { headers: { 'Content-Type': 'text/event-stream' } })
    : new Response('[]', { headers: { 'Content-Type': 'application/json' } });
  const store = createStore();
  store.dispatch(chat.setCurrentConversationId('A'));
  await store.dispatch(chat.sendMessageThunk('测试提前结束'));
  assert.match(store.getState().chat.messages[1].content, /^部分正文\n\n连接提前结束/);
  assert.equal(store.getState().chat.isLoading, false);
});

test('accepted cards remain locked when subsequent report generation fails', async () => {
  const store = createStore();
  store.dispatch(chat.setCurrentConversationId('A'));
  store.dispatch(chat.appendMessage({ id: 'card-message', role: 'assistant', kind: 'normal',
    content: '请选择', createdAt: 0,
    skillUi: { card_id: 'card-one', skill_name: 'train_multi_segment', data: {} } }));
  global.fetch = async () => new Response(new ReadableStream({ start(controller) {
    controller.enqueue(frame('skill_submitted', { card_id: 'card-one', skill_name: 'train_multi_segment',
      submitted: { selections: { s1: 'option-one' } } }));
    controller.enqueue(frame('assistant', '已确认车次'));
    controller.enqueue(frame('error', { message: '生成失败，可继续' }));
    controller.enqueue(frame('done', 'finish', 500));
    controller.close();
  } }), { headers: { 'Content-Type': 'text/event-stream' } });
  const response = await store.dispatch(chat.submitTrainSkillThunk({ skillName: 'train_multi_segment',
    cardId: 'card-one', payload: { segments: [] } })).unwrap();
  assert.equal(response.ok, true);
  assert.equal(response.msg, '生成失败，可继续');
  assert.equal(store.getState().chat.messages[0].skillUi.submitted.selections.s1, 'option-one');
});

test('refresh recovery replaces its partial snapshot with durable history on completion', async () => {
  const store = createStore();
  store.dispatch(chat.setCurrentConversationId('A'));
  let status = 'running';
  global.fetch = async (url) => new Response(JSON.stringify(String(url).endsWith('/run')
    ? { run_id: 'run-one', status, partial_text: '正在生成', result: {}, stage: 'itinerary' }
    : { messages: [{ id: 'saved-message', run_id: 'run-one', role: 'assistant', content: '完整行程', extra_info: {} }] }),
    { headers: { 'Content-Type': 'application/json' } });
  await store.dispatch(chat.recoverRunThunk('A'));
  await store.dispatch(chat.recoverRunThunk('A'));
  assert.equal(store.getState().chat.messages.length, 1);
  assert.equal(store.getState().chat.isLoading, true);
  status = 'succeeded';
  await store.dispatch(chat.recoverRunThunk('A'));
  assert.equal(store.getState().chat.messages.length, 1);
  assert.equal(store.getState().chat.messages[0].id, 'saved-message');
  assert.equal(store.getState().chat.isLoading, false);
});

test('retrying a dropped message preserves its request identity until successful completion', async () => {
  const api = loadTs(path.join(root, 'lib/api.ts'));
  const storage = new Map();
  global.localStorage = { getItem: key => storage.get(key) || null,
    setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) };
  const ids = [];
  global.fetch = async (_, options) => {
    ids.push(JSON.parse(options.body).client_request_id);
    return new Response('');
  };
  await api.streamChatApi('A', 'same message');
  await api.streamChatApi('A', 'same message');
  assert.equal(ids[0], ids[1]);
  api.clearPendingRequest('A');
  await api.streamChatApi('A', 'same message');
  assert.notEqual(ids[0], ids[2]);
  delete global.localStorage;
});

test('archiving the selected conversation persists status and clears the current selection', async () => {
  const store = createStore();
  store.dispatch(chat.setConversations([{ id: 'A', title: '旅行', status: 'active' }]));
  store.dispatch(chat.setCurrentConversationId('A'));
  global.fetch = async (url, options) => {
    assert.match(String(url), /\/conversations\/A$/);
    assert.equal(options.method, 'PATCH');
    assert.deepEqual(JSON.parse(options.body), { status: 'archived' });
    return new Response(JSON.stringify({ id: 'A', title: '旅行', status: 'archived' }), {
      headers: { 'Content-Type': 'application/json' },
    });
  };
  await store.dispatch(chat.updateConversationStatusThunk({ id: 'A', status: 'archived' })).unwrap();
  assert.equal(store.getState().chat.currentConversationId, null);
  assert.equal(store.getState().chat.conversations[0].status, 'archived');
});

test('restoring an archived conversation preserves another selected conversation', async () => {
  const store = createStore();
  store.dispatch(chat.setConversations([{ id: 'A', title: '旅行', status: 'archived' }]));
  store.dispatch(chat.setCurrentConversationId('B'));
  global.fetch = async () => new Response(JSON.stringify({ id: 'A', title: '旅行', status: 'active' }), {
    headers: { 'Content-Type': 'application/json' },
  });
  await store.dispatch(chat.updateConversationStatusThunk({ id: 'A', status: 'active' })).unwrap();
  assert.equal(store.getState().chat.currentConversationId, 'B');
  assert.equal(store.getState().chat.conversations[0].status, 'active');
});

test('failed archive keeps the conversation and current selection intact', async () => {
  const store = createStore();
  store.dispatch(chat.setConversations([{ id: 'A', title: '旅行', status: 'active' }]));
  store.dispatch(chat.setCurrentConversationId('A'));
  global.fetch = async () => new Response(JSON.stringify({ msg: '服务暂不可用' }), {
    status: 503, headers: { 'Content-Type': 'application/json' },
  });
  const result = await store.dispatch(chat.updateConversationStatusThunk({ id: 'A', status: 'archived' }));
  assert.equal(result.meta.requestStatus, 'rejected');
  assert.equal(store.getState().chat.currentConversationId, 'A');
  assert.equal(store.getState().chat.conversations[0].status, 'active');
});
