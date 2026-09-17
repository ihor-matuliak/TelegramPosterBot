const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function setup() {
    const nodes = new Map();
    const context = {
        console: { error() {} },
        document: {
            addEventListener() {},
            getElementById(id) {
                if (!nodes.has(id)) nodes.set(id, { style: {}, innerHTML: '' });
                return nodes.get(id);
            },
        },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync('web/static/app.js', 'utf8'), context);
    context.chats = [{ id: 'one', chat_peer: '@test', is_active: true,
        interval_minutes: 60, next_post_at: new Date(Date.now() - 60000).toISOString() }];
    return { context, nodes };
}

test('expired timers show global pause reason and escape it', () => {
    const { context, nodes } = setup();
    vm.runInContext("posterStatus = {state: 'paused', reason: 'Аварійна пауза <test>'}; renderChatsTable(chats)", context);
    const html = nodes.get('chatsTableBody').innerHTML;
    assert.match(html, /Аварійна пауза &lt;test&gt;/);
    assert.doesNotMatch(html, /Готовий до відправки|У черзі на відправку/);
});

test('running expired chat is queued; future timer is never rounded down to ready', () => {
    const { context, nodes } = setup();
    vm.runInContext("posterStatus = {state: 'running'}; renderChatsTable(chats)", context);
    assert.match(nodes.get('chatsTableBody').innerHTML, /У черзі на відправку/);
    context.chats[0].next_post_at = new Date(Date.now() + 20000).toISOString();
    context.renderChatsTable(context.chats);
    assert.match(nodes.get('chatsTableBody').innerHTML, /Через 1 хв/);
});

test('status arriving after chats repaints existing rows with correct state', async () => {
    const { context, nodes } = setup();
    vm.runInContext('cachedChats = chats; renderChatsTable(chats)', context);
    context.fetch = async () => ({ ok: true, json: async () => ({
        is_running: false, poster: { state: 'paused', reason: 'Аварійна пауза' },
    }) });
    await context.fetchStatus();
    assert.match(nodes.get('chatsTableBody').innerHTML, /Аварійна пауза/);
    assert.equal(nodes.get('masterToggleText').textContent, 'Аварійна пауза');
});

test('failed status refresh replaces stale running indication', async () => {
    const { context, nodes } = setup();
    vm.runInContext("posterStatus = {state: 'running'}; cachedChats = chats", context);
    context.fetch = async () => ({ ok: false });
    await context.fetchStatus();
    assert.match(nodes.get('chatsTableBody').innerHTML, /Немає зв’язку з сервером/);
});

test('failed toggle surfaces error without claiming success', async () => {
    const { context } = setup();
    let message;
    context.alert = text => { message = text; };
    context.fetch = async () => ({ ok: false, json: async () => ({ detail: 'DB offline' }) });
    await context.toggleMasterPoster();
    assert.equal(message, 'DB offline');
    assert.equal(vm.runInContext('isMasterRunning', context), false);
});
