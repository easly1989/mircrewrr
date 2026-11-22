// Session expired: any 401 from the admin API sends the user back to the login page.
const _fetch = window.fetch;
window.fetch = async (...args) => {
    const r = await _fetch(...args);
    if (r.status === 401) window.location.href = '/admin/login';
    return r;
};

const { createApp, ref, reactive, computed, onMounted, onUnmounted, watch, nextTick } = Vue;

const app = createApp({
    delimiters: ['[[', ']]'],
    setup() {
        // === Navigation ===
        const startTab = (location.hash || '').slice(1);  // /admin#sites opens the Sites tab
        const currentTab = ref(['dashboard', 'sites', 'config', 'logs'].includes(startTab) ? startTab : 'dashboard');
        const tabs = [
            { id: 'dashboard', label: 'Dashboard', icon: '<svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"/></svg>' },
            { id: 'sites', label: 'Sites', icon: '<svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9"/></svg>' },
            { id: 'config', label: 'Configuration', icon: '<svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>' },
            { id: 'logs', label: 'Logs', icon: '<svg width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>' },
        ];

        // === Look: theme and Dashboard view, both remembered in this browser ===
        const store = {
            get(key, allowed, fallback) {
                try { const v = localStorage.getItem(key); return allowed.includes(v) ? v : fallback; } catch (e) { return fallback; }
            },
            set(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* private mode: not remembered */ } },
        };
        const themes = [
            { id: 'system', label: 'System', icon: 'i-monitor' },
            { id: 'light', label: 'Light', icon: 'i-sun' },
            { id: 'dark', label: 'Dark', icon: 'i-moon' },
        ];
        const theme = ref(store.get('admin-theme', ['system', 'light', 'dark'], 'system'));
        function setTheme(id) {
            theme.value = id;
            store.set('admin-theme', id);
            if (id === 'system') delete document.documentElement.dataset.theme;
            else document.documentElement.dataset.theme = id;
        }
        const dashView = ref(store.get('admin-view', ['detailed', 'simple'], 'detailed'));
        function setView(id) { dashView.value = id; store.set('admin-view', id); }
        const introPlaying = ref(true);  // the path lights up once, when the page first loads
        const statusLoaded = ref(false);
        const slug = (name) => 'site-' + String(name).toLowerCase().replace(/[^a-z0-9]+/g, '-');

        // === Status ===
        const status = ref({ sites: {}, active_sites: [], available_plugins: [] });

        // === Sites ===
        const sites = ref({});
        const plugins = ref({});

        // === Site Modal ===
        const siteModal = reactive({ open: false, editing: null, tab: 'connection' });
        const siteModalTabs = [
            { id: 'connection', label: 'Connection' },
            { id: 'mappings', label: 'Mappings' },
            { id: 'scraping', label: 'Scraping' },
            { id: 'advanced', label: 'Advanced' },
            { id: 'code', label: 'Code Editor' },
        ];
        const siteForm = reactive({});
        const customConfig = reactive({});
        const codeRefs = reactive({});

        // CodeMirror instances for advanced tab
        const advancedEditors = {};

        // === Code Editor ===
        const codeEditor = reactive({
            currentFile: null,
            content: '',
            loading: false,
            saving: false,
            instance: null,
        });
        const codeEditorContainer = ref(null);

        // === Config ===
        const configForm = reactive({
            api_key: '',
            cf_bypass_url: '',
            cf_bypass_timeout: 60000,
            log_level: 'INFO',
            external_indexers: [],
        });
        const MASK = '\u2022'.repeat(8);  // what the server returns instead of an API key
        const configSaved = ref(false);
        const account = reactive({ current: '', username: '', password: '', error: '', busy: false });

        // === Logs ===
        const logs = ref([]);
        const logFilter = ref('');
        const logPaused = ref(false);
        const logSource = ref('');  // '' = this proxy, otherwise an external indexer name
        const logContainer = ref(null);
        let eventSource = null;

        // === External indexers ===
        const externalNames = ref([]);
        const externals = ref({});  // name -> status payload from /admin/api/external/<name>/status
        let fetchingExternal = false;
        const extSettings = reactive({ open: false, name: '', items: [], form: {}, restartRequired: false, busy: false, error: '' });

        // === Plugin Modal ===
        const pluginModal = reactive({ open: false });
        const pluginForm = reactive({ id: '', name: '', description: '' });

        // === Toasts ===
        const toasts = ref([]);

        // === Computed ===
        const currentManifest = computed(() => {
            const pid = siteForm.plugin;
            return pid ? plugins.value[pid] : null;
        });

        const filteredLogs = computed(() => {
            if (!logFilter.value) return logs.value;
            return logs.value.filter(l => l.level === logFilter.value);
        });

        // === Helpers ===
        function toast(message, type = 'success') {
            toasts.value.push({ message, type });
            setTimeout(() => toasts.value.shift(), 3000);
        }

        const pct = (p) => Math.round(Math.min(Math.max(p || 0, 0), 1) * 100);
        const formatSpeed = (bps) => {
            if (!bps) return '0 B/s';
            const u = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
            const i = Math.min(Math.floor(Math.log(bps) / Math.log(1024)), u.length - 1);
            return `${(bps / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`;
        };
        const formatEta = (s) => {
            if (!(s > 0)) return '';
            if (s >= 3600) return `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m`;
            return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
        };
        const stateBadge = (state) => ({
            downloading: 'badge-primary', completed: 'badge-success', queued: 'badge-muted',
            paused: 'badge-warning', error: 'badge-destructive',
        }[state] || 'badge-muted');
        const externalBadge = (ext) => (!ext || !ext.up ? 'badge-destructive' : ext.status === 'ok' ? 'badge-success' : 'badge-warning');
        const externalLabel = (ext) => (!ext ? 'checking' : !ext.up ? 'down' : ext.status);
        const solverLabel = (s) => (!s || !s.configured ? 'Not configured' : s.cookies_valid ? 'Cookies valid' : 'Standby');
        const externalTone = (ext) => (!ext ? 'muted' : !ext.up ? 'bad' : ext.status === 'ok' ? 'ok' : 'warn');
        const toneIcon = (tone) => ({ ok: 'i-check-circle', warn: 'i-alert-triangle', bad: 'i-x-circle', muted: 'i-minus-circle' }[tone] || '');

        // One entry per site for the Dashboard: its state, the four steps of a search through it
        // (callers, proxy, Cloudflare solver, forum session) and what fixes a break.
        const siteList = computed(() => Object.entries(status.value.sites || {}).map(([name, info]) => {
            const blocked = !!info.login_blocked;
            const loggedIn = !!info.logged_in;
            const solved = !!info.cf_valid;
            const failed = !!info.status && info.status !== 'ok';
            let tone = 'ok', stateText = 'Working';
            if (failed) { tone = 'bad'; stateText = 'Error'; }
            else if (blocked) { tone = 'bad'; stateText = 'Login blocked'; }
            else if (!loggedIn) { tone = 'warn'; stateText = 'Not logged in'; }
            // The solver is an external service the panel watches: when it is down, the break is there.
            const solverName = externalNames.value.find(n => externals.value[n]?.solver?.configured);
            const solverDown = !!solverName && !!externals.value[solverName] && !externals.value[solverName].up;
            const steps = [
                { kind: 'Callers', name: 'Prowlarr, Sonarr, Radarr', tone: 'muted', text: '' },
                { kind: 'Proxy', name, tone: failed ? 'bad' : blocked ? 'warn' : 'ok', text: failed ? 'Error' : blocked ? 'Answers 503' : 'Answering' },
                { kind: 'Cloudflare solver', name: solverName || 'Byparr', tone: solverDown ? 'bad' : solved ? 'ok' : 'muted', text: solverDown ? 'Unreachable' : solved ? 'Cookies valid' : 'Standby' },
                { kind: 'Forum session', name: 'mircrew-releases.org', tone: blocked ? 'bad' : loggedIn ? 'ok' : 'muted', text: blocked ? 'Login blocked' : loggedIn ? 'Logged in' : 'Not logged in yet' },
            ];
            const fix = failed ? { text: `The site reports an error: ${info.status}` }
                : solverDown ? { text: `The break is at the Cloudflare solver: ${solverName} is unreachable (${externals.value[solverName].error || 'no answer'}).` }
                : blocked ? { text: `The break is between the solver and the forum. The forum refused the login: ${info.login_blocked}`, action: 'Retry login' }
                : null;
            if (solverDown && tone === 'ok') { tone = 'bad'; stateText = 'Solver down'; }
            const summary = blocked ? `Searches answer 503. The forum said: ${info.login_blocked}`
                : loggedIn ? `Logged in. Thanks cache: ${info.thanks_cached || 0} topics.`
                : 'The next search logs in on its own.';
            return { name, info, tone, icon: toneIcon(tone), stateText, steps, fix, summary, blocked, solverDown };
        }));

        // One verdict for the whole proxy: sites and external services together.
        const verdict = computed(() => {
            const bad = siteList.value.filter(s => s.tone === 'bad');
            const down = externalNames.value.filter(n => externals.value[n] && !externals.value[n].up);
            const warn = siteList.value.filter(s => s.tone === 'warn');
            if (bad.length || down.length) {
                const n = bad.length + down.length;
                const blocked = bad.filter(s => s.blocked);
                const title = blocked.length === bad.length && !down.length
                    ? `${n} site${n > 1 ? 's' : ''} cannot log in`
                    : `${n} thing${n > 1 ? 's' : ''} need${n > 1 ? '' : 's'} attention`;
                const parts = [...bad.map(s => `${s.name}: ${s.blocked ? 'the forum refused the login' : s.solverDown ? 'the Cloudflare solver is unreachable' : 'it reports an error'}`),
                               ...down.map(d => `${d} is unreachable`)];
                return { tone: 'bad', icon: 'i-x-circle', title, text: parts.join('. ') + '.', target: bad[0]?.name || '' };
            }
            if (warn.length) {
                return { tone: 'warn', icon: 'i-alert-triangle', title: `${warn.length} site${warn.length > 1 ? 's are' : ' is'} not logged in`,
                         text: 'The next search logs in on its own.' };
            }
            const n = siteList.value.length;
            return { tone: 'ok', icon: 'i-check-circle', title: 'Everything is working',
                     text: `${n} site${n > 1 ? 's' : ''} answering searches.` };
        });

        function formatUptime(seconds) {
            if (!seconds) return '0s';
            const d = Math.floor(seconds / 86400);
            const h = Math.floor((seconds % 86400) / 3600);
            const m = Math.floor((seconds % 3600) / 60);
            if (d > 0) return `${d}d ${h}h`;
            if (h > 0) return `${h}h ${m}m`;
            return `${m}m`;
        }

        // === Tab switching ===
        function switchTab(tabId) {
            currentTab.value = tabId;
            if (tabId === 'sites') fetchSites();
            if (tabId === 'config') fetchConfig();
            if (tabId === 'dashboard') fetchExternal();
        }

        // === API calls ===
        async function fetchStatus() {
            try {
                const r = await fetch('/admin/api/status');
                status.value = await r.json();
                if (!statusLoaded.value) {
                    statusLoaded.value = true;
                    setTimeout(() => { introPlaying.value = false; }, 1500);  // the path lights up once, after the first data
                }
            } catch (e) { console.error('Status fetch failed:', e); }
        }

        async function fetchExternal() {
            if (fetchingExternal) return;  // a down indexer takes up to 5 s: don't pile requests up
            fetchingExternal = true;
            try {
                await Promise.all(externalNames.value.map(async (name) => {
                    try {
                        const r = await fetch(`/admin/api/external/${encodeURIComponent(name)}/status`);
                        externals.value = { ...externals.value, [name]: await r.json() };
                    } catch (e) {
                        externals.value = { ...externals.value, [name]: { name, up: false, error: 'Panel request failed', queue: [] } };
                    }
                }));
            } finally { fetchingExternal = false; }
        }

        // === External indexer settings ===
        const extSettingsUrl = (name, what) => `/admin/api/external/${encodeURIComponent(name)}/${what}`;

        function showExtSettings(data) {
            extSettings.items = data.settings || [];
            extSettings.form = Object.fromEntries(extSettings.items.map(s => [s.key, s.value]));
            extSettings.restartRequired = !!data.restart_required;
        }

        async function openExtSettings(name) {
            try {
                const r = await fetch(extSettingsUrl(name, 'settings'));
                const data = await r.json();
                if (!r.ok) return toast(data.error || 'Cannot load settings', 'error');
                Object.assign(extSettings, { open: true, name, error: '' });
                showExtSettings(data);
            } catch (e) {
                toast('Cannot load settings', 'error');
            }
        }

        async function saveExtSettings() {
            // Only what changed; an emptied field drops the override and goes back to .env.
            const changes = {};
            for (const s of extSettings.items) {
                const v = String(extSettings.form[s.key] ?? '').trim();
                if (v !== s.value && (v || s.saved)) changes[s.key] = v;
            }
            if (!Object.keys(changes).length) return toast('Nothing to save');
            extSettings.busy = true;
            extSettings.error = '';
            try {
                const r = await fetch(extSettingsUrl(extSettings.name, 'settings'), {
                    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes),
                });
                const data = await r.json();
                if (!r.ok) {
                    extSettings.error = data.error || 'Save failed';
                } else {
                    showExtSettings(data);
                    toast('Settings saved');
                }
            } catch (e) {
                extSettings.error = 'Save failed';
            } finally {
                extSettings.busy = false;
            }
        }

        async function restartExt() {
            extSettings.busy = true;
            try {
                const r = await fetch(extSettingsUrl(extSettings.name, 'restart'), { method: 'POST' });
                const data = await r.json();
                if (!r.ok) return toast(data.error || 'Restart failed', 'error');
                extSettings.open = false;
                toast(`${extSettings.name} is restarting`);
                setTimeout(fetchExternal, 8000);
            } catch (e) {
                toast('Restart failed', 'error');
            } finally {
                extSettings.busy = false;
            }
        }

        async function fetchSites() {
            try {
                const r = await fetch('/admin/api/sites');
                sites.value = await r.json();
            } catch (e) { console.error('Sites fetch failed:', e); }
        }

        async function fetchPlugins() {
            try {
                const r = await fetch('/admin/api/plugins');
                plugins.value = await r.json();
            } catch (e) { console.error('Plugins fetch failed:', e); }
        }

        async function fetchConfig() {
            try {
                const r = await fetch('/admin/api/config');
                const data = await r.json();
                Object.assign(configForm, {
                    api_key: data.api_key || '',
                    cf_bypass_url: data.cf_bypass_url || data.flaresolverr_url || '',
                    cf_bypass_timeout: data.cf_bypass_timeout || data.flaresolverr_timeout || 60000,
                    log_level: data.log_level || 'INFO',
                    external_indexers: (data.external_indexers || []).map(e => ({ ...e })),
                });
                externalNames.value = (data.external_indexers || []).map(e => e.name);
                if (logSource.value && !externalNames.value.includes(logSource.value)) logSource.value = '';
                fetchExternal();
                fetchAccount();
            } catch (e) { console.error('Config fetch failed:', e); }
        }

        async function saveConfig() {
            try {
                const r = await fetch('/admin/api/config', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(configForm),
                });
                if (r.ok) {
                    configSaved.value = true;
                    setTimeout(() => configSaved.value = false, 3000);
                    toast('Configuration saved');
                    fetchConfig();
                } else {
                    toast((await r.json()).error || 'Failed to save config', 'error');
                }
            } catch (e) { toast('Failed to save config', 'error'); }
        }

        async function fetchAccount() {
            try {
                const r = await fetch('/admin/api/account');
                account.current = (await r.json()).username || '';
                if (!account.username) account.username = account.current;
            } catch (e) { console.error('Account fetch failed:', e); }
        }

        async function saveAccount() {
            account.error = '';
            account.busy = true;
            try {
                const r = await fetch('/admin/api/account', {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username: account.username, password: account.password }),
                });
                const data = await r.json();
                if (r.ok) {
                    account.current = data.username;
                    account.password = '';
                    toast('Account saved');
                } else {
                    account.error = data.error || 'Failed to save the account';
                }
            } catch (e) { account.error = 'Failed to save the account'; }
            account.busy = false;
        }

        function addIndexer() {
            configForm.external_indexers.push({ name: '', url: '', api_key: '' });
        }

        function clearMask(ind) {
            if (ind.api_key === MASK) ind.api_key = '';  // typing must not append to the placeholder
        }

        // === Site Modal ===
        function resetSiteForm() {
            Object.keys(siteForm).forEach(k => delete siteForm[k]);
            Object.keys(customConfig).forEach(k => delete customConfig[k]);
            Object.assign(siteForm, { name: '', plugin: '', base_url: '', username: '', password: '' });
            // Destroy advanced editors
            Object.keys(advancedEditors).forEach(k => { delete advancedEditors[k]; });
            if (codeEditor.instance) {
                codeEditor.instance = null;
            }
            codeEditor.currentFile = null;
            codeEditor.content = '';
        }

        function openAddSite() {
            resetSiteForm();
            siteModal.open = true;
            siteModal.editing = null;
            siteModal.tab = 'connection';
        }

        function openEditSite(name, cfg) {
            resetSiteForm();
            const pluginId = cfg.plugin || cfg.type || '';
            Object.assign(siteForm, {
                name: name,
                plugin: pluginId,
                base_url: cfg.base_url || '',
                username: cfg.username || '',
                password: '',
            });

            // Load custom config into editable format
            const manifest = plugins.value[pluginId];
            if (manifest && manifest.custom_config) {
                const siteCustom = cfg.custom || {};
                for (const [key, schema] of Object.entries(manifest.custom_config)) {
                    const value = siteCustom[key] ?? schema.default;
                    if (schema.type === 'key_value_map' && value) {
                        customConfig[key] = Object.entries(value).map(([k, v]) => ({ key: String(k), value: String(v) }));
                    } else if (schema.type === 'integer_list' && value) {
                        customConfig[key] = [...value];
                    } else if (schema.type === 'code') {
                        customConfig[key] = value || '';
                    }
                }
            }

            siteModal.open = true;
            siteModal.editing = name;
            siteModal.tab = 'connection';
        }

        function closeSiteModal() {
            siteModal.open = false;
            siteModal.editing = null;
            resetSiteForm();
        }

        function onPluginChange() {
            const manifest = currentManifest.value;
            if (!manifest) return;
            // Set default values from manifest
            for (const [key, schema] of Object.entries(manifest.config_schema || {})) {
                if (schema.default && !siteForm[key]) {
                    siteForm[key] = schema.default;
                }
            }
        }

        // Custom config helpers
        function addKvRow(key) {
            if (!customConfig[key]) customConfig[key] = [];
            customConfig[key].push({ key: '', value: '' });
        }

        function removeKvRow(key, index) {
            customConfig[key].splice(index, 1);
        }

        function addListItem(key) {
            if (!customConfig[key]) customConfig[key] = [];
            customConfig[key].push(0);
        }

        function buildCustomPayload() {
            const manifest = currentManifest.value;
            if (!manifest || !manifest.custom_config) return {};

            const result = {};
            for (const [key, schema] of Object.entries(manifest.custom_config)) {
                if (schema.type === 'key_value_map' && customConfig[key]) {
                    const obj = {};
                    for (const row of customConfig[key]) {
                        if (row.key !== '') obj[row.key] = row.value;
                    }
                    result[key] = obj;
                } else if (schema.type === 'integer_list' && customConfig[key]) {
                    result[key] = customConfig[key].map(v => parseInt(v) || 0);
                } else if (schema.type === 'code') {
                    // Read from CodeMirror instance if exists
                    if (advancedEditors[key]) {
                        result[key] = advancedEditors[key].getValue();
                    } else if (customConfig[key] !== undefined) {
                        result[key] = customConfig[key];
                    }
                }
            }
            return result;
        }

        async function saveSite() {
            if (siteModal.editing) {
                const payload = {
                    base_url: siteForm.base_url,
                    username: siteForm.username,
                };
                if (siteForm.password) payload.password = siteForm.password;
                payload.custom = buildCustomPayload();

                try {
                    const r = await fetch(`/admin/api/sites/${siteModal.editing}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload),
                    });
                    const data = await r.json();
                    if (r.ok) {
                        toast(data.warning || 'Site updated');
                        fetchSites();
                        fetchStatus();
                    } else {
                        toast(data.error || 'Update failed', 'error');
                    }
                } catch (e) { toast('Failed to update site', 'error'); }
            } else {
                const payload = {
                    name: siteForm.name,
                    plugin: siteForm.plugin,
                    base_url: siteForm.base_url,
                    username: siteForm.username,
                    password: siteForm.password,
                };
                try {
                    const r = await fetch('/admin/api/sites', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload),
                    });
                    const data = await r.json();
                    if (r.ok || r.status === 201) {
                        toast(data.warning || 'Site created');
                        fetchSites();
                        fetchStatus();
                    } else {
                        toast(data.error || 'Failed to create site', 'error');
                    }
                } catch (e) { toast('Failed to create site', 'error'); }
            }
            closeSiteModal();
        }

        async function toggleSite(name) {
            try {
                const r = await fetch(`/admin/api/sites/${name}/toggle`, { method: 'POST' });
                const data = await r.json();
                if (r.ok) {
                    toast(`Site ${data.enabled ? 'enabled' : 'disabled'}`);
                    fetchSites();
                    fetchStatus();
                }
            } catch (e) { toast('Toggle failed', 'error'); }
        }

        async function retryLogin(name) {
            try {
                const r = await fetch(`/admin/api/sites/${name}/retry-login`, { method: 'POST' });
                const data = await r.json();
                if (r.ok && data.ok) toast('Login succeeded');
                else toast(data.error || 'Login failed, see the logs', 'error');
                fetchStatus();
            } catch (e) { toast('Retry login failed', 'error'); }
        }

        async function deleteSite(name) {
            if (!confirm(`Delete site "${name}"? This action cannot be undone.`)) return;
            try {
                const r = await fetch(`/admin/api/sites/${name}`, { method: 'DELETE' });
                if (r.ok) {
                    toast('Site deleted');
                    fetchSites();
                    fetchStatus();
                }
            } catch (e) { toast('Delete failed', 'error'); }
        }

        // === Plugin Creation ===
        function openCreatePlugin() {
            Object.assign(pluginForm, { id: '', name: '', description: '' });
            pluginModal.open = true;
        }

        function closePluginModal() {
            pluginModal.open = false;
        }

        async function createPlugin() {
            if (!pluginForm.id || !pluginForm.name) {
                toast('Plugin ID and Name are required', 'error');
                return;
            }
            try {
                const r = await fetch('/admin/api/plugins', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(pluginForm),
                });
                const data = await r.json();
                if (r.ok || r.status === 201) {
                    toast('Plugin created');
                    fetchPlugins();
                    closePluginModal();
                } else {
                    toast(data.error || 'Failed to create plugin', 'error');
                }
            } catch (e) {
                toast('Failed to create plugin', 'error');
            }
        }

        // === Code Editor (plugin files) ===
        async function loadPluginFile(file) {
            const pluginId = siteForm.plugin;
            if (!pluginId) return;

            codeEditor.loading = true;
            codeEditor.currentFile = file.path;

            try {
                const r = await fetch(`/admin/api/plugins/${pluginId}/files/${file.path}`);
                const data = await r.json();
                if (r.ok) {
                    codeEditor.content = data.content;
                    // Set loading=false BEFORE nextTick so the container ref is rendered
                    codeEditor.loading = false;
                    await nextTick();
                    initCodeEditor(file);
                } else {
                    codeEditor.loading = false;
                    toast(data.error || 'Failed to load file', 'error');
                }
            } catch (e) {
                codeEditor.loading = false;
                toast('Failed to load file', 'error');
            }
        }

        function initCodeEditor(file) {
            if (codeEditor.instance) {
                codeEditor.instance.toTextArea();
                codeEditor.instance = null;
            }

            const container = codeEditorContainer.value;
            if (!container) return;

            // Create textarea
            container.innerHTML = '';
            const textarea = document.createElement('textarea');
            container.appendChild(textarea);
            textarea.value = codeEditor.content;

            const modeMap = { python: 'python', xml: 'xml', json: 'application/json' };
            codeEditor.instance = CodeMirror.fromTextArea(textarea, {
                mode: modeMap[file.language] || 'python',
                theme: 'panel',
                lineNumbers: true,
                lineWrapping: true,
                tabSize: 4,
                indentWithTabs: false,
                indentUnit: 4,
                matchBrackets: true,
                autoCloseBrackets: true,
            });
            codeEditor.instance.setSize('100%', '400px');
        }

        async function savePluginFile() {
            if (!codeEditor.instance || !codeEditor.currentFile) return;
            const pluginId = siteForm.plugin;
            codeEditor.saving = true;

            try {
                const r = await fetch(`/admin/api/plugins/${pluginId}/files/${codeEditor.currentFile}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: codeEditor.instance.getValue() }),
                });
                const data = await r.json();
                if (r.ok) {
                    toast('File saved');
                } else {
                    toast(data.error || 'Save failed', 'error');
                }
            } catch (e) {
                toast('Failed to save file', 'error');
            } finally {
                codeEditor.saving = false;
            }
        }

        // Initialize CodeMirror for advanced tab code fields
        watch(() => siteModal.tab, async (newTab) => {
            if (newTab === 'advanced' && currentManifest.value) {
                await nextTick();
                const manifest = currentManifest.value;
                for (const [key, schema] of Object.entries(manifest.custom_config || {})) {
                    if (schema.type === 'code' && schema.group === 'advanced' && codeRefs[key]) {
                        if (advancedEditors[key]) continue; // Already initialized

                        const el = codeRefs[key];
                        el.innerHTML = '';
                        const textarea = document.createElement('textarea');
                        el.appendChild(textarea);
                        textarea.value = customConfig[key] || schema.default || '';

                        const modeMap = { python: 'python', xml: 'xml', json: 'application/json' };
                        advancedEditors[key] = CodeMirror.fromTextArea(textarea, {
                            mode: modeMap[schema.language] || 'text/plain',
                            theme: 'panel',
                            lineNumbers: true,
                            lineWrapping: true,
                            tabSize: 4,
                            indentWithTabs: false,
                        });
                        advancedEditors[key].setSize('100%', '250px');
                    }
                }
            }
        });

        // === Log Stream ===
        function connectLogStream() {
            if (eventSource) eventSource.close();
            const src = logSource.value;
            eventSource = new EventSource(src ? `/admin/api/external/${encodeURIComponent(src)}/logs` : '/admin/api/logs');
            eventSource.onmessage = (e) => {
                if (logPaused.value) return;
                try {
                    const entries = JSON.parse(e.data);
                    logs.value.push(...entries);
                    if (logs.value.length > 1000) {
                        logs.value = logs.value.slice(-500);
                    }
                    nextTick(() => {
                        if (logContainer.value) {
                            logContainer.value.scrollTop = logContainer.value.scrollHeight;
                        }
                    });
                } catch (err) { console.error('Log parse error:', err); }
            };
            eventSource.onerror = () => {
                setTimeout(connectLogStream, 5000);
            };
        }

        watch(logSource, () => {
            logs.value = [];
            connectLogStream();
        });

        // === Lifecycle ===
        let statusInterval, externalInterval;

        onMounted(() => {
            fetchStatus();
            fetchSites();
            fetchPlugins();
            fetchConfig();
            connectLogStream();
            statusInterval = setInterval(() => {
                fetchStatus();
                if (currentTab.value === 'sites') fetchSites();
            }, 10000);
            externalInterval = setInterval(() => {
                if (currentTab.value === 'dashboard' || currentTab.value === 'logs') fetchExternal();
            }, 5000);
        });

        onUnmounted(() => {
            if (eventSource) eventSource.close();
            clearInterval(statusInterval);
            clearInterval(externalInterval);
        });

        return {
            // Nav
            currentTab, tabs, switchTab,
            // Look
            themes, theme, setTheme, dashView, setView, introPlaying, statusLoaded, slug,
            // Status
            status, siteList, verdict, toneIcon, externalTone,
            // Sites
            sites, plugins,
            siteModal, siteModalTabs,
            siteForm, customConfig, codeRefs,
            currentManifest,
            openAddSite, openEditSite, closeSiteModal,
            onPluginChange,
            addKvRow, removeKvRow, addListItem,
            saveSite, toggleSite, deleteSite, retryLogin,
            // Plugin Creation
            pluginModal, pluginForm,
            openCreatePlugin, closePluginModal, createPlugin,
            // Code Editor
            codeEditor, codeEditorContainer,
            loadPluginFile, savePluginFile,
            // Config
            configForm, configSaved, saveConfig, addIndexer, clearMask,
            account, saveAccount,
            // External indexers
            externalNames, externals, extSettings, openExtSettings, saveExtSettings, restartExt,
            pct, formatSpeed, formatEta, stateBadge, externalBadge, externalLabel, solverLabel,
            // Logs
            logs, logFilter, logPaused, logSource, logContainer, filteredLogs,
            // Toasts
            toasts,
            // Helpers
            formatUptime,
        };
    }
});

app.mount('#app');
