const { createApp, ref, reactive, computed, onMounted, onUnmounted, watch, nextTick } = Vue;

const app = createApp({
    delimiters: ['[[', ']]'],
    setup() {
        // Navigation
        const currentTab = ref('dashboard');
        const tabs = [
            { id: 'dashboard', label: 'Dashboard', icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"/></svg>' },
            { id: 'sites', label: 'Sites', icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9"/></svg>' },
            { id: 'config', label: 'Configuration', icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>' },
            { id: 'logs', label: 'Logs', icon: '<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>' },
        ];

        // Status
        const status = ref({ sites: {}, active_sites: [], available_site_types: [] });

        // Sites
        const sites = ref({});
        const showAddSite = ref(false);
        const editingSite = ref(null);
        const siteForm = reactive({ name: '', type: 'mircrew', base_url: '', username: '', password: '' });

        // Config
        const configForm = reactive({ api_key: '', flaresolverr_url: '', flaresolverr_timeout: 60000, log_level: 'INFO' });
        const configSaved = ref(false);

        // Logs
        const logs = ref([]);
        const logFilter = ref('');
        const logPaused = ref(false);
        const logContainer = ref(null);
        let eventSource = null;

        // Toasts
        const toasts = ref([]);

        const filteredLogs = computed(() => {
            if (!logFilter.value) return logs.value;
            return logs.value.filter(l => l.level === logFilter.value);
        });

        function toast(message, type = 'success') {
            toasts.value.push({ message, type });
            setTimeout(() => toasts.value.shift(), 3000);
        }

        function formatUptime(seconds) {
            if (!seconds) return '0s';
            const d = Math.floor(seconds / 86400);
            const h = Math.floor((seconds % 86400) / 3600);
            const m = Math.floor((seconds % 3600) / 60);
            if (d > 0) return `${d}d ${h}h`;
            if (h > 0) return `${h}h ${m}m`;
            return `${m}m`;
        }

        function logLevelClass(level) {
            const classes = {
                'DEBUG': 'text-gray-500',
                'INFO': 'text-blue-400',
                'WARNING': 'text-yellow-400',
                'ERROR': 'text-red-400',
            };
            return classes[level] || 'text-gray-400';
        }

        async function fetchStatus() {
            try {
                const r = await fetch('/admin/api/status');
                status.value = await r.json();
            } catch (e) { console.error('Status fetch failed:', e); }
        }

        async function fetchSites() {
            try {
                const r = await fetch('/admin/api/sites');
                sites.value = await r.json();
            } catch (e) { console.error('Sites fetch failed:', e); }
        }

        async function fetchConfig() {
            try {
                const r = await fetch('/admin/api/config');
                const data = await r.json();
                Object.assign(configForm, {
                    api_key: data.api_key || '',
                    flaresolverr_url: data.flaresolverr_url || '',
                    flaresolverr_timeout: data.flaresolverr_timeout || 60000,
                    log_level: data.log_level || 'INFO',
                });
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
                }
            } catch (e) { toast('Failed to save config', 'error'); }
        }

        function editSite(name, cfg) {
            editingSite.value = name;
            Object.assign(siteForm, {
                name: name,
                type: cfg.type,
                base_url: cfg.base_url || '',
                username: cfg.username || '',
                password: '',
            });
        }

        function closeModal() {
            showAddSite.value = false;
            editingSite.value = null;
            Object.assign(siteForm, { name: '', type: 'mircrew', base_url: '', username: '', password: '' });
        }

        async function saveSite() {
            if (editingSite.value) {
                const payload = { ...siteForm };
                if (!payload.password) delete payload.password;
                try {
                    const r = await fetch(`/admin/api/sites/${editingSite.value}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload),
                    });
                    if (r.ok) { toast('Site updated'); fetchSites(); }
                } catch (e) { toast('Failed to update site', 'error'); }
            } else {
                try {
                    const r = await fetch('/admin/api/sites', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(siteForm),
                    });
                    const data = await r.json();
                    if (r.ok || r.status === 201) {
                        toast(data.warning || 'Site added');
                        fetchSites();
                        fetchStatus();
                    } else {
                        toast(data.error || 'Failed to add site', 'error');
                    }
                } catch (e) { toast('Failed to add site', 'error'); }
            }
            closeModal();
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

        async function deleteSite(name) {
            if (!confirm(`Delete site "${name}"?`)) return;
            try {
                const r = await fetch(`/admin/api/sites/${name}`, { method: 'DELETE' });
                if (r.ok) {
                    toast('Site deleted');
                    fetchSites();
                    fetchStatus();
                }
            } catch (e) { toast('Delete failed', 'error'); }
        }

        function connectLogStream() {
            if (eventSource) eventSource.close();
            eventSource = new EventSource('/admin/api/logs');
            eventSource.onmessage = (e) => {
                if (logPaused.value) return;
                try {
                    const entries = JSON.parse(e.data);
                    logs.value.push(...entries);
                    // Keep max 1000 entries in UI
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

        // Polling status every 10s
        let statusInterval;

        onMounted(() => {
            fetchStatus();
            fetchSites();
            fetchConfig();
            connectLogStream();
            statusInterval = setInterval(() => {
                fetchStatus();
                if (currentTab.value === 'sites') fetchSites();
            }, 10000);
        });

        onUnmounted(() => {
            if (eventSource) eventSource.close();
            clearInterval(statusInterval);
        });

        return {
            currentTab, tabs,
            status, sites,
            showAddSite, editingSite, siteForm,
            configForm, configSaved,
            logs, logFilter, logPaused, logContainer, filteredLogs,
            toasts,
            formatUptime, logLevelClass,
            fetchSites, fetchStatus, fetchConfig,
            saveConfig, editSite, closeModal, saveSite,
            toggleSite, deleteSite,
        };
    }
});

app.mount('#app');
