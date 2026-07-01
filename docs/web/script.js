// ============================================
// Cyrene Documentation — JavaScript
// Navigation, Search, i18n, Theme, Mobile Menu
// ============================================

(function() {
  'use strict';

  // --- Page data ---
  const PAGES = [
    'overview', 'installation', 'configuration',
    'usage-workbench', 'usage-legacy', 'usage-cli',
    'architecture', 'memory', 'knowledge',
    'browser', 'mcp', 'scheduler', 'subagents', 'search',
    'quick-chat',
    'development', 'cli-reference'
  ];

  const DEFAULT_PAGE = 'overview';

  // --- i18n translations ---
  const TRANSLATIONS = {
    // Sidebar nav — section labels
    'nav.getting-started': { zh: '入门', en: 'Getting Started' },
    'nav.usage': { zh: '使用', en: 'Usage' },
    'nav.core-concepts': { zh: '核心概念', en: 'Core Concepts' },
    'nav.features': { zh: '功能', en: 'Features' },
    'nav.development': { zh: '开发', en: 'Development' },
    // Sidebar nav — page links
    'nav.overview': { zh: '概述', en: 'Overview' },
    'nav.installation': { zh: '安装', en: 'Installation' },
    'nav.configuration': { zh: '配置', en: 'Configuration' },
    'nav.usage-workbench': { zh: 'Workbench UI', en: 'Workbench UI' },
    'nav.usage-legacy': { zh: 'Legacy UI', en: 'Legacy UI' },
    'nav.usage-cli': { zh: 'CLI', en: 'CLI' },
    'nav.architecture': { zh: '双阶段循环', en: 'Two-Phase Loop' },
    'nav.memory': { zh: '记忆系统', en: 'Memory System' },
    'nav.subagents': { zh: '子代理系统', en: 'Sub-agents' },
    'nav.knowledge': { zh: '知识库', en: 'Knowledge' },
    'nav.browser': { zh: '浏览器实况', en: 'Browser Live View' },
    'nav.mcp': { zh: 'MCP 协议', en: 'MCP Protocol' },
    'nav.scheduler': { zh: '任务调度', en: 'Task Scheduler' },
    'nav.search': { zh: '搜索', en: 'Search' },
    'nav.quick-chat': { zh: '快捷对话', en: 'Quick Chat' },
    'nav.development': { zh: '开发指南', en: 'Development' },
    'nav.cli-reference': { zh: '命令参考', en: 'CLI Reference' },
    // Sidebar extras
    'sidebar.github': { zh: 'GitHub', en: 'GitHub' },
    'sidebar.aria-label': { zh: '文档导航', en: 'Documentation Navigation' },
    'theme.aria-label': { zh: '切换主题', en: 'Toggle theme' },
    // Search
    'search.placeholder': { zh: '搜索文档...', en: 'Search docs...' },
    'search.no-results': { zh: '无匹配结果', en: 'No results' },
    // Theme
    'theme.light': { zh: '浅色', en: 'Light' },
    'theme.dark': { zh: '深色', en: 'Dark' },
    // Lang toggle
    'lang.zh': { zh: '中', en: '中' },
    'lang.en': { zh: 'EN', en: 'EN' },
    // Mobile menu
    'menu.toggle': { zh: '切换菜单', en: 'Toggle menu' },
    // Page descriptions (for search results)
    'page.overview': { zh: 'Cyrene 概述：特性、技术栈、快速开始', en: 'Cyrene overview: features, tech stack, quick start' },
    'page.installation': { zh: '在 Linux/macOS/Windows 上安装 Cyrene', en: 'Install Cyrene on Linux, macOS, or Windows' },
    'page.configuration': { zh: '环境变量、加密配置仓库、运行时设置', en: 'Environment variables, encrypted config store, runtime settings' },
    'page.usage-workbench': { zh: 'Workbench UI 使用指南：项目隔离、意图分流、逐步执行', en: 'Workbench UI guide: project isolation, intent dispatch, step-by-step execution' },
    'page.usage-legacy': { zh: 'Legacy Agent UI 功能详解', en: 'Legacy Agent UI feature reference' },
    'page.usage-cli': { zh: 'CLI 命令和交互式本地 CLI 使用', en: 'CLI commands and interactive local CLI usage' },
    'page.architecture': { zh: '双阶段代理循环、项目结构、安全模型', en: 'Two-phase agent loop, project structure, security model' },
    'page.memory': { zh: '三层记忆架构：上下文窗口、短期记忆、SOUL.md', en: 'Three-layer memory: context window, short-term, SOUL.md' },
    'page.subagents': { zh: '并行子代理系统：生命周期、通信、使用场景', en: 'Parallel sub-agent system: lifecycle, communication, use cases' },
    'page.knowledge': { zh: '文档上传、嵌入索引、实体管理', en: 'Document upload, embedding, entity management' },
    'page.browser': { zh: 'Playwright 浏览器实况与登录接管', en: 'Playwright browser live view and login takeover' },
    'page.mcp': { zh: 'MCP 协议支持：stdio 和 SSE 传输', en: 'MCP protocol support: stdio and SSE transports' },
    'page.scheduler': { zh: 'Cron/间隔/一次性任务和主动抽奖系统', en: 'Cron, interval, one-shot tasks and proactive lottery' },
    'page.search': { zh: 'SimpleXNG 内置搜索和深度研究管道', en: 'SimpleXNG built-in search and deep research pipeline' },
    'page.quick-chat': { zh: '全局快捷键唤起的浮动聊天窗口', en: 'Global shortcut floating chat window' },
    'page.development': { zh: '调试、测试、编码规范、CI/发布流程', en: 'Debugging, testing, conventions, CI/release' },
    'page.cli-reference': { zh: 'CLI 完整命令参考和 Telegram/WeChat 配置', en: 'Complete CLI reference and Telegram/WeChat setup' },
  };

  // --- State ---
  let currentPage = null;
  let currentLang = localStorage.getItem('cyrene-docs-lang') || 'zh';

  // --- DOM refs ---
  const sidebar = document.getElementById('sidebar');
  const backdrop = document.getElementById('sidebarBackdrop');
  const searchInput = document.getElementById('searchInput');
  const searchResults = document.getElementById('searchResults');

  // ==========================================
  // Navigation
  // ==========================================
  function navigateTo(pageId) {
    if (!PAGES.includes(pageId)) {
      pageId = DEFAULT_PAGE;
    }

    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));

    const target = document.getElementById('page-' + pageId);
    if (target) target.classList.add('active');

    document.querySelectorAll('.nav-item').forEach(item => {
      item.classList.toggle('active', item.dataset.page === pageId);
    });

    if (history.pushState) {
      history.pushState(null, '', '#' + pageId);
    }

    currentPage = pageId;
    closeMobileMenu();
    hideSearchResults();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  window.navigateTo = navigateTo;

  // ==========================================
  // i18n
  // ==========================================
  function t(key) {
    return TRANSLATIONS[key] ? (TRANSLATIONS[key][currentLang] || TRANSLATIONS[key]['zh']) : key;
  }

  function applyLang() {
    document.documentElement.setAttribute('data-lang', currentLang);
    // Update all data-i18n elements
    document.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.dataset.i18n;
      const text = t(key);
      if (!text) return;
      if (el.tagName === 'INPUT') {
        el.placeholder = text;
      } else {
        el.textContent = text;
      }
    });
    // Update topbar lang toggle
    const langCodes = document.querySelectorAll('.topbar-lang-text [data-lang-code]');
    langCodes.forEach(el => {
      el.classList.toggle('lang-label-active', el.dataset.langCode === currentLang);
    });
    // Update search placeholder
    if (searchInput) searchInput.placeholder = t('search.placeholder');
    // Update mobile menu aria-label
    const menuBtn = document.getElementById('mobileMenuToggle');
    if (menuBtn) menuBtn.setAttribute('aria-label', t('menu.toggle'));
    // Update sidebar nav aria-label
    const sidebarEl = document.querySelector('.sidebar');
    if (sidebarEl) sidebarEl.setAttribute('aria-label', t('sidebar.aria-label'));
    // Update theme toggle aria-label
    const themeBtn = document.getElementById('themeToggle');
    if (themeBtn) themeBtn.setAttribute('aria-label', t('theme.aria-label'));
  }

  function toggleLang() {
    currentLang = currentLang === 'zh' ? 'en' : 'zh';
    localStorage.setItem('cyrene-docs-lang', currentLang);
    applyLang();
  }
  window.toggleLang = toggleLang;

  // ==========================================
  // Theme
  // ==========================================
  function getTheme() {
    return document.documentElement.getAttribute('data-theme') || 'light';
  }

  function setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('cyrene-docs-theme', theme);
  }

  function toggleTheme() {
    setTheme(getTheme() === 'dark' ? 'light' : 'dark');
  }
  window.toggleTheme = toggleTheme;

  // ==========================================
  // Search
  // ==========================================
  function getPageContent(pageId) {
    const el = document.getElementById('page-' + pageId);
    if (!el) return '';
    // Clone to avoid modifying live DOM
    const clone = el.cloneNode(true);
    // Remove code blocks and flow diagrams from search (too noisy)
    clone.querySelectorAll('.code-block, .flow-diagram, .table-wrapper').forEach(n => n.remove());
    return clone.textContent || '';
  }

  function buildSearchIndex() {
    return PAGES.map(id => ({
      id,
      title: t('page.' + id) || id,
      text: getPageContent(id)
    }));
  }

  let searchIndex = null;

  function doSearch(query) {
    if (!query || query.length < 1) {
      hideSearchResults();
      return;
    }
    if (!searchIndex) searchIndex = buildSearchIndex();

    const q = query.toLowerCase();
    const results = [];

    for (const page of searchIndex) {
      const idx = page.text.toLowerCase().indexOf(q);
      if (idx >= 0) {
        // Extract a snippet around the match
        const start = Math.max(0, idx - 40);
        const end = Math.min(page.text.length, idx + q.length + 60);
        let snippet = page.text.slice(start, end).replace(/\s+/g, ' ').trim();
        if (start > 0) snippet = '...' + snippet;
        if (end < page.text.length) snippet += '...';
        results.push({ id: page.id, title: page.title, snippet });
      }
    }

    // Sort: pages with title match first
    results.sort((a, b) => {
      const aTitle = a.title.toLowerCase().includes(q) ? 0 : 1;
      const bTitle = b.title.toLowerCase().includes(q) ? 0 : 1;
      return aTitle - bTitle;
    });

    showSearchResults(results);
  }

  function showSearchResults(results) {
    if (!searchResults) return;
    if (results.length === 0) {
      searchResults.innerHTML = '<div class="search-result-item" style="color:var(--color-text-tertiary);cursor:default;">' + t('search.no-results') + '</div>';
    } else {
      searchResults.innerHTML = results.map(r =>
        '<a href="#" class="search-result-item" onclick="event.preventDefault(); window._navigateTo(\'' + r.id + '\'); return false;">' +
          '<div>' + r.title + '</div>' +
          '<div class="match">' + escapeHtml(r.snippet) + '</div>' +
        '</a>'
      ).join('');
    }
    searchResults.classList.add('open');
  }

  window._navigateTo = function(id) {
    navigateTo(id);
  };

  function hideSearchResults() {
    if (searchResults) {
      searchResults.classList.remove('open');
    }
  }

  function escapeHtml(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
  }

  // ==========================================
  // Mobile Menu
  // ==========================================
  function toggleMobileMenu() {
    sidebar.classList.toggle('open');
    backdrop.classList.toggle('open');
    document.body.style.overflow = sidebar.classList.contains('open') ? 'hidden' : '';
  }

  function closeMobileMenu() {
    sidebar.classList.remove('open');
    backdrop.classList.remove('open');
    document.body.style.overflow = '';
  }

  window.toggleMobileMenu = toggleMobileMenu;

  // ==========================================
  // Event Listeners
  // ==========================================

  // Search
  if (searchInput) {
    searchInput.addEventListener('input', function() {
      doSearch(this.value);
    });
    searchInput.addEventListener('focus', function() {
      if (this.value) doSearch(this.value);
    });
    searchInput.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') hideSearchResults();
      if (e.key === 'Enter') {
        const first = searchResults.querySelector('.search-result-item');
        if (first) first.click();
      }
    });
    // Close search on click outside
    document.addEventListener('click', function(e) {
      if (!e.target.closest('.topbar-search')) hideSearchResults();
    });
  }

  // Keyboard shortcuts
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      if (searchResults && searchResults.classList.contains('open')) {
        hideSearchResults();
        return;
      }
      closeMobileMenu();
    }
    // Ctrl/Cmd + K: focus search
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      if (searchInput) searchInput.focus();
    }
    // Ctrl/Cmd + ] : next page
    if (e.key === ']' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      const idx = PAGES.indexOf(currentPage);
      if (idx < PAGES.length - 1) navigateTo(PAGES[idx + 1]);
    }
    // Ctrl/Cmd + [ : prev page
    if (e.key === '[' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      const idx = PAGES.indexOf(currentPage);
      if (idx > 0) navigateTo(PAGES[idx - 1]);
    }
  });

  // History popstate
  window.addEventListener('popstate', function() {
    const hash = location.hash.replace('#', '');
    if (hash && PAGES.includes(hash)) navigateTo(hash);
  });

  // Hash change
  window.addEventListener('hashchange', function() {
    const h = location.hash.replace('#', '');
    if (h && PAGES.includes(h) && h !== currentPage) navigateTo(h);
  });

  // ==========================================
  // Initialization
  // ==========================================
  function init() {
    // Theme
    const savedTheme = localStorage.getItem('cyrene-docs-theme');
    if (savedTheme) {
      setTheme(savedTheme);
    } else {
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      if (prefersDark) setTheme('dark');
    }

    // Language
    applyLang();

    // Rebuild search index after language is applied
    searchIndex = buildSearchIndex();

    // Page from hash
    const hash = location.hash.replace('#', '');
    const startPage = (hash && PAGES.includes(hash)) ? hash : DEFAULT_PAGE;
    navigateTo(startPage);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
