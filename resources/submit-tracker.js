/*
 * Submit without leaving the page, follow the grading, and say when it is done
 * (A26, #137). Shared by the problem page and the IDE.
 *
 *   HNSubmit.submit(form, {container, onAccepted, onError})
 *       POSTs the form with fetch(), as JSON (see is_async_submit), and on
 *       success adds a result card to `container` and starts tracking.
 *   HNSubmit.track(payload, {container, onUpdate})
 *       Follows one submission to its final state.
 *   HNSubmit.toast({...})
 *       A bottom-right notice.
 *
 * This file can run more than once: the site's AJAX navigation re-executes
 * #js_media on every page change. Everything stateful lives on one
 * window.HNSubmit created the first time, so trackers and toasts survive a
 * navigation, and nothing is bound to window/document twice (PATCHES.md 20).
 */
(function () {
  'use strict';
  if (window.HNSubmit) return;

  var I18N = {};

  function t(key, fallback) {
    var value = I18N[key];
    return value == null ? fallback : value;
  }

  function fmt(template, values) {
    return String(template).replace(/%\((\w+)\)s/g, function (_, name) {
      return values[name] == null ? '' : values[name];
    });
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value == null || value === false) return;
        if (key === 'text') node.textContent = value;
        else if (key === 'className') node.className = value;
        else node.setAttribute(key, value === true ? '' : value);
      });
    }
    (children || []).forEach(function (child) {
      if (child == null) return;
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    });
    return node;
  }

  function formatTime(seconds) {
    if (seconds == null) return '';
    return (Math.round(seconds * 1000) / 1000).toLocaleString(undefined, {maximumFractionDigits: 3}) + ' s';
  }

  function formatMemory(kb) {
    if (kb == null) return '';
    if (kb >= 1024) return (Math.round(kb / 1024 * 10) / 10).toLocaleString() + ' MB';
    return Math.round(kb).toLocaleString() + ' KB';
  }

  function formatPoints(value) {
    if (value == null) return '';
    return (Math.round(value * 100) / 100).toLocaleString();
  }

  /* ------------------------------------------------------------- toasts */

  var toastRegion = null;
  var toastKeys = {};

  function ensureToastRegion() {
    if (toastRegion && document.body.contains(toastRegion)) return toastRegion;
    toastRegion = el('div', {
      className: 'hn-toast-region',
      'aria-live': 'polite',
      'aria-relevant': 'additions',
      role: 'region',
      'aria-label': t('notifications', 'Notifications'),
    });
    document.body.appendChild(toastRegion);
    return toastRegion;
  }

  /*
   * opts: {key, tone ('ok'|'bad'|'neutral'), title, body, href, actionText,
   *        timeout (ms, 0 = stay)}
   * A toast never takes focus and never navigates by itself; only a click on
   * it (or its action) opens `href`.
   */
  function toast(opts) {
    if (opts.key && toastKeys[opts.key]) return null;
    var region = ensureToastRegion();
    var closeBtn = el('button', {
      type: 'button',
      className: 'hn-toast-close',
      'aria-label': t('close', 'Close'),
      text: '×',
    });
    var action = opts.href ? el('a', {
      className: 'hn-toast-action',
      href: opts.href,
      text: opts.actionText || t('view', 'View'),
    }) : null;
    var node = el('div', {
      className: 'hn-toast hn-toast-' + (opts.tone || 'neutral'),
      role: 'status',
    }, [
      el('div', {className: 'hn-toast-main'}, [
        el('div', {className: 'hn-toast-title', text: opts.title || ''}),
        opts.body ? el('div', {className: 'hn-toast-body', text: opts.body}) : null,
        action,
      ]),
      closeBtn,
    ]);

    var timer = null;
    var timeout = opts.timeout == null ? 9000 : opts.timeout;

    function dismiss() {
      if (timer) window.clearTimeout(timer);
      node.classList.add('hn-toast-leaving');
      window.setTimeout(function () {
        if (node.parentNode) node.parentNode.removeChild(node);
      }, 200);
    }

    function arm() {
      if (!timeout) return;
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(dismiss, timeout);
    }

    function pause() {
      if (timer) window.clearTimeout(timer);
      timer = null;
    }

    closeBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      dismiss();
    });
    if (opts.href) {
      node.classList.add('hn-toast-clickable');
      node.addEventListener('click', function (e) {
        if (e.target.closest('a, button')) return;
        window.location.href = opts.href;
      });
    }
    node.addEventListener('mouseenter', pause);
    node.addEventListener('mouseleave', arm);
    node.addEventListener('focusin', pause);
    node.addEventListener('focusout', arm);

    // Keep the stack short: the result cards keep everything for later.
    while (region.children.length >= 4) region.removeChild(region.firstChild);
    region.appendChild(node);
    if (opts.key) toastKeys[opts.key] = true;
    arm();
    return {dismiss: dismiss, node: node};
  }

  /* ------------------------------------------------------------ verdicts */

  function verdictTone(payload) {
    if (!payload.graded) return 'neutral';
    if (payload.result_hidden) return 'neutral';
    if (payload.status === 'CE' || payload.status === 'IE') return 'bad';
    if (payload.result === 'AC') return payload.result_class === '_AC' ? 'partial' : 'ok';
    if (payload.status === 'AB') return 'neutral';
    return 'bad';
  }

  function statusLine(payload) {
    if (payload.graded) {
      if (payload.result_hidden) return t('graded_hidden', 'Graded (result hidden)');
      return payload.result_text || payload.status_text;
    }
    if (payload.status === 'QU') return t('queued', 'Waiting for a judge…');
    if (payload.status === 'P') return t('compiling', 'Compiling…');
    if (payload.status === 'G') {
      var n = Math.max((payload.current_testcase || 1) - 1, 0);
      return n ? fmt(t('grading_case', 'Grading… (%(n)s tests done)'), {n: n}) : t('grading', 'Grading…');
    }
    return payload.status_text;
  }

  function factsFor(payload) {
    var facts = [];
    if (payload.graded && !payload.result_hidden) {
      if (payload.points != null && payload.total) {
        facts.push(formatPoints(payload.points) + '/' + formatPoints(payload.total));
      }
      if (payload.time != null && payload.status === 'D') facts.push(formatTime(payload.time));
      if (payload.memory != null && payload.status === 'D') facts.push(formatMemory(payload.memory));
    }
    return facts;
  }

  /* --------------------------------------------------------------- cards */

  function cardFor(id) {
    return document.querySelectorAll('[data-submission-card="' + id + '"]');
  }

  function buildCard(payload) {
    var card = el('div', {
      className: 'hn-sub-card',
      'data-submission-card': payload.id,
    });
    renderCard(card, payload);
    return card;
  }

  function renderCard(card, payload) {
    var tone = verdictTone(payload);
    card.className = 'hn-sub-card hn-tone-' + tone + (payload.graded ? ' is-graded' : ' is-pending');
    card.textContent = '';
    var head = el('div', {className: 'hn-sub-card-head'}, [
      el('span', {className: 'hn-sub-card-title', text: fmt(t('card_title', 'Submission #%(id)s · %(problem)s · %(language)s'), {
        id: payload.id,
        problem: payload.problem ? payload.problem.code : '',
        language: payload.language || '',
      })}),
      el('a', {className: 'hn-sub-card-link', href: payload.url, text: t('details', 'Details')}),
    ]);
    var status = el('div', {className: 'hn-sub-card-status'}, [
      payload.graded ? null : el('i', {className: 'fa fa-spinner fa-pulse', 'aria-hidden': 'true'}),
      el('span', {className: 'hn-sub-card-verdict', text: statusLine(payload)}),
    ]);
    factsFor(payload).forEach(function (fact) {
      status.appendChild(el('span', {className: 'hn-sub-card-fact', text: fact}));
    });
    card.appendChild(head);
    card.appendChild(status);
    if (payload.error) {
      card.appendChild(el('pre', {className: 'hn-sub-card-error', text: payload.error}));
    }
  }

  function addCard(container, payload) {
    if (!container) return;
    var existing = container.querySelector('[data-submission-card="' + payload.id + '"]');
    if (existing) {
      renderCard(existing, payload);
      return;
    }
    container.insertBefore(buildCard(payload), container.firstChild);
    // Keep the list from growing forever on a long session.
    var cards = container.querySelectorAll('.hn-sub-card');
    for (var i = 8; i < cards.length; i++) cards[i].parentNode.removeChild(cards[i]);
  }

  /* ------------------------------------------------------------ tracking */

  var trackers = {};
  var notified = {};
  var receiver = null;

  function ensureReceiver(eventUrl, lastMsg) {
    if (!eventUrl || typeof window.EventReceiver !== 'function' || !window.io) return null;
    if (receiver) return receiver;
    try {
      receiver = new window.EventReceiver(eventUrl, [], lastMsg || 0, function (message) {
        // Any event on a tracked channel just means "look now"; the JSON
        // endpoint is the source of truth, so a duplicate, late or
        // out-of-order event cannot put a card into a wrong state.
        // The receiver does not say which channel a message came on, and
        // there are rarely more than one or two trackers, so poke them all.
        Object.keys(trackers).forEach(function (id) {
          if (trackers[id]) trackers[id].poke();
        });
      });
    } catch (e) {
      receiver = null;
    }
    return receiver;
  }

  function updateReceiverChannels() {
    if (!receiver) return;
    var channels = Object.keys(trackers).map(function (id) { return trackers[id].channel; });
    if (channels.length) receiver.updateChannels(channels);
  }

  function notifyDone(payload) {
    if (notified[payload.id]) return;
    notified[payload.id] = true;
    var facts = factsFor(payload);
    var verdict = statusLine(payload);
    toast({
      key: 'sub-done-' + payload.id,
      tone: verdictTone(payload),
      title: (payload.problem ? payload.problem.code + ' · ' : '') + '#' + payload.id,
      body: verdict + (facts.length ? ' · ' + facts.join(' · ') : ''),
      href: payload.url,
      actionText: t('view_result', 'View result'),
    });
  }

  function Tracker(payload, opts) {
    var self = this;
    this.id = payload.id;
    this.channel = payload.channel;
    this.payload = payload;
    this.listeners = [];
    if (opts && opts.onUpdate) this.listeners.push(opts.onUpdate);
    this.started = Date.now();
    this.failures = 0;
    this.timer = null;
    this.inflight = false;
    this.again = false;

    this.poke = function () {
      if (self.inflight) {
        self.again = true;
        return;
      }
      if (self.timer) window.clearTimeout(self.timer);
      self.fetch();
    };
  }

  Tracker.prototype.delay = function () {
    var age = Date.now() - this.started;
    var base = age < 15000 ? 1000 : age < 60000 ? 2500 : 6000;
    return base * Math.pow(2, Math.min(this.failures, 4));
  };

  Tracker.prototype.schedule = function () {
    var self = this;
    if (this.timer) window.clearTimeout(this.timer);
    // Give up quietly after 15 minutes; the card keeps its Details link.
    if (Date.now() - this.started > 15 * 60 * 1000) return this.stop();
    this.timer = window.setTimeout(function () { self.fetch(); }, this.delay());
  };

  Tracker.prototype.apply = function (payload) {
    // Never go back from a final state to a pending one: a late response to
    // an earlier poll must not resurrect the spinner.
    if (this.payload.graded && !payload.graded) return;
    this.payload = payload;
    var cards = cardFor(this.id);
    for (var i = 0; i < cards.length; i++) renderCard(cards[i], payload);
    this.listeners.forEach(function (fn) {
      try { fn(payload); } catch (e) { /* a listener must not stop tracking */ }
    });
    if (payload.graded) {
      notifyDone(payload);
      this.stop();
    }
  };

  Tracker.prototype.fetch = function () {
    var self = this;
    if (this.stopped) return;
    this.inflight = true;
    window.fetch(this.payload.json_url, {
      credentials: 'same-origin',
      headers: {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
      cache: 'no-store',
    }).then(function (resp) {
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      return resp.json();
    }).then(function (payload) {
      self.failures = 0;
      self.apply(payload);
    }).catch(function () {
      self.failures++;
    }).then(function () {
      self.inflight = false;
      if (self.stopped) return;
      if (self.again) {
        self.again = false;
        self.fetch();
      } else {
        self.schedule();
      }
    });
  };

  Tracker.prototype.stop = function () {
    this.stopped = true;
    if (this.timer) window.clearTimeout(this.timer);
    this.timer = null;
    if (trackers[this.id] === this) delete trackers[this.id];
    updateReceiverChannels();
  };

  function track(payload, opts) {
    opts = opts || {};
    if (opts.container) addCard(opts.container, payload);
    var existing = trackers[payload.id];
    if (existing) {
      if (opts.onUpdate) existing.listeners.push(opts.onUpdate);
      return existing;
    }
    var tracker = new Tracker(payload, opts);
    if (payload.graded) {
      // Judged before we even asked (a compile error can be that fast).
      tracker.apply(payload);
      return tracker;
    }
    trackers[payload.id] = tracker;
    ensureReceiver(opts.eventUrl, payload.last_msg);
    updateReceiverChannels();
    // First status right away, so a very fast grading is never missed.
    tracker.fetch();
    return tracker;
  }

  /* -------------------------------------------------------------- submit */

  function readJson(resp) {
    var type = resp.headers.get('Content-Type') || '';
    if (type.indexOf('application/json') === -1) return Promise.resolve(null);
    return resp.json().catch(function () { return null; });
  }

  /*
   * opts: {container, eventUrl, onAccepted(payload), onError(message, errors),
   *        onSettled()}
   * Resolves either way; never re-sends on its own. A request that may have
   * reached the server but whose answer was lost is reported as "unknown",
   * with a pointer to the submission list, rather than retried into a
   * duplicate.
   */
  function submit(form, opts) {
    opts = opts || {};
    var data = new FormData(form);
    function fail(message, errors) {
      if (opts.onError) opts.onError(message, errors || null);
    }
    return window.fetch(form.action, {
      method: 'POST',
      body: data,
      credentials: 'same-origin',
      headers: {'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'},
    }).then(function (resp) {
      return readJson(resp).then(function (payload) {
        if (resp.ok && payload && payload.ok) {
          if (opts.onAccepted) opts.onAccepted(payload);
          track(payload, {container: opts.container, eventUrl: opts.eventUrl});
          return payload;
        }
        if (payload && payload.error) return fail(payload.error, payload.errors);
        if (resp.redirected && /\/accounts\/login|\/login/.test(resp.url)) {
          return fail(t('session_expired', 'Your session has expired. Log in again (your code is kept here).'));
        }
        if (resp.status === 403) {
          return fail(t('forbidden', 'The request was refused. Reload the page and try again; your code is kept.'));
        }
        if (resp.status === 429) return fail(t('rate_limited', 'Too many submissions. Wait a moment and try again.'));
        return fail(fmt(t('server_error', 'The server could not take the submission (HTTP %(status)s).'), {status: resp.status}));
      });
    }, function () {
      fail(t('network_unknown', 'The connection dropped. The submission may or may not have been received: check your submission list before submitting again.'));
    }).then(function (result) {
      if (opts.onSettled) opts.onSettled();
      return result;
    });
  }

  window.HNSubmit = {
    setI18n: function (strings) {
      Object.keys(strings || {}).forEach(function (key) { I18N[key] = strings[key]; });
    },
    submit: submit,
    track: track,
    toast: toast,
    addCard: addCard,
    format: {time: formatTime, memory: formatMemory, points: formatPoints, fmt: fmt},
    t: t,
  };
})();
