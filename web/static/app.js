// ==========================================================
// TELEGRAM AUTO-POSTER DASHBOARD APP LOGIC (MULTI-POSTS)
// ==========================================================

let activeTab = 'chats';
let isMasterRunning = false;
let postsList = [];
let currentPostId = null;
let currentSourceMsgId = null;
let currentSourceChat = 'me';
let foundChatsData = [];

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
        const data = await res.json();

        // Update User & Premium info
        const userSubtitle = document.getElementById('userSubtitle');
        const premiumBadge = document.getElementById('premiumBadge');
        if (data.is_authorized && data.user) {
            userSubtitle.textContent = `👤 ${data.user.name} (@${data.user.username || 'без юзернейму'})`;
            if (data.user.is_premium) {
                premiumBadge.style.display = 'inline-block';
            }
        } else {
            userSubtitle.textContent = `⚠️ Не авторизовано (запустіть python auth.py)`;
        }

        // Update Master Toggle
        isMasterRunning = data.is_running;
        const btn = document.getElementById('masterToggleBtn');
        const text = document.getElementById('masterToggleText');
        if (isMasterRunning) {
            btn.className = 'master-toggle-btn running';
            text.textContent = 'Розсилка активна (Увімкнено)';
        } else {
            btn.className = 'master-toggle-btn paused';
            text.textContent = 'Розсилка на паузі (Вимкнено)';
        }

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
        console.error('Error fetching status:', err);
    }
}

async function toggleMasterPoster() {
    try {
        const res = await fetch('/api/settings/toggle', { method: 'POST' });
        const data = await res.json();
        isMasterRunning = data.is_running;
        fetchStatus();
    } catch (err) {
        console.error('Error toggling poster:', err);
    }
}

// ==================== CHATS TABLE ====================

async function fetchChats() {
    try {
        const res = await fetch('/api/chats');
        const chats = await res.json();
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
            statusBadge = `<span class="badge badge-paused">⏸️ Вимкнено</span>`;
        } else if (chat.status === 'slowmode_wait') {
            statusBadge = `<span class="badge badge-slowmode">⏳ SlowMode</span>`;
        } else if (chat.status === 'error' || chat.status === 'restricted') {
            statusBadge = `<span class="badge badge-error" title="${chat.last_error || ''}">🚫 ${chat.last_error || 'Помилка'}</span>`;
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
            const diffMinutes = Math.round((nextDate - now) / 60000);
            if (diffMinutes <= 0) {
                nextPostLabel = `<span style="color: var(--accent-green); font-weight: 600;">Готовий до відправки</span>`;
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
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        alert('🛡️ Всі налаштування безпеки та анти-флуду збережено!');
        fetchStatus();
    } catch (err) {
        alert('Помилка оновлення налаштувань: ' + err);
    }
}

// ==================== CONTEXTUAL AI DISCOVERY & QUALIFICATION ====================

let activeDiscoverySessionId = null;
let discoveryPollingInterval = null;
let currentDiscoveryCandidates = [];
let activeDiscoveryFilter = 'all';
let selectedDiscoveryPeers = new Set();

function setFinderQuery(q) {
    document.getElementById('finderQueryInput').value = q;
    handleFinderSearch(new Event('submit'));
}

async function handleFinderSearch(e) {
    if (e && e.preventDefault) e.preventDefault();
    const query = document.getElementById('finderQueryInput').value.trim();
    if (!query) return;

    const btn = document.getElementById('finderSubmitBtn');
    const stepper = document.getElementById('discoveryStepper');
    const resultsCard = document.getElementById('finderResultsCard');
    const grid = document.getElementById('discoveryCardsGrid');

    btn.disabled = true;
    btn.innerHTML = '⏳ <span>AI аналізує запит...</span>';
    stepper.style.display = 'block';
    resultsCard.style.display = 'block';
    grid.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; color: var(--text-secondary); padding: 40px;">⏳ Пошук груп у Telegram та аналіз семантики через Gemini 3.5 Flash Lite...</div>';

    updateDiscoveryProgress(1, 'AI аналізує запит та генерує семантичні підзапити...');

    if (discoveryPollingInterval) clearInterval(discoveryPollingInterval);
    selectedDiscoveryPeers.clear();
    updateSelectedCountUI();

    try {
        const res = await fetch('/api/discovery/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: query, limit: 30 })
        });
        const data = await res.json();
        activeDiscoverySessionId = data.session_id;

        // Start Polling search session progress
        discoveryPollingInterval = setInterval(() => pollDiscoverySession(activeDiscoverySessionId), 2000);
    } catch (err) {
        btn.disabled = false;
        btn.innerHTML = '🧠 Знайти та кваліфікувати';
        grid.innerHTML = `<div style="grid-column: 1 / -1; text-align: center; color: var(--accent-red); padding: 30px;">Помилка запуску пошуку: ${err}</div>`;
    }
}

async function pollDiscoverySession(sessionId) {
    try {
        const res = await fetch(`/api/discovery/results/${sessionId}`);
        const data = await res.json();

        if (data.search) {
            const status = data.search.status;
            if (status === 'searching') {
                updateDiscoveryProgress(2, `Пошук каналів та повідомлень у Telegram API... (знайдено: ${data.search.total_candidates || 0})`);
            } else if (status === 'scoring') {
                updateDiscoveryProgress(3, `Розрахунок семантичної подібності та AI-кваліфікація кандидатів...`);
            } else if (status === 'completed' || status === 'failed') {
                clearInterval(discoveryPollingInterval);
                const btn = document.getElementById('finderSubmitBtn');
                btn.disabled = false;
                btn.innerHTML = '🧠 Знайти та кваліфікувати';

                if (status === 'completed') {
                    updateDiscoveryProgress(4, `Готово! Знайдено ${data.results.length} кваліфікованих цільових груп.`);
                    currentDiscoveryCandidates = data.results || [];
                    renderDiscoveryCards(currentDiscoveryCandidates);
                } else {
                    document.getElementById('discoveryStatusText').textContent = '⚠️ ' + (data.search.error_message || 'Помилка пошуку');
                }
            }
        }
    } catch (err) {
        console.error('Polling error:', err);
    }
}

function updateDiscoveryProgress(step, text) {
    const fill = document.getElementById('discoveryProgressFill');
    const statusText = document.getElementById('discoveryStatusText');
    const widthPct = step === 1 ? '25%' : step === 2 ? '50%' : step === 3 ? '75%' : '100%';
    if (fill) fill.style.width = widthPct;
    if (statusText) statusText.textContent = text;

    for (let i = 1; i <= 4; i++) {
        const el = document.getElementById(`step-${i}`);
        if (el) {
            el.classList.toggle('active', i <= step);
        }
    }
}

function setDiscoveryFilter(filterType) {
    activeDiscoveryFilter = filterType;
    document.querySelectorAll('.filter-chip').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-filter') === filterType);
    });
    renderDiscoveryCards(currentDiscoveryCandidates);
}

function renderDiscoveryCards(results) {
    const grid = document.getElementById('discoveryCardsGrid');
    const title = document.getElementById('finderResultsCountTitle');

    let filtered = results;
    if (activeDiscoveryFilter === 'group') {
        filtered = results.filter(r => r.chat_type === 'group' || r.chat_type === 'supergroup');
    } else if (activeDiscoveryFilter === 'channel') {
        filtered = results.filter(r => r.chat_type === 'channel');
    }

    if (title) title.textContent = `Кваліфіковані результати (${filtered.length})`;

    if (!filtered.length) {
        grid.innerHTML = `<div style="grid-column: 1 / -1; text-align: center; color: var(--text-secondary); padding: 40px;">За цим фільтром нічого не знайдено.</div>`;
        return;
    }

    grid.innerHTML = filtered.map(item => {
        const isAdded = item.status === 'added_to_posting';
        const isChecked = selectedDiscoveryPeers.has(item.telegram_peer);
        const usernameClean = item.telegram_peer.replace(/^@/, '');

        return `
            <div class="discovery-card ${isAdded ? 'added' : ''}" id="disc-card-${item.id}">
                <div class="discovery-card-header">
                    <div style="display: flex; gap: 10px; align-items: center; min-width: 0;">
                        <input type="checkbox" class="discovery-checkbox" data-id="${item.id}" data-peer="${item.telegram_peer}" data-title="${escapeHtml(item.title)}" onchange="toggleDiscoverySelection(this)" ${isChecked ? 'checked' : ''} ${isAdded ? 'disabled' : ''}>
                        <div class="discovery-card-title">${escapeHtml(item.title)}</div>
                    </div>
                    <div class="discovery-score-badge ${item.classification}">${item.final_score}% збіг</div>
                </div>

                <div class="discovery-peer">${escapeHtml(item.telegram_peer)} • ${item.chat_type} • 👥 ${(item.members_count || 0).toLocaleString()} учасників</div>

                ${item.description ? `<div class="discovery-desc">${escapeHtml(item.description)}</div>` : ''}

                <!-- AI Metrics Bar -->
                <div class="metric-row">
                    <div class="metric-item">
                        <div style="display: flex; justify-content: space-between;"><span>Релевантність</span> <span>${item.relevance_score}%</span></div>
                        <div class="metric-bar-bg"><div class="metric-bar-fill" style="width: ${item.relevance_score}%; background: var(--accent-blue);"></div></div>
                    </div>
                    <div class="metric-item">
                        <div style="display: flex; justify-content: space-between;"><span>Promo Score</span> <span>${item.promo_score}%</span></div>
                        <div class="metric-bar-bg"><div class="metric-bar-fill" style="width: ${item.promo_score}%; background: #a855f7;"></div></div>
                    </div>
                    <div class="metric-item">
                        <div style="display: flex; justify-content: space-between;"><span>Активність</span> <span>${item.activity_score}%</span></div>
                        <div class="metric-bar-bg"><div class="metric-bar-fill" style="width: ${item.activity_score}%; background: var(--accent-green);"></div></div>
                    </div>
                </div>

                <!-- AI Reason callout -->
                <div class="ai-reason-box">
                    💡 <strong>AI висновок:</strong> ${escapeHtml(item.ai_reason || 'Підходить за тематикою та інтентом')}
                </div>

                <!-- Actions -->
                <div class="discovery-card-actions">
                    <div style="display: flex; gap: 8px;">
                        <a href="https://t.me/${usernameClean}" target="_blank" class="tg-btn tg-btn-secondary tg-btn-sm" style="text-decoration: none;">
                            🔗 Відкрити
                        </a>
                        <button type="button" class="tg-btn tg-btn-secondary tg-btn-sm" onclick="hideDiscoveryCandidate('${item.id}')" title="Не показувати більше">
                            👎 Приховати
                        </button>
                    </div>

                    ${isAdded ? `
                        <span style="color: var(--accent-green); font-size: 12px; font-weight: 600;">
                            ✅ Додано в розсилку
                        </span>
                    ` : `
                        <button type="button" class="tg-btn tg-btn-primary tg-btn-sm" id="btn-add-${item.id}" onclick="addDiscoverySingleChat('${item.id}', '${item.telegram_peer}', '${escapeHtml(item.title)}')">
                            + Додати в розсилку
                        </button>
                    `}
                </div>
            </div>
        `;
    }).join('');
}

function toggleDiscoverySelection(cb) {
    const peer = cb.getAttribute('data-peer');
    if (cb.checked) {
        selectedDiscoveryPeers.add(peer);
    } else {
        selectedDiscoveryPeers.delete(peer);
    }
    updateSelectedCountUI();
}

function updateSelectedCountUI() {
    const count = selectedDiscoveryPeers.size;
    const btn = document.getElementById('batchAddFoundBtn');
    const span = document.getElementById('selectedCountSpan');
    if (span) span.textContent = count;
    if (btn) btn.style.display = (count > 0) ? 'inline-flex' : 'none';
}

async function addDiscoverySingleChat(resultId, peer, title) {
    const interval = parseInt(document.getElementById('finderDefaultInterval').value, 10);
    const postSelectVal = document.getElementById('finderDefaultPost')?.value;
    const targetPostId = (postSelectVal === 'default') ? null : postSelectVal;
    
    const btn = document.getElementById(`btn-add-${resultId}`);
    if (btn) {
        btn.disabled = true;
        btn.textContent = '⏳ Додаємо...';
    }

    try {
        const res = await fetch('/api/discovery/add-to-posting', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                result_id: resultId,
                chat_peer: peer,
                interval_minutes: interval,
                title: title,
                post_id: targetPostId
            })
        });
        const data = await res.json();
        if (data.status === 'ok') {
            if (btn) {
                btn.outerHTML = `<span style="color: var(--accent-green); font-size: 12px; font-weight: 600;">✅ Додано в розсилку</span>`;
            }
            fetchChats();
            fetchPosts();
            fetchStatus();
        } else {
            alert('Помилка при додаванні: ' + (data.message || 'Невідома помилка'));
            if (btn) {
                btn.disabled = false;
                btn.textContent = '+ Додати в розсилку';
            }
        }
    } catch (err) {
        alert('Помилка мережі: ' + err);
        if (btn) {
            btn.disabled = false;
            btn.textContent = '+ Додати в розсилку';
        }
    }
}

async function hideDiscoveryCandidate(resultId) {
    try {
        await fetch('/api/discovery/hide', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ result_id: resultId })
        });
        const card = document.getElementById(`disc-card-${resultId}`);
        if (card) {
            card.style.opacity = '0';
            card.style.transform = 'scale(0.95)';
            setTimeout(() => card.remove(), 200);
        }
    } catch (err) {
        console.error('Error hiding candidate:', err);
    }
}

async function handleBatchAddDiscovery() {
    if (!selectedDiscoveryPeers.size) return;

    const interval = parseInt(document.getElementById('finderDefaultInterval').value, 10);
    const postSelectVal = document.getElementById('finderDefaultPost')?.value;
    const targetPostId = (postSelectVal === 'default') ? null : postSelectVal;

    const checkboxes = document.querySelectorAll('.discovery-checkbox:checked:not(:disabled)');
    const itemsToAdd = [];

    checkboxes.forEach(cb => {
        itemsToAdd.push({
            result_id: cb.getAttribute('data-id'),
            chat_peer: cb.getAttribute('data-peer'),
            interval_minutes: interval,
            title: cb.getAttribute('data-title') || '',
            post_id: targetPostId
        });
    });

    const btn = document.getElementById('batchAddFoundBtn');
    btn.disabled = true;
    btn.textContent = `⏳ Додаємо ${itemsToAdd.length} чатів...`;

    try {
        const res = await fetch('/api/discovery/batch-add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ items: itemsToAdd })
        });
        const data = await res.json();
        alert(`🎉 Успішно додано ${data.added_count} груп до вашої розсилки!`);
        selectedDiscoveryPeers.clear();
        updateSelectedCountUI();
        fetchChats();
        fetchPosts();
        fetchStatus();
        if (activeDiscoverySessionId) {
            const r = await fetch(`/api/discovery/results/${activeDiscoverySessionId}`);
            const d = await r.json();
            renderDiscoveryCards(d.results || []);
        }
    } catch (err) {
        alert('Помилка пакетного додавання: ' + err);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '📥 Додати всі вибрані (<span id="selectedCountSpan">0</span>)';
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
    document.getElementById(modalId).classList.add('active');
}

function closeModal() {
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
