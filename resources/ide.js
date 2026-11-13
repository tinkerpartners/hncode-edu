/*
 * The problem IDE (/problem/<code>/ide): statement | editor over console,
 * LeetCode-style. Submitting goes through the shared submit form and
 * submit-tracker.js (A26); "Run" posts the editor and the test cases to
 * problem_run and polls the run (A22).
 *
 * Config comes from window.HN_IDE plus #hn-ide's data-config (the
 * per-problem part), both rendered by templates/problem/ide.html.
 *
 * The strict contest IDE is this same page in contest mode. There a
 * navigation would drop the contestant out of fullscreen, so switching
 * problem swaps the problem in place: contest-strict.js intercepts the click
 * and calls window.HNIDE.switchTo(href).
 */
(function () {
  'use strict';

  var cfg = window.HN_IDE;
  var root = document.getElementById('hn-ide');
  if (!cfg || !root || root.getAttribute('data-ready')) return;
  root.setAttribute('data-ready', '1');

  function readProblemConfig(node) {
    try {
      return JSON.parse(node.getAttribute('data-config') || '{}');
    } catch (e) {
      return {};
    }
  }

  function applyProblemConfig(problemCfg) {
    Object.keys(problemCfg).forEach(function (key) { cfg[key] = problemCfg[key]; });
    cfg.fileio = cfg.fileio || {input: '', output: ''};
  }

  applyProblemConfig(readProblemConfig(root));

  var T = cfg.i18n || {};
  var fmt = window.HNSubmit ? window.HNSubmit.format.fmt : function (s) { return s; };

  function store(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      if (value === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch (e) { /* private mode, blocked storage: conveniences only */ }
    return null;
  }

  function $id(id) { return document.getElementById(id); }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      var value = attrs[key];
      if (value == null || value === false) return;
      if (key === 'text') node.textContent = value;
      else if (key === 'className') node.className = value;
      else node.setAttribute(key, value === true ? '' : value);
    });
    (children || []).forEach(function (child) {
      if (child != null) node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    });
    return node;
  }

  function editor() { return window.ace_source || null; }

  function csrfToken() {
    var input = document.querySelector('#problem_submit input[name=csrfmiddlewaretoken]');
    return input ? input.value : '';
  }

  function isFileOnly() {
    var option = $('#id_language').find('option:selected');
    return option.attr('data-file-only') === '1';
  }

  /* --------------------------------------------------------------- tabs */

  function bindTabs(selector, attr, onShow) {
    var buttons = root.querySelectorAll(selector);
    function show(name) {
      for (var i = 0; i < buttons.length; i++) {
        var button = buttons[i];
        var active = button.getAttribute(attr) === name;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
        var panel = $id(button.getAttribute('aria-controls'));
        if (panel) panel.hidden = !active;
      }
      if (onShow) onShow(name);
    }
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener('click', function () { show(this.getAttribute(attr)); });
    }
    return show;
  }

  var showLeftTab = bindTabs('.hn-pane-left .hn-tab', 'data-tab');
  var showConsoleTab = bindTabs('#ide-console .hn-tab', 'data-console', function () {
    setConsoleCollapsed(false);
  });

  /* ------------------------------------------------------------ resizing */

  var main = $id('ide-main');
  var left = $id('ide-left');
  var consoleEl = $id('ide-console');

  function resizeEditor() {
    var ed = editor();
    if (ed) ed.resize();
  }

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function applySizes() {
    var leftPct = parseFloat(store('hn-ide:left'));
    if (leftPct) main.style.setProperty('--hn-left', clamp(leftPct, 20, 75) + '%');
    var consolePx = parseFloat(store('hn-ide:console'));
    if (consolePx && consoleEl) consoleEl.style.setProperty('--hn-console', clamp(consolePx, 120, 900) + 'px');
  }

  function dragGutter(gutter, onMove, onEnd) {
    if (!gutter) return;
    gutter.addEventListener('pointerdown', function (e) {
      if (e.button !== 0) return;
      e.preventDefault();
      gutter.setPointerCapture(e.pointerId);
      root.classList.add('is-resizing');
      function move(ev) { onMove(ev); resizeEditor(); }
      function up(ev) {
        gutter.releasePointerCapture(ev.pointerId);
        gutter.removeEventListener('pointermove', move);
        gutter.removeEventListener('pointerup', up);
        gutter.removeEventListener('pointercancel', up);
        root.classList.remove('is-resizing');
        if (onEnd) onEnd();
        resizeEditor();
      }
      gutter.addEventListener('pointermove', move);
      gutter.addEventListener('pointerup', up);
      gutter.addEventListener('pointercancel', up);
    });
  }

  var gutterV = $id('ide-gutter-v');
  var gutterH = $id('ide-gutter-h');

  function setLeft(pct) {
    pct = clamp(pct, 20, 75);
    main.style.setProperty('--hn-left', pct + '%');
    store('hn-ide:left', String(pct));
  }

  function setConsole(px) {
    if (!consoleEl) return;
    var right = $id('ide-right');
    px = clamp(px, 120, Math.max(160, right.clientHeight - 160));
    consoleEl.style.setProperty('--hn-console', px + 'px');
    store('hn-ide:console', String(px));
  }

  dragGutter(gutterV, function (e) {
    var rect = main.getBoundingClientRect();
    setLeft((e.clientX - rect.left) / rect.width * 100);
  });

  dragGutter(gutterH, function (e) {
    var rect = $id('ide-right').getBoundingClientRect();
    setConsole(rect.bottom - e.clientY);
  });

  if (gutterV) {
    gutterV.addEventListener('keydown', function (e) {
      var current = left.getBoundingClientRect().width / main.getBoundingClientRect().width * 100;
      if (e.key === 'ArrowLeft') setLeft(current - 3);
      else if (e.key === 'ArrowRight') setLeft(current + 3);
      else return;
      e.preventDefault();
      resizeEditor();
    });
  }
  if (gutterH) {
    gutterH.addEventListener('keydown', function (e) {
      var current = consoleEl.getBoundingClientRect().height;
      if (e.key === 'ArrowUp') setConsole(current + 30);
      else if (e.key === 'ArrowDown') setConsole(current - 30);
      else return;
      e.preventDefault();
      resizeEditor();
    });
  }

  function setConsoleCollapsed(collapsed) {
    if (!consoleEl) return;
    consoleEl.classList.toggle('is-collapsed', collapsed);
    var toggle = $id('ide-console-toggle');
    if (toggle) toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    resizeEditor();
  }

  var consoleToggle = $id('ide-console-toggle');
  if (consoleToggle) {
    consoleToggle.addEventListener('click', function () {
      setConsoleCollapsed(!consoleEl.classList.contains('is-collapsed'));
    });
  }

  applySizes();
  window.addEventListener('resize', resizeEditor);

  /* ------------------------------------------------------ editor toolbar */

  function setFontSize(size) {
    var ed = editor();
    if (!ed) return;
    size = clamp(size, 10, 28);
    ed.setFontSize(size);
    store('hn-ide:font', String(size));
  }

  $('#ide-font-inc').on('click', function () {
    var ed = editor();
    if (ed) setFontSize((parseInt(ed.getFontSize(), 10) || 15) + 1);
  });
  $('#ide-font-dec').on('click', function () {
    var ed = editor();
    if (ed) setFontSize((parseInt(ed.getFontSize(), 10) || 15) - 1);
  });

  $('#ide-upload').on('click', function () {
    var input = $id('id_source_file');
    if (input) input.click();
  });

  $('#ide-reset').on('click', function () {
    if (!window.confirm(T.resetConfirm)) return;
    if (window.resetSubmitDraft) window.resetSubmitDraft();
  });

  $('#ide-fullscreen').on('click', function () {
    var doc = document;
    if (doc.fullscreenElement) {
      doc.exitFullscreen();
    } else if (root.requestFullscreen) {
      root.requestFullscreen().catch(function () {});
    }
  });
  document.addEventListener('fullscreenchange', function () {
    root.classList.toggle('is-fullscreen', document.fullscreenElement === root);
    resizeEditor();
  });

  $('#ace_source').on('ace_load', function (e, ed) {
    var size = parseInt(store('hn-ide:font'), 10);
    if (size) ed.setFontSize(size);
    if (cfg.darkEditor) ed.setTheme('ace/theme/twilight');
    ed.commands.addCommand({
      name: 'hnRun',
      bindKey: {win: "Ctrl-'", mac: "Command-'"},
      exec: function () { runCode(); },
      readOnly: true,
    });
    window.setTimeout(resizeEditor, 0);
  });

  document.addEventListener('keydown', function (e) {
    var mod = e.ctrlKey || e.metaKey;
    if (!mod) return;
    if (e.key === "'") {
      e.preventDefault();
      runCode();
    } else if (e.key === 'Enter' && !e.target.closest('.ace_editor')) {
      e.preventDefault();
      submitCode();
    }
  });

  /* ---------------------------------------------------------- test cases */

  function casesKey() { return 'hn-ide:cases:' + cfg.problem; }
  var cases = [];
  var activeCase = 0;
  var inputBox = $id('ide-input');
  var expectedBox = $id('ide-expected');
  var casesBar = $id('ide-cases');

  function labelText(node) {
    return (node && node.textContent || '').trim().toLowerCase();
  }

  /*
   * Best effort: pair up the statement's example blocks. Statements label
   * them in many ways ("Input"/"Output", "Dữ liệu vào"/"Kết quả", collapsible
   * "Test 1" admonitions), so look at the nearest label before each <pre>.
   * Whatever this finds is only a starting point the user can edit.
   */
  function samplesFromStatement() {
    var statement = $id('ide-statement');
    if (!statement) return [];
    var pres = statement.querySelectorAll('pre');
    var inputRe = /(input|inp\b|vào|vao|đầu vào|nhập)/;
    var outputRe = /(output|out\b|ra\b|đầu ra|kết quả|ket qua|xuất)/;
    var found = [];
    var pending = null;

    for (var i = 0; i < pres.length; i++) {
      var pre = pres[i];
      var text = pre.textContent.replace(/\r/g, '');
      if (text.length > 20000) continue;
      var label = '';
      var summary = pre.closest('details') && pre.closest('details').querySelector('summary');
      if (summary) label = labelText(summary);
      if (!label) {
        var node = pre;
        for (var hops = 0; node && hops < 6 && !label; hops++) {
          var prev = node.previousElementSibling;
          while (prev && !labelText(prev)) prev = prev.previousElementSibling;
          if (prev && prev.tagName !== 'PRE' && !prev.querySelector('pre')) {
            label = labelText(prev).slice(0, 80);
          } else if (prev) {
            break;
          } else {
            node = node.parentElement;
            if (node === statement) break;
          }
        }
      }
      var isOutput = outputRe.test(label) && !/^.*input/.test(label);
      var isInput = !isOutput && inputRe.test(label);
      if (isInput) {
        pending = {input: text, expected: ''};
        found.push(pending);
      } else if (isOutput && pending) {
        pending.expected = text;
        pending = null;
      }
    }
    return found.slice(0, cfg.maxCases);
  }

  function saveCases() {
    store(casesKey(), JSON.stringify({cases: cases, active: activeCase}));
  }

  function readCaseFields() {
    if (!cases[activeCase] || !inputBox) return;
    cases[activeCase].input = inputBox.value;
    cases[activeCase].expected = expectedBox.value;
  }

  function renderCases() {
    if (!casesBar) return;
    casesBar.textContent = '';
    cases.forEach(function (c, i) {
      var tab = el('button', {
        type: 'button',
        role: 'tab',
        className: 'hn-case' + (i === activeCase ? ' is-active' : ''),
        'aria-selected': i === activeCase ? 'true' : 'false',
        'data-case': i,
      }, [fmt(T.caseN, {n: i + 1})]);
      if (cases.length > 1) {
        tab.appendChild(el('span', {
          className: 'hn-case-x',
          role: 'button',
          tabindex: '0',
          'aria-label': T.removeCase,
          title: T.removeCase,
          'data-remove': i,
          text: '×',
        }));
      }
      casesBar.appendChild(tab);
    });
    if (cases.length < cfg.maxCases) {
      casesBar.appendChild(el('button', {
        type: 'button',
        className: 'hn-case hn-case-add',
        'aria-label': T.addCase,
        title: T.addCase,
        'data-add': '1',
        text: '+',
      }));
    }
    inputBox.value = cases[activeCase] ? cases[activeCase].input : '';
    expectedBox.value = cases[activeCase] ? (cases[activeCase].expected || '') : '';
  }

  function selectCase(i) {
    readCaseFields();
    activeCase = clamp(i, 0, cases.length - 1);
    renderCases();
    saveCases();
  }

  if (casesBar) {
    casesBar.addEventListener('click', function (e) {
      var remove = e.target.closest('[data-remove]');
      if (remove) {
        e.stopPropagation();
        readCaseFields();
        cases.splice(parseInt(remove.getAttribute('data-remove'), 10), 1);
        activeCase = clamp(activeCase, 0, cases.length - 1);
        renderCases();
        saveCases();
        return;
      }
      if (e.target.closest('[data-add]')) {
        readCaseFields();
        var copy = cases[activeCase] ? {input: cases[activeCase].input, expected: ''} : {input: '', expected: ''};
        cases.push(copy);
        activeCase = cases.length - 1;
        renderCases();
        saveCases();
        inputBox.focus();
        return;
      }
      var tab = e.target.closest('[data-case]');
      if (tab) selectCase(parseInt(tab.getAttribute('data-case'), 10));
    });
    casesBar.addEventListener('keydown', function (e) {
      if ((e.key === 'Enter' || e.key === ' ') && e.target.hasAttribute('data-remove')) {
        e.preventDefault();
        e.target.click();
      }
    });
    [inputBox, expectedBox].forEach(function (box) {
      box.addEventListener('input', function () {
        readCaseFields();
        saveCases();
      });
    });

    loadCases();
  }

  function loadCases() {
    if (!casesBar) return;
    var saved = null;
    try {
      saved = JSON.parse(store(casesKey()) || 'null');
    } catch (e) {
      saved = null;
    }
    if (saved && Array.isArray(saved.cases) && saved.cases.length) {
      cases = saved.cases.slice(0, cfg.maxCases).map(function (c) {
        return {input: String(c.input || ''), expected: String(c.expected || '')};
      });
      activeCase = clamp(saved.active || 0, 0, cases.length - 1);
    } else {
      cases = samplesFromStatement();
      activeCase = 0;
      if (!cases.length) cases = [{input: '', expected: ''}];
    }
    renderCases();
  }

  /* ------------------------------------------------------------ file I/O */

  var ioFiles = $id('ide-io-files');
  var ioIn = $id('ide-io-in');
  var ioOut = $id('ide-io-out');
  function ioKey() { return 'hn-ide:io:' + cfg.problem; }

  function ioMode() {
    var checked = root.querySelector('input[name=ide-io]:checked');
    return checked ? checked.value : 'std';
  }

  function setIoMode(mode) {
    var radio = root.querySelector('input[name=ide-io][value=' + mode + ']');
    if (radio) radio.checked = true;
    if (ioFiles) ioFiles.hidden = mode !== 'file';
  }

  function loadIo() {
    if (!ioFiles) return;
    var savedIo = null;
    try {
      savedIo = JSON.parse(store(ioKey()) || 'null');
    } catch (e) {
      savedIo = null;
    }
    // Default to whatever the problem itself uses, so code written for the
    // judge's file I/O runs unchanged.
    ioIn.value = (savedIo && savedIo.input) || cfg.fileio.input || '';
    ioOut.value = (savedIo && savedIo.output) || cfg.fileio.output || '';
    setIoMode(savedIo ? savedIo.mode : (cfg.fileio.input || cfg.fileio.output ? 'file' : 'std'));
  }

  if (ioFiles) {
    loadIo();
    root.querySelectorAll('input[name=ide-io]').forEach(function (radio) {
      radio.addEventListener('change', function () {
        setIoMode(ioMode());
        saveIo();
      });
    });
    [ioIn, ioOut].forEach(function (input) { input.addEventListener('input', saveIo); });
  }

  function saveIo() {
    store(ioKey(), JSON.stringify({mode: ioMode(), input: ioIn.value.trim(), output: ioOut.value.trim()}));
  }

  /* ----------------------------------------------------------------- run */

  var runBtn = $id('ide-run');
  var runResult = $id('ide-run-result');
  var currentRun = null;
  var runTimer = null;

  function formatTime(s) { return window.HNSubmit ? window.HNSubmit.format.time(s) : s + ' s'; }
  function formatMemory(kb) { return window.HNSubmit ? window.HNSubmit.format.memory(kb) : kb + ' KB'; }

  function verdictClass(verdict) {
    if (!verdict) return 'pending';
    if (verdict === 'AC' || verdict === 'OK') return verdict === 'AC' ? 'ok' : 'neutral-ok';
    if (verdict === 'AB') return 'neutral';
    return 'bad';
  }

  function setRunBusy(busy) {
    if (!runBtn) return;
    runBtn.disabled = busy;
    runBtn.classList.toggle('is-busy', busy);
  }

  function pre(text, className) {
    return el('pre', {className: 'hn-pre' + (className ? ' ' + className : ''), text: text});
  }

  var shownCase = 0;

  function renderRun(payload) {
    if (!runResult) return;
    runResult.textContent = '';
    var head = el('div', {className: 'hn-run-head'});
    if (!payload.finished) {
      head.appendChild(el('span', {className: 'hn-run-verdict is-pending'}, [
        el('i', {className: 'fa fa-spinner fa-pulse', 'aria-hidden': 'true'}),
        ' ',
        payload.status === 'QU' ? T.waiting : payload.status === 'P' ? T.compiling : T.running,
      ]));
      var cancel = el('button', {type: 'button', className: 'hn-btn hn-btn-ghost', text: T.cancel});
      cancel.addEventListener('click', function () { abortRun(payload); });
      head.appendChild(cancel);
      if (payload.slow) head.appendChild(el('span', {className: 'hn-muted', text: T.stillRunning}));
    } else {
      head.appendChild(el('span', {
        className: 'hn-run-verdict is-' + verdictClass(payload.verdict),
        text: payload.verdict_text || payload.status_text,
      }));
      var times = payload.cases.filter(function (c) { return c.time != null; });
      if (times.length) {
        var maxTime = Math.max.apply(null, times.map(function (c) { return c.time; }));
        var maxMem = Math.max.apply(null, times.map(function (c) { return c.memory || 0; }));
        head.appendChild(el('span', {className: 'hn-run-fact', text: formatTime(maxTime)}));
        head.appendChild(el('span', {className: 'hn-run-fact', text: formatMemory(maxMem)}));
      }
    }
    runResult.appendChild(head);

    if (payload.status === 'CE' || (payload.error && payload.status !== 'D')) {
      runResult.appendChild(el('div', {className: 'hn-label', text: T.compileError}));
      runResult.appendChild(pre(payload.error || payload.status_text, 'is-error'));
      return;
    }
    if (!payload.cases || !payload.cases.length) return;

    shownCase = clamp(shownCase, 0, payload.cases.length - 1);
    var bar = el('div', {className: 'hn-cases', role: 'tablist'});
    payload.cases.forEach(function (c, i) {
      var tab = el('button', {
        type: 'button',
        role: 'tab',
        className: 'hn-case is-' + verdictClass(c.done ? c.verdict : null) + (i === shownCase ? ' is-active' : ''),
        'aria-selected': i === shownCase ? 'true' : 'false',
      }, [el('span', {className: 'hn-dot', 'aria-hidden': 'true'}), fmt(T.caseN, {n: i + 1})]);
      tab.addEventListener('click', function () {
        shownCase = i;
        renderRun(payload);
      });
      bar.appendChild(tab);
    });
    runResult.appendChild(bar);

    var c = payload.cases[shownCase];
    if (c.done) {
      var line = el('div', {className: 'hn-run-case-head'}, [
        el('span', {className: 'hn-run-verdict is-' + verdictClass(c.verdict), text: c.verdict_text}),
        c.time != null ? el('span', {className: 'hn-run-fact', text: formatTime(c.time)}) : null,
        c.memory != null ? el('span', {className: 'hn-run-fact', text: formatMemory(c.memory)}) : null,
      ]);
      runResult.appendChild(line);
      if (c.feedback) runResult.appendChild(el('div', {className: 'hn-muted', text: c.feedback}));
    }
    runResult.appendChild(el('div', {className: 'hn-label', text: T.input}));
    runResult.appendChild(pre(c.input || ' '));
    if (c.done) {
      runResult.appendChild(el('div', {className: 'hn-label', text: T.output}));
      runResult.appendChild(pre(c.output || T.noOutput, c.output ? '' : 'is-empty'));
      if (c.output_truncated) runResult.appendChild(el('div', {className: 'hn-muted', text: T.truncated}));
      if (c.extended_feedback) runResult.appendChild(pre(c.extended_feedback, 'is-error'));
    }
    if (c.expected != null) {
      runResult.appendChild(el('div', {className: 'hn-label', text: T.expected}));
      runResult.appendChild(pre(c.expected));
    }
  }

  function runError(message) {
    setRunBusy(false);
    if (!runResult) return;
    runResult.textContent = '';
    runResult.appendChild(el('div', {className: 'hn-run-head'}, [
      el('span', {className: 'hn-run-verdict is-bad', text: T.runFailed}),
    ]));
    runResult.appendChild(el('p', {text: message}));
  }

  function pollRun(payload, started) {
    if (runTimer) window.clearTimeout(runTimer);
    if (currentRun !== payload.id) return;
    var age = Date.now() - started;
    var delay = age < 5000 ? 500 : age < 20000 ? 1000 : 2500;
    runTimer = window.setTimeout(function () {
      window.fetch(payload.url, {
        credentials: 'same-origin',
        headers: {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
        cache: 'no-store',
      }).then(function (resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
      }).then(function (next) {
        if (currentRun !== next.id) return;
        next.slow = !next.finished && Date.now() - started > 20000;
        renderRun(next);
        if (next.finished) {
          currentRun = null;
          setRunBusy(false);
        } else if (Date.now() - started < 5 * 60 * 1000) {
          pollRun(next, started);
        } else {
          currentRun = null;
          setRunBusy(false);
        }
      }).catch(function () {
        if (Date.now() - started < 5 * 60 * 1000) pollRun(payload, started);
        else runError(T.network);
      });
    }, delay);
  }

  function abortRun(payload) {
    var data = new FormData();
    data.append('csrfmiddlewaretoken', csrfToken());
    window.fetch(payload.abort_url, {
      method: 'POST',
      body: data,
      credentials: 'same-origin',
      headers: {'X-Requested-With': 'XMLHttpRequest'},
    }).catch(function () {});
  }

  function runCode() {
    if (!runBtn || runBtn.disabled || !cfg.runEnabled) return;
    if (isFileOnly()) {
      showConsoleTab('run');
      runError(T.fileOnly);
      return;
    }
    readCaseFields();
    saveCases();
    var ed = editor();
    var data = new FormData();
    data.append('csrfmiddlewaretoken', csrfToken());
    data.append('language', $('#id_language').val());
    data.append('source', ed ? ed.getSession().getValue() : $('#id_source').val());
    data.append('cases', JSON.stringify(cases.map(function (c) {
      return {input: c.input, expected: c.expected && c.expected.trim() ? c.expected : null};
    })));
    data.append('io_mode', ioMode());
    if (ioMode() === 'file') {
      data.append('fileio_input', ioIn.value.trim());
      data.append('fileio_output', ioOut.value.trim());
    }

    setRunBusy(true);
    showConsoleTab('run');
    shownCase = activeCase;
    renderRun({finished: false, status: 'QU', cases: []});

    window.fetch(cfg.runUrl, {
      method: 'POST',
      body: data,
      credentials: 'same-origin',
      headers: {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
    }).then(function (resp) {
      var type = resp.headers.get('Content-Type') || '';
      if (type.indexOf('application/json') === -1) {
        if (resp.redirected) return runError(T.sessionExpired);
        return runError(T.runFailed + ' (HTTP ' + resp.status + ')');
      }
      return resp.json().then(function (payload) {
        if (!resp.ok || !payload.ok) {
          var message = payload.error || T.runFailed;
          if (payload.errors) {
            Object.keys(payload.errors).forEach(function (field) {
              message += ' ' + payload.errors[field].join(' ');
            });
          }
          return runError(message);
        }
        currentRun = payload.id;
        renderRun(payload);
        if (payload.finished) setRunBusy(false);
        else pollRun(payload, Date.now());
      });
    }, function () {
      runError(T.network);
    });
  }

  if (runBtn) runBtn.addEventListener('click', runCode);

  $('#id_language').on('change', function () {
    if (runBtn) runBtn.title = isFileOnly() ? T.fileOnly : runBtn.getAttribute('data-title') || runBtn.title;
  });
  if (runBtn) runBtn.setAttribute('data-title', runBtn.title);

  /* -------------------------------------------------------------- submit */

  var submitBtn = $id('ide-submit');
  var hiddenSubmit = $id('submit-button');
  var leftList = $id('ide-submissions');
  var navigationRefresh = 0;
  var navigationResults = {};

  function updateProblemNavigation(payload) {
    if (!payload.graded || navigationResults[payload.id] || !$id('ide-problem-nav')) return;
    navigationResults[payload.id] = true;
    var refresh = ++navigationRefresh;
    // Ask the server for best results, instead of letting the last WA replace
    // an earlier AC. Only copy status: a completed request for the old problem
    // must not move the active marker after the user has switched problems.
    window.fetch(window.location.href, {credentials: 'same-origin', cache: 'no-store'})
      .then(function (resp) { if (!resp.ok) throw new Error('navigation'); return resp.text(); })
      .then(function (html) {
        if (refresh !== navigationRefresh) return;
        var doc = new DOMParser().parseFromString(html, 'text/html');
        var nav = $id('ide-problem-nav');
        if (!nav) return;
        nav.querySelectorAll('[data-problem]').forEach(function (chip) {
          var other = Array.from(doc.querySelectorAll('#ide-problem-nav [data-problem]')).find(function (item) {
            return item.dataset.problem === chip.dataset.problem;
          });
          if (!other) return;
          ['new', 'ok', 'bad', 'partial', 'hidden'].forEach(function (state) {
            chip.classList.toggle('is-' + state, other.dataset.state === state);
          });
          chip.dataset.state = other.dataset.state;
          chip.title = other.title;
          chip.setAttribute('aria-label', other.getAttribute('aria-label'));
        });
      }).catch(function () { delete navigationResults[payload.id]; });
  }

  function submitCode() {
    if (!submitBtn || submitBtn.disabled) return;
    $('#problem_submit').trigger('submit');
  }

  window.hnSubmitHooks = {
    container: function () { return $id('ide-submit-results'); },
    errorTarget: function () { return $id('ide-submit-error'); },
    onAccepted: function (payload) {
      var empty = $id('ide-submit-empty');
      if (empty) empty.hidden = true;
      var leftEmpty = $id('ide-submissions-empty');
      if (leftEmpty) leftEmpty.hidden = true;
      if (window.HNSubmit && leftList) window.HNSubmit.addCard(leftList, payload);
      if (window.HNSubmit) window.HNSubmit.track(payload, {
        eventUrl: $('#problem_submit').attr('data-event-url'),
        onUpdate: updateProblemNavigation,
      });
    },
    onError: function () {
      showConsoleTab('submit');
    },
    onSettled: function () {},
  };

  if (submitBtn) {
    submitBtn.addEventListener('click', function () {
      showConsoleTab('submit');
      submitCode();
    });
  }

  // The shared submit JS disables its own (hidden) button while posting and
  // during a contest's rate-limit countdown; the header button follows it.
  if (hiddenSubmit && submitBtn) {
    var sync = function () {
      submitBtn.disabled = hiddenSubmit.disabled;
      var countdown = $id('countdown-timer');
      submitBtn.classList.toggle('is-busy', hiddenSubmit.disabled && !(countdown && countdown.textContent));
      submitBtn.title = countdown && countdown.textContent ? countdown.textContent : submitBtn.getAttribute('data-title');
    };
    submitBtn.setAttribute('data-title', submitBtn.title);
    new MutationObserver(sync).observe(hiddenSubmit, {attributes: true, attributeFilter: ['disabled']});
    var countdownEl = $id('countdown-timer');
    if (countdownEl) new MutationObserver(sync).observe(countdownEl, {childList: true, characterData: true, subtree: true});
    sync();
  }

  /* ---------------------------------------------------- past submissions */

  function renderRecent() {
    if (!window.HNSubmit || !leftList) return;
    leftList.textContent = '';
    var recent = Array.isArray(cfg.recent) ? cfg.recent : [];
    var leftEmpty = $id('ide-submissions-empty');
    if (leftEmpty) leftEmpty.hidden = recent.length > 0;
    // Oldest first, so the newest ends up on top.
    recent.slice().reverse().forEach(function (payload) {
      if (payload.graded) window.HNSubmit.addCard(leftList, payload);
      else window.HNSubmit.track(payload, {
        container: leftList, eventUrl: $('#problem_submit').attr('data-event-url'),
        onUpdate: updateProblemNavigation,
      });
    });
  }

  renderRecent();
  showLeftTab('statement');

  /* ------------------------------------------- switching problem in place */

  function replaceInner(selector, doc) {
    var mine = root.querySelector(selector);
    var theirs = doc.querySelector(selector);
    if (mine && theirs) mine.innerHTML = theirs.innerHTML;
    return mine;
  }

  // The language list differs between problems. Keep the choice when the new
  // problem allows it; the shared change handler sets the editor mode and
  // loads this problem's draft or template.
  function swapLanguages(doc) {
    var select = $id('id_language');
    var next = doc.getElementById('id_language');
    if (!select || !next) return;
    var current = select.value;
    select.innerHTML = next.innerHTML;
    if (select.querySelector('option[value="' + current + '"]')) select.value = current;
  }

  function switchTo(href) {
    return window.fetch(href, {
      credentials: 'same-origin',
      headers: {'Accept': 'text/html'},
    }).then(function (resp) {
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      return resp.text();
    }).then(function (html) {
      // DOMParser never runs the page's scripts: only markup is taken over.
      var doc = new DOMParser().parseFromString(html, 'text/html');
      var next = doc.getElementById('hn-ide');
      if (!next || !next.getAttribute('data-config')) throw new Error('not an IDE page');

      readCaseFields();
      saveCases();
      if (window.flushSubmitDraft) window.flushSubmitDraft();
      if (currentRun) currentRun = null;
      if (runTimer) window.clearTimeout(runTimer);
      setRunBusy(false);

      applyProblemConfig(readProblemConfig(next));
      root.setAttribute('data-config', next.getAttribute('data-config'));
      window.HN_DRAFT_SCOPE = cfg.draftScope;
      window.HN_TEMPLATE_PROBLEM = cfg.problemId;

      replaceInner('.hn-ide-title', doc);
      var title = root.querySelector('.hn-ide-title');
      var nextTitle = doc.querySelector('.hn-ide-title');
      if (title && nextTitle) title.title = nextTitle.title;
      replaceInner('#ide-problem-nav', doc);
      replaceInner('.hn-ide-meta', doc);
      var statement = replaceInner('#ide-statement', doc);
      if (statement) {
        if (window.renderKatex) {
          try { window.renderKatex(statement); } catch (e) { /* a math error must not block the switch */ }
        }
        if ($.fn.unveil) $(statement).find('img.unveil').unveil(200);
      }
      var form = $id('problem_submit');
      var nextForm = doc.getElementById('problem_submit');
      if (form && nextForm) form.setAttribute('action', nextForm.getAttribute('action'));
      document.title = doc.title;

      // Editor: this problem's draft, or its template.
      swapLanguages(doc);
      $('#id_language').trigger('change');

      // Out of submissions for this problem, or still in the contest's
      // rate-limit countdown (which is per contest, not per problem).
      var countdown = $id('countdown-timer');
      if (hiddenSubmit) hiddenSubmit.disabled = !!cfg.submitBlocked || !!(countdown && countdown.textContent);
      loadCases();
      loadIo();
      if (runResult) {
        runResult.textContent = '';
        runResult.appendChild(el('p', {className: 'hn-muted', text: T.runFirst}));
      }
      var results = $id('ide-submit-results');
      if (results) results.textContent = '';
      var error = $id('ide-submit-error');
      if (error) error.textContent = '';
      var empty = $id('ide-submit-empty');
      if (empty) empty.hidden = false;
      renderRecent();
      showLeftTab('statement');
      showConsoleTab('cases');
      var pane = root.querySelector('#ide-tab-statement');
      if (pane) pane.scrollTop = 0;
    });
  }

  window.HNIDE = {switchTo: switchTo};
})();
