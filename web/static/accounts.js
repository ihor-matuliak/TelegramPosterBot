// ==========================================================
// POSTING ACCOUNTS: list, per-account chats scope, add-account login mode
// ==========================================================

let accountsData = { ready: false, workers_enabled: true, primary: null, accounts: [] };
let currentChatsAccount = loadSavedChatsAccount();
let authMode = 'primary'; // 'primary' = session of the main account, 'account' = add an additional account
let accountLoginId = null;

function loadSavedChatsAccount() {
    try { return localStorage.getItem('chatsAccount') || 'primary'; } catch (e) { return 'primary'; }
}

function saveChatsAccount(key) {
    try { localStorage.setItem('chatsAccount', key); } catch (e) { /* storage unavailable */ }
}

function initAccountsUI() {
    const addBtn = document.getElementById('addAccountBtn');
    if (addBtn) addBtn.addEventListener('click', openAddAccountModal);
    const select = document.getElementById('chatsAccountSelect');
    if (select) select.addEventListener('change', () => {
        currentChatsAccount = select.value;
        saveChatsAccount(currentChatsAccount);
        renderAccountPicker();
        fetchChats();
    });
}

function findAccount(key) {
    if (!key || key === 'primary') return accountsData.primary;
    return accountsData.accounts.find(a => a.key === key) || null;
}

function accountLabel(key) {
    if (!key || key === 'primary') return 'Основний акаунт';
    const acc = findAccount(key);
    return acc ? (acc.label || 'Акаунт') : 'Акаунт';
}

function currentAccountPosterStatus() {
    if (currentChatsAccount === 'primary') return posterStatus;
    const acc = findAccount(currentChatsAccount);
    return (acc && acc.poster) || { state: 'unknown', reason: 'Стан акаунта уточнюється' };
}

function chatsAccountQuery() {
    return `account=${encodeURIComponent(currentChatsAccount)}`;
}

function chatsAccountPayload() {
    return currentChatsAccount === 'primary' ? null : currentChatsAccount;
}

async function fetchAccounts() {
    try {
        const res = await fetch('/api/accounts');
        if (!res.ok) throw new Error('Не вдалося отримати акаунти');
        accountsData = await res.json();
        if (currentChatsAccount !== 'primary' && !findAccount(currentChatsAccount)) {
            currentChatsAccount = 'primary';
            saveChatsAccount('primary');
        }
        renderAccountPicker();
        renderAccountsGrid();
    } catch (err) {
        console.error('Error fetching accounts:', err);
    }
}

function renderAccountPicker() {
    const picker = document.getElementById('chatsAccountPicker');
    const select = document.getElementById('chatsAccountSelect');
    const hasAccounts = accountsData.ready && accountsData.accounts.length > 0;
    if (picker) picker.hidden = !hasAccounts;
    if (select) {
        let options = `<option value="primary">⭐ Основний акаунт</option>`;
        accountsData.accounts.forEach(a => {
            const icon = a.posting_enabled ? '🟢' : '⏸️';
            options += `<option value="${escapeHtml(a.key)}">${icon} ${escapeHtml(a.label || 'Акаунт')}</option>`;
        });
        select.innerHTML = options;
        select.value = currentChatsAccount;
    }
    document.querySelectorAll('.js-target-account').forEach(el => {
        el.textContent = accountLabel(currentChatsAccount);
    });
    const badge = document.getElementById('accountsNavBadge');
    if (badge) badge.textContent = 1 + accountsData.accounts.length;
}

function renderAccountsGrid() {
    const grid = document.getElementById('accountsGrid');
    if (!grid) return;
    const notice = document.getElementById('accountsMigrationNotice');
    if (notice) notice.hidden = !!accountsData.ready;
    const workersNotice = document.getElementById('accountsWorkersNotice');
    if (workersNotice) workersNotice.hidden = accountsData.workers_enabled !== false;
    const addBtn = document.getElementById('addAccountBtn');
    if (addBtn) addBtn.disabled = !accountsData.ready;

    const cards = [];
    if (accountsData.primary) cards.push(accountCardHtml(accountsData.primary));
    accountsData.accounts.forEach(a => cards.push(accountCardHtml(a)));
    if (accountsData.ready && !accountsData.accounts.length) {
        cards.push(`<div class="accounts-empty">Додаткових акаунтів ще немає. Натисніть «+ Додати акаунт» і увійдіть за QR-кодом або номером телефону.</div>`);
    }
    grid.innerHTML = cards.join('');
}

function accountCardHtml(acc) {
    const isPrimary = !!acc.is_primary;
    const user = isPrimary ? (acc.user || {}) : acc;
    const name = isPrimary ? (user.name || 'Не авторизовано') : (acc.label || acc.first_name || 'Акаунт');
    const username = user.username ? `@${user.username}` : 'без юзернейму';
    const phone = user.phone ? `+${user.phone}` : '';
    const initial = (name || '?').trim().charAt(0).toUpperCase() || '?';

    const badges = [];
    if (isPrimary) {
        badges.push(`<span class="badge badge-active">⭐ Основний</span>`);
        badges.push(acc.user ? `<span class="badge badge-active">🟢 Сесія активна</span>` : `<span class="badge badge-error">⚠️ Не авторизовано</span>`);
    } else {
        if (acc.status === 'unauthorized') badges.push(`<span class="badge badge-error">🔴 Сесію завершено</span>`);
        else if (acc.status === 'error') badges.push(`<span class="badge badge-error">⚠️ Помилка</span>`);
        else badges.push(`<span class="badge badge-active">🟢 Сесія активна</span>`);
        badges.push(acc.posting_enabled
            ? `<span class="badge badge-active">▶️ Розсилка увімкнена</span>`
            : `<span class="badge badge-paused">⏸️ Розсилка вимкнена</span>`);
    }
    if (user.is_premium) badges.push(`<span class="premium-star">⭐ Premium</span>`);

    const poster = acc.poster || {};
    const reason = poster.state === 'running' ? 'Працює за розкладом' : (poster.reason || '');
    const settings = (typeof lastSettings !== 'undefined' && lastSettings) || {};
    const hourLimit = settings.max_posts_per_hour || 30;
    const dayLimit = settings.max_posts_per_day || 300;
    const key = escapeHtml(acc.key);

    const actions = [`<button type="button" class="tg-btn tg-btn-secondary tg-btn-sm" onclick="openAccountChats('${key}')">💬 Чати (${acc.chats_total || 0})</button>`];
    if (isPrimary) {
        actions.push(`<button type="button" class="tg-btn tg-btn-secondary tg-btn-sm" onclick="handleHeaderAuthClick()">👤 Сесія</button>`);
    } else {
        actions.push(acc.posting_enabled
            ? `<button type="button" class="tg-btn tg-btn-secondary tg-btn-sm" onclick="toggleAccountPosting('${key}', false)">⏸️ Вимкнути розсилку</button>`
            : `<button type="button" class="tg-btn tg-btn-primary tg-btn-sm" onclick="toggleAccountPosting('${key}', true)">▶️ Увімкнути розсилку</button>`);
        actions.push(`<button type="button" class="tg-btn tg-btn-secondary tg-btn-sm" onclick="renameAccount('${key}')" title="Перейменувати">✏️</button>`);
        actions.push(`<button type="button" class="tg-btn tg-btn-danger tg-btn-sm" onclick="deleteAccount('${key}')" title="Видалити акаунт">🗑️</button>`);
    }

    return `
        <div class="account-card ${isPrimary ? 'primary' : ''}">
            <div class="account-card-top">
                <div class="account-card-avatar">${escapeHtml(initial)}</div>
                <div class="account-card-info">
                    <div class="account-card-name" title="${escapeHtml(name)}">${escapeHtml(name)}</div>
                    <div class="account-card-sub">${escapeHtml(username)}${phone ? ' • ' + escapeHtml(phone) : ''}</div>
                </div>
            </div>
            <div class="account-card-badges">${badges.join('')}</div>
            <div class="account-card-stats">
                <div class="account-stat"><div class="account-stat-val">${acc.chats_active || 0}/${acc.chats_total || 0}</div><div class="account-stat-label">активних чатів</div></div>
                <div class="account-stat"><div class="account-stat-val">${acc.hourly || 0}/${hourLimit}</div><div class="account-stat-label">постів за год</div></div>
                <div class="account-stat"><div class="account-stat-val">${acc.daily || 0}/${dayLimit}</div><div class="account-stat-label">постів за добу</div></div>
            </div>
            ${reason ? `<div class="account-card-reason">Стан: ${escapeHtml(reason)}</div>` : ''}
            ${acc.last_error ? `<div class="account-card-error">⚠️ ${escapeHtml(acc.last_error)}</div>` : ''}
            ${acc.chats_error ? `<div class="account-card-error">🚫 Чатів з помилками: ${acc.chats_error}</div>` : ''}
            <div class="account-card-actions">${actions.join('')}</div>
        </div>
    `;
}

function openAccountChats(key) {
    currentChatsAccount = key || 'primary';
    saveChatsAccount(currentChatsAccount);
    renderAccountPicker();
    switchTab('chats');
}

async function toggleAccountPosting(accountId, enabled) {
    const acc = findAccount(accountId);
    if (enabled && acc && !acc.chats_total && !confirm('У цього акаунта ще немає чатів. Увімкнути розсилку все одно?')) return;
    try {
        const res = await fetch(`/api/accounts/${accountId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ posting_enabled: enabled })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Не вдалося змінити стан розсилки акаунта');
        if (enabled && !isMasterRunning) alert('Розсилку акаунта увімкнено. Зверніть увагу: головний перемикач розсилки зараз на паузі.');
        await fetchAccounts();
    } catch (err) {
        alert(err.message);
    }
}

async function renameAccount(accountId) {
    const acc = findAccount(accountId);
    const label = prompt('Нова назва акаунта:', acc ? acc.label : '');
    if (label === null || !label.trim()) return;
    try {
        const res = await fetch(`/api/accounts/${accountId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ label: label.trim() })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Не вдалося перейменувати акаунт');
        await fetchAccounts();
    } catch (err) {
        alert(err.message);
    }
}

async function deleteAccount(accountId) {
    const acc = findAccount(accountId);
    const name = acc ? acc.label : 'акаунт';
    if (!confirm(`Видалити «${name}»?\n\n• Сесію буде завершено в Telegram.\n• Разом з акаунтом буде видалено його список чатів (${acc ? acc.chats_total : 0}).\n• Загальні варіанти постів не зміняться.`)) return;
    try {
        const res = await fetch(`/api/accounts/${accountId}`, { method: 'DELETE' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Не вдалося видалити акаунт');
        if (currentChatsAccount === accountId) {
            currentChatsAccount = 'primary';
            saveChatsAccount('primary');
        }
        await fetchAccounts();
        fetchChats();
        fetchStatus();
    } catch (err) {
        alert(err.message);
    }
}

// ==================== ADD-ACCOUNT LOGIN MODE ====================

function openAddAccountModal() {
    if (!accountsData.ready) {
        alert('Спочатку виконайте міграцію бази даних для акаунтів.');
        return;
    }
    openAuthModal('account');
}

async function cancelAccountLogin() {
    const loginId = accountLoginId;
    accountLoginId = null;
    if (!loginId) return;
    try {
        await fetch(`/api/accounts/login/${loginId}`, { method: 'DELETE' });
    } catch (e) {
        console.warn('Could not cancel account login:', e);
    }
}

async function accountLoginRequest(path, body) {
    const res = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Помилка додавання акаунта');
    return data;
}
