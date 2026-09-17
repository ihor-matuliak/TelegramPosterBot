const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function setup(fetch) {
    const nodes = new Map();
    const context = {
        fetch, AbortSignal, console, setTimeout: () => 1, clearTimeout() {},
        document: {
            addEventListener() {},
            getElementById(id) {
                if (!nodes.has(id)) nodes.set(id, { value: '', classList: { toggle() {} } });
                return nodes.get(id);
            },
        },
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync('web/static/discovery.js', 'utf8'), context);
    return { context, nodes };
}

test('start uses search_id and sends minimum participants', async () => {
    const requests = [];
    const { context, nodes } = setup(async (url, options) => {
        requests.push({ url, options });
        return { ok: true, json: async () => ({ search_id: 'correct-id', status: 'pending' }) };
    });
    context.document.getElementById('finderQueryInput').value = 'OnlyFans, Reddit';
    context.document.getElementById('finderMinMembers').value = '500';
    vm.runInContext('pollDiscoverySession = async () => {};', context);
    await context.handleFinderSearch({ preventDefault() {} });
    assert.equal(vm.runInContext('activeDiscoverySessionId', context), 'correct-id');
    assert.deepEqual(JSON.parse(requests[0].options.body), { query: 'OnlyFans, Reddit', min_members: 500 });
    assert.equal(nodes.get('finderStopBtn').disabled, false);
});

test('HTTP errors cannot become empty successful searches', async () => {
    const { context } = setup(async () => ({ ok: false, json: async () => ({ detail: 'Migration missing' }) }));
    await assert.rejects(context.discoveryRequest('/api/discovery/search'), /Migration missing/);
});

test('stop targets the real server session', async () => {
    const urls = [];
    const { context, nodes } = setup(async url => {
        urls.push(url);
        return { ok: true, json: async () => ({ status: 'stopped' }) };
    });
    vm.runInContext('activeDiscoverySessionId = "actual-id"; discoveryRunning = true; pollDiscoverySession = async () => {};', context);
    await context.stopDiscoverySearch();
    assert.equal(urls[0], '/api/discovery/search/actual-id/stop');
    assert.equal(nodes.get('finderSubmitBtn').disabled, false);
});
