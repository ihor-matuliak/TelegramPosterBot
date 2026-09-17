// Continuous discovery: server owns the job; the page only observes it.
let activeDiscoverySessionId = null;
let discoveryTimer = null;
let discoveryRunning = false;
let discoveryView = 'session';
let discoveryOffset = 0;
let discoveryTotal = 0;
let discoveryGeneration = 0;
let discoveryPollInFlight = false;
let discoveryRenderSignature = '';
const discoveryAdding = new Set();
let currentDiscoveryCandidates = [];
let selectedDiscoveryPeers = new Set();
const discoveryPageSize = 30;
const discoveryTerminal = new Set(['stopped', 'completed', 'failed']);

async function discoveryRequest(url, options = {}) {
    const response = await fetch(url, { ...options, signal: AbortSignal.timeout(45000) });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Не вдалося виконати запит.');
    return data;
}

function discoveryMessage(message, error = false) {
    const node = document.getElementById('discoveryStatusText');
    node.textContent = message;
    node.classList.toggle('discovery-error', error);
}

function setDiscoveryRunning(running) {
    discoveryRunning = running;
    document.getElementById('finderSubmitBtn').disabled = running;
    document.getElementById('finderStopBtn').disabled = !running;
    document.getElementById('finderQueryInput').disabled = running;
    document.getElementById('finderMinMembers').disabled = running;
}

function setFinderQuery(query) {
    if (!discoveryRunning) document.getElementById('finderQueryInput').value = query;
}

async function handleFinderSearch(event) {
    event?.preventDefault();
    if (discoveryRunning) return;
    const query = document.getElementById('finderQueryInput').value.trim();
    const minMembers = Number(document.getElementById('finderMinMembers').value);
    if (!query || !Number.isInteger(minMembers) || minMembers < 0) return;
    setDiscoveryRunning(true);
    discoveryMessage('Запускаємо пошук…');
    try {
        const data = await discoveryRequest('/api/discovery/search', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query, min_members: minMembers }),
        });
        if (!data.search_id) throw new Error('Сервер не повернув ідентифікатор пошуку.');
        activeDiscoverySessionId = data.search_id;
        discoveryView = 'session';
        discoveryOffset = 0;
        document.getElementById('discoveryDecision').value = 'accepted';
        selectedDiscoveryPeers.clear();
        document.getElementById('finderResultsCard').hidden = false;
        await pollDiscoverySession();
    } catch (error) {
        setDiscoveryRunning(false);
        discoveryMessage(error.message, true);
        // A lost POST response may still have created the server task.
        await restoreDiscoverySession(false);
    }
}

async function restoreDiscoverySession(showErrors = true) {
    try {
        const data = await discoveryRequest('/api/discovery/active');
        if (data.search) {
            activeDiscoverySessionId = data.search.id;
            document.getElementById('finderQueryInput').value = data.search.query;
            document.getElementById('finderMinMembers').value = data.search.min_members || 0;
            setDiscoveryRunning(true);
            document.getElementById('finderResultsCard').hidden = false;
            await pollDiscoverySession();
        } else if (data.error && showErrors) discoveryMessage(data.error, true);
    } catch (error) {
        if (showErrors) discoveryMessage(error.message, true);
    }
}

async function stopDiscoverySearch() {
    if (!activeDiscoverySessionId) return;
    document.getElementById('finderStopBtn').disabled = true;
    discoveryMessage('Зупиняємо пошук і зберігаємо результати…');
    try {
        await discoveryRequest(`/api/discovery/search/${activeDiscoverySessionId}/stop`, { method: 'POST' });
        setDiscoveryRunning(false);
        await pollDiscoverySession();
    } catch (error) {
        document.getElementById('finderStopBtn').disabled = false;
        discoveryMessage(error.message, true);
    }
}

async function pollDiscoverySession() {
    clearTimeout(discoveryTimer);
    if (discoveryPollInFlight) {
        discoveryTimer = setTimeout(pollDiscoverySession, 1000);
        return;
    }
    if (!activeDiscoverySessionId) return;
    discoveryPollInFlight = true;
    const sessionId = activeDiscoverySessionId;
    const generation = discoveryGeneration;
    try {
        const decision = document.getElementById('discoveryDecision').value;
        const data = await discoveryRequest(`/api/discovery/results/${sessionId}?offset=${discoveryOffset}&limit=${discoveryPageSize}&decision=${decision}`);
        if (sessionId !== activeDiscoverySessionId) return;
        const session = data.search;
        setDiscoveryRunning(!discoveryTerminal.has(session.status));
        const ai = { ready: 'AI увімкнено', fallback: 'AI тимчасово недоступний — перевірка за правилами', disabled: 'Перевірка за правилами', pending: 'AI готується' };
        const wait = session.next_request_at ? ` Наступна спроба: ${new Date(session.next_request_at).toLocaleTimeString()}.` : '';
        discoveryMessage(data.error || session.error_message || `${session.progress_message || 'Пошук триває.'}${wait}`, Boolean(data.error || session.error_message));
        document.getElementById('discoveryStats').textContent = `Нових: ${session.total_candidates || 0} · Цільових: ${session.total_relevant || 0} · Запитів: ${session.queries_run || 0} · ${ai[session.ai_status] || 'AI готується'}`;
        document.getElementById('discoveryCurrentQuery').textContent = session.current_query ? `Поточний запит: ${session.current_query}` : '';
        if (discoveryView === 'session' && generation === discoveryGeneration) applyDiscoveryPage(data);
    } catch (error) {
        discoveryMessage(`Не вдалося оновити стан: ${error.message} Повторюємо перевірку.`, true);
    } finally {
        discoveryPollInFlight = false;
        if (discoveryRunning) discoveryTimer = setTimeout(pollDiscoverySession, 3000);
    }
}

async function showDiscoveryView(view) {
    discoveryView = view;
    discoveryOffset = 0;
    selectedDiscoveryPeers.clear();
    document.getElementById('finderResultsCard').hidden = false;
    document.getElementById('discoveryDecision').value = view === 'history' ? 'all' : 'accepted';
    await loadDiscoveryPage();
}

async function loadDiscoveryPage() {
    const generation = ++discoveryGeneration;
    const decision = document.getElementById('discoveryDecision').value;
    try {
        if (discoveryView === 'session') {
            if (!activeDiscoverySessionId) {
                applyDiscoveryPage({ results: [], total: 0 });
                return;
            }
            await pollDiscoverySession();
        } else {
            const data = await discoveryRequest(`/api/discovery/history?offset=${discoveryOffset}&limit=${discoveryPageSize}&decision=${decision}&include_hidden=true`);
            if (generation === discoveryGeneration) applyDiscoveryPage(data);
        }
    } catch (error) { discoveryMessage(error.message, true); }
}

function applyDiscoveryPage(data) {
    currentDiscoveryCandidates = data.results || [];
    discoveryTotal = data.total || 0;
    document.getElementById('finderResultsCountTitle').textContent = `${discoveryView === 'history' ? 'Історія знахідок' : 'Результати пошуку'} (${discoveryTotal})`;
    document.getElementById('discoveryPrev').disabled = discoveryOffset === 0;
    document.getElementById('discoveryNext').disabled = discoveryOffset + discoveryPageSize >= discoveryTotal;
    document.getElementById('discoveryPageLabel').textContent = discoveryTotal ? `${discoveryOffset + 1}–${Math.min(discoveryOffset + discoveryPageSize, discoveryTotal)} з ${discoveryTotal}` : 'Немає результатів';
    renderDiscoveryCards(currentDiscoveryCandidates);
}

function changeDiscoveryPage(direction) {
    discoveryOffset = Math.max(0, discoveryOffset + direction * discoveryPageSize);
    selectedDiscoveryPeers.clear();
    loadDiscoveryPage();
}

function discoveryNode(tag, className, text) {
    const node = document.createElement(tag);
    node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function renderDiscoveryCards(results) {
    const grid = document.getElementById('discoveryCardsGrid');
    const signature = JSON.stringify([results, [...selectedDiscoveryPeers], [...discoveryAdding]]);
    if (signature === discoveryRenderSignature) return;
    discoveryRenderSignature = signature;
    const openDetails = new Set([...grid.querySelectorAll('details[open]')].map(node => node.id));
    // Preserve focus when a live update replaces cards.
    const focusedId = grid.contains(document.activeElement) ? document.activeElement.id : null;
    grid.replaceChildren();
    const decisions = { accepted: 'Цільова група', rejected: 'Відхилено', unverified: 'Не перевірено', below_minimum: 'Мало учасників', existing: 'Уже в розсилці' };
    const access = { writable: 'Можна писати', join_required: 'Потрібно вступити', approval_required: 'Вступ за заявкою', restricted: 'Запис обмежено', unknown: 'Доступ невідомий' };
    const ads = { allowed: 'Реклама дозволена за описом', prohibited: 'Реклама заборонена за описом', unknown: 'Правила реклами не підтверджені' };
    if (!results.length) grid.append(discoveryNode('p', 'discovery-empty', 'За цим фільтром поки немає груп. Нові результати з’являтимуться під час пошуку.'));
    for (const item of results) {
        const card = discoveryNode('article', 'discovery-card');
        const heading = discoveryNode('div', 'discovery-card-header');
        const label = discoveryNode('label', 'discovery-select-label');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.id = `select-${item.id}`;
        checkbox.checked = selectedDiscoveryPeers.has(item.id);
        checkbox.disabled = item.status === 'added_to_posting' || item.chat_type !== 'group';
        checkbox.addEventListener('change', () => {
            if (checkbox.checked) selectedDiscoveryPeers.add(item.id); else selectedDiscoveryPeers.delete(item.id);
            updateSelectedCountUI();
        });
        label.append(checkbox, discoveryNode('span', 'discovery-card-title', item.title));
        heading.append(label, discoveryNode('span', 'discovery-score-badge', decisions[item.decision] || 'Архів'));
        card.append(heading);
        const count = item.members_count == null ? 'учасники: невідомо' : `${Number(item.members_count).toLocaleString()} учасників`;
        card.append(discoveryNode('p', 'discovery-peer', `${item.telegram_peer} · ${count}`));
        card.append(discoveryNode('p', 'discovery-desc', item.description || 'Опис недоступний'));
        card.append(discoveryNode('p', 'discovery-access', `${access[item.posting_access] || access.unknown} · ${ads[item.ad_policy] || ads.unknown}`));
        card.append(discoveryNode('p', 'ai-reason-box', `${item.assessed_by === 'ai' ? 'AI' : 'Перевірка'}: ${item.ai_reason || 'Результат попередньої версії парсера.'}`));
        if (item.evidence?.length) {
            const details = document.createElement('details');
            details.id = `evidence-${item.id}`;
            details.open = openDetails.has(details.id);
            details.append(discoveryNode('summary', '', 'На чому ґрунтується оцінка'));
            for (const quote of item.evidence) details.append(discoveryNode('blockquote', 'discovery-evidence', quote));
            card.append(details);
        }
        const actions = discoveryNode('div', 'discovery-card-actions');
        const username = (item.telegram_peer || '').replace(/^@/, '');
        if (/^[a-zA-Z0-9_]+$/.test(username)) {
            const link = discoveryNode('a', 'tg-btn tg-btn-secondary', 'Відкрити');
            link.href = `https://t.me/${username}`;
            link.target = '_blank'; link.rel = 'noopener noreferrer'; actions.append(link);
        }
        if (item.status !== 'hidden') {
            const hide = discoveryNode('button', 'tg-btn tg-btn-secondary', 'Приховати');
            hide.id = `hide-${item.id}`;
            hide.addEventListener('click', () => hideDiscoveryCandidate(item.id)); actions.append(hide);
        } else actions.append(discoveryNode('span', '', 'Приховано'));
        if (item.status === 'added_to_posting') actions.append(discoveryNode('span', '', 'Додано в розсилку'));
        else if (item.chat_type === 'group') {
            const add = discoveryNode('button', 'tg-btn tg-btn-primary', 'Додати в розсилку');
            add.id = `add-${item.id}`;
            add.disabled = discoveryAdding.has(item.id);
            add.addEventListener('click', () => addDiscoverySingleChat(item, add)); actions.append(add);
        }
        card.append(actions); grid.append(card);
    }
    if (focusedId) document.getElementById(focusedId)?.focus({ preventScroll: true });
    updateSelectedCountUI();
}

function updateSelectedCountUI() {
    const button = document.getElementById('batchAddFoundBtn');
    button.hidden = selectedDiscoveryPeers.size === 0;
    button.textContent = `Додати вибрані (${selectedDiscoveryPeers.size})`;
}

function discoveryAddPayload(item) {
    const post = document.getElementById('finderDefaultPost').value;
    return { result_id: item.id, chat_peer: item.telegram_peer, title: item.title,
        interval_minutes: Number(document.getElementById('finderDefaultInterval').value),
        post_id: post && post !== 'default' ? post : null };
}

async function addDiscoverySingleChat(item, button) {
    if (discoveryAdding.has(item.id)) return;
    discoveryAdding.add(item.id);
    button.disabled = true;
    try {
        const data = await discoveryRequest('/api/discovery/add-to-posting', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(discoveryAddPayload(item)),
        });
        if (data.status !== 'ok') throw new Error(data.message);
        item.status = 'added_to_posting'; selectedDiscoveryPeers.delete(item.id);
        renderDiscoveryCards(currentDiscoveryCandidates); fetchChats();
    } catch (error) { discoveryMessage(error.message, true); }
    finally { discoveryAdding.delete(item.id); renderDiscoveryCards(currentDiscoveryCandidates); }
}

async function hideDiscoveryCandidate(id) {
    try {
        await discoveryRequest('/api/discovery/hide', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ result_id: id }),
        });
        selectedDiscoveryPeers.delete(id);
        await loadDiscoveryPage();
    } catch (error) { discoveryMessage(error.message, true); }
}

async function handleBatchAddDiscovery() {
    // One explicit add at a time: progress remains visible and FloodWait stops the batch.
    const items = currentDiscoveryCandidates.filter(item => selectedDiscoveryPeers.has(item.id));
    const button = document.getElementById('batchAddFoundBtn');
    button.disabled = true;
    try {
        for (let index = 0; index < items.length; index++) {
            if (index) await new Promise(resolve => setTimeout(resolve, 15000 + Math.random() * 20000));
            discoveryMessage(`Додаємо групу ${index + 1} з ${items.length}…`);
            const data = await discoveryRequest('/api/discovery/add-to-posting', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(discoveryAddPayload(items[index])),
            });
            if (data.status !== 'ok') throw new Error(data.message);
            selectedDiscoveryPeers.delete(items[index].id);
        }
        await loadDiscoveryPage(); fetchChats();
    } catch (error) { discoveryMessage(error.message, true); }
    finally { button.disabled = false; updateSelectedCountUI(); }
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('finderStopBtn').addEventListener('click', stopDiscoverySearch);
    document.getElementById('discoveryHistoryBtn').addEventListener('click', () => showDiscoveryView('history'));
    document.getElementById('discoverySessionBtn').addEventListener('click', () => showDiscoveryView('session'));
    document.getElementById('discoveryDecision').addEventListener('change', () => { discoveryOffset = 0; selectedDiscoveryPeers.clear(); loadDiscoveryPage(); });
    document.getElementById('discoveryPrev').addEventListener('click', () => changeDiscoveryPage(-1));
    document.getElementById('discoveryNext').addEventListener('click', () => changeDiscoveryPage(1));
    restoreDiscoverySession();
});
