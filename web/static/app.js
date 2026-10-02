// ==========================================================
// TELEGRAM AUTO-POSTER DASHBOARD APP LOGIC (MULTI-POSTS)
// ==========================================================

let activeTab = 'chats';
let isMasterRunning = false;
let posterStatus = { state: 'unknown', reason: 'Стан розсилки уточнюється' };
let cachedChats = [];
let postsList = [];
let currentPostId = null;
let currentSourceMsgId = null;
let currentSourceChat = 'me';
let foundChatsData = [];
let isAuthorized = false;
let currentUser = null;
let currentAuthPhone = '';
let currentPhoneCodeHash = '';
let qrPollInterval = null;
let currentAuthMethod = 'qr';

document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initEventListeners();
    fetchStatus();
    fetchPosts().then(() => fetchChats());
    fetchLogs();

    // Auto-refresh stats and chats every 10 seconds
    setInterval(() => {
        fetchStatus();
        if (activeTab === 'chats') fetchChats();
        if (activeTab === 'logs') fetchLogs();
    }, 10000);
});

// ==================== NAVIGATION ====================

function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(item => {
        item.addEventListener('click', () => {
            const targetTab = item.getAttribute('data-tab');
            switchTab(targetTab);
        });
    });
}

function switchTab(tabId) {
    activeTab = tabId;
    document.querySelectorAll('.nav-item').forEach(el => {
        el.classList.toggle('active', el.getAttribute('data-tab') === tabId);
    });
    document.querySelectorAll('.tab-pane').forEach(el => {
        el.classList.toggle('active', el.id === `tab-${tabId}`);
    });

    if (tabId === 'chats') fetchChats();
    if (tabId === 'post') fetchPosts();
    if (tabId === 'logs') fetchLogs();
    if (tabId === 'settings') fetchStatus();
}

// ==================== EVENT LISTENERS ====================

function initEventListeners() {
    // Master Toggle Button
    const toggleBtn = document.getElementById('masterToggleBtn');
    toggleBtn.addEventListener('click', toggleMasterPoster);

    // Auth Action Buttons
    const headerAuthBtn = document.getElementById('headerAuthBtn');
    if (headerAuthBtn) headerAuthBtn.addEventListener('click', handleHeaderAuthClick);

    const bannerAuthBtn = document.getElementById('bannerAuthBtn');
    if (bannerAuthBtn) bannerAuthBtn.addEventListener('click', () => openAuthModal());

    const settingsLoginBtn = document.getElementById('settingsLoginBtn');
    if (settingsLoginBtn) settingsLoginBtn.addEventListener('click', () => openAuthModal());

    const settingsLogoutBtn = document.getElementById('settingsLogoutBtn');
    if (settingsLogoutBtn) settingsLogoutBtn.addEventListener('click', handleLogout);

    const settingsResetBtn = document.getElementById('settingsResetBtn');
    if (settingsResetBtn) settingsResetBtn.addEventListener('click', handleResetSession);

    const accountLogoutBtn = document.getElementById('accountLogoutBtn');
    if (accountLogoutBtn) accountLogoutBtn.addEventListener('click', handleLogout);

    // Auth Step Action Buttons
    const loginBotTokenBtn = document.getElementById('authLoginBotTokenBtn');
    if (loginBotTokenBtn) loginBotTokenBtn.addEventListener('click', handleAuthLoginBotToken);

    const sendCodeBtn = document.getElementById('authSendCodeBtn');
    if (sendCodeBtn) sendCodeBtn.addEventListener('click', handleAuthSendCode);

    const verifyCodeBtn = document.getElementById('authVerifyCodeBtn');
    if (verifyCodeBtn) verifyCodeBtn.addEventListener('click', handleAuthVerifyCode);

    const verify2faBtn = document.getElementById('authVerify2faBtn');
    if (verify2faBtn) verify2faBtn.addEventListener('click', handleAuthVerify2FA);

    const doneBtn = document.getElementById('authDoneBtn');
    if (doneBtn) doneBtn.addEventListener('click', () => closeModal());

    const changePhoneBtn = document.getElementById('authChangePhoneBtn');
    if (changePhoneBtn) changePhoneBtn.addEventListener('click', () => showAuthStep(1));

    const toggle2faBtn = document.getElementById('toggle2faVisibilityBtn');
    if (toggle2faBtn) toggle2faBtn.addEventListener('click', toggle2faVisibility);

    // QR & Reauth Listeners
    const tabBtnQr = document.getElementById('tabBtnQr');
    if (tabBtnQr) tabBtnQr.addEventListener('click', () => switchAuthMethod('qr'));

    const tabBtnPhone = document.getElementById('tabBtnPhone');
    if (tabBtnPhone) tabBtnPhone.addEventListener('click', () => switchAuthMethod('phone'));

    const refreshQrBtn = document.getElementById('refreshQrBtn');
    if (refreshQrBtn) refreshQrBtn.addEventListener('click', () => startQrAuth());

    const bannerReauthProfileBtn = document.getElementById('bannerReauthProfileBtn');
    if (bannerReauthProfileBtn) bannerReauthProfileBtn.addEventListener('click', () => {
        if (currentUser) {
            openAccountModal(currentUser);
        } else {
            openAuthModal();
        }
    });

    const reactivateAllChatsBtn = document.getElementById('reactivateAllChatsBtn');
    if (reactivateAllChatsBtn) reactivateAllChatsBtn.addEventListener('click', handleReactivateAllChats);

    // Enter key shortcuts in auth inputs
    const botTokenInp = document.getElementById('authBotTokenInput');
    if (botTokenInp) botTokenInp.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); handleAuthLoginBotToken(); }
    });
    const phoneInp = document.getElementById('authPhoneInput');
    if (phoneInp) phoneInp.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); handleAuthSendCode(); }
    });
    const codeInp = document.getElementById('authCodeInput');
    if (codeInp) codeInp.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); handleAuthVerifyCode(); }
    });
    const passInp = document.getElementById('auth2faInput');
    if (passInp) passInp.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); handleAuthVerify2FA(); }
    });

    // Add Chat Modal
    document.getElementById('openAddChatModalBtn').addEventListener('click', () => {
        updateModalPostSelects();
        openModal('addChatModal');
    });
    document.getElementById('openBatchModalBtn').addEventListener('click', () => {
        updateModalPostSelects();
        openModal('batchModal');
    });
    
    // Close Modals
    document.querySelectorAll('.close-modal-btn').forEach(btn => {
        btn.addEventListener('click', () => closeModal());
    });

    // Close modal on click outside
    document.querySelectorAll('.modal-overlay').forEach(overlay => {
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) closeModal();
        });
    });

    // Form Submissions
    document.getElementById('addChatForm').addEventListener('submit', handleAddChat);
    document.getElementById('batchChatForm').addEventListener('submit', handleBatchChats);
    document.getElementById('postEditForm').addEventListener('submit', handleSavePost);
    document.getElementById('settingsForm').addEventListener('submit', handleSaveSettings);

    // Post Action Buttons
    const setDefaultBtn = document.getElementById('setDefaultPostBtn');
    if (setDefaultBtn) setDefaultBtn.addEventListener('click', handleSetDefaultPost);

    const deletePostBtn = document.getElementById('deletePostBtn');
    if (deletePostBtn) deletePostBtn.addEventListener('click', handleDeletePost);

    const assignAllBtn = document.getElementById('assignAllChatsToThisPostBtn');
    if (assignAllBtn) assignAllBtn.addEventListener('click', assignAllChatsToCurrentPost);

    const unassignAllBtn = document.getElementById('unassignAllChatsFromThisPostBtn');
    if (unassignAllBtn) unassignAllBtn.addEventListener('click', unassignAllChatsFromCurrentPost);

    // Contextual AI Discovery Form
    document.getElementById('finderSearchForm').addEventListener('submit', handleFinderSearch);
    document.getElementById('batchAddFoundBtn').addEventListener('click', handleBatchAddDiscovery);

    // Premium Saved Messages Fetch
    document.getElementById('fetchSavedMsgBtn').addEventListener('click', handleFetchSavedMessage);

    // Spintax Preview
    document.getElementById('testSpintaxBtn').addEventListener('click', handleSpintaxPreview);

    // Live Message Preview Input
    const postContentInput = document.getElementById('postContent');
    postContentInput.addEventListener('input', updateMessagePreview);
}

// ==================== API CALLS & STATUS ====================

async function fetchStatus() {
    try {
        const res = await fetch('/api/status');
        if (!res.ok) throw new Error('Не вдалося отримати стан розсилки');
        const data = await res.json();

        // Update User & Premium info
        const userSubtitle = document.getElementById('userSubtitle');
        const premiumBadge = document.getElementById('premiumBadge');
        const headerAuthBtn = document.getElementById('headerAuthBtn');
        const headerAuthIcon = document.getElementById('headerAuthIcon');
        const headerAuthBtnText = document.getElementById('headerAuthBtnText');
        const unauthBanner = document.getElementById('unauthAlertBanner');

        // Settings tab account card
        const settingsAuthBadge = document.getElementById('settingsAuthBadge');
        const settingsAccName = document.getElementById('settingsAccName');
        const settingsAccPhone = document.getElementById('settingsAccPhone');
        const settingsLoginBtn = document.getElementById('settingsLoginBtn');
        const settingsLogoutBtn = document.getElementById('settingsLogoutBtn');

        const botBadge = document.getElementById('botWarningBadge');
        const botAlertBanner = document.getElementById('botAlertBanner');
        const accBotWarning = document.getElementById('accBotWarning');

        isAuthorized = !!data.is_authorized;
        currentUser = data.user || null;
        const isBot = !!(data.is_bot || (currentUser && currentUser.is_bot));

        if (botBadge) botBadge.style.display = (isAuthorized && isBot) ? 'inline-block' : 'none';
        if (botAlertBanner) botAlertBanner.style.display = (isAuthorized && isBot) ? 'flex' : 'none';
        if (accBotWarning) accBotWarning.style.display = (isAuthorized && isBot) ? 'block' : 'none';

        if (isAuthorized && currentUser) {
            if (isBot) {
                userSubtitle.innerHTML = `🤖 <span style="color: #f87171;">${escapeHtml(currentUser.name)}</span> <span style="color: #ef4444;">(@${escapeHtml(currentUser.username || 'бот')}) [БОТ - розсилка неможлива]</span>`;
            } else {
                userSubtitle.innerHTML = `👤 <span>${escapeHtml(currentUser.name)}</span> <span style="color: var(--accent-blue-hover)">(@${escapeHtml(currentUser.username || 'без юзернейму')})</span>`;
            }

            if (currentUser.is_premium && !isBot) {
                premiumBadge.style.display = 'inline-block';
            } else {
                premiumBadge.style.display = 'none';
            }

            if (headerAuthBtn) {
                headerAuthBtn.className = isBot ? 'header-auth-btn unauth' : 'header-auth-btn auth';
                if (headerAuthIcon) headerAuthIcon.textContent = isBot ? '🤖' : '👤';
                if (headerAuthBtnText) headerAuthBtnText.textContent = currentUser.name || (isBot ? 'Бот' : 'Акаунт');
                headerAuthBtn.title = isBot ? '⚠️ Авторизовано як бот! Натисніть для зміни' : 'Керування акаунтом Telegram';
            }

            if (unauthBanner) unauthBanner.style.display = 'none';

            if (settingsAuthBadge) {
                settingsAuthBadge.className = isBot ? 'auth-status-badge unauth' : 'auth-status-badge auth';
                settingsAuthBadge.textContent = isBot ? '⚠️ Авторизовано як БОТ' : '🟢 Авторизовано';
            }
            if (settingsAccName) settingsAccName.textContent = currentUser.name + (isBot ? ' (БОТ)' : '');
            if (settingsAccPhone) {
                const phoneStr = currentUser.phone ? `+${currentUser.phone}` : '';
                const userStr = currentUser.username ? ` (@${currentUser.username})` : '';
                settingsAccPhone.textContent = isBot
                    ? `Токен бота • ⚠️ Не має права розсилати у звичайні групи`
                    : `${phoneStr}${userStr} • Сесія активна (MTProto)`;
            }
            if (settingsLoginBtn) settingsLoginBtn.style.display = 'none';
            if (settingsLogoutBtn) settingsLogoutBtn.style.display = 'inline-block';

        } else {
            userSubtitle.innerHTML = `⚠️ <span style="color: #ef5350;">Не авторизовано</span> <button type="button" id="subtitleAuthBtn" class="auth-link-btn" onclick="openAuthModal()">[Увійти в акаунт]</button>`;

            if (premiumBadge) premiumBadge.style.display = 'none';

            if (headerAuthBtn) {
                headerAuthBtn.className = 'header-auth-btn unauth';
                if (headerAuthIcon) headerAuthIcon.textContent = '🔑';
                if (headerAuthBtnText) headerAuthBtnText.textContent = 'Увійти';
                headerAuthBtn.title = 'Авторизуватись у Telegram';
            }

            if (unauthBanner) unauthBanner.style.display = 'flex';

            if (settingsAuthBadge) {
                settingsAuthBadge.className = 'auth-status-badge unauth';
                settingsAuthBadge.textContent = '⚠️ Не авторизовано';
            }
            if (settingsAccName) settingsAccName.textContent = 'Акаунт не підключено';
            if (settingsAccPhone) settingsAccPhone.textContent = 'Для публікації оголошень потрібна авторизація';
            if (settingsLoginBtn) settingsLoginBtn.style.display = 'inline-block';
            if (settingsLogoutBtn) settingsLogoutBtn.style.display = 'none';
        }

        // Update Master Toggle
        isMasterRunning = data.is_running;
        posterStatus = data.poster || { state: isMasterRunning ? 'unknown' : 'paused', reason: isMasterRunning ? 'Стан планувальника невідомий' : 'Розсилку призупинено' };
        const btn = document.getElementById('masterToggleBtn');
        const text = document.getElementById('masterToggleText');
        if (isMasterRunning) {
            btn.className = 'master-toggle-btn running';
            text.textContent = 'Розсилка активна (Увімкнено)';
        } else {
            btn.className = 'master-toggle-btn paused';
            text.textContent = 'Розсилка на паузі (Вимкнено)';
        }
        if (posterStatus.reason) text.textContent = posterStatus.reason;
        btn.title = posterStatus.until
            ? `Наступна перевірка: ${new Date(posterStatus.until).toLocaleTimeString()}`
            : (posterStatus.reason || 'Натисніть, щоб призупинити розсилку');
        renderChatsTable(cachedChats);

        // Update Stat Badges
        document.getElementById('statTotalChats').textContent = data.total_chats;
        document.getElementById('statActiveChats').textContent = data.active_chats;
        document.getElementById('statErrorChats').textContent = data.error_chats;
        document.getElementById('chatsNavBadge').textContent = data.total_chats;

        // Update Rate Limit Chips
        document.getElementById('rateHourly').textContent = data.hourly_posts || 0;
        document.getElementById('rateDaily').textContent = data.daily_posts || 0;

        // Settings inputs
        if (data.settings) {
            document.getElementById('limitHourly').textContent = data.settings.max_posts_per_hour || 30;
            document.getElementById('limitDaily').textContent = data.settings.max_posts_per_day || 300;

            document.getElementById('minDelayInput').value = data.settings.min_delay_seconds || 15;
            document.getElementById('maxDelayInput').value = data.settings.max_delay_seconds || 35;
            document.getElementById('jitterInput').value = data.settings.jitter_minutes || 3;

            document.getElementById('maxHourlyInput').value = data.settings.max_posts_per_hour || 30;
            document.getElementById('maxDailyInput').value = data.settings.max_posts_per_day || 300;
            document.getElementById('batchSizeInput').value = data.settings.batch_size || 8;
            document.getElementById('batchRestInput').value = data.settings.batch_rest_minutes || 8;

            document.getElementById('enableTypingInput').checked = data.settings.enable_typing_simulation !== false;
            document.getElementById('enableAntiFingerprintInput').checked = data.settings.enable_anti_fingerprint !== false;
            document.getElementById('enableSpintaxInput').checked = data.settings.enable_spintax !== false;

            document.getElementById('enableNightModeInput').checked = data.settings.enable_night_mode === true;
            document.getElementById('nightStartInput').value = data.settings.night_start_hour ?? 1;
            document.getElementById('nightEndInput').value = data.settings.night_end_hour ?? 8;
            document.getElementById('circuitBreakerInput').checked = data.settings.auto_circuit_breaker !== false;
        }
    } catch (err) {
        posterStatus = { state: 'unknown', reason: 'Немає зв’язку з сервером' };
        document.getElementById('masterToggleText').textContent = posterStatus.reason;
        renderChatsTable(cachedChats);
        console.error('Error fetching status:', err);
    }
}

async function toggleMasterPoster() {
    try {
        const res = await fetch('/api/settings/toggle', { method: 'POST' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Не вдалося змінити стан розсилки');
        isMasterRunning = data.is_running;
        await fetchStatus();
    } catch (err) {
        console.error('Error toggling poster:', err);
        alert(err.message);
    }
}

// ==================== CHATS TABLE ====================

async function fetchChats() {
    try {
        const res = await fetch('/api/chats');
        if (!res.ok) throw new Error('Не вдалося отримати чати');
        const chats = await res.json();
        cachedChats = chats;
        renderChatsTable(chats);
    } catch (err) {
        console.error('Error fetching chats:', err);
    }
}

function renderChatsTable(chats) {
    const tbody = document.getElementById('chatsTableBody');
    if (!chats.length) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-secondary); padding: 30px;">Немає доданих чатів. Натисніть «+ Додати чат» або скористайтесь «🔍 Пошук & Парсер чатів».</td></tr>`;
        return;
    }

    const now = new Date();

    tbody.innerHTML = chats.map(chat => {
        let statusBadge = '';
        if (!chat.is_active) {
            statusBadge = `<span class="badge badge-paused" title="${escapeHtml(chat.last_error || '')}">⏸️ Вимкнено${chat.last_error ? ': ' + escapeHtml(chat.last_error) : ''}</span>`;
        } else if (chat.status === 'slowmode_wait') {
            statusBadge = `<span class="badge badge-slowmode">⏳ SlowMode</span>`;
        } else if (chat.status === 'error' || chat.status === 'restricted') {
            statusBadge = `<span class="badge badge-error" title="${escapeHtml(chat.last_error || '')}">🚫 ${escapeHtml(chat.last_error || 'Помилка')}</span>`;
        } else {
            statusBadge = `<span class="badge badge-active">🟢 Активний</span>`;
        }

        let intervalLabel = `${chat.interval_minutes} хв`;
        if (chat.interval_minutes === 60) intervalLabel = '1 година';
        else if (chat.interval_minutes === 1440) intervalLabel = '1 доба';
        else if (chat.interval_minutes === 4320) intervalLabel = '3 дні';

        let nextPostLabel = '—';
        if (chat.next_post_at && chat.is_active) {
            const nextDate = new Date(chat.next_post_at);
            const diffMinutes = Math.ceil((nextDate - now) / 60000);
            if (posterStatus.state !== 'running') {
                nextPostLabel = `<span class="badge badge-paused">${escapeHtml(posterStatus.reason || 'Очікування розсилки')}</span>`;
            } else if (nextDate <= now) {
                nextPostLabel = `<span style="color: var(--accent-green); font-weight: 600;">У черзі на відправку</span>`;
            } else {
                nextPostLabel = `Через ${diffMinutes} хв (${nextDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })})`;
            }
        }

        // Generate Post Variant select options
        let postOptionsHtml = `<option value="default" ${!chat.post_id ? 'selected' : ''}>⭐️ За замовчуванням</option>`;
        postsList.forEach(p => {
            const isSel = (chat.post_id === p.id);
            postOptionsHtml += `<option value="${p.id}" ${isSel ? 'selected' : ''}>${escapeHtml(p.title)}</option>`;
        });

        return `
            <tr>
                <td><strong>${escapeHtml(chat.title || chat.chat_peer)}</strong></td>
                <td><code style="color: var(--accent-blue-hover);">${escapeHtml(chat.chat_peer)}</code></td>
                <td>
                    <select class="tg-table-select" onchange="changeChatPost('${chat.id}', this.value)" title="Оберіть варіант оголошення для цього чату">
                        ${postOptionsHtml}
                    </select>
                </td>
                <td>${intervalLabel}</td>
                <td>${statusBadge}</td>
                <td>${nextPostLabel}</td>
                <td>
                    <div style="display: flex; gap: 8px;">
                        <button class="tg-btn tg-btn-secondary tg-btn-sm" onclick="toggleChatActive('${chat.id}', ${!chat.is_active})">
                            ${chat.is_active ? '⏸️' : '▶️'}
                        </button>
                        <button class="tg-btn tg-btn-danger tg-btn-sm" onclick="deleteChat('${chat.id}')">
                            🗑️
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

async function changeChatPost(chatId, newPostId) {
    try {
        const payload = { post_id: (newPostId === 'default') ? null : newPostId };
        await fetch(`/api/chats/${chatId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        fetchPosts();
        fetchStatus();
    } catch (err) {
        console.error('Error changing chat post variant:', err);
    }
}

async function handleAddChat(e) {
    e.preventDefault();
    const chatPeer = document.getElementById('chatPeerInput').value.trim();
    const interval = parseInt(document.getElementById('chatIntervalSelect').value, 10);
    const postSelectVal = document.getElementById('chatPostSelect').value;
    const postId = (postSelectVal === 'default') ? null : postSelectVal;

    if (!chatPeer) return;

    try {
        await fetch('/api/chats', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ chat_peer: chatPeer, interval_minutes: interval, post_id: postId })
        });
        closeModal();
        document.getElementById('chatPeerInput').value = '';
        fetchChats();
        fetchPosts();
        fetchStatus();
    } catch (err) {
        alert('Помилка при додаванні чату: ' + err);
    }
}

async function handleBatchChats(e) {
    e.preventDefault();
    const textLinks = document.getElementById('batchLinksInput').value.trim();
    const interval = parseInt(document.getElementById('batchIntervalSelect').value, 10);
    const postSelectVal = document.getElementById('batchPostSelect').value;
    const postId = (postSelectVal === 'default') ? null : postSelectVal;

    if (!textLinks) return;

    try {
        const res = await fetch('/api/chats/batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text_links: textLinks, interval_minutes: interval, post_id: postId })
        });
        const data = await res.json();
        alert(`Успішно імпортовано: ${data.added_count} чатів!`);
        closeModal();
        document.getElementById('batchLinksInput').value = '';
        fetchChats();
        fetchPosts();
        fetchStatus();
    } catch (err) {
        alert('Помилка імпорту: ' + err);
    }
}

async function toggleChatActive(chatId, newActiveState) {
    try {
        await fetch(`/api/chats/${chatId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_active: newActiveState, reset_status: true })
        });
        fetchChats();
        fetchStatus();
    } catch (err) {
        console.error('Error updating chat:', err);
    }
}

async function deleteChat(chatId) {
    if (!confirm('Ви дійсно хочете видалити цей чат зі списку розсилки?')) return;
    try {
        await fetch(`/api/chats/${chatId}`, { method: 'DELETE' });
        fetchChats();
        fetchPosts();
        fetchStatus();
    } catch (err) {
        console.error('Error deleting chat:', err);
    }
}

function updateModalPostSelects() {
    const chatPostSelect = document.getElementById('chatPostSelect');
    const batchPostSelect = document.getElementById('batchPostSelect');
    const finderDefaultPost = document.getElementById('finderDefaultPost');
    
    let options = `<option value="default">⭐️ За замовчуванням (Основне)</option>`;
    postsList.forEach(p => {
        options += `<option value="${p.id}">${escapeHtml(p.title)}</option>`;
    });

    if (chatPostSelect) chatPostSelect.innerHTML = options;
    if (batchPostSelect) batchPostSelect.innerHTML = options;
    if (finderDefaultPost) finderDefaultPost.innerHTML = options;
}

// ==================== MULTI-POST TEMPLATES & PREMIUM EMOJIS ====================

async function fetchPosts() {
    try {
        const res = await fetch('/api/posts');
        const data = await res.json();
        postsList = data.all_posts || [];

        // If no active post ID selected or active post deleted, pick default or first
        if (!currentPostId || !postsList.some(p => p.id === currentPostId)) {
            if (data.active_post) {
                currentPostId = data.active_post.id;
            } else if (postsList.length > 0) {
                currentPostId = postsList[0].id;
            } else {
                currentPostId = null;
            }
        }

        renderPostTabs();
        loadPostIntoEditor(currentPostId);
        updateModalPostSelects();
    } catch (err) {
        console.error('Error fetching posts:', err);
    }
}

function renderPostTabs() {
    const container = document.getElementById('postTabsContainer');
    if (!container) return;

    let html = '';
    postsList.forEach((p, idx) => {
        const isCurrent = (p.id === currentPostId);
        const activeClass = isCurrent ? 'active' : '';
        const isDefault = p.is_active;
        const defaultBadge = isDefault ? '<span class="tab-badge-default">⭐️ Default</span>' : '';
        const countBadge = `<span class="tab-badge-count" title="Прив'язано чатів">${p.assigned_chats_count || 0} чатів</span>`;

        html += `
            <button type="button" class="post-tab-pill ${activeClass}" onclick="selectPostTab('${p.id}')">
                <span>${escapeHtml(p.title || `Варіант #${idx + 1}`)}</span>
                ${defaultBadge}
                ${countBadge}
            </button>
        `;
    });

    // Add New Variant button
    html += `
        <button type="button" class="post-tab-add-btn" onclick="createNewPostTab()">
            <span>➕ Новий варіант</span>
        </button>
    `;

    container.innerHTML = html;
}

function selectPostTab(postId) {
    currentPostId = postId;
    renderPostTabs();
    loadPostIntoEditor(postId);
}

function createNewPostTab() {
    currentPostId = null;
    currentSourceMsgId = null;
    currentSourceChat = 'me';
    document.getElementById('postTitle').value = `Оголошення #${postsList.length + 1}`;
    document.getElementById('postContent').value = '';
    document.getElementById('postContent').focus();
    renderPostTabs();
    updateMessagePreview();

    // Toggle default button state
    const defBtn = document.getElementById('setDefaultPostBtn');
    if (defBtn) defBtn.style.display = 'none';
    const delBtn = document.getElementById('deletePostBtn');
    if (delBtn) delBtn.style.display = 'none';
    const section = document.getElementById('postAssignedChatsSection');
    if (section) section.style.display = 'none';
}

function loadPostIntoEditor(postId) {
    const defBtn = document.getElementById('setDefaultPostBtn');
    const delBtn = document.getElementById('deletePostBtn');

    if (!postId) {
        if (defBtn) defBtn.style.display = 'none';
        if (delBtn) delBtn.style.display = 'none';
        const section = document.getElementById('postAssignedChatsSection');
        if (section) section.style.display = 'none';
        return;
    }

    const post = postsList.find(p => p.id === postId);
    if (!post) return;

    currentPostId = post.id;
    currentSourceMsgId = post.source_msg_id;
    currentSourceChat = post.source_chat_peer || 'me';

    document.getElementById('postTitle').value = post.title || '';
    document.getElementById('postContent').value = post.content || '';
    updateMessagePreview();

    if (defBtn) {
        defBtn.style.display = 'inline-block';
        defBtn.textContent = post.is_active ? '⭐️ Основний (Default)' : 'Зробити основним';
        defBtn.disabled = post.is_active;
    }
    if (delBtn) {
        delBtn.style.display = 'inline-block';
        delBtn.disabled = (postsList.length <= 1);
    }

    renderPostAssignedChats(postId);
}

async function renderPostAssignedChats(postId) {
    const section = document.getElementById('postAssignedChatsSection');
    const grid = document.getElementById('postChatsAssignmentGrid');
    const countEl = document.getElementById('postAssignedCount');
    if (!section || !grid) return;

    if (!postId) {
        section.style.display = 'none';
        return;
    }

    section.style.display = 'block';

    try {
        const res = await fetch('/api/chats');
        const chats = await res.json();
        
        const assignedChats = chats.filter(c => c.post_id === postId);
        if (countEl) countEl.textContent = `${assignedChats.length} із ${chats.length}`;

        if (!chats.length) {
            grid.innerHTML = `<div style="grid-column: 1 / -1; color: var(--text-muted); font-size: 13px; padding: 10px;">Немає доданих чатів для прив'язки.</div>`;
            return;
        }

        grid.innerHTML = chats.map(c => {
            const isAssigned = (c.post_id === postId);
            return `
                <label style="display: flex; align-items: center; gap: 8px; background: var(--bg-input); padding: 8px 12px; border-radius: var(--radius-sm); font-size: 13px; cursor: pointer; border: 1px solid ${isAssigned ? 'var(--accent-blue)' : 'transparent'};">
                    <input type="checkbox" onchange="togglePostChatLink('${c.id}', '${postId}', this.checked)" ${isAssigned ? 'checked' : ''}>
                    <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(c.title || c.chat_peer)}">
                        ${escapeHtml(c.title || c.chat_peer)}
                    </span>
                </label>
            `;
        }).join('');
    } catch (err) {
        console.error('Error rendering assigned chats:', err);
    }
}

async function togglePostChatLink(chatId, postId, isChecked) {
    try {
        const newPostId = isChecked ? postId : null;
        await fetch(`/api/chats/${chatId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ post_id: newPostId })
        });
        await fetchPosts();
        if (activeTab === 'chats') fetchChats();
    } catch (err) {
        console.error('Error toggling chat post link:', err);
    }
}

async function assignAllChatsToCurrentPost() {
    if (!currentPostId) return;
    try {
        const res = await fetch('/api/chats');
        const chats = await res.json();
        for (const c of chats) {
            await fetch(`/api/chats/${c.id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ post_id: currentPostId })
            });
        }
        alert('⚡ Варіант успішно призначено на всі підключені чати!');
        await fetchPosts();
        if (activeTab === 'chats') fetchChats();
    } catch (err) {
        alert('Помилка призначення: ' + err);
    }
}

async function unassignAllChatsFromCurrentPost() {
    if (!currentPostId) return;
    try {
        const res = await fetch('/api/chats');
        const chats = await res.json();
        for (const c of chats) {
            if (c.post_id === currentPostId) {
                await fetch(`/api/chats/${c.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ post_id: null })
                });
            }
        }
        alert('🔄 Усі чати переведено на дефолтний варіант!');
        await fetchPosts();
        if (activeTab === 'chats') fetchChats();
    } catch (err) {
        alert('Помилка скидання: ' + err);
    }
}

function updateMessagePreview() {
    const content = document.getElementById('postContent').value || 'Тут буде відображатися превʼю вашого оголошення...';
    document.getElementById('previewContent').innerHTML = escapeHtml(content).replace(/\n/g, '<br>');

    const timeEl = document.getElementById('previewTime');
    const now = new Date();
    timeEl.textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

async function handleSavePost(e) {
    e.preventDefault();
    const title = document.getElementById('postTitle').value.trim();
    const content = document.getElementById('postContent').value.trim();

    if (!content) {
        alert('Текст оголошення не може бути пустим!');
        return;
    }

    try {
        const payload = {
            title,
            content,
            post_id: currentPostId,
            source_msg_id: currentSourceMsgId,
            source_chat_peer: currentSourceChat
        };

        const res = await fetch('/api/posts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.post) {
            currentPostId = data.post.id;
        }
        alert('✅ Варіант оголошення успішно збережено!');
        await fetchPosts();
        fetchChats();
    } catch (err) {
        alert('Помилка збереження: ' + err);
    }
}

async function handleSetDefaultPost() {
    if (!currentPostId) {
        alert('Спочатку збережіть це оголошення!');
        return;
    }

    try {
        await fetch(`/api/posts/${currentPostId}/set-default`, { method: 'POST' });
        alert('⭐️ Оголошення встановлено як основне (дефолтне) для розсилки!');
        await fetchPosts();
        fetchChats();
    } catch (err) {
        alert('Помилка встановлення дефолтного поста: ' + err);
    }
}

async function handleDeletePost() {
    if (!currentPostId) return;

    if (postsList.length <= 1) {
        alert('Не можна видалити єдине оголошення в системі!');
        return;
    }

    if (!confirm('Ви дійсно хочете видалити цей варіант оголошення? Усі привʼязані чати автоматично перейдуть на дефолтне.')) return;

    try {
        const res = await fetch(`/api/posts/${currentPostId}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.status === 'ok') {
            alert('🗑️ Варіант оголошення видалено!');
            currentPostId = null;
            await fetchPosts();
            fetchChats();
        } else {
            alert('⚠️ ' + (data.detail || 'Не вдалося видалити'));
        }
    } catch (err) {
        alert('Помилка видалення: ' + err);
    }
}

async function handleFetchSavedMessage() {
    try {
        const btn = document.getElementById('fetchSavedMsgBtn');
        btn.textContent = '⏳ Синхронізація з Telegram...';
        btn.disabled = true;

        const payload = {
            post_id: currentPostId,
            create_new: !currentPostId
        };

        const res = await fetch('/api/telegram/sync-saved-post', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        btn.textContent = '⭐ 📥 Завантажити зі «Збережених»';
        btn.disabled = false;

        if (data.status === 'ok') {
            currentSourceMsgId = data.message_id;
            currentSourceChat = 'me';
            if (data.post) {
                currentPostId = data.post.id;
            }
            alert(`🎉 Повідомлення #${data.message_id} успішно завантажено!\n\n• Premium емодзі: ${data.has_custom_emojis ? 'ТАК (анімовані) ⭐' : 'Звичайні'}\n• Сутностей: ${data.entities_count}\n\nЗбережено для 100% нативної відправки.`);
            await fetchPosts();
            fetchChats();
        } else {
            alert('⚠️ ' + (data.message || 'Не вдалося завантажити повідомлення'));
        }
    } catch (err) {
        alert('Помилка імпорту зі збережених: ' + err);
    }
}

async function handleSpintaxPreview() {
    const content = document.getElementById('postContent').value.trim();
    if (!content) {
        alert('Введіть текст оголошення для перевірки Spintax!');
        return;
    }

    try {
        const res = await fetch('/api/posts/preview-spintax', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: content })
        });
        const data = await res.json();
        document.getElementById('previewContent').innerHTML = escapeHtml(data.sample).replace(/\n/g, '<br>');
    } catch (err) {
        console.error('Error generating spintax preview:', err);
    }
}

// ==================== ADVANCED SECURITY SETTINGS ====================

async function handleSaveSettings(e) {
    e.preventDefault();
    const payload = {
        min_delay_seconds: parseInt(document.getElementById('minDelayInput').value, 10),
        max_delay_seconds: parseInt(document.getElementById('maxDelayInput').value, 10),
        jitter_minutes: parseInt(document.getElementById('jitterInput').value, 10),

        max_posts_per_hour: parseInt(document.getElementById('maxHourlyInput').value, 10),
        max_posts_per_day: parseInt(document.getElementById('maxDailyInput').value, 10),
        batch_size: parseInt(document.getElementById('batchSizeInput').value, 10),
        batch_rest_minutes: parseInt(document.getElementById('batchRestInput').value, 10),

        enable_typing_simulation: document.getElementById('enableTypingInput').checked,
        enable_anti_fingerprint: document.getElementById('enableAntiFingerprintInput').checked,
        enable_spintax: document.getElementById('enableSpintaxInput').checked,

        enable_night_mode: document.getElementById('enableNightModeInput').checked,
        night_start_hour: parseInt(document.getElementById('nightStartInput').value, 10),
        night_end_hour: parseInt(document.getElementById('nightEndInput').value, 10),
        auto_circuit_breaker: document.getElementById('circuitBreakerInput').checked
    };

    try {
        const response = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Не вдалося зберегти налаштування');
        }
        alert('🛡️ Всі налаштування безпеки та анти-флуду збережено!');
        fetchStatus();
    } catch (err) {
        alert('Помилка оновлення налаштувань: ' + err);
    }
}

// ==================== LOGS ====================

async function fetchLogs() {
    try {
        const res = await fetch('/api/logs');
        const logs = await res.json();
        const feed = document.getElementById('logsFeed');

        if (!logs.length) {
            feed.innerHTML = `<div style="text-align: center; color: var(--text-secondary); padding: 20px;">Логів поки немає.</div>`;
            return;
        }

        feed.innerHTML = logs.map(log => {
            const time = new Date(log.created_at).toLocaleString();
            return `
                <div class="log-entry ${log.status}">
                    <div>
                        <strong>${escapeHtml(log.chat_peer)}</strong>: ${escapeHtml(log.details || log.status)}
                    </div>
                    <div style="color: var(--text-secondary); font-size: 11px;">
                        ${time}
                    </div>
                </div>
            `;
        }).join('');
    } catch (err) {
        console.error('Error fetching logs:', err);
    }
}

// ==================== UTILS ====================

function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) modal.classList.add('active');
}

function closeModal() {
    stopQrPolling();
    document.querySelectorAll('.modal-overlay').forEach(m => m.classList.remove('active'));
}

function escapeHtml(str) {
    if (!str) return '';
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// ==================== TELEGRAM UI AUTHENTICATION ====================

function handleHeaderAuthClick() {
    if (isAuthorized && currentUser) {
        openAccountModal(currentUser);
    } else {
        openAuthModal();
    }
}

function openAuthModal() {
    clearAuthErrors();
    const phoneInput = document.getElementById('authPhoneInput');
    if (phoneInput) phoneInput.value = currentAuthPhone || '+380';
    switchAuthMethod('qr');
    openModal('authModal');
}

function openAccountModal(user) {
    if (!user) return;
    document.getElementById('accName').textContent = user.name || 'Telegram Користувач';
    document.getElementById('accUsername').textContent = user.username ? `@${user.username}` : '@без юзернейму';
    document.getElementById('accPhone').textContent = user.phone ? `+${user.phone}` : '';
    const premBadge = document.getElementById('accPremiumBadge');
    if (premBadge) {
        premBadge.style.display = user.is_premium ? 'inline-block' : 'none';
    }
    const accBotWarning = document.getElementById('accBotWarning');
    if (accBotWarning) {
        accBotWarning.style.display = user.is_bot ? 'block' : 'none';
    }
    openModal('accountModal');
}

function showAuthStep(stepNumber) {
    clearAuthErrors();
    const steps = ['Qr', '1', '2', '3', '4'];
    steps.forEach(s => {
        const el = document.getElementById(`authStep${s}`);
        if (el) el.classList.toggle('active', String(s).toLowerCase() === String(stepNumber).toLowerCase());
    });

    if (String(stepNumber) === '1') {
        setTimeout(() => {
            const inp = document.getElementById('authPhoneInput');
            if (inp) inp.focus();
        }, 150);
    } else if (String(stepNumber) === '2') {
        const codeInput = document.getElementById('authCodeInput');
        if (codeInput) {
            codeInput.value = '';
            setTimeout(() => codeInput.focus(), 150);
        }
    } else if (String(stepNumber) === '3') {
        const passInput = document.getElementById('auth2faInput');
        if (passInput) {
            passInput.value = '';
            setTimeout(() => passInput.focus(), 150);
        }
    }
}

function clearAuthErrors() {
    [1, 2, 3].forEach(s => {
        const errEl = document.getElementById(`authStep${s}Error`);
        if (errEl) {
            errEl.style.display = 'none';
            errEl.textContent = '';
        }
    });
}

function showAuthError(stepNumber, message) {
    const errEl = document.getElementById(`authStep${stepNumber}Error`);
    if (errEl) {
        errEl.textContent = message;
        errEl.style.display = 'block';
    }
}

async function handleAuthLoginBotToken() {
    clearAuthErrors();
    const tokenInput = document.getElementById('authBotTokenInput');
    const token = tokenInput ? tokenInput.value.trim() : '';

    const btn = document.getElementById('authLoginBotTokenBtn');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span>⏳ Авторизація через Bot Token...</span>';
    }

    try {
        const res = await fetch('/api/auth/login-bot-token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bot_token: token || null })
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || 'Не вдалося авторизувати токен бота');
        }
        handleAuthSuccess(data.user);
    } catch (err) {
        showAuthError(1, err.message || 'Помилка авторизації токена бота');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<span>⚡ Авторизуватись через Bot Token</span>';
        }
    }
}

async function handleAuthSendCode() {
    clearAuthErrors();
    const phoneInput = document.getElementById('authPhoneInput');
    const phone = phoneInput.value.trim();
    if (!phone || phone.length < 8) {
        showAuthError(1, 'Введіть коректний номер телефону у міжнародному форматі (наприклад: +380991234567)');
        phoneInput.focus();
        return;
    }

    const btn = document.getElementById('authSendCodeBtn');
    btn.disabled = true;
    btn.innerHTML = '<span>⏳ Запит коду...</span>';

    try {
        const res = await fetch('/api/auth/send-code', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ phone })
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || 'Не вдалося надіслати код');
        }

        currentAuthPhone = data.phone || phone;
        currentPhoneCodeHash = data.phone_code_hash || '';
        document.getElementById('authPhoneDisplay').textContent = currentAuthPhone;
        const deliveryNotice = document.getElementById('authDeliveryNotice');
        if (deliveryNotice && data.delivery_text) {
            deliveryNotice.innerHTML = `📬 <strong>Куди надіслано:</strong> ${data.delivery_text}.<br><span style="color: #e5a93c;">⚠️ Код надходить у додаток Telegram (чат «Telegram» з синьою галочкою), а НЕ в SMS!</span>`;
        }
        showAuthStep(2);
    } catch (err) {
        showAuthError(1, err.message || 'Помилка надсилання коду підтвердження');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<span>Отримати код</span>';
    }
}

async function handleAuthVerifyCode() {
    clearAuthErrors();
    const codeInput = document.getElementById('authCodeInput');
    const code = codeInput.value.trim();
    if (!code) {
        showAuthError(2, 'Введіть код підтвердження');
        codeInput.focus();
        return;
    }

    const btn = document.getElementById('authVerifyCodeBtn');
    btn.disabled = true;
    btn.innerHTML = '<span>⏳ Перевірка...</span>';

    try {
        const res = await fetch('/api/auth/verify-code', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                phone: currentAuthPhone,
                code: code,
                phone_code_hash: currentPhoneCodeHash
            })
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || 'Помилка перевірки коду');
        }

        if (data.status === 'needs_2fa') {
            showAuthStep(3);
        } else if (data.status === 'ok') {
            handleAuthSuccess(data.user);
        }
    } catch (err) {
        showAuthError(2, err.message || 'Невірний код підтвердження');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<span>Підтвердити</span>';
    }
}

async function handleAuthVerify2FA() {
    clearAuthErrors();
    const passInput = document.getElementById('auth2faInput');
    const password = passInput.value;
    if (!password) {
        showAuthError(3, 'Введіть хмарний пароль 2FA');
        passInput.focus();
        return;
    }

    const btn = document.getElementById('authVerify2faBtn');
    btn.disabled = true;
    btn.innerHTML = '<span>⏳ Перевірка...</span>';

    try {
        const endpoint = currentAuthMethod === 'qr' ? '/api/auth/qr/2fa' : '/api/auth/verify-2fa';
        const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password })
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || data.message || 'Невірний пароль 2FA');
        }

        if (data.status === 'ok') {
            handleAuthSuccess(data.user);
        }
    } catch (err) {
        showAuthError(3, err.message || 'Помилка 2FA авторизації');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<span>Увійти</span>';
    }
}

function handleAuthSuccess(user) {
    stopQrPolling();
    const detailsEl = document.getElementById('authSuccessDetails');
    if (user && detailsEl) {
        if (user.is_bot) {
            detailsEl.innerHTML = `
                <div>Авторизовано як <strong>БОТ</strong>: <strong>${escapeHtml(user.name)}</strong> (@${escapeHtml(user.username || 'бот')})</div>
                <div style="font-size: 13px; color: #f87171; margin-top: 6px;">⚠️ УВАГА: Telegram боти не мають можливості розсилати у групи! Для розсилки потрібен реальний профіль.</div>
            `;
        } else {
            detailsEl.innerHTML = `
                <div>Ви успішно увійшли під профілем: <strong>${escapeHtml(user.name)}</strong> (@${escapeHtml(user.username || 'без юзернейму')})</div>
                <div style="font-size: 13px; color: var(--accent-green); margin-top: 6px;">🟢 Профіль підключено (MTProto). Система готова до розсилки!</div>
            `;
        }
    }
    showAuthStep(4);
    fetchStatus();
    fetchChats();
}

function toggle2faVisibility() {
    const passInput = document.getElementById('auth2faInput');
    const btn = document.getElementById('toggle2faVisibilityBtn');
    if (passInput.type === 'password') {
        passInput.type = 'text';
        btn.textContent = '🙈';
    } else {
        passInput.type = 'password';
        btn.textContent = '👁️';
    }
}

async function handleLogout() {
    if (!confirm('Ви дійсно бажаєте вийти з Telegram акаунту? Поточну сесію буде видалено.')) {
        return;
    }

    try {
        const res = await fetch('/api/auth/logout', { method: 'POST' });
        const data = await res.json();
        closeModal();
        alert(data.message || 'Ви успішно вийшли з акаунту');
        fetchStatus();
    } catch (err) {
        alert('Помилка при виході з акаунту: ' + err);
    }
}

async function handleResetSession() {
    if (!confirm('Скинути файл сесії? Це видалить застарілий сесійний ключ і перепідключить клієнт Telegram.')) {
        return;
    }

    try {
        const res = await fetch('/api/auth/reset-session', { method: 'POST' });
        const data = await res.json();
        alert(data.message || 'Сесію скинуто');
        fetchStatus();
    } catch (err) {
        alert('Помилка скидання сесії: ' + err);
    }
}

// ==================== QR CODE AUTHENTICATION ====================

function switchAuthMethod(method) {
    currentAuthMethod = method;
    const btnQr = document.getElementById('tabBtnQr');
    const btnPhone = document.getElementById('tabBtnPhone');
    const stepQr = document.getElementById('authStepQr');
    const stepPhone = document.getElementById('authStep1');

    clearAuthErrors();

    if (method === 'qr') {
        if (btnQr) { btnQr.className = 'tg-btn tg-btn-primary'; }
        if (btnPhone) { btnPhone.className = 'tg-btn tg-btn-secondary'; }
        if (stepQr) stepQr.classList.add('active');
        if (stepPhone) stepPhone.classList.remove('active');
        startQrAuth();
    } else {
        stopQrPolling();
        if (btnQr) { btnQr.className = 'tg-btn tg-btn-secondary'; }
        if (btnPhone) { btnPhone.className = 'tg-btn tg-btn-primary'; }
        if (stepQr) stepQr.classList.remove('active');
        if (stepPhone) stepPhone.classList.add('active');
        setTimeout(() => {
            const inp = document.getElementById('authPhoneInput');
            if (inp) inp.focus();
        }, 150);
    }
}

async function startQrAuth() {
    stopQrPolling();
    const loadingText = document.getElementById('qrLoadingText');
    const svgHolder = document.getElementById('qrSvgHolder');
    const qrErrorMsg = document.getElementById('qrErrorMsg');

    if (loadingText) {
        loadingText.style.display = 'block';
        loadingText.textContent = '⏳ Генерація QR-коду...';
    }
    if (svgHolder) {
        svgHolder.style.display = 'none';
        svgHolder.innerHTML = '';
    }
    if (qrErrorMsg) {
        qrErrorMsg.style.display = 'none';
        qrErrorMsg.textContent = '';
    }

    try {
        const res = await fetch('/api/auth/qr/start', { method: 'POST' });
        const data = await res.json();
        if (data.status === 'error') {
            throw new Error(data.error || 'Не вдалося створити QR-код');
        }

        if (data.svg && svgHolder) {
            svgHolder.innerHTML = data.svg;
            svgHolder.style.display = 'block';
            if (loadingText) loadingText.style.display = 'none';
        }

        qrPollInterval = setInterval(checkQrStatus, 2500);
    } catch (err) {
        if (loadingText) loadingText.textContent = '❌ Помилка створення QR';
        if (qrErrorMsg) {
            qrErrorMsg.textContent = err.message || 'Помилка генерації QR-коду';
            qrErrorMsg.style.display = 'block';
        }
    }
}

function stopQrPolling() {
    if (qrPollInterval) {
        clearInterval(qrPollInterval);
        qrPollInterval = null;
    }
}

async function checkQrStatus() {
    try {
        const res = await fetch('/api/auth/qr/status');
        if (!res.ok) return;
        const data = await res.json();

        if (data.status === 'success' && data.user) {
            stopQrPolling();
            handleAuthSuccess(data.user);
        } else if (data.status === 'needs_2fa') {
            stopQrPolling();
            showAuthStep(3);
        } else if (data.status === 'expired') {
            stopQrPolling();
            const qrErrorMsg = document.getElementById('qrErrorMsg');
            if (qrErrorMsg) {
                qrErrorMsg.textContent = '⚠️ Термін дії QR-коду вичерпано. Натисніть «Оновити QR-код».';
                qrErrorMsg.style.display = 'block';
            }
        } else if (data.status === 'error') {
            stopQrPolling();
            const qrErrorMsg = document.getElementById('qrErrorMsg');
            if (qrErrorMsg) {
                qrErrorMsg.textContent = data.error || 'Помилка перевірки QR-коду';
                qrErrorMsg.style.display = 'block';
            }
        }
    } catch (e) {
        console.warn('QR status check error:', e);
    }
}

// ==================== REACTIVATE ALL CHATS ====================

async function handleReactivateAllChats() {
    const btn = document.getElementById('reactivateAllChatsBtn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ Відновлення...';
    }
    try {
        const res = await fetch('/api/chats/reactivate-all', { method: 'POST' });
        const data = await res.json();
        alert(data.message || 'Чати успішно відновлено!');
        await fetchChats();
        await fetchStatus();
    } catch (e) {
        alert('Помилка відновлення чатів: ' + e.message);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '🔄 Відновити всі чати';
        }
    }
}

