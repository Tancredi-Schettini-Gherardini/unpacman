/* unpacman — the reading interface (the Deslopify reader).
 *
 * Three surfaces and one rule: never show the machinery. Whoever is here wants
 * a paper explained, so the pipeline appears as five plain sentences about what
 * is happening, and the result appears as something to read.
 *
 * No build step and no framework, matching the rest of this repository: there
 * is no Node on the machines this runs on, and a tool you cannot edit and
 * reload in one second is a tool that stops being edited. */

import { drawMap } from './map.js';
import { $, api, bytes, confirmAction, h, mathText, mount, toast, typeset } from './dom.js';
import * as notes from './notes.js';
import { openViewer, ZOOM_MAX, ZOOM_MIN } from './paper.js';
import * as settings from './settings.js';
import * as glossary from './glossary.js';
import * as hints from './hints.js';
import * as firstAsk from './firstask.js';
import * as ready from './ready.js';
import * as lifecycle from './lifecycle.js';
import * as feedback from './feedback.js';
import * as diag from './diag.js';
import * as signup from './news.js';
import * as about from './about.js';
import { askFirst } from './questions.js';
import { createSearchPapers } from './search-papers.js';
import { numberSection } from './headings.js';

// ------------------------------------------------------------------ dom

const tpl = (id) => $(`#${id}`).content.firstElementChild.cloneNode(true);

// ---------------------------------------------------------------- helpers

function duration(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const m = Math.floor(s / 60);
  if (m >= 60) return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
  return m ? `${m} min ${String(s % 60).padStart(2, '0')}s` : `${s}s`;
}

function ago(iso) {
  if (!iso) return '';
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (Number.isNaN(d)) return '';
  if (d < 90) return 'just now';
  if (d < 3600) return `${Math.round(d / 60)} min ago`;
  if (d < 86400) return `${Math.round(d / 3600)} h ago`;
  if (d < 86400 * 7) return `${Math.round(d / 86400)} d ago`;
  return new Date(iso).toLocaleDateString();
}

// Tables from Markdown can be wider than the measure; give each its own
// scroller so the reading column itself never scrolls sideways.
function wrapTables(root) {
  root.querySelectorAll('table').forEach(t => {
    if (t.parentElement?.classList.contains('table-wrap')) return;
    const wrap = h('div', { class: 'table-wrap' });
    t.replaceWith(wrap);
    wrap.append(t);
  });
}

// ----------------------------------------------------------------- state

const state = {
  timer: null, slug: null, file: null,
  // The view on screen ("companion:<slug>", "paper:<slug>", …) and where each
  // was scrolled to, so moving between the companion and the paper returns
  // the reader to the line they left.
  view: null, scrolls: {},
  viewer: null,              // the drawn paper, kept while its paper is open
  // The reading on screen, or last on screen for this paper: its level, and
  // its run when it has one. The paper view, the bar and the explanations all
  // follow it, so moving to the paper and back returns to the same companion.
  reading: null,
  afterRender: null,
  // Papers known to have a companion, so the bar can offer them side by side
  // with the paper; and the companion last on screen, which that view shows.
  withCompanion: new Set(),
  lastCompanion: null,
  // What each thing on screen is — a paper, several papers read together, a
  // search (library.KIND_LEVELS) — learnt from whatever loaded it, so that
  // the bar offers what it has.
  kinds: new Map(),
  searchPicker: null,
  searchDraft: null,
  // While a paper, or several, is being chosen: what a PDF dropped on the
  // page (or added under it) does — join them, to be read together.
  addTo: null,
};

// A paper and a collection each have Expert and Non-expert editions. A search
// has one mode. Levels keep their histories and harnesses separate.
const LEVEL_NAMES = { expert: 'Expert', general: 'Non-expert',
  'collection-expert': 'Expert', 'collection-nonexpert': 'Non-expert', search: 'Search' };
const OTHER = { expert: 'general', general: 'expert',
  'collection-expert': 'collection-nonexpert',
  'collection-nonexpert': 'collection-expert' };
const COLLECTION_LEVELS = new Set(['collection-expert', 'collection-nonexpert']);
const collectionLevel = (level = null) => COLLECTION_LEVELS.has(level) ? level
  : settings.get().level === 'general' ? 'collection-nonexpert' : 'collection-expert';
// The readings that first ask their reader what they know (questions.js): a
// paper's Non-expert reading, and a collection's (the server's
// questions.ASKING_LEVEL).
const ASKS_FIRST = new Set(['general', 'collection-nonexpert']);

// What each kind of thing calls itself and what reading it gives.
const KIND = {
  paper: { result: 'companion', results: 'Companion', ready: 'Companion ready',
           earlier: 'Earlier companion', again: 'New run' },
  collection: { result: 'collection companion', results: 'Companion', ready: 'Companion ready',
                earlier: 'Earlier companion', again: 'New run' },
  search: { result: 'search', results: 'Results', ready: 'Results ready',
            earlier: 'Earlier results', again: 'Search again' },
};
const kindOf = (thing) => (thing?.kind in KIND ? thing.kind : 'paper');
// "Expert reading", "Non-expert reading", "Search".
const readingName = (level) => (level === 'search' ? 'Search'
  : level in LEVEL_NAMES ? `${LEVEL_NAMES[level]} reading` : '');
function noteKind(paper) {
  if (paper?.slug) state.kinds.set(paper.slug, kindOf(paper));
  return kindOf(paper);
}

function levelHash(slug, level, run = null) {
  return `#/p/${slug}${level ? `/${level}` : ''}${level && run ? `/${run}` : ''}`;
}

function sideHash(slug, level = null, run = null) {
  return `#/p/${slug}/side${level ? `/${level}` : ''}${level && run ? `/${run}` : ''}`;
}

function query(params) {
  const q = Object.entries(params).filter(([, v]) => v).map(([k, v]) => `${k}=${encodeURIComponent(v)}`);
  return q.length ? `?${q.join('&')}` : '';
}

// The reading last on screen for this paper, if it was this one.
function readingOf(slug) {
  return state.reading?.slug === slug ? state.reading : null;
}

function stopPolling() {
  if (state.timer) { clearInterval(state.timer); state.timer = null; }
}

// Setting the hash to the one already there fires no event, so the page would
// never move: pressing Expert on a paper opened from the library started the
// reading and left the buttons saying "starting…" for good.
function go(slug, view = 'main') {
  const want = !slug ? '#/'
    : view === 'paper' ? `#/p/${slug}/paper`
      : view === 'runs' ? `#/p/${slug}/runs`
        : `#/p/${slug}`;
  if (location.hash === want) route(); else location.hash = want;
}

// "Settings" where the bar is short of room (app.css): since About joined
// it, the whole name no longer fits beside the others on a narrow window.
function settingsButton() {
  return h('button', { class: 'ghost', type: 'button', id: 'open-settings',
                       'aria-label': 'Advanced settings', onclick: () => settings.open() },
    h('span', { class: 'label-long', text: 'Advanced settings' }),
    h('span', { class: 'label-short', text: 'Settings' }));
}

// Resolves once the view the hash names has been drawn.
function navigate(hash) {
  return new Promise((resolve) => {
    state.afterRender = resolve;
    if (location.hash === hash) route(); else location.hash = hash;
  });
}

// #/p/<slug>                 the paper, at its newest companion
// #/p/<slug>/<level>[/<run>]  one level's reading, or one reading in particular
// #/p/<slug>/paper[/<page>]   the paper itself
// #/p/<slug>/side[/<level>[/<run>]]  a companion and the paper, side by side
// #/p/<slug>/runs             every reading made from this paper
// #/ready                     Getting ready: what this computer still needs
function parseRoute() {
  if (location.hash === '#/ready') return { view: 'ready' };
  const m = location.hash.match(
    /^#\/p\/([A-Za-z0-9._-]+)(?:\/([a-z-]+)(?:\/([A-Za-z0-9._-]+)(?:\/([A-Za-z0-9._-]+))?)?)?/);
  if (!m) return null;
  const [, slug, part, extra, more] = m;
  if (part === 'side') {
    const level = extra in LEVEL_NAMES ? extra : null;
    return { slug, view: 'side', level, run: level ? more || null : null };
  }
  if (part === 'paper') {
    return { slug, view: 'paper', page: extra && /^\d+$/.test(extra) ? Number(extra) : null };
  }
  if (part === 'runs') return { slug, view: 'runs' };
  if (part in LEVEL_NAMES) return { slug, view: 'main', level: part, run: extra || null };
  return { slug, view: 'main', level: null, run: null };
}

function currentSlug() {
  return parseRoute()?.slug || null;
}

// Companion (or the reading, before there is one) and the paper, side by side
// in the bar: the two things a reader moves between.
function viewSwitch(slug, active, mainLabel) {
  const back = readingOf(slug);
  const last = state.lastCompanion?.slug === slug ? state.lastCompanion : null;
  const tab = (label, view, short = null) => h('a', {
    class: `seg${active === view ? ' on' : ''}`, 'data-view': view,
    href: view === 'paper' ? `#/p/${slug}/paper`
      : view === 'runs' ? `#/p/${slug}/runs`
        : view === 'side' ? sideHash(slug, last?.level, last?.run)
          : back ? levelHash(slug, back.level, back.run) : `#/p/${slug}`,
    'aria-current': active === view ? 'page' : null,
    title: short ? label : null,
  }, short ? [h('span', { class: 'seg-long', text: label }), h('span', { class: 'seg-short', text: short })]
    : label);
  // Side by side needs a companion to put beside the paper. Several papers
  // have their papers listed instead; a search has its results and its runs.
  const kind = state.kinds.get(slug) || 'paper';
  if (kind === 'search') {
    return h('nav', { class: 'switch', 'aria-label': 'Results or runs' },
      tab(mainLabel === 'Companion' ? 'Results' : mainLabel, 'main'), tab('Runs', 'runs'));
  }
  if (kind === 'collection') {
    return h('nav', { class: 'switch', 'aria-label': 'Reading, papers, or runs' },
      tab(mainLabel, 'main'), tab('Papers', 'paper'), tab('Runs', 'runs'));
  }
  return h('nav', { class: 'switch', 'aria-label': 'Companion, paper, both, or runs' },
    tab(mainLabel, 'main'), tab('Paper', 'paper'),
    state.withCompanion.has(slug) ? tab('Side by side', 'side', 'Both') : null,
    tab('Runs', 'runs'));
}

// `tips`: the screen whose tips the "?" shows again (hints.js).
function barFor(slug, active, mainLabel, tips) {
  mount($('#bar-right'),
    viewSwitch(slug, active, mainLabel),
    tips ? hints.button(tips) : null,
    feedback.button(),
    h('button', { class: 'ghost newpaper', text: 'New paper', onclick: () => go(null) }));
}

function setReading(slug, level, run) {
  state.reading = { slug, level, run: run || null };
  notes.setReading(state.reading);
}

function restoreScroll(key) {
  const y = state.scrolls[key];
  window.scrollTo(0, typeof y === 'number' ? y : 0);
}

// ------------------------------------------------------------ drop view

async function renderDrop({ keepHash = false, showLibrary = true } = {}) {
  state.searchPicker?.dispose();
  state.searchPicker = null;
  // Leaving an in-flight upload means leaving that selection too. Otherwise a
  // request which finishes after the new drop screen appears can resurrect the
  // old papers and carry them into the next attempt.
  await cancelActiveUpload({ restore: false });
  stopPolling();
  state.addTo = null;
  state.slug = keepHash ? state.slug : null;
  state.file = null;
  state.view = keepHash ? `choice:${state.slug}` : 'home';
  if (!keepHash) notes.detach();
  mount($('#bar-right'), ready.pill(), about.button(), hints.button(keepHash ? 'launch' : 'home'),
        feedback.button(), settingsButton());
  // Known a moment later, if this page has not asked yet.
  const here = state.view;
  ready.state().then(() => {
    const bar = $('#bar-right');
    const pill = ready.pill();
    if (pill && state.view === here && !bar.querySelector('.ready-pill')) bar.prepend(pill);
  });

  const view = tpl('tpl-drop');
  mount($('#view'), view);
  const library = $('#library', view);
  library.hidden = !showLibrary;
  // A new version waiting: said at the top of the home page, once it is known.
  if (!keepHash) {
    lifecycle.updateStatus().then((status) => {
      const note = lifecycle.banner(status);
      if (note && view.isConnected && !view.querySelector('.update-banner')) view.prepend(note);
    });
    // Whether to hear about new versions: asked once, the first time (news.js).
    signup.maybeAsk(view);
  }

  const drop = $('#drop', view);
  const input = $('#file', view);
  const chosen = $('#chosen', view);

  // The box is the input's <label>, so a click opens the file dialog by itself.
  // It must not be opened from script as well: that asked for two dialogs per
  // click, and Safari shows the first, then delivers the chosen file to the
  // second request, which has replaced it and is never shown — so choosing a
  // PDF did nothing at all (reproduced in WebKit, 2026-09-29). Only the
  // keyboard needs help, because Enter and Space do not activate a label.
  drop.addEventListener('click', (e) => {
    if (drop.classList.contains('busy')) e.preventDefault();
  });
  drop.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      if (!drop.classList.contains('busy')) input.click();
    }
  });
  input.addEventListener('change', () => {
    const files = [...input.files];
    // Cleared at once, so that choosing the same file again — after running
    // OCR on it, say — is still a change.
    input.value = '';
    if (files.length) take(files, drop, chosen);
  });

  // A question about the literature, rather than a paper: a search.
  const find = $('#find', view);
  find.addEventListener('submit', (e) => {
    e.preventDefault();
    const field = $('#find-q', find);
    const asked = field.value.trim();
    if (asked.length < 3) { field.focus(); toast('Say what you are looking for, in a few words.', 'err'); return; }
    startSearch(asked, drop, chosen);
  });

  // A paper named by its arXiv number, or several: fetched by the server, then
  // taken as chosen PDFs are (arxivItems, intake). What was typed stays, so
  // that a number mistyped can be put right.
  const byNumber = $('#arxiv', view);
  byNumber.addEventListener('submit', (e) => {
    e.preventDefault();
    const field = $('#arxiv-q', byNumber);
    const items = arxivItems(field.value);
    if (!items.length) {
      field.focus();
      toast('Give a paper’s arXiv number, such as 2309.01234.', 'err');
      return;
    }
    if (drop.classList.contains('busy')) return;
    take(items, drop, chosen);
  });

  if (pendingDrop) {
    const files = pendingDrop;
    pendingDrop = null;
    take(files, drop, chosen);
  }

  if (showLibrary) await renderLibrary(library);
  if (!keepHash && !state.file) hints.show('home');
}

// The whole page accepts a drop, not only the dashed box: aiming at a target
// is a thing a tool asks of you, and there is no reason to ask. Registered once
// for the life of the page — attached per visit to the home view, the handlers
// piled up and each drop was handled several times over.
let pendingDrop = null;

function dragFiles(e) {
  return [...(e.dataTransfer?.types || [])].includes('Files');
}

document.addEventListener('dragover', (e) => {
  if (!dragFiles(e)) return;
  e.preventDefault();
  const drop = $('#drop');
  if (drop && !drop.hidden) drop.classList.add('over');
  $('#drop-more')?.classList.add('over');
});

document.addEventListener('dragleave', (e) => {
  // Only when the drag leaves the window, not every time it crosses a child.
  if (e.relatedTarget !== null) return;
  $('#drop')?.classList.remove('over');
  $('#drop-more')?.classList.remove('over');
});

document.addEventListener('drop', (e) => {
  if (!dragFiles(e)) return;
  e.preventDefault();
  $('#drop')?.classList.remove('over');
  $('#drop-more')?.classList.remove('over');
  const files = [...(e.dataTransfer?.files || [])];
  if (!files.length) return;
  const drop = $('#drop'), chosen = $('#chosen');
  if (drop && chosen) {
    // Not while files are still being read: that would start a second
    // upload over the first.
    if (drop.classList.contains('busy')) return;
    // A paper, or several, already chosen: what is dropped joins them, so
    // that papers can be gathered one at a time before a reading starts.
    if (state.addTo && !chosen.hidden) { state.addTo(files); return; }
    take(files, drop, chosen);
    return;
  }
  // Dropped onto a companion or a reading in progress: that is a new paper.
  pendingDrop = files;
  go(null);
});

// One file is a paper; several are papers to read together. A paper already
// chosen is joined by more, dropped or chosen later (addToCollectionFiles).
function take(files, drop, chosen) {
  if (files.length === 1) upload(files[0], drop, chosen);
  else uploadMany(files, drop, chosen);
}

const isPdf = (file) => !file.type || file.type === 'application/pdf' || /\.pdf$/i.test(file.name);
const MAX_TOGETHER = 12;

// A paper named by its arXiv number rather than chosen as a file. It goes
// wherever a chosen PDF goes — alone, several to read together, or added to
// papers already chosen — and only `intake` tells the two apart: the server
// fetches it from arXiv (arxiv.py), from an address it makes itself.
const arxivItem = (number) => ({ name: `arXiv:${number}`, type: 'application/pdf', arxiv: number });

// The arXiv numbers in what was typed or pasted — one, or several to read
// together: new ones (2309.01234) and old ones (math/0601001), with a version
// or without, alone or inside an arxiv.org address or a line copied from a
// paper ("arXiv:2309.01234v2 [math.GT] 12 Oct 2023"). Text with none is sent
// as it is, for the server to say what it is not.
const ARXIV_NUMBER = /(?:\b\d{4}\.\d{4,5}|\b[a-z]+(?:-[a-z]+)?(?:\.[a-z]+(?:-[a-z]+)?)?\/\d{7})(?:v\d+)?\b/gi;
function arxivItems(text) {
  const typed = String(text || '').trim();
  const found = [...typed.matchAll(ARXIV_NUMBER)].map(m => m[0]);
  return [...new Set(found.length ? found : typed ? [typed] : [])].map(arxivItem);
}

// What a chosen PDF, or a paper named by its arXiv number, becomes on the
// server: a paper either way, as POST /api/papers or /api/arxiv answers.
function intake(item, signal) {
  if (item.arxiv) return api('POST', '/api/arxiv', { id: item.arxiv }, { signal });
  const form = new FormData();
  form.set('file', item);
  return api('POST', '/api/papers', form, { signal });
}

// Hidden while a choice is being made, as the drop box is: one thing at a time
// (the search, and the box for an arXiv number).
function setFinding(shown) {
  for (const form of [$('#find'), $('#arxiv')]) if (form) form.hidden = !shown;
}

// Uploads are deliberately cancellable.  A PDF request already in flight is
// aborted; papers created before cancellation are removed only when this
// upload created them, never when they were already in the library.
let activeUpload = null;

function resetUploadChoice(drop, chosen) {
  state.file = null;
  state.addTo = null;
  drop.classList.remove('busy');
  drop.hidden = false;
  chosen.hidden = true;
  mount(chosen);
  setFinding(true);
  void renderLibrary($('#library'));
}

function beginUpload(drop, chosen, restore) {
  const controller = new AbortController();
  const fresh = new Set();
  let cancelled = false;
  const upload = {
    signal: controller.signal,
    fresh,
    get cancelled() { return cancelled; },
    async cancel({ restore: shouldRestore = true, announce = false } = {}) {
      if (cancelled) return;
      cancelled = true;
      controller.abort();
      if (activeUpload === upload) activeUpload = null;
      await Promise.all([...fresh].map(async (slug) => {
        try { await api('DELETE', `/api/papers/${slug}`); } catch { /* best effort */ }
      }));
      if (shouldRestore) restore();
      if (announce) toast('Upload cancelled.');
    },
  };
  activeUpload = upload;
  return upload;
}

function finishUpload(upload) {
  if (activeUpload === upload) activeUpload = null;
}

async function cancelActiveUpload(options) {
  if (activeUpload) await activeUpload.cancel(options);
}

function cancelUploadButton(upload) {
  return h('button', { class: 'linkish', type: 'button', text: 'Cancel upload',
                       onclick: () => void upload.cancel({ announce: true }) });
}

// Several PDFs at once: each becomes a paper as a single one would (and is
// still readable on its own), then they are put together, to be read as one.
async function uploadMany(files, drop, chosen) {
  const pdfs = files.filter(isPdf);
  if (pdfs.length < files.length) {
    toast(`${files.length - pdfs.length} of those files ${files.length - pdfs.length === 1 ? 'is' : 'are'} not a PDF and ${files.length - pdfs.length === 1 ? 'was' : 'were'} left out.`);
  }
  if (pdfs.length < 2) {
    if (pdfs.length) upload(pdfs[0], drop, chosen);
    else toast('unpacman reads PDFs. None of those is one.', 'err');
    return;
  }
  if (pdfs.length > MAX_TOGETHER) {
    toast(`At most ${MAX_TOGETHER} papers can be read together. Choose fewer.`, 'err');
    return;
  }
  drop.classList.add('busy');
  setFinding(false);
  const upload = beginUpload(drop, chosen, () => resetUploadChoice(drop, chosen));
  const lines = pdfs.map(f => h('li', { class: 'many-item' },
    h('span', { class: 'spin' }), ' ', h('span', { class: 'many-name', text: f.name })));
  const numbers = pdfs.every(f => f.arxiv);
  mount(chosen, h('div', { class: 'chosen-sub', text: numbers
          ? `Fetching ${pdfs.length} papers from arXiv…` : `Reading ${pdfs.length} files…` }),
        h('ol', { class: 'many' }, lines),
        h('div', { class: 'chosen-foot' }, cancelUploadButton(upload)));
  chosen.hidden = false;

  const got = [];
  const fresh = upload.fresh;
  const failed = [];
  for (const [i, file] of pdfs.entries()) {
    try {
      const paper = await intake(file, upload.signal);
      if (upload.cancelled) {
        if (!paper.existing) {
          fresh.add(paper.slug);
          try { await api('DELETE', `/api/papers/${paper.slug}`); } catch { /* best effort */ }
        }
        return;
      }
      got.push(paper);
      if (!paper.existing) fresh.add(paper.slug);
      mount(lines[i], h('span', { class: 'many-ok', 'aria-hidden': 'true', text: '✓' }), ' ',
            h('span', { class: 'many-name', text: paper.title }));
    } catch (err) {
      if (upload.cancelled) return;
      failed.push(`${file.name}: ${err.message}`);
      mount(lines[i], h('span', { class: 'many-bad', 'aria-hidden': 'true', text: '✕' }), ' ',
            h('span', { class: 'many-name', text: `${file.name} — ${err.message}` }));
    }
  }
  if (upload.cancelled) return;
  drop.classList.remove('busy');
  finishUpload(upload);

  const papers = [...new Map(got.map(p => [p.slug, p])).values()];
  const giveUp = (message) => {
    mount(chosen, h('div', { class: 'chosen-sub', text: message }),
      h('ol', { class: 'many' }, lines),
      h('div', { class: 'chosen-foot' },
        h('button', { class: 'linkish', type: 'button',
                      text: numbers ? 'Try other numbers' : 'Choose other PDFs', onclick: async () => {
          for (const slug of fresh) { try { await api('DELETE', `/api/papers/${slug}`); } catch { /* harmless */ } }
          mount(chosen); chosen.hidden = true; drop.hidden = false; setFinding(true);
          renderLibrary($('#library'));
        } })));
  };
  if (papers.length < 2) {
    if (papers.length === 1) {
      toast(failed.length ? 'Only one of those could be read; here it is on its own.'
        : 'Those are the same paper; here it is.');
      showChoice(papers[0], drop, chosen, { fresh: fresh.has(papers[0].slug) });
    } else {
      giveUp('None of those could be read.');
    }
    return;
  }
  let together;
  try {
    together = await api('POST', '/api/collections', { papers: papers.map(p => p.slug) });
  } catch (err) {
    toast(err.message, 'err');
    giveUp('The papers could not be put together.');
    return;
  }
  if (together.existing) toast('You have read these papers together before — opening them.');
  showChoice(together, drop, chosen, { fresh: !together.existing, freshMembers: [...fresh],
                                       notes: failed });
}

// A first PDF need not decide that it will be read alone: under it, a small
// drop box takes more — dropped there or anywhere on the page, or chosen —
// one at a time or several at once (2026-10-13). As the large one, it is the
// file input's <label>: clicking it opens the dialog by itself, and only the
// keyboard is helped (two dialogs per click lost Safari's choice, 2026-09-29).
function addStrip(kind, add) {
  const input = h('input', { type: 'file', accept: 'application/pdf,.pdf', multiple: true,
                             hidden: true });
  input.addEventListener('change', () => {
    const files = [...input.files];
    input.value = '';
    if (files.length) add(files);
  });
  const strip = h('label', { class: 'drop-more', id: 'drop-more', tabindex: '0' },
    input,
    h('span', { class: 'drop-more-plus', 'aria-hidden': 'true', text: '+' }),
    h('span', {}, h('b', { text: 'Add another paper' }),
      kind === 'collection' ? ' — drop a PDF here, or click to choose one.'
        : ', to read them together — drop a PDF here, or click to choose one.'));
  strip.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
  });
  // Or a paper named by its arXiv number, fetched and added the same way. A
  // form of its own beside the strip: inside it, a click on the box would
  // open the file dialog.
  const number = h('input', { type: 'text', maxlength: '1000', autocomplete: 'off',
                              autocapitalize: 'off', spellcheck: 'false',
                              'aria-label': 'The arXiv number of a paper to add',
                              placeholder: 'or its arXiv number, e.g. 2309.01234' });
  const byNumber = h('form', { class: 'more-arxiv' }, number,
    h('button', { class: 'ghost', type: 'submit', text: 'Add' }));
  byNumber.addEventListener('submit', (e) => {
    e.preventDefault();
    const items = arxivItems(number.value);
    if (!items.length) {
      number.focus();
      toast('Give a paper’s arXiv number, such as 2309.01234.', 'err');
      return;
    }
    add(items);
  });
  return h('div', { class: 'more' }, strip, byNumber);
}

async function addToCollectionFiles(paper, drop, chosen, choice, files) {
  const pdfs = files.filter(isPdf);
  if (pdfs.length < files.length) {
    toast(`${files.length - pdfs.length} of those files ${files.length - pdfs.length === 1 ? 'is' : 'are'} not a PDF and ${files.length - pdfs.length === 1 ? 'was' : 'were'} left out.`);
  }
  if (!pdfs.length) return;

  const kind = noteKind(paper);
  const members = kind === 'collection' ? (paper.members || []).map(m => m.slug) : [paper.slug];
  if (members.length + pdfs.length > MAX_TOGETHER) {
    toast(`A collection can contain at most ${MAX_TOGETHER} papers. Choose fewer.`, 'err');
    return;
  }

  drop.classList.add('busy');
  const upload = beginUpload(drop, chosen, () => showChoice(paper, drop, chosen, choice));
  const lines = pdfs.map(f => h('li', { class: 'many-item' },
    h('span', { class: 'spin' }), ' ', h('span', { class: 'many-name', text: f.name })));
  mount(chosen, h('div', { class: 'chosen-name', text: paper.title }),
        h('div', { class: 'chosen-sub', text: `Adding ${pdfs.length} paper${pdfs.length === 1 ? '' : 's'}…` }),
        h('ol', { class: 'many' }, lines),
        h('div', { class: 'chosen-foot' }, cancelUploadButton(upload)));
  chosen.hidden = false;

  const got = [];
  const fresh = upload.fresh;
  const failed = [];
  for (const [i, file] of pdfs.entries()) {
    try {
      const added = await intake(file, upload.signal);
      if (upload.cancelled) {
        if (!added.existing) {
          fresh.add(added.slug);
          try { await api('DELETE', `/api/papers/${added.slug}`); } catch { /* best effort */ }
        }
        return;
      }
      got.push(added);
      if (!added.existing) fresh.add(added.slug);
      mount(lines[i], h('span', { class: 'many-ok', 'aria-hidden': 'true', text: '✓' }), ' ',
            h('span', { class: 'many-name', text: added.title }));
    } catch (err) {
      if (upload.cancelled) return;
      failed.push(`${file.name}: ${err.message}`);
      mount(lines[i], h('span', { class: 'many-bad', 'aria-hidden': 'true', text: '✕' }), ' ',
            h('span', { class: 'many-name', text: `${file.name} — ${err.message}` }));
    }
  }
  if (upload.cancelled) return;
  drop.classList.remove('busy');
  finishUpload(upload);

  const slugs = [...new Set([...members, ...got.map(p => p.slug)])];
  if (slugs.length === new Set(members).size) {
    toast('None of those files adds a new paper to this collection.');
    showChoice(paper, drop, chosen, choice);
    return;
  }

  let together;
  try {
    together = await api('POST', '/api/collections', { papers: slugs });
  } catch (err) {
    // New source papers should not be stranded merely because a collection
    // record could not be made. They are already visible in the library.
    toast(`The additional papers were uploaded, but could not be grouped: ${err.message}`, 'err');
    showChoice(paper, drop, chosen, choice);
    return;
  }

  // A just-created collection was only a staging container. Once it grows,
  // remove that unrun container while retaining all of its member papers.
  if (kind === 'collection' && choice.fresh && paper.slug !== together.slug) {
    try { await api('DELETE', `/api/papers/${paper.slug}`); } catch { /* harmless */ }
  }
  const priorFresh = kind === 'collection' ? choice.freshMembers
    : choice.fresh ? [paper.slug] : [];
  showChoice(together, drop, chosen, {
    fresh: !together.existing,
    freshMembers: [...new Set([...priorFresh, ...fresh])],
    notes: [...(choice.notes || []), ...failed],
  });
}

// A question about the literature: made a search (or found again, if it has
// been asked before), then started like a reading.
async function startSearch(asked, drop, chosen) {
  let search;
  try { search = await api('POST', '/api/searches', { query: asked }); }
  catch (err) { toast(err.message, 'err'); return; }
  if (search.existing && search.state === 'reading') {
    toast('That search is running now — opening it.');
    go(search.slug);
    return;
  }
  showChoice(search, drop, chosen, { fresh: !search.existing });
}

async function upload(file, drop, chosen) {
  if (!isPdf(file)) {
    toast('unpacman reads PDFs. That file is not one.', 'err');
    return;
  }
  drop.classList.add('busy');
  const upload = beginUpload(drop, chosen, () => resetUploadChoice(drop, chosen));
  mount(chosen, h('div', { class: 'chosen-sub' },
    h('span', { class: 'spin' }), file.arxiv ? ` Fetching ${file.name} from arXiv…` : ' Reading the file…'),
  h('div', { class: 'chosen-foot' }, cancelUploadButton(upload)));
  chosen.hidden = false;

  let paper;
  try {
    paper = await intake(file, upload.signal);
    if (upload.cancelled) {
      if (!paper.existing) {
        upload.fresh.add(paper.slug);
        try { await api('DELETE', `/api/papers/${paper.slug}`); } catch { /* best effort */ }
      }
      return;
    }
    if (!paper.existing) upload.fresh.add(paper.slug);
  } catch (err) {
    if (upload.cancelled) return;
    chosen.hidden = true;
    mount(chosen);
    toast(err.message, 'err');
    return;
  } finally {
    drop.classList.remove('busy');
    finishUpload(upload);
  }

  // The same bytes dropped twice are the same paper. It can still be the first
  // member of a new collection, so leave it at the launch panel rather than
  // automatically taking the reader to its earlier companion.
  if (paper.existing) {
    if (paper.state === 'ready' || paper.state === 'reading') {
      toast('You already have this paper. Add another paper to read them together.');
    } else {
      toast('You uploaded this paper before; it has not been read yet.');
    }
  }
  showChoice(paper, drop, chosen, { fresh: !paper.existing });
}

// Once a paper is chosen, the empty drop box has done its job; leaving it on
// screen pulls the eye away from the decisions that remain. Choosing a level
// starts nothing: the reading begins only when Start is pressed, with the AI
// and the limits in plain view above it, so that what is about to be spent is
// seen before it is. `back` is for reading a paper again: there, the way out
// returns to the paper rather than offering to swap it for another.
// `readings` says what each level already has, so that neither choice hides
// what starting it does; `level` is the one to offer first ("Also read it for
// a non-expert").
//
// The same panel starts several papers read together (their list, at either
// reader level) and a search (its question, editable). `freshMembers` are the
// papers uploaded for a collection just now, discarded with it if the reader
// changes their mind; `notes` what could not be read of what they dropped.
function showChoice(paper, drop, chosen, { fresh, back = null, readings = null, level = null,
                                          freshMembers = [], notes = [] }) {
  state.file = paper;
  const kind = noteKind(paper);
  drop.hidden = true;
  setFinding(false);
  chosen.hidden = false;
  const library = $('#library');
  if (library && !library.hidden) renderLibrary(library);
  const again = { fresh, back, readings, freshMembers, notes };

  const busy = (lv) => readings?.[lv]?.state === 'reading';
  // The level asked for, else the last one chosen here — never one already
  // being read, which could not be started again.
  let chosenLevel = kind === 'collection' ? collectionLevel(level) : kind === 'search' ? 'search'
    : [level, settings.get().level].find(lv => lv in OTHER && !busy(lv)) || null;
  // Which AI, how hard, within what limits: filled in by the panel below.
  // Until it has loaded, Start waits — pressed in that moment it would run the
  // server's default, which may be the expensive model this browser was
  // deliberately switched away from.
  let pick = null;
  let valid = false;
  let none = false;              // no AI can read here at all
  let blocked = '';              // what this computer still needs (ready.js)
  let starting = false;
  let sending = false;           // the reading's request is on its way

  // More papers join this one (or these), to be read together: from the small
  // drop box under it, or dropped anywhere on the page (the document's drop
  // handler) — never once the reading is being started.
  const add = (files) => {
    if (starting || sending) return;
    addToCollectionFiles(paper, drop, chosen, again, files);
  };
  state.addTo = kind === 'search' ? null : add;
  const more = kind === 'search' ? null : addStrip(kind, add);

  // --- what is being started -------------------------------------------------
  let sub = [];
  let what = [];
  let question = null;           // a search's question, which may be changed
  const card = (lv, title, blurb, setting = null) => {
    const input = h('input', { type: 'radio', name: 'level', value: lv, class: 'sr-only',
                               disabled: busy(lv) });
    input.checked = chosenLevel === lv;
    input.addEventListener('change', () => {
      chosenLevel = lv;
      if (setting) settings.set({ level: setting }, 'choice');
      paint();
    });
    return h('label', { class: 'choice', 'data-level': lv }, input,
      h('b', { text: title }), h('span', { text: blurb }),
      busy(lv)
        ? h('span', { class: 'choice-note', text: 'Being read now.' })
        : readings?.[lv]?.state === 'ready'
          ? h('span', { class: 'choice-note',
                        text: 'You have this companion. A new one keeps it until the new one is finished.' })
          : null);
  };
  if (kind === 'paper') {
    sub = [paper.original_name];
    if (paper.words) sub.push(`about ${Number(paper.words).toLocaleString()} words`);
    what = [
      more,
      h('div', { class: 'ask', id: 'ask-level', text: 'How well do you know this area?' }),
      h('div', { class: 'choices', role: 'radiogroup', 'aria-labelledby': 'ask-level' },
        card('expert', 'Expert', 'I work in this field. Keep the technical detail.', 'expert'),
        card('general', 'Non-expert',
             'Explain the ideas and what they rest on. A few questions about what you know come first.', 'general')),
    ];
  } else if (kind === 'collection') {
    const members = paper.members || [];
    sub = [`${members.length} papers, to be read together`];
    what = [
      h('ol', { class: 'members' }, members.map(m => h('li', {},
        h('a', { href: `#/p/${m.slug}/runs`, text: m.title }),
        // Taken out again before the reading, as easily as it was added.
        // Not from a set opened to read it again (`back`): that set is what
        // is being read.
        back ? null : h('button', { class: 'linkish member-remove', type: 'button', text: 'Remove',
                                    'aria-label': `Remove ${m.title}`,
                                    onclick: () => removeMember(m.slug) })))),
      more,
      h('div', { class: 'ask', id: 'ask-level', text: 'Who is this collection companion for?' }),
      h('div', { class: 'choices', role: 'radiogroup', 'aria-labelledby': 'ask-level' },
        card('collection-expert', 'Expert',
             'Precise main results or findings, historical context, and bibliography for each paper.', 'expert'),
        card('collection-nonexpert', 'Non-expert',
             'The same focused sections, with the background and notation support of a non-expert companion. '
             + 'A few questions about what you know come first.', 'general')),
    ];
  } else {
    sub = ['A search of the literature'];
    question = h('textarea', { class: 'find-edit', id: 'search-q', rows: '2', maxlength: '1000',
                               'aria-label': 'What you are looking for' });
    question.value = paper.query || paper.title;
    question.addEventListener('input', () => paint());
    what = [
      h('label', { class: 'ask', for: 'search-q', text: 'What are you looking for?' }),
      question,
      h('p', { class: 'launch-note',
               text: 'unpacman looks on arXiv, zbMATH Open, OpenAlex and the wider web, '
                     + 'confirms every paper it lists at its source, and lists them with '
                     + 'references and links. Any paper with an open PDF can then be added '
                     + 'here, to read, in one click.' }),
    ];
  }

  const mapToggle = h('input', { type: 'checkbox', checked: settings.get().proofMap ? true : null });
  mapToggle.addEventListener('change', () => settings.set({ proofMap: mapToggle.checked }, 'choice'));
  // Written for the reader's own background, when they have given one: said
  // here, before Start, and easily left out of this one reading.
  const forYou = kind === 'search' ? null : forYouAddon();

  const panel = h('div', { class: 'launch-ai' });
  const start = h('button', { class: 'primary start', type: 'button', id: 'start-reading' });
  const hint = h('span', { class: 'launch-hint' });
  const label = () => kind === 'search' ? 'Start the search' : 'Start generating the companion';
  const asked = () => !question || question.value.trim().length >= 3;
  const paint = () => {
    start.disabled = starting || !pick || !valid || !chosenLevel || busy(chosenLevel) || !!blocked
      || !asked();
    if (starting) return;
    start.textContent = label();
    // What still stands in the way, with the way to put it right.
    const fix = h('a', { href: '#/ready', class: 'launch-fix', text: 'Getting ready' });
    if (none) mount(hint, 'No AI can read here yet. ', fix);
    else if (blocked) mount(hint, `${blocked} `, fix);
    else {
      mount(hint, !chosenLevel ? 'Choose a level first.'
        : !asked() ? 'Say what you are looking for.'
          : !pick ? 'Loading the choice of AI…'
            : !valid ? 'Check the limits above.'
              : ASKS_FIRST.has(chosenLevel)
                ? 'A few questions come next (they cost a few cents); then the reading starts.'
                : 'Nothing is spent until you start it.');
    }
  };
  agentPanel(panel, (now) => {
    pick = now.pick; valid = now.valid; none = !!now.none; blocked = now.blocked || '';
    paint();
  }, kind === 'paper' ? {} : { level: chosenLevel, latex: kind === 'collection' });
  paint();

  // The reading request: the level, the AI and its limits, and whatever this
  // kind of thing adds (the proof map; a search's question; the answers).
  async function send(extra = {}) {
    sending = true;
    try {
      await api('POST', `/api/papers/${paper.slug}/read`,
                { level: chosenLevel, ...pick, ...settings.readingCaps(),
                  ...(kind === 'paper' ? settings.readingOptions() : {}),
                  ...(question ? { query: question.value.trim() } : {}),
                  ...(kind !== 'search' && settings.get().useCv === false ? { use_cv: false } : {}),
                  ...extra });
    } finally {
      sending = false;
    }
    if (kind !== 'search') firstAsk.begin();
    await navigate(levelHash(paper.slug, chosenLevel));
  }

  start.addEventListener('click', async () => {
    if (start.disabled) return;
    // A Non-expert reading asks first what its reader knows (questions.js),
    // of one paper or of several read together.
    if (ASKS_FIRST.has(chosenLevel)) {
      const questioned = chosenLevel;
      askFirst(chosen, {
        paper,
        // Chosen with the reader's background too, unless it is left out.
        pick: settings.get().useCv === false ? { ...pick, use_cv: false } : pick,
        start: (profile) => send(profile ? { profile } : {}),
        back: () => showChoice(paper, drop, chosen, { ...again, level: questioned }),
      });
      return;
    }
    starting = true;
    const controls = [...chosen.querySelectorAll('button, select, input, textarea')];
    controls.forEach(c => { c.dataset.was = c.disabled ? '1' : ''; c.disabled = true; });
    mount(start, h('span', { class: 'spin' }), ' Starting…');
    try {
      await send();
    } catch (err) {
      toast(err.message, 'err');
      starting = false;
      controls.forEach(c => { c.disabled = c.dataset.was === '1'; });
      paint();
    }
  });

  const unread = notes.length ? h('div', { class: 'notice' },
    `${notes.length === 1 ? 'One file' : `${notes.length} files`} could not be read and `
    + `${notes.length === 1 ? 'is' : 'are'} left out: ${notes.join('; ')}`) : null;
  mount(chosen,
    h('div', { class: 'chosen-name', text: paper.title }),
    h('div', { class: 'chosen-sub', text: sub.filter(Boolean).join(' · ') }),
    unread,
    what,
    panel,
    kind === 'paper' ? h('label', { class: 'reading-addon' }, mapToggle,
      h('span', {}, h('b', { text: 'For pure math papers, add a one-page diagram (optional)' }))) : null,
    forYou,
    h('div', { class: 'launch' }, start,
      kind === 'search' ? null
        : h('a', { class: 'primary start', href: `#/p/${paper.slug}/paper`,
                   text: kind === 'collection' ? 'Look at the papers first' : 'Look at the paper first' }),
      hint),
    h('div', { class: 'chosen-foot' },
      back
        ? h('button', { class: 'linkish', text: 'Back', onclick: back })
        : h('button', { class: 'linkish', onclick: undo,
                        text: (kind !== 'search' && (fresh || freshMembers.length))
                          ? 'Cancel upload'
                          : kind === 'search' ? 'Ask something else'
                            : kind === 'collection' ? 'Choose different PDFs'
                              : 'Choose a different PDF' }),
      // The method and everything else, from here too: this is where a
      // change to them is about to be used.
      h('button', { class: 'linkish', type: 'button', text: 'Advanced settings',
                    onclick: () => settings.open() })));
  hints.show('launch');

  // A paper taken out of a set before it is read: the others are read together
  // — or the one left, alone — and what was uploaded only for this set, and the
  // set as it was if it was made just now, are not kept.
  async function removeMember(slug) {
    if (starting || sending || drop.classList.contains('busy')) return;
    const rest = (paper.members || []).map(m => m.slug).filter(s => s !== slug);
    if (!rest.length) return;
    drop.classList.add('busy');
    // Nothing here may be started, or changed again, while the set is remade.
    const controls = [...chosen.querySelectorAll('button, select, input, textarea')];
    controls.forEach(c => { c.dataset.was = c.disabled ? '1' : ''; c.disabled = true; });
    let next;
    let choice;
    try {
      if (rest.length > 1) {
        next = await api('POST', '/api/collections', { papers: rest });
        choice = { fresh: !next.existing, freshMembers: freshMembers.filter(s => s !== slug), notes };
      } else {
        const status = await api('GET', `/api/papers/${rest[0]}`);
        next = status.paper;
        choice = { fresh: freshMembers.includes(rest[0]), readings: status.readings, notes };
      }
    } catch (err) {
      drop.classList.remove('busy');
      controls.forEach(c => { c.disabled = c.dataset.was === '1'; });
      toast(err.message, 'err');
      return;
    }
    const gone = [...(fresh && next.slug !== paper.slug ? [paper.slug] : []),
                  ...(freshMembers.includes(slug) ? [slug] : [])];
    for (const s of gone) {
      try { await api('DELETE', `/api/papers/${s}`); } catch { /* harmless */ }
    }
    drop.classList.remove('busy');
    if (state.file !== paper) return;          // the reader has moved on meanwhile
    showChoice(next, drop, chosen, choice);
  }

  // Changing your mind should leave nothing behind. A paper uploaded a moment
  // ago and never read is discarded; one that was already here is kept — and
  // so, for several papers, is each one that was here before.
  async function undo() {
    for (const slug of [...(fresh ? [paper.slug] : []), ...freshMembers]) {
      try { await api('DELETE', `/api/papers/${slug}`); } catch { /* harmless */ }
    }
    state.file = null;
    state.addTo = null;
    chosen.hidden = true;
    mount(chosen);
    drop.hidden = false;
    setFinding(true);
    const input = $('#file');
    if (input) input.value = '';
    if (location.hash.startsWith('#/p/')) go(null);
    else renderLibrary($('#library'));
  }
}

// The launch panel's line about the reader's background (Advanced settings ›
// Your mathematical background): shown once there is one, ticked unless the
// reader has left it out before, and kept in step when it is changed there.
function forYouAddon() {
  const box = h('input', { type: 'checkbox' });
  box.addEventListener('change', () => settings.set({ useCv: box.checked }, 'choice'));
  const line = h('label', { class: 'reading-addon for-you', hidden: true }, box,
    h('span', {}, h('b', { text: 'Write it for your background' }),
      h('small', {}, 'The mathematical background in Advanced settings · ',
        h('button', { class: 'linkish', type: 'button', text: 'See or change it',
                      onclick: (e) => { e.preventDefault(); settings.open({ at: 'cv' }); } }))));
  const show = (text) => {
    line.hidden = !text;
    box.checked = settings.get().useCv !== false;
  };
  settings.background().then(show);
  const heard = (e) => {
    if (!line.isConnected) { window.removeEventListener('unpacman:cv', heard); return; }
    show(e.detail);
  };
  window.addEventListener('unpacman:cv', heard);
  return line;
}

// ------------------------------------------------------------- the AI

// A model's name in running text, kept on one line: "GPT-" at the end of one
// line and "5.5" at the start of the next is two names, not one.
const unbroken = (name) => name.replace(/-/g, '‑');

// The launch panel's word on the web: the AIs of a reading (`groups`, the
// first and any other steps') that cannot be counted on to search it here —
// models on this Mac, or reached through OpenRouter — so that their references
// may come from memory. Null when every one searches.
function blindNote(groups, models) {
  const blind = groups.filter(x => x.provider === 'open' && !x.web);
  if (!blind.length) return null;
  const local = blind.some(x => x.account === 'ollama');
  const hosted = blind.find(x => x.account !== 'ollama');
  const text = hosted && !local
    ? `A model reached through ${hosted.label} may not be able to search the web here, so the `
      + 'references it gives may come from memory: check them.'
    : hosted
      ? 'Models on this Mac cannot search the web here, and models reached through '
        + `${hosted.label} may not be able to, so the references they give may come from memory: `
        + 'check them.'
      : 'Open-weight models cannot search the web here, so the references they give come '
        + 'from memory: check them.';
  return h('div', { class: 'launch-note', text: text + (models.some(x => x.free)
    ? ' On this Mac a reading takes much longer than with Claude or ChatGPT, and the '
      + 'model takes most of the Mac’s memory while it reads: other programs slow down.'
    : '') });
}

// Which AI reads the paper, how hard it thinks, and the two limits the reading
// stops at — a small form, in view, just above Start. Every choice is one of
// the Advanced settings (settings.js), kept in this browser, and changing it
// here changes it there; the server's own limits show until one is set.
// `onChange({pick, valid})` hears every change: `pick` is what the reading
// request carries, `valid` whether the limits typed can be sent. `level`, for
// a search or a collection reading, whose default limits are their own;
// `latex`, whether what is started needs LaTeX (only searches do not).
async function agentPanel(box, onChange, opts = {}) {
  const { level = null, latex = true } = opts;
  const menu = level ? await settings.menuFor(level) : await settings.menu();
  if (!menu) {                             // the server's own default still applies
    onChange({ pick: {}, valid: true });
    return;
  }
  // Started with --model: the page begins there each visit, unless the reader
  // has chosen since it opened.
  const saved = menu.pinned && !settings.chosen('reading') ? null : settings.get().reading;
  const groups = (menu.groups || []).map(g => ({ ...g, models: [...g.models] }));
  if (saved?.custom) {
    const g = groups.find(x => x.account === saved.account);
    if (g && !g.models.some(m => m.id === saved.model)) {
      g.models.push({ id: saved.model, name: saved.model, custom: true,
                      small: /haiku|(?:^|-)(?:mini|nano)(?:-|$)/i.test(saved.model),
                      priced: false,
                      cap: { usd: null, minutes: menu.limits?.max_minutes ?? null } });
    }
  }
  if (!groups.length || !menu.default) {
    mount(box, h('div', { class: 'agent-warn' },
      'No AI is installed to read with here. ',
      h('a', { href: '#/ready', text: 'Getting ready' }), ' installs one.'));
    onChange({ pick: null, valid: false, none: true });
    return;
  }

  const locate = (p) => {
    const g = groups.find(x => x.account === p?.account);
    const m = g?.models.find(x => x.id === p?.model);
    return g && m ? { g, m } : null;
  };
  const start = locate(saved) ? saved : menu.default;
  const at = locate(start) || locate(menu.default) || { g: groups[0], m: groups[0].models[0] };
  const limits = menu.limits || {};

  const modelPurpose = level === 'search' ? 'For searching papers' : 'For creating the companion';
  const modelSel = h('select', { id: 'launch-model', 'aria-label': `AI ${modelPurpose.toLowerCase()}`,
                               'aria-describedby': 'launch-model-purpose' },
    groups.map((g, gi) => h('optgroup', {
      // Signed in but unable to read here (ready.py codex_trouble) is not "not signed in".
      label: g.outdated ? `${g.label} — needs updating`
        : g.signed_in === false ? `${g.label} — not signed in` : g.label },
      g.models.map((m, mi) => h('option', { value: `${gi}:${mi}`, text: m.name })))));
  modelSel.value = `${groups.indexOf(at.g)}:${at.g.models.indexOf(at.m)}`;
  const current = () => {
    const [gi, mi] = modelSel.value.split(':').map(Number);
    return { g: groups[gi], m: groups[gi].models[mi] };
  };

  // Effort as a row of choices, all visible: which one is "a lot" is clearer
  // beside the others than inside a closed menu.
  const efforts = h('div', { class: 'seg-row', role: 'radiogroup', 'aria-label': 'Effort' });
  let effort = start.effort;
  const fillEfforts = (g) => {
    const has = (id) => g.efforts.some(e => e.id === id);
    effort = has(effort) ? effort : has('high') ? 'high' : (g.efforts[0]?.id || '');
    mount(efforts, g.efforts.map(e => {
      const input = h('input', { type: 'radio', name: 'effort', value: e.id, class: 'sr-only' });
      input.checked = e.id === effort;
      input.addEventListener('change', () => { effort = e.id; changed(); });
      return h('label', { class: 'seg-opt' }, input, h('span', { text: e.name }));
    }));
    efforts.closest('.launch-row')?.toggleAttribute('hidden', !g.efforts.length);
  };

  const usdRange = limits.usd_range || [0.1, 200];
  const minRange = limits.minutes_range || [1, 600];
  const usd = h('input', { type: 'number', id: 'launch-usd', inputmode: 'decimal',
                           min: String(usdRange[0]), max: String(usdRange[1]), step: '0.5',
                           'aria-label': 'Money limit, in dollars' });
  const minutes = h('input', { type: 'number', id: 'launch-minutes', inputmode: 'numeric',
                               min: String(minRange[0]), max: String(minRange[1]), step: '5',
                               'aria-describedby': 'launch-time-advice',
                               'aria-label': level === 'search' ? 'Time limit, in minutes'
                                 : 'Planned time, in minutes' });
  const usdBox = h('span', { class: 'cap-field' }, h('span', { class: 'cap-unit', text: '$' }), usd);
  const reset = h('button', { class: 'linkish cap-reset', type: 'button', text: 'Use the defaults',
                              onclick: () => {
                                settings.set({ maxUsd: null, maxMinutes: null }, 'choice');
                                fillCaps(); changed();
                              } });
  const capNote = h('div', { class: 'launch-note' });
  const capError = h('div', { class: 'launch-error', hidden: true });
  const modelNote = h('div', { class: 'launch-note' });
  // Give each kind of run its own suggested range beside the time field.
  const recommendedMinutes = level === 'search'
    ? settings.SEARCH_RECOMMENDED_MINUTES : settings.RECOMMENDED_MINUTES;
  const timeAdvice = h('div', { id: 'launch-time-advice', class: 'launch-note time-advice',
                              text: level === 'search'
                                ? settings.SEARCH_TIME_ADVICE : settings.TIME_ADVICE });
  const adviseTime = () => {
    const n = Number(minutes.value);
    timeAdvice.classList.toggle('low', minutes.value.trim() !== '' && Number.isFinite(n)
                                       && n < recommendedMinutes);
  };

  // The later steps given to other AIs in Advanced settings (settings.js):
  // shown here, before Start, as everything a reading will spend is.
  const steps = settings.stepChoices(menu);
  const stepBox = h('div', { class: 'launch-steps' });
  const hasSteps = (menu.steps || []).length > 1;
  const openSteps = () => settings.open({ at: 'steps' });
  if (hasSteps && steps.length) {
    mount(stepBox,
      h('ul', { class: 'step-ais' }, steps.map(c => h('li', {},
        h('span', { class: 'step-ai-title', text: `${c.title}:` }), ' ',
        unbroken(c.m.name), c.effort ? `, ${c.g.efforts.find(e => e.id === c.effort)?.name || c.effort} effort` : '',
        // Claude's and ChatGPT's models say whose they are; others are named by where they run.
        ['claude', 'codex'].includes(c.g.provider) ? null
          : h('span', { class: 'step-ai-via', text: ` (${c.g.label})` })))),
      h('button', { class: 'linkish', type: 'button', text: 'Change', onclick: openSteps }));
  } else if (hasSteps) {
    mount(stepBox, h('button', { class: 'linkish step-other', type: 'button',
      text: 'Use another AI for the check or the revision…', onclick: openSteps }));
  }
  // The first step whose AI cannot be held to a money limit, if any, and
  // whether any can — the money limit binds on the steps that have a price.
  const unpriced = () => [current().m, ...steps.map(c => c.m)].find(x => !x.priced);
  const anyPriced = () => [current().m, ...steps.map(c => c.m)].some(x => x.priced);

  // The limits the reading will have: the ones set here or in Advanced
  // settings, else the server's for this model.
  const fillCaps = () => {
    const { m } = current();
    const s = settings.get();
    const off = unpriced();
    const priced = anyPriced();
    const dflt = { usd: (m.cap?.usd ?? limits.max_usd) ?? null,
                   minutes: m.cap?.minutes ?? limits.max_minutes ?? null };
    if (!m.priced && priced) dflt.usd = limits.max_usd ?? null;
    usd.value = priced ? String(s.maxUsd ?? dflt.usd ?? '') : '';
    usd.disabled = !priced;
    usd.placeholder = priced ? '' : '—';
    usdBox.classList.toggle('off', !priced);
    minutes.value = String(s.maxMinutes ?? dflt.minutes ?? '');
    adviseTime();
    reset.hidden = s.maxUsd == null && s.maxMinutes == null;
    const which = off && off !== m ? ` (${steps.find(c => c.m === off)?.title.toLowerCase()})`
      : off && steps.length ? ' (the first reading)' : '';
    const why = off?.free ? 'runs on this Mac at no cost' : 'has no per-token price here';
    capNote.textContent = !off
      ? `The ${level === 'search' ? 'search' : 'reading'} stops at whichever limit it reaches `
        + 'first. On a subscription, the money is what the same work would cost through the API.'
      : priced ? `${unbroken(off.name)}${which} ${why}: the money limit counts the other steps, `
                 + 'and the time limit all of them.'
        : `${unbroken(off.name)}${which} ${why}, so only the time limit applies.`;
    if (level !== 'search') capNote.textContent +=
      ' Review and revision may each use up to 5 extra minutes (up to 10 extra in total).';
  };
  const check = (input, range, unit) => {
    if (input.disabled) return null;
    const n = Number(input.value);
    if (input.value.trim() === '' || !Number.isFinite(n) || n < range[0] || n > range[1]) {
      return `The ${unit} limit must be between ${unit === 'money' ? '$' : ''}${range[0]} and `
        + `${unit === 'money' ? '$' : ''}${range[1]}${unit === 'time' ? ' minutes' : ''}.`;
    }
    return null;
  };
  // Checks the limits typed, and keeps them only when the reader typed them:
  // drawing the panel must not change a setting.
  const saveCaps = (keep = true) => {
    const errors = [check(usd, usdRange, 'money'), check(minutes, minRange, 'time')].filter(Boolean);
    capError.hidden = !errors.length;
    capError.textContent = errors.join(' ');
    if (errors.length) return false;
    if (!keep) return true;
    const { m } = current();
    const dflt = { usd: m.cap?.usd ?? limits.max_usd, minutes: m.cap?.minutes ?? limits.max_minutes };
    const s = settings.get();
    // A limit equal to the default is no limit of the reader's own: kept
    // empty, it follows the server's if the server's changes.
    const u = usd.disabled ? s.maxUsd : (Number(usd.value) === dflt.usd ? null : Number(usd.value));
    const t = Number(minutes.value) === dflt.minutes ? null : Number(minutes.value);
    if (u !== s.maxUsd || t !== s.maxMinutes) settings.set({ maxUsd: u, maxMinutes: t }, 'choice');
    reset.hidden = u == null && t == null;
    return true;
  };

  // `user`: the reader changed something, rather than the panel being drawn.
  // Only then is the choice kept — a server started with --model begins
  // there on each visit until the reader chooses otherwise.
  // Whatever stops the reading from starting: the first AI it would use that
  // cannot run here, or LaTeX.
  const blockerOf = (g) => [g, ...steps.map(c => c.g)]
    .map(x => ready.blocker(x, menu.ready, { latex })).find(Boolean) || '';
  const changed = (user = true) => {
    const { g, m } = current();
    const pick = { account: g.account, model: m.id };
    if (m.custom) pick.custom = true;
    if (effort) pick.effort = effort;
    if (user) settings.set({ reading: pick }, 'choice');
    const blocked = blockerOf(g);
    mount(modelNote,
      // Unable to read here — too old for a reading's protections, or installed
      // without a part it needs (ready.py codex_trouble): said so, and where to mend it.
      g.outdated ? h('div', { class: 'agent-warn' },
        `Codex, which ChatGPT reads with here, ${g.outdated_why
          || 'is too old for the protections a reading runs under'}. `,
        h('a', { href: '#/ready', text: 'Getting ready' }), ' updates it in a minute.')
      // An AI reached with a key, or on this Mac, says what it lacks (ready.py open_status).
      : g.signed_in === false && g.provider === 'open' ? h('div', { class: 'agent-warn' },
        `${g.not_ready || `${g.label} is not ready on this computer.`} `,
        h('a', { href: '#/ready', text: 'Open Getting ready' }), '.')
      : g.signed_in === false ? h('div', { class: 'agent-warn' },
        `${g.provider === 'claude' ? 'Claude' : 'ChatGPT'} is not signed in on this computer. `,
        h('a', { href: '#/ready', text: 'Sign in' }), ' — it takes a minute.') : null,
      m.small ? h('div', { class: 'agent-warn',
        text: `${unbroken(m.name)} is a small model: fine for trying unpacman out, but small `
              + 'models tend to miss the very gaps it exists to find.' }) : null,
      // What a model reached through another host cannot be counted on to do
      // here (atelier.providers.openweight): search the web — which DeepSeek's
      // own service does (`web`). Through OpenRouter it is not known to work.
      blindNote([g, ...steps.map(c => c.g)], [m, ...steps.map(c => c.m)]),
      steps.filter(c => c.g.signed_in === false && c.g.account !== g.account).map(c => h('div', {
        class: 'agent-warn' }, ...(c.g.outdated
          ? [`${c.title} runs on ${c.g.label}, whose Codex ${c.g.outdated_why
              || 'is too old for the protections a reading runs under'}. `,
             h('a', { href: '#/ready', text: 'Getting ready' }), ' updates it in a minute.']
          : [`${c.title} runs on ${c.g.label}, which is not signed in on this computer. `,
             h('a', { href: '#/ready', text: 'Sign in' }), ' — it takes a minute.']))));
    Object.assign(pick, settings.readingSteps(menu));
    onChange({ pick, valid: saveCaps(user), blocked });
  };
  const typed = () => {
    const { g, m } = current();
    const pick = { account: g.account, model: m.id, ...(m.custom ? { custom: true } : {}),
                   ...(effort ? { effort } : {}), ...settings.readingSteps(menu) };
    onChange({ pick, valid: saveCaps(), blocked: blockerOf(g) });
  };

  modelSel.addEventListener('change', () => { fillEfforts(current().g); fillCaps(); changed(); });
  usd.addEventListener('input', typed);
  minutes.addEventListener('input', () => { adviseTime(); typed(); });

  const row = (label, forId, ...kids) => h('div', { class: 'launch-row' },
    h('label', { class: 'launch-label', for: forId, text: label }),
    h('div', { class: 'launch-field' }, ...kids));
  mount(box,
    row('AI', 'launch-model', modelSel,
      h('div', { id: 'launch-model-purpose', class: 'launch-note', text: modelPurpose }),
      modelNote, hasSteps ? stepBox : null),
    row('Effort', null, efforts),
    row('Limits', 'launch-usd',
      h('div', { class: 'caps' }, usdBox, h('span', { class: 'cap-or', text: 'or' }),
        h('span', { class: 'cap-field' }, minutes, h('span', { class: 'cap-unit', text: 'min' })),
        reset),
      timeAdvice, capError, capNote));
  fillEfforts(at.g);
  fillCaps();
  changed(false);

  // Changed in Advanced settings while this panel is on screen: draw it again.
  const off = settings.onChange((now, patch, source) => {
    if (!box.isConnected) { off(); return; }
    if (source === 'choice') return;
    if ('reading' in patch || 'maxUsd' in patch || 'maxMinutes' in patch || 'steps' in patch) {
      off();
      agentPanel(box, onChange, opts);
    }
  });
}

const ROW_NOTE = {
  reading: (p) => (kindOf(p) === 'search' ? 'searching now' : 'reading now'),
  ready: () => 'Completed',
  stopped: () => 'stopped',
  incomplete: () => 'Incomplete draft',
  review_unfinished: () => 'Draft — review unfinished',
  unread: (p) => (kindOf(p) === 'search' ? 'not searched yet' : 'not read yet'),
};

// What a row in the library is, when it is not one paper.
function rowTag(p) {
  if (kindOf(p) === 'search') return h('span', { class: 'row-tag', text: 'Search' });
  if (kindOf(p) === 'collection') {
    return h('span', { class: 'row-tag', text: `${(p.members || []).length} papers` });
  }
  return null;
}

async function renderLibrary(root) {
  let papers = [];
  try { papers = await api('GET', '/api/papers'); } catch { return; }
  // The paper being chosen is already on screen above — and so, for several,
  // is each of them; listing them again below asks the reader to notice they
  // are the same ones.
  const above = new Set([state.file?.slug, ...(state.file?.members || []).map(m => m.slug)]);
  papers = papers.filter(p => !above.has(p.slug));
  if (!papers.length) { mount(root); return; }
  papers.forEach(noteKind);
  mount(root,
    h('h2', { text: 'Your papers' }),
    // A paper opens at its runs; a search at its latest results.
    papers.map(p => h('button', {
      class: 'row', onclick: () => go(p.slug, kindOf(p) === 'search' ? 'main' : 'runs'),
    },
      h('span', { class: 'row-title' }, rowTag(p), p.title),
      h('span', { class: `row-note ${p.state === 'reading' ? 'live' : ['incomplete', 'review_unfinished'].includes(p.state) ? 'incomplete' : ''}`,
                  text: ROW_NOTE[p.state]?.(p) ?? '' }))));
}

// ---------------------------------------------------------- paper readings

// A paper read more than once has one companion per level that it opens at —
// the newest — and earlier ones kept to compare. The list says which is which.
function runState(run, kind = 'paper') {
  if (run.running) return kind === 'search' ? 'Searching now' : 'Reading now';
  if (run.delivery?.label) return run.delivery.label;
  if (run.state === 'incomplete') return run.companion ? 'Incomplete draft' : 'Incomplete';
  if (run.companion) {
    const finished = run.state === 'done' || run.state === 'kept';
    // A server from before `current` says nothing: every companion is ready.
    if (run.current !== false) return finished ? KIND[kind].ready : 'Draft available';
    return finished ? KIND[kind].earlier : 'Earlier draft';
  }
  if (run.state === 'cancelled') return 'Stopped';
  if (run.state === 'failed') return 'Failed';
  if (run.state === 'done') return 'Finished';
  return run.state || 'Recorded';
}

// "6 Oct 2026, 14:05": the month in words, so that 05/10 is never read as
// 10 May, and no seconds — runs are told apart by their numbers.
function runWhen(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString(undefined, {
    day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' });
}

// "Run 3 · Expert": numbered from the paper's first reading, so a run keeps
// its name wherever it appears and however many come after it.
function runName(run) {
  const level = LEVEL_NAMES[run.level] || 'Reading';
  return run.number ? `Run ${run.number} · ${level}` : `${level} run`;
}

async function renderRuns(slug) {
  stopPolling();
  state.slug = slug;

  let status;
  try { status = await api('GET', `/api/papers/${slug}`); }
  catch (err) { toast(err.message, 'err'); go(null); return; }
  const kind = noteKind(status.paper);

  notes.attach(slug);
  if (status.companion) state.withCompanion.add(slug);
  state.view = `runs:${slug}`;
  const main = status.companion ? 'Companion' : status.run ? 'Reading' : 'Back';
  barFor(slug, 'runs', main, 'runs');

  const runs = status.runs || [];
  const cards = runs.map(run => {
    const bits = [runWhen(run.started),
                  run.agents?.length > 1 ? run.agents.map(a => a.name).join(' + ') : run.agent?.name];
    if (run.own_method) bits.push('your own method');
    // Written for the reader: their background, their answers, or both.
    const forWhom = [run.profile?.cv ? 'your background' : '',
                     run.profile?.answered ? `your ${run.profile.answered} answers` : ''].filter(Boolean);
    if (forWhom.length) bits.push(`for ${forWhom.join(' and ')}`);
    if (run.elapsed) bits.push(duration(run.elapsed));
    if (run.cost) bits.push(`$${Number(run.cost).toFixed(2)}`);
    const shown = run.companion && run.current === true && !run.running;
    const card = h('a', {
      class: `run-card${run.running ? ' live' : ''}`,
      href: levelHash(slug, run.level, run.id),
    },
    h('span', { class: 'run-main' },
      h('span', { class: 'run-title', text: runName(run) }),
      h('span', { class: 'run-meta', text: bits.filter(Boolean).join(' · ') })),
    h('span', { class: `run-status${run.running ? ' live' : unfinishedRun(run) ? ' incomplete' : shown ? ' current' : ''}`,
                text: runState(run, kind) }));
    // A run still reading is stopped first, from its own page; then it can go.
    const del = run.running ? h('span', { class: 'run-del-none' })
      : h('button', { class: 'run-del', type: 'button', text: 'Delete',
                      'aria-label': `Delete ${runName(run)}`,
                      onclick: () => deleteRun(slug, run, kind, status) });
    return h('div', { class: 'run-row' }, card, del);
  });

  const lead = {
    paper: 'Generate another companion, or open any earlier run.',
    collection: 'Create another collection companion, or open any earlier run.',
    search: 'Search again — for what is new since, or with the question changed — or open '
            + 'any earlier search.',
  }[kind];
  const note = {
    paper: 'Newest first. Opening the paper shows the newest companion at each level; earlier '
           + 'ones stay here to compare.',
    collection: 'Newest first. Opening these papers shows the newest companion; earlier ones stay '
                + 'here to compare.',
    search: 'Newest first. Opening the search shows the newest results; earlier ones stay here '
            + 'to compare.',
  }[kind];
  const tidy = h('div', { class: 'runs-tidy', hidden: true });
  const view = h('section', { class: 'stage runs-page' },
    h('div', { class: 'runs-head' },
      h('div', {},
        h('h1', { text: status.paper.title }),
        h('p', { text: lead })),
      h('div', { class: 'runs-actions' },
        h('button', { class: 'run-new', text: KIND[kind].again,
                      onclick: () => readAgain(slug, null, () => renderRuns(slug)) }),
        kind === 'search' ? null
          : h('a', { class: 'ghost', href: `#/p/${slug}/paper`,
                     text: kind === 'collection' ? 'The papers' : 'Open paper' }))),
    h('div', { class: 'runs-list' },
      h('h2', { text: 'Runs' }),
      runs.length > 1 ? h('p', { class: 'runs-note', text: note }) : null,
      cards.length ? cards : h('p', { class: 'empty', text: kind === 'search'
        ? 'Nothing has been searched for yet.' : 'This paper has not been read yet.' })),
    tidy);
  mount($('#view'), view);
  restoreScroll(state.view);
  hints.show('runs');
  fillTidy(tidy, slug, status, kind);
}

// Below the runs: how much room this paper takes, and the two ways to give it
// back — its earlier runs, or the paper itself. Measured after the page is
// drawn, since it means reading every file.
async function fillTidy(box, slug, status, kind) {
  let space;
  try { space = await api('GET', `/api/papers/${slug}/space`); } catch { return; }
  if (!box.isConnected) return;
  const spare = space.earlier || { count: 0, bytes: 0 };
  const what = { paper: 'This paper', collection: 'This collection', search: 'This search' }[kind];
  mount(box,
    h('p', { class: 'runs-space',
             text: `${what} and its runs take ${bytes(space.total)} on this computer.` }),
    h('div', { class: 'runs-tidy-actions' },
      spare.count ? h('button', { class: 'ghost', type: 'button',
        text: `Delete the earlier runs (${spare.count}, ${bytes(spare.bytes)})`,
        onclick: () => tidyPaper(slug, status, kind, spare) }) : null,
      h('button', { class: 'linkish danger-link', type: 'button',
                    text: { paper: 'Delete this paper', collection: 'Delete this collection',
                            search: 'Delete this search' }[kind],
                    onclick: () => deletePaper(slug, status, kind, space) })));
  box.hidden = false;
}

// After a deletion: anything this page remembers of what is gone is let go.
function forgetRun(slug, run) {
  if (state.reading?.slug === slug && state.reading.run === run) state.reading = null;
  if (state.lastCompanion?.slug === slug && state.lastCompanion.run === run) state.lastCompanion = null;
}

async function deleteRun(slug, run, kind, status) {
  let size = null;
  try { size = (await api('GET', `/api/papers/${slug}/space`)).runs?.[run.id]; } catch { /* unknown */ }
  const result = run.companion ? (kind === 'search' ? 'the results it found' : `the ${KIND[kind].result} it wrote`)
    : 'what it wrote';
  const ok = await confirmAction({
    title: `Delete ${runName(run)}?`,
    body: [
      `This deletes the run and ${result}${size ? ` (${bytes(size)})` : ''}. `
        + `The ${kind === 'search' ? 'search' : 'paper'} stays, and so do its other runs and your explanations.`,
      run.companion && run.current === true && status.runs.filter(r => r.level === run.level && r.companion).length > 1
        ? `It is the ${KIND[kind].result} this ${kind === 'search' ? 'search' : 'reading level'} opens with; `
          + 'the newest earlier one takes its place.' : null,
      'This cannot be undone.',
    ],
    yes: 'Delete', danger: true,
  });
  if (!ok) return;
  try {
    const out = await api('POST', `/api/papers/${slug}/runs/delete`, { run: run.id });
    forgetRun(slug, run.id);
    toast(`${runName(run)} deleted${out.freed ? ` — ${bytes(out.freed)} freed` : ''}.`);
  } catch (err) {
    toast(err.message, 'err');
  }
  await renderRuns(slug);
}

async function tidyPaper(slug, status, kind, spare) {
  const ok = await confirmAction({
    title: `Delete ${spare.count} earlier run${spare.count === 1 ? '' : 's'}?`,
    body: [
      kind === 'search'
        ? 'This keeps the newest results, and any search still going, and deletes every other '
          + `run of this search — earlier results, and searches that stopped or failed — freeing ${bytes(spare.bytes)}.`
        : `This keeps the ${KIND[kind].result} each level opens with, and any reading still going, `
          + `and deletes every other run — earlier ${KIND[kind].result}s, and readings that stopped `
          + `or failed — freeing ${bytes(spare.bytes)}.`,
      'This cannot be undone.',
    ],
    yes: 'Delete them', danger: true,
  });
  if (!ok) return;
  try {
    const out = await api('POST', `/api/papers/${slug}/tidy`);
    (spare.runs || []).forEach(run => forgetRun(slug, run));
    toast(`${out.deleted} run${out.deleted === 1 ? '' : 's'} deleted — ${bytes(out.freed)} freed.`);
  } catch (err) {
    toast(err.message, 'err');
  }
  await renderRuns(slug);
}

async function deletePaper(slug, status, kind, space) {
  const n = (status.runs || []).filter(r => r.id !== 'kept').length;
  const name = { paper: 'paper', collection: 'collection', search: 'search' }[kind];
  const ok = await confirmAction({
    title: `Delete this ${name}?`,
    body: [
      h('p', { class: 'set-intro confirm-name', text: status.paper.title }),
      n ? `This deletes it from unpacman with ${n === 1 ? 'its run' : `all ${n} of its runs`}`
          + ` — ${kind === 'search' ? 'the results' : 'the companions'}, your explanations, `
          + `everything unpacman kept for it — freeing ${bytes(space.total)}.`
        : `This deletes it from unpacman, and everything unpacman kept for it — freeing ${bytes(space.total)}.`,
      kind === 'collection' ? 'The papers in it stay among your papers.'
        : kind === 'search' ? 'Papers you added from it stay among your papers.'
          : 'The PDF you gave unpacman is still wherever you had it.',
      'This cannot be undone.',
    ],
    yes: `Delete the ${name}`, danger: true,
  });
  if (!ok) return;
  try {
    const out = await api('DELETE', `/api/papers/${slug}`);
    if (state.viewer?.slug === slug) dropViewer();
    state.withCompanion.delete(slug);
    if (state.reading?.slug === slug) state.reading = null;
    if (state.lastCompanion?.slug === slug) state.lastCompanion = null;
    toast(`Deleted${out.freed ? ` — ${bytes(out.freed)} freed` : ''}.`);
    go(null);
  } catch (err) {
    toast(err.message, 'err');
    await renderRuns(slug);
  }
}

// --------------------------------------------------------- reading view

async function renderPaper(slug, level = null, run = null) {
  stopPolling();
  state.slug = slug;

  let status;
  try {
    status = await api('GET', `/api/papers/${slug}${query({ level, run })}`);
  } catch (err) {
    toast(err.message, 'err');
    go(null);
    return;
  }

  notes.attach(slug);
  noteKind(status.paper);
  if (status.companion) state.withCompanion.add(slug);
  level = status.level;
  const running = status.run?.running;
  // A reading named in the address is shown as it is: its companion if it
  // wrote one, its progress or its failure if not. Otherwise the level's
  // newest reading decides — its progress while it runs, else the companion.
  const named = run && status.run?.id === run;
  if (named ? status.view_companion : (!running && status.companion)) {
    await renderCompanion(slug, level, named ? run : null, status);
    return;
  }
  if (!status.run) {
    await renderDrop({ keepHash: true });
    showChoice(status.paper, $('#drop'), $('#chosen'),
               { fresh: false, readings: status.readings });
    return;
  }

  const view = tpl('tpl-reading');
  mount($('#view'), view);
  state.view = `reading:${slug}:${status.run.id}`;
  setReading(slug, level, status.run.id);
  barFor(slug, 'main', 'Reading', 'reading');
  paintReading(view, status);
  hints.show('reading');

  if (running) {
    const runId = status.run.id;
    state.timer = setInterval(async () => {
      if (state.slug !== slug) { stopPolling(); return; }
      let next;
      try { next = await api('GET', `/api/papers/${slug}${query({ level, run: runId })}`); }
      catch { return; }
      if (!next.run?.running) {
        stopPolling();
        if (next.view_companion) { await renderCompanion(slug, level, runId, next); return; }
      }
      paintReading(view, next);
    }, 2000);
  }
}

function paintReading(view, status) {
  const run = status.run;
  const slug = status.paper.slug;
  const level = status.level;
  $('#r-title', view).textContent = status.paper.title;

  const kind = kindOf(status.paper);
  const bits = [];
  if (level) bits.push(readingName(level));
  // Which of the paper's runs this is, once there is more than one.
  if (run?.number && status.runs?.length > 1) bits.push(`run ${run.number}`);
  if (run?.agents?.length > 1) bits.push(run.agents.map(a => a.name).join(' + '));
  else if (run?.agent) bits.push(run.agent.name);
  if (run?.running) {
    const p = run.pass;
    bits.push(p ? `step ${p.index} of ${p.of}` : 'starting');
    bits.push(duration(run.elapsed));
    if (run.cost) bits.push(`$${Number(run.cost).toFixed(2)}`);
  } else if (run) {
    bits.push(run.state === 'done' ? 'finished' : run.state);
    if (run.elapsed) bits.push(duration(run.elapsed));
  }
  $('#r-meta', view).textContent = bits.join(' · ');

  mount($('#r-steps', view), status.steps.map(s => h('li', { class: `step ${s.state}` },
    h('div', { class: 'step-title' }, s.title,
      // Which AI takes this step, when the steps are not all one AI's.
      s.agent ? h('span', { class: 'step-agent', text: ` · ${s.agent.name}` }) : null),
    h('div', { class: 'step-detail', text: s.detail }),
    s.summary ? h('div', { class: 'step-summary', text: s.summary }) : null)));

  // What can be read meanwhile: an earlier companion at this level, which the
  // new one will take the place of only when it is finished, and the
  // companion at the other level.
  const meanwhile = [];
  if (status.companion && status.companion_run !== run?.id) {
    meanwhile.push(h('a', { class: 'linkish', href: levelHash(slug, level, status.companion_run),
                            text: kind === 'search' ? 'See the previous results'
                              : `Read the previous ${KIND[kind].result}` }));
  }
  const other = level && OTHER[level];
  if (other && status.readings?.[other]?.state === 'ready') {
    meanwhile.push(h('a', { class: 'linkish', href: levelHash(slug, other),
                            text: `Read the ${LEVEL_NAMES[other].toLowerCase()} companion` }));
  }

  const foot = $('#r-foot', view);
  if (run?.running) {
    mount(foot,
      h('span', { text: `You can close this page; the ${kind === 'search' ? 'search' : 'reading'} `
                        + 'continues, and unpacman keeps the Mac awake until it is done. Closing '
                        + 'the lid would pause it.' }),
      kind === 'search' ? null : h('a', { class: 'linkish', href: `#/p/${slug}/paper`,
        text: kind === 'collection' ? 'Look at the papers while you wait'
          : 'Read the paper while you wait' }),
      meanwhile,
      h('button', {
        class: 'linkish', text: 'Stop',
        onclick: async (e) => {
          e.currentTarget.disabled = true;
          try { await api('POST', `/api/papers/${slug}/stop`, { run: run.id, level }); }
          catch (err) { toast(err.message, 'err'); }
        },
      }));
  } else if (unfinishedRun(run)) {
    mount(foot, incompleteNotice(status),
      status.view_companion ? h('a', { class: 'ghost', href: levelHash(slug, level, run.id),
                                      text: 'Read the saved draft' }) : null,
      meanwhile);
  } else if (status.companion_run && status.companion_run === run?.id) {
    mount(foot, h('a', { class: 'ghost', href: levelHash(slug, level),
                         text: kind === 'search' ? 'See the results' : `Read the ${KIND[kind].result}` }));
  } else {
    // The plain account when there is one ("not signed in"), else what the
    // reading itself recorded.
    const why = status.plain_failure || run?.error || run?.reason || status.finished_because
      || 'The reading stopped before it produced a companion.';
    mount(foot, h('span', { text: why }),
          /Getting ready/.test(status.plain_failure || '')
            ? h('a', { class: 'linkish', href: '#/ready', text: 'Getting ready' }) : null,
          h('button', { class: 'linkish', text: 'Try again',
                        onclick: () => readAgain(slug, level) }),
          meanwhile,
          // A reading stopped by the limits it was given is no fault to report.
          run?.state === 'failed'
            ? h('button', { class: 'linkish', text: 'Tell us about it',
                            onclick: () => feedback.open({ kind: 'problem', about: why }) })
            : null);
  }
  // The first run's suggestion, while it runs: the paper can be read, and
  // asked about, meanwhile (firstask.js). Not once it has stopped.
  if (kind !== 'search') firstAsk.at('reading', { slug, kind, running: !!run?.running });
}

// Reading a paper again goes back through the choice, not straight to a run:
// the likeliest reason to try again is to try it on a different AI — or at
// the other level.
async function readAgain(slug, level = null, back = null, stillHere = () => true) {
  let status;
  try { status = await api('GET', `/api/papers/${slug}${query({ level })}`); }
  catch (err) { if (stillHere()) toast(err.message, 'err'); return; }
  if (!stillHere()) return;
  await renderDrop({ keepHash: true, showLibrary: false });
  state.slug = slug;
  mount($('#bar-right'), ready.pill(), about.button(), hints.button('launch'), feedback.button(),
        h('button', { class: 'ghost newpaper', text: 'New paper', onclick: () => go(null) }));
  showChoice(status.paper, $('#drop'), $('#chosen'),
             { fresh: false, readings: status.readings, level,
               back: back || (() => renderPaper(slug, status.level)) });
}

// ------------------------------------------------------- companion view

const FIXED_WORDS = { fixed: 'Fixed', rejected: 'Disputed' };
// The passes that check a companion against the paper (server.STEP_LABELS
// calls both "Checking against the paper").
const CHECKS = new Set(['verifier', 'auditor']);

function unfinishedRun(run) {
  return !run?.running && (['incomplete', 'review_unfinished'].includes(run?.delivery?.status)
    || run?.state === 'incomplete');
}

function continueDraftControl(status) {
  if (!status.run?.delivery?.can_continue) return null;
  const minutes = h('input', { type: 'number', min: '1', max: '600', step: '1',
    value: '10', required: true, 'aria-label': 'Additional time in minutes' });
  const error = h('p', { class: 'launch-error', role: 'alert', hidden: true });
  const start = h('button', { type: 'submit', class: 'primary', text: 'Continue' });
  const form = h('form', { class: 'continue-draft-form', hidden: true },
    h('label', {}, 'Additional time (minutes) ', minutes),
    h('p', { text: 'Keeps this draft, research, figures, and your answers. Uses the same AI choices '
      + 'and spending limit. The earlier run remains available. '
      + 'For runs with independent checking, at least half the time is reserved for review and revision.' }),
    error, start);
  const open = h('button', { type: 'button', class: 'ghost', text: 'Continue this draft',
    'aria-expanded': 'false', onclick: () => {
      form.hidden = !form.hidden;
      open.setAttribute('aria-expanded', String(!form.hidden));
      if (!form.hidden) minutes.focus({ preventScroll: true });
    } });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (start.disabled || !form.reportValidity()) return;
    start.disabled = true; minutes.disabled = true; error.hidden = true;
    start.textContent = 'Starting…';
    try {
      const result = await api('POST', `/api/papers/${status.paper.slug}/continue`, {
        ...settings.readingCaps(), run: status.run.id, max_minutes: Number(minutes.value),
      });
      await navigate(levelHash(status.paper.slug, status.level, result.run_id));
    } catch (err) {
      error.textContent = err.message; error.hidden = false;
      start.disabled = false; minutes.disabled = false; start.textContent = 'Continue';
    }
  });
  return h('div', { class: 'continue-draft' }, open, form);
}

function incompleteNotice(status, missing = []) {
  const steps = status?.steps || [];
  const delivery = status?.run?.delivery;
  const recorded = steps.flatMap(step => step.problems || []).map(problem =>
    problem.replaceAll('`progress/reading/guide.md`', 'The draft').replaceAll('**', ''));
  const issues = [...new Set(delivery ? [...(delivery.missing || []), ...(delivery.issues || [])]
    : recorded.length ? recorded : missing.length ? [`Missing sections: ${missing.join(', ')}.`] : [])]
    .map(p => p.replaceAll('`progress/reading/guide.md`', 'The draft').replaceAll('**', '')
      .replace('The draft is missing required section heading(s): ', 'Missing sections: ')
      .replace(' Use the exact headings required by the selected method.', '')
      .replace(/^The draft must end with the mandatory References cited in this companion bibliography\..*$/,
        'Missing bibliography: References cited in this companion.')
      .replace(/^The draft is missing the short Notice.*$/,
        'Missing introductory notice about scope and source checking.'));
  const labels = { done: 'completed', incomplete: 'incomplete', skipped: 'not performed' };
  return h('div', { class: 'notice warn incomplete-notice', role: 'status' },
    h('strong', { text: delivery?.label || 'Incomplete draft' }),
    h('p', { text: delivery?.status === 'review_unfinished'
      ? 'The required content is present. Review, revision, or final delivery checks remain unfinished.'
      : 'Required content is still missing. The saved draft is available below.' }),
    h('ul', {}, steps.map(step => h('li', {
      text: `${step.title}: ${labels[step.state] || step.state}.`,
    }))),
    issues.length ? h('ul', { class: 'incomplete-issues' },
      issues.map(text => h('li', { text }))) : null,
    status?.run?.reason ? h('details', {}, h('summary', { text: 'Full stop reason' }),
      h('p', { text: status.run.reason })) : null,
    continueDraftControl(status));
}

// The companion, filling the page — or, `side`, in the left pane of the
// side-by-side view with the paper in the right one. One builder for both, so
// that what a companion shows never differs between the two.
async function renderCompanion(slug, level = null, run = null, status = null, { side = false } = {}) {
  state.searchPicker?.dispose();
  state.searchPicker = null;
  stopPolling();
  state.slug = slug;

  let doc;
  try { doc = await api('GET', `/api/papers/${slug}/companion${query({ level, run })}`); }
  catch (err) {
    toast(err.message, 'err');
    // Asked for side by side with no companion to show: the paper's own page
    // says what there is instead.
    if (side) go(slug);
    return;
  }
  level = doc.level;
  // Only a paper has a paper to put beside its companion.
  if (side && doc.kind && doc.kind !== 'paper') { go(slug); return; }
  // Everything below comes from this one reading: its files, its map, its
  // review, and the status that says whether anything checked it.
  const from = query({ run: doc.run_id, level: doc.run_id ? null : level });
  if (!status || status.run?.id !== doc.run_id) {
    status = await api('GET', `/api/papers/${slug}${query({ level, run: doc.run_id })}`)
      .catch(() => null);
  }

  const view = tpl('tpl-companion');
  // What the companion scrolls in: the window, or its pane.
  let scroller = window;
  let paperPane = null;
  if (side) {
    const left = h('div', { class: 'side-pane side-left', role: 'region', 'aria-label': 'The companion' }, view);
    paperPane = h('div', { class: 'side-pane side-right', role: 'region', 'aria-label': 'The paper' });
    window.scrollTo(0, 0);
    mount($('#view'), splitView(left, paperPane));
    scroller = left;
  } else {
    mount($('#view'), view);
  }
  if (doc.kind !== 'search') firstAsk.at(side ? 'side' : 'companion');
  notes.attach(slug);
  setReading(slug, level, doc.run_id);
  state.withCompanion.add(slug);
  state.lastCompanion = { slug, level, run: doc.run_id };
  barFor(slug, side ? 'side' : 'main', 'Companion', side ? 'side' : 'companion');

  const rail = $('#rail', view);
  const paper = $('#paper', view);
  const review = doc.review;
  const findings = review?.findings || [];
  const kind = noteKind({ slug, kind: doc.kind || status?.paper?.kind });

  // --- head ---------------------------------------------------------------
  const words = doc.sections.reduce(
    (n, s) => n + (s.html.replace(/<[^>]+>/g, ' ').split(/\s+/).filter(Boolean).length), 0);
  // Credited to the reading that wrote this companion, which is not always the
  // latest one: a second reading that failed early leaves the first in place.
  const by = doc.by || {};
  const who = by.agent?.name;
  const sub = [];
  const levelText = level ? readingName(level) : '';
  // Several AIs: who wrote it, who checked it, who revised it.
  const stepsBy = (by.steps || []).filter(st => st.agent);
  const verb = { draft: 'written', check: 'checked', revision: 'revised' };
  const credit = stepsBy.length > 1
    ? stepsBy.map(st => `${verb[st.slot] || 'read'} by ${st.agent.name}`).join(', ')
    : null;
  if (credit) sub.push(levelText ? `${levelText}, ${credit}` : credit.charAt(0).toUpperCase() + credit.slice(1));
  else if (levelText) sub.push(who ? `${levelText} by ${who}` : levelText);
  else if (who) sub.push(`Read by ${who}`);
  // Read more than once: which run wrote this, as the list of runs names it.
  // Its number for life (readings.py): once runs can be deleted, "of" how many
  // would no longer add up.
  if (doc.number && doc.runs > 1) sub.push(`run ${doc.number}`);
  // Read with the reader's own version of the method (Advanced settings).
  if (by.own_method?.length) sub.push('with your own method');
  if (words) sub.push(`about ${Math.max(1, Math.round(words / 230))} min to read`);

  const head = [h('h1', { text: doc.title })];
  if (unfinishedRun(status?.run)) head.push(incompleteNotice(status, doc.missing));
  else if (status?.run?.delivery?.status === 'completed') {
    head.push(h('div', { class: 'delivery-complete', role: 'status', text: 'Completed' }));
  }
  if (sub.length) head.push(h('div', { class: 'sub', text: sub.join(' · ') }));
  // Written for the reader — their background (Advanced settings), their
  // answers to the questions before it (questions.js), or both: said, and shown.
  const forReader = by.profile;
  if (forReader && doc.run_id && (forReader.cv || forReader.asked)) {
    const n = forReader.asked;
    const answers = `your answers to ${n} question${n === 1 ? '' : 's'} about what you already know`;
    head.push(h('div', { class: 'tailored' },
      forReader.cv && n ? `Written for your mathematical background and ${answers}. `
        : n ? `Written for ${answers}. `
          : 'Written for the mathematical background you gave in Advanced settings. ',
      h('button', { class: 'linkish', type: 'button',
                    text: forReader.cv && n ? 'See them' : n ? 'See your answers' : 'See it',
                    onclick: () => showAnswers(slug, doc.run_id) })));
  }

  // The other audience's companion, a click away — or the offer to make one.
  const other = OTHER[level];
  const otherState = doc.readings?.[other]?.state;
  if (!other) {
    // A search has one mode.
  } else if (otherState && otherState !== 'unread') {
    const levels = kind === 'collection'
      ? ['collection-expert', 'collection-nonexpert'] : ['expert', 'general'];
    head.push(h('nav', { class: 'levels', 'aria-label': 'Which reading' },
      levels.map(lv => h('a', {
        class: `seg${lv === level ? ' on' : ''}`, href: levelHash(slug, lv),
        'aria-current': lv === level ? 'page' : null,
        text: `${LEVEL_NAMES[lv]}${doc.readings[lv].state === 'reading' ? ' · reading now' : ''}`,
      }))));
  } else {
    const audience = other.endsWith('expert') && !other.endsWith('nonexpert')
      ? 'an expert' : 'a non-expert';
    head.push(h('div', { class: 'also' },
      h('button', { class: 'linkish', onclick: () => readAgain(slug, other),
                    text: `Also read it for ${audience}` })));
  }

  // An earlier companion, opened from the list of runs: not the one this
  // level opens at, which is newer.
  // What this is, in the notices below: "expert companion", "joint reading",
  // "search"; and what opening it again shows.
  const earlier = kind === 'paper' ? `${LEVEL_NAMES[level].toLowerCase()} companion`
    : KIND[kind].result;
  const opening = { paper: 'Opening the paper', collection: 'Opening these papers',
                    search: 'Opening the search' }[kind];
  if (doc.current) {
    const c = doc.current;
    head.push(h('div', { class: 'notice' },
      `This is an earlier ${earlier}, from run ${doc.number}. `
      + `${opening} shows the newest, from run ${c.number}`
      + `${c.started ? ` (${runWhen(c.started)})` : ''}. `,
      h('a', { href: levelHash(slug, level, c.id),
               text: `${kind === 'search' ? 'See' : 'Read'} run ${c.number}` }), '.'));
  }
  // A newer reading at this level that has not taken this one's place.
  if (doc.newer) {
    const n = doc.newer;
    const named = kind === 'paper' ? `${LEVEL_NAMES[level].toLowerCase()} reading`
      : KIND[kind].result;
    const about = [n.number ? `run ${n.number}` : '', !n.running && n.started ? ago(n.started) : '']
      .filter(Boolean).join(', ');
    const said = about ? ` (${about})` : '';
    head.push(n.running
      ? h('div', { class: 'notice' },
          `A new ${named}${said} is under way. This is the previous one; the new one `
          + 'takes its place when it is finished. ',
          h('a', { href: levelHash(slug, level, n.id), text: 'See its progress' }), '.')
      : h('div', { class: 'notice' },
          `A later ${named}${said} stopped before it produced ${kind === 'search'
            ? 'results' : `a ${kind === 'paper' ? 'companion' : 'reading'}`}, so this is `
          + 'the earlier one. ',
          h('button', { class: 'linkish', text: 'Try again',
                        onclick: () => readAgain(slug, level) }), '.'));
  }

  // The scope warning is the one thing in the preamble a reader must not
  // scroll past, so it is lifted out and given its own frame.
  const pre = h('div', { html: doc.preamble || '' });
  pre.querySelectorAll('h1').forEach(el => el.remove());
  // The method asks for it as a quotation; a reading may write it as a bold
  // paragraph instead, and it is framed either way.
  const quote = pre.querySelector('blockquote')
    || [...pre.querySelectorAll('p')].find(el => el.firstChild?.nodeName === 'STRONG'
         && /^(Notice|Scope and correctness warning)\b/.test(el.firstChild.textContent.trim()));
  if (quote) {
    head.push(h('div', { class: 'scope', html: quote.innerHTML }));
    quote.remove();
  }

  // An explanation that was checked and never revised must say so up front.
  // Handing it over without comment would present ten known problems as a
  // finished companion — the reassurance this tool exists to withhold.
  if (review && findings.length && !review.revised) {
    const serious = review.counts.blocking;
    head.push(h('div', { class: `notice ${serious ? 'warn' : ''}` },
      `This explanation was checked against the paper but not revised. The check raised `
      + `${findings.length} point${findings.length === 1 ? '' : 's'}`
      + (serious ? `, ${serious} of them serious` : '') + ', which are still open. ',
      h('a', { href: '#s-review', onclick: jump('s-review'), text: 'See what it found' }), '.'));
  }
  // A draft that nothing ever checked is the least trustworthy thing this tool
  // can produce, and it must never be mistaken for a finished companion. The
  // pass that checks it against the paper is the `verifier` in the five-pass
  // pipeline and the `auditor` in the three-pass one.
  const checked = status?.steps?.some(st => CHECKS.has(st.role) && st.state === 'done');
  if (kind === 'collection' && !review && !checked && !unfinishedRun(status?.run)) {
    // Older collections had no review pass; newer unfinished ones may also
    // stop before their independent reviewer reaches the source papers.
    head.push(h('div', { class: 'notice warn' },
      'This collection companion was not checked by a second reader against '
      + 'the papers. Treat it as a draft and check important claims in the sources.'));
  } else if (kind === 'search') {
    head.push(h('div', { class: 'notice' },
      'The search was asked to list only papers it had found at their source. Still, check '
      + 'a reference before you rely on it or cite it — each one links to where it was found.'));
  } else if (!review && !checked && !unfinishedRun(status?.run)) {
    head.push(h('div', { class: 'notice warn' },
      'This explanation has not been checked against the paper — the reading stopped '
      + 'before a second reader could test it. Treat it as a first draft. ',
      h('button', { class: 'linkish', text: 'Read the paper again',
                    onclick: () => readAgain(slug, level) }), '.'));
  }
  // A small model's companion looks exactly like a strong model's. Only this
  // line tells them apart, and a clean result from one is the reassurance the
  // harness notes warn about.
  // An open-weight model on this Mac searched nothing: what it cites, it
  // remembered — and one reached through OpenRouter may not have searched
  // either. DeepSeek's own service searches (`web`).
  const remembered = (a) => a?.provider === 'open' && !a.web;
  const blind = [by.agent, ...stepsBy.map(st => st.agent)].filter(remembered);
  if (blind.length) {
    const local = blind.some(a => a.account === 'ollama');
    const hosted = blind.filter(a => a.account !== 'ollama');
    const where = hosted.every(a => a.account === 'openrouter') ? 'through OpenRouter'
      : 'through another service';
    head.push(h('div', { class: 'notice warn' }, !hosted.length
      ? 'An open-weight model worked on this companion, and could not search the web here: its '
        + 'Historical context and its references come from what the model remembers. Check every '
        + 'reference before you rely on it.'
      : `${local ? `Models on this Mac and reached ${where} worked` : `A model reached ${where} worked`} `
        + 'on this companion, and may not have been able to search the web: its Historical context '
        + `and its references may come from what ${local ? 'they remember' : 'the model remembers'}. `
        + 'Check every reference before you rely on it.'));
  }
  // A small model's step, when the steps were several AIs': named with its step.
  const smallStep = stepsBy.length > 1 ? stepsBy.find(st => st.agent.small) : null;
  if (smallStep) {
    const stepName = { draft: 'The first reading', check: 'The check', revision: 'The revision' };
    head.push(h('div', { class: 'notice' },
      `${stepName[smallStep.slot] || smallStep.title} was made by ${smallStep.agent.name}, a small model. Small models tend `
      + 'to miss the very gaps unpacman exists to find, so treat what it found — and what it '
      + 'did not — with care.'));
  } else if (by.agent?.small) {
    head.push(h('div', { class: 'notice' }, kind === 'search'
      ? `This search was made by ${who}, a small model. Small models find less, and are more `
        + 'likely to get a reference wrong.'
      : `This reading was made by ${who}, a small model. Small models tend to miss the `
        + 'very gaps unpacman exists to find, so a clean result here is weak evidence.'));
  }
  if (pre.textContent.trim()) head.push(h('div', { class: 'body' }, ...pre.childNodes));

  // --- sections -----------------------------------------------------------
  const bySection = new Map();
  for (const f of findings) {
    const key = f.place.section;
    if (key) (bySection.get(key) || bySection.set(key, []).get(key)).push(f);
  }

  let sectionNumber = 0;
  const secs = doc.sections.map(s => {
    const raised = bySection.get(s.key) || [];
    const title = h('h2', { text: s.label });
    if (raised.length) title.append(flag(raised));
    const section = h('section', { class: `sec ${s.tone || ''}`, id: `s-${s.key}` },
      title, h('div', { class: 'body', html: s.html }));
    // References and the separate map/review panels are unnumbered back matter.
    if (kind !== 'search' && s.key !== 'references') numberSection(section, ++sectionNumber);
    return section;
  });
  // A search's papers, as cards to add and read, where its guide lists them.
  if (kind === 'search' && doc.results?.length) {
    const key = `${slug}:${doc.run_id}`;
    if (state.searchDraft?.key !== key) state.searchDraft = { key, data: {} };
    const picker = createSearchPapers({ slug, doc, max: MAX_TOGETHER, draft: state.searchDraft.data,
      onContinue: async (papers, stillHere) => {
        const target = papers.length === 1 ? papers[0]
          : await api('POST', '/api/collections', { papers: papers.map(p => p.slug) });
        if (!stillHere()) return;
        await readAgain(target.slug, null, () => renderPaper(slug, level, doc.run_id), stillHere);
      } });
    state.searchPicker = picker;
    head.push(picker.panel);
    placeResults(secs, doc, slug, picker);
  }

  // The map belongs beside the proofs, not in an appendix — and never after
  // the bibliography, even in a companion that lacks About the proofs. (Older
  // companions' "Structure of the proofs" arrives under the same key.)
  if (doc.has_map) {
    const proofs = doc.sections.findIndex(s => s.key === 'proofs');
    const refs = doc.sections.findIndex(s => s.key === 'references');
    const at = proofs >= 0 ? proofs + 1 : refs >= 0 ? refs : secs.length;
    const holder = h('div', { class: 'graph-wrap', id: 'graph-root' });
    const title = h('h2', { text: 'Proof map' });
    if ((bySection.get('map') || []).length) title.append(flag(bySection.get('map')));
    const block = h('section', { class: 'sec', id: 's-map' }, title, holder,
      doc.files['map.pdf'] ? h('div', { class: 'map-note' },
        h('a', { href: `/api/papers/${slug}/file/map.pdf${from}`, target: '_blank',
                 rel: 'noopener', text: 'Open the map as a PDF' })) : null);
    secs.splice(at, 0, block);
  }

  if (findings.length) secs.push(reviewSection(review));

  if (doc.missing.length) {
    secs.push(h('div', { class: 'missing' },
      `This companion is missing ${doc.missing.length === 1 ? 'a section' : 'sections'} the method asks for: `
      + doc.missing.join(', ') + '.'));
  }

  mount(paper, ...head, ...secs);
  wrapTables(paper);
  typeset(paper);
  markStatements(paper);
  linkCitations(paper);
  const reading = { level, run: doc.run_id };
  const termsNote = h('p', { class: 'rail-terms', hidden: true });
  const say = (text, kind, retry) => {
    termsNote.hidden = !text;
    termsNote.className = `rail-terms ${kind || ''}`;
    mount(termsNote, kind === 'busy' ? h('span', { class: 'spin' }) : null, text ? ` ${text} ` : '',
      retry ? h('button', { class: 'linkish', type: 'button', text: 'Try again', onclick: retry }) : null);
  };
  // Key terms are for a companion to papers; a list of papers has none.
  if (kind !== 'search') glossary.link(paper, slug, reading, say);
  // Switched in Advanced settings while this companion is open.
  const offTerms = settings.onChange((now, patch) => {
    if (!document.body.contains(paper)) { offTerms(); return; }
    if (!('linkTerms' in patch)) return;
    if (now.linkTerms) glossary.link(paper, slug, reading, say);
    else { glossary.unlink(paper); say(''); }
  });

  // Where the reader left this companion, to return them to. The map is drawn
  // after the text, and taller than its placeholder: the browser then moves
  // the text down by the map's height to keep it in view, which puts a reader
  // who was below the map a screenful lower than where they left. So once the
  // map is in, the place is set again — unless they have moved since.
  const viewKey = side ? `side:${slug}:${doc.run_id || level}` : `companion:${slug}:${doc.run_id || level}`;
  const wanted = state.scrolls[viewKey];
  let moved = false;
  const moving = () => { moved = true; };
  for (const kind of ['wheel', 'touchstart', 'keydown', 'mousedown']) {
    (side ? scroller : window).addEventListener(kind, moving, { passive: true, once: true });
  }
  const putBack = () => {
    if (moved || typeof wanted !== 'number' || !paper.isConnected) return;
    if (side) scroller.scrollTop = wanted; else window.scrollTo(0, wanted);
  };

  if (doc.has_map) {
    api('GET', `/api/papers/${slug}/map${from}`)
      .then(data => { drawMap($('#graph-root'), data, { typeset }); requestAnimationFrame(putBack); })
      .catch(() => mount($('#graph-root'), h('div', { class: 'map-note', text: 'The map could not be drawn.' })));
  }

  // --- the rail -----------------------------------------------------------
  const entries = secs.filter(s => s.id).map(s => ({
    id: s.id,
    label: s.querySelector('h2').firstChild.textContent,
    tone: s.classList.contains('warn') ? 'warn' : '',
  }));
  const links = entries.map(e => h('a', {
    href: `#${e.id}`, text: e.label, class: e.tone, onclick: jump(e.id),
  }));

  // The paper opens here, drawn in the page, so that it can be selected and
  // explained like the companion; the PDF file itself is one click further.
  // Several papers are listed instead; a search has no paper of its own.
  const files = kind === 'search' ? []
    : [h('a', { href: `#/p/${slug}/paper`, text: kind === 'collection' ? 'The papers' : 'The paper' })];
  if (doc.files['companion.pdf']) {
    files.push(h('a', { href: `/api/papers/${slug}/file/companion.pdf${from}`,
                        target: '_blank', rel: 'noopener',
                        text: unfinishedRun(status?.run)
                          ? 'Saved draft as a PDF' : 'This companion as a PDF' }));
  }
  // Since the method's twelfth version the PDF is typeset from a LaTeX source
  // the reading keeps; it is the thing to take away and edit.
  if (doc.files['companion.tex']) {
    files.push(h('a', { href: `/api/papers/${slug}/file/companion.tex${from}`,
                        download: '', text: 'Its LaTeX source' }));
  }

  mount(rail,
    h('div', { class: 'rail-head', text: 'Contents' }),
    links,
    files.length ? h('div', { class: 'rail-files' },
      h('div', { class: 'rail-head', text: 'Files' }), files) : null,
    h('p', { class: 'rail-tip', text: HINT }),
    termsNote);

  // --- scrollspy ----------------------------------------------------------
  // The section being read is the last one whose heading has passed a line a
  // little below the bar. Asking which sections merely touch a band picked the
  // tail of the previous one, which is not what anybody is reading.
  const targets = secs.filter(s => s.id);
  const byId = new Map(links.map((a, i) => [entries[i].id, a]));
  let ticking = false;
  const spy = () => {
    ticking = false;
    if (!document.body.contains(paper)) { scroller.removeEventListener('scroll', onScroll); return; }
    // A little below the bar — or, in a pane, below the row of contents at
    // its top.
    const line = side ? scroller.getBoundingClientRect().top + 72 : 120;
    let active = targets[0];
    for (const s of targets) if (s.getBoundingClientRect().top <= line) active = s;
    // At the very bottom the last section may never reach the line; it is
    // still the one on screen.
    const atEnd = side
      ? scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4
      : window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
    if (atEnd) active = targets[targets.length - 1];
    byId.forEach((a, id) => a.classList.toggle('on', id === active?.id));
    // When the rail is a sideways row, bring the current section into it.
    // Scrolling the rail itself, not the link, so the page does not move.
    const on = active && byId.get(active.id);
    if (on && rail.scrollWidth > rail.clientWidth + 4) {
      const left = on.offsetLeft - 16;
      if (left < rail.scrollLeft || on.offsetLeft + on.offsetWidth > rail.scrollLeft + rail.clientWidth) {
        rail.scrollTo({ left, behavior: 'smooth' });
      }
    }
  };
  const onScroll = () => { if (!ticking) { ticking = true; requestAnimationFrame(spy); } };
  scroller.addEventListener('scroll', onScroll, { passive: true });
  state.view = viewKey;
  if (side) {
    scroller.scrollTop = wanted || 0;
    paperInPane(slug, paperPane);
  } else {
    restoreScroll(state.view);
  }
  spy();
  hints.show(side ? 'side' : 'companion');
}

async function renderSide(slug, level = null, run = null) {
  // Without a reading named, the one last on screen for this paper.
  const last = state.lastCompanion?.slug === slug ? state.lastCompanion : null;
  if (!level && last) ({ level, run } = last);
  await renderCompanion(slug, level, run, null, { side: true });
}

// The paper in the right-hand pane: the same drawing as the Paper view, moved
// here, so that neither view draws the pages twice.
async function paperInPane(slug, pane) {
  const v = viewerFor(slug);
  const where = h('span', { class: 'viewer-page' });
  mount(pane,
    h('div', { class: 'side-bar' },
      h('span', { class: 'side-bar-title', text: 'The paper' }), where, zoomControls(v),
      h('a', { href: `/api/papers/${slug}/file/source.pdf`, target: '_blank', rel: 'noopener',
               text: 'The PDF file' })),
    v.body);
  const opened = await v.ready;
  if (!opened || !pane.isConnected) return;
  opened.setScroller(pane);
  const count = () => { where.textContent = `page ${opened.current()} of ${opened.pages}`; };
  let ticking = false;
  pane.addEventListener('scroll', () => {
    if (!ticking) { ticking = true; requestAnimationFrame(() => { ticking = false; count(); }); }
  }, { passive: true });
  pane.scrollTop = state.scrolls[`side-paper:${slug}`] || 0;
  count();
}

// Zoom (Tancredi, 2026-10-23): smaller, back to the width of the page, or
// larger — as a pinch on the pages does (paper.js). One level for every
// paper, kept in this browser; shown wherever the paper is.
function zoomControls(v) {
  const step = (text, label, dir) => h('button', {
    class: 'zoom-step', type: 'button', text, title: label, 'aria-label': label, disabled: true,
    onclick: () => v.api?.zoomStep(dir) });
  const out = step('−', 'Zoom out', -1);
  const larger = step('+', 'Zoom in', 1);
  const level = h('button', { class: 'zoom-level', type: 'button', text: '100%', disabled: true,
                              title: 'Back to the width of the page',
                              onclick: () => v.api?.setZoom(1) });
  const box = h('span', { class: 'zoom', role: 'group', 'aria-label': 'Zoom' }, out, level, larger);
  v.ready.then((opened) => {
    if (!opened || !box.isConnected) return;
    const show = (z) => {
      if (!box.isConnected) { stop(); return; }
      level.textContent = `${Math.round(z * 100)}%`;
      level.setAttribute('aria-label', `Zoom ${Math.round(z * 100)}%: back to the width of the page`);
      level.disabled = false;
      out.disabled = z <= ZOOM_MIN;
      larger.disabled = z >= ZOOM_MAX;
    };
    const stop = opened.onZoom(show);
    show(opened.zoom());
  });
  return box;
}

// The divider between the two panes: dragged, or moved with the arrow keys
// when it has the focus; a double click puts it back in the middle. Where it
// was left is remembered in this browser. On a narrow screen the panes are
// stacked, the companion above the paper, and it moves up and down instead.
const SPLIT_KEY = 'deslopify.split';
const STACKED = matchMedia('(max-width: 879.98px)');

function splitView(left, right) {
  const grip = h('div', {
    class: 'side-split', role: 'separator', tabindex: '0',
    'aria-label': 'Divider between the companion and the paper',
    'aria-valuemin': '20', 'aria-valuemax': '80',
    title: 'Drag to resize · double-click to centre',
  });
  const root = h('section', { class: 'side' }, left, grip, right);
  let frac = 0.5;
  try {
    const kept = Number(localStorage.getItem(SPLIT_KEY));
    if (kept >= 0.2 && kept <= 0.8) frac = kept;
  } catch { /* storage off: the middle */ }
  const apply = (f, keep = false) => {
    frac = Math.min(0.8, Math.max(0.2, f));
    root.style.setProperty('--split', `${(frac * 100).toFixed(2)}%`);
    grip.setAttribute('aria-valuenow', String(Math.round(frac * 100)));
    grip.setAttribute('aria-orientation', STACKED.matches ? 'horizontal' : 'vertical');
    if (keep) { try { localStorage.setItem(SPLIT_KEY, String(frac)); } catch { /* not kept */ } }
  };
  apply(frac);

  // Followed on the window, so that a pointer moving faster than the divider
  // is still followed; captured as well where the browser allows it.
  grip.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    try { grip.setPointerCapture(e.pointerId); } catch { /* followed on the window */ }
    root.classList.add('dragging');
    const move = (ev) => {
      const r = root.getBoundingClientRect();
      apply(STACKED.matches ? (ev.clientY - r.top) / r.height : (ev.clientX - r.left) / r.width);
    };
    const up = () => {
      root.classList.remove('dragging');
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
      apply(frac, true);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
  });
  grip.addEventListener('dblclick', () => apply(0.5, true));
  grip.addEventListener('keydown', (e) => {
    const step = e.shiftKey ? 0.1 : 0.02;
    const by = { ArrowLeft: -step, ArrowUp: -step, ArrowRight: step, ArrowDown: step }[e.key];
    const to = { Home: 0.2, End: 0.8, Enter: 0.5 }[e.key];
    if (by === undefined && to === undefined) return;
    e.preventDefault();
    apply(to ?? frac + by, true);
  });
  const turned = () => { if (root.isConnected) apply(frac); else STACKED.removeEventListener('change', turned); };
  STACKED.addEventListener('change', turned);
  return root;
}

// A theorem, proposition or question the method sets as its own paragraph —
// a bold label, then the statement — is shown as a statement, not as prose.
// When the statement introduces a formula, the immediately following display
// belongs to it too: it may be the conclusion or a load-bearing hypothesis,
// rather than merely nearby supporting notation. So does whatever goes on
// with the statement after it (`continues`), as in the PDF.
// The method names the common result, question, algorithm, and definition
// forms; readings also write Claim, Remark and the like.
const STATEMENT = /^(Theorem|Proposition|Lemma|Corollary|Conjecture|Question|Problem|Algorithm|Definition|Claim|Remark|Example|Fact|Assumption|Hypothesis|Observation)\b/;

function markStatements(root) {
  root.querySelectorAll('.body > p').forEach(p => {
    const first = p.firstChild;
    const match = first?.nodeType === Node.ELEMENT_NODE && first.tagName === 'STRONG'
      ? STATEMENT.exec(first.textContent.trim()) : null;
    if (match) {
      const kind = match[1];
      const tone = ['Conjecture', 'Question', 'Problem'].includes(kind) ? 'violet'
        : ['Algorithm', 'Definition', 'Assumption', 'Hypothesis'].includes(kind) ? 'green'
        : ['Remark', 'Example', 'Observation'].includes(kind) ? 'amber' : 'blue';
      const formulas = [];
      let next = p.nextElementSibling;
      let open = sentenceOpen(p.textContent);
      while (next?.matches('.math.block')) {
        formulas.push(next);
        open = displayOpen(next);
        next = next.nextElementSibling;
      }
      for (; next; next = next.nextElementSibling) {
        const more = continues(next, open);
        if (more === null) break;
        formulas.push(next);
        open = more;
      }
      if (!formulas.length) {
        p.classList.add('statement', `statement-${tone}`);
        return;
      }
      const box = document.createElement('div');
      box.className = `statement statement-${tone}`;
      p.before(box);
      box.append(p, ...formulas);
    }
  });
}

// What may follow a statement's paragraph and displays and still be part of
// it — the same rules as the PDF's boxes (summary_pdf._statement_end; a
// tester's report, 2026-10-23: boxes that stopped short of the statement). A
// sentence a display interrupted goes on in lower case ("for every prime p",
// "where a_k is …"), and the method writes a statement in italics, so a
// paragraph wholly in italics goes on with it too; a display or a list belongs
// to it after a sentence left open (a colon leaves it open). Upright prose
// beginning a new sentence, a source note, another statement: after the box.
// Returns whether the sentence is still open after `el`, or null if `el` is
// not part of the statement.
function continues(el, open) {
  if (el.matches('.math.block')) return open ? displayOpen(el) : null;
  if (el.matches('ol, ul')) return open ? false : null;
  if (el.matches('p') && (/\p{Ll}/u.test(firstLetter(el)) || whollyItalic(el))) {
    return sentenceOpen(el.textContent);
  }
  return null;
}

// The first letter a reader sees, inside emphasis; none if a formula, a link
// or bold type comes first (the PDF's rules see a command there).
function firstLetter(el) {
  for (const n of el.childNodes) {
    if (n.nodeType === Node.TEXT_NODE) {
      const t = n.nodeValue.trim();
      if (t) return t[0];
    } else if (n.nodeType === Node.ELEMENT_NODE) {
      return n.matches('em, i') ? firstLetter(n) : '';
    }
  }
  return '';
}

function whollyItalic(p) {
  const parts = [...p.childNodes].filter(n => n.nodeType !== Node.TEXT_NODE || n.nodeValue.trim());
  return parts[0]?.nodeType === Node.ELEMENT_NODE && parts[0].matches('em, i')
    && (parts.length === 1 || (parts.length === 2 && parts[1].nodeType === Node.TEXT_NODE
                               && /^[.,;:]$/.test(parts[1].nodeValue.trim())));
}

function sentenceOpen(text) {
  return !/[.?!]$/.test(text.trim());
}

// A display's own last mark, read from its source (KaTeX keeps it), past any
// equation number or label.
function displayOpen(el) {
  const tex = el.querySelector('annotation[encoding="application/x-tex"]')?.textContent
    ?? el.textContent;
  return sentenceOpen(tex.replace(/(?:\\(?:tag\*?|label)\{[^{}]*\}|\\(?:notag|nonumber)|\s)+$/, ''));
}

// Citation keys — [Mil56], [GM74, Theorem 2] — link to their entry in the
// companion's bibliography, and show it on hover. A bracket that is not a key
// of that bibliography is left exactly as written.
const CITE_KEY = /^[A-Za-z][A-Za-z+.-]{1,4}[0-9]{2}[a-z]?$/;

function linkCitations(root) {
  const refs = root.querySelector('#s-references');
  if (!refs) return;
  const entries = new Map();
  refs.querySelectorAll('li').forEach(li => {
    const m = li.textContent.trim().match(/^\[([^\]]+)\]\s*/);
    if (!m || !CITE_KEY.test(m[1]) || entries.has(m[1])) return;
    li.id = `ref-${m[1]}`;
    li.classList.add('bibentry');
    entries.set(m[1], li.textContent.trim().slice(m[0].length).replace(/\s+/g, ' '));
  });
  if (!entries.size) return;

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.nodeValue.includes('[')
      && !n.parentElement.closest('.katex, .math, a, code, pre, .bibentry'))
      ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT,
  });
  const nodes = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) nodes.push(n);
  for (const node of nodes) {
    const text = node.nodeValue;
    const re = /\[([^[\]\n]+?)\]/g;
    let last = 0;
    let out = null;
    for (let m = re.exec(text); m; m = re.exec(text)) {
      const parts = m[1].split(/;\s*/);
      const keys = parts.map(part => part.split(',')[0].trim());
      if (!keys.every(k => entries.has(k))) continue;
      out = out || document.createDocumentFragment();
      out.append(text.slice(last, m.index), '[');
      parts.forEach((part, i) => {
        if (i) out.append('; ');
        out.append(h('a', { class: 'cite', href: `#ref-${keys[i]}`, title: entries.get(keys[i]),
                            text: part.trim(), onclick: jumpTo(`ref-${keys[i]}`) }));
      });
      out.append(']');
      last = m.index + m[0].length;
    }
    if (out) { out.append(text.slice(last)); node.replaceWith(out); }
  }
}

// Scroll to an entry and mark it for a moment, so the eye lands on it.
function jumpTo(id) {
  return (ev) => {
    ev.preventDefault();
    const el = document.getElementById(id);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.remove('lit');
    void el.offsetWidth;
    el.classList.add('lit');
  };
}

function jump(id) {
  return (ev) => {
    ev.preventDefault();
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
}

function flag(raised) {
  const serious = raised.some(f => f.severity === 'blocking');
  const open = raised.filter(f => !f.answer).length;
  const text = `${raised.length} point${raised.length === 1 ? '' : 's'} raised`
    + (open && open < raised.length ? `, ${open} open` : '');
  return h('a', { class: `flag ${serious ? 'serious' : ''}`, href: '#s-review',
                  onclick: jump('s-review'), text });
}

function reviewSection(review) {
  const findings = review.findings;
  const n = findings.length;
  const intro = review.revised
    ? `A second reader, who had not written this companion, read it against the paper and raised `
      + `${n} point${n === 1 ? '' : 's'}. Each was then answered — fixed, or disputed with a reason.`
    : `A second reader, who had not written this companion, read it against the paper and raised `
      + `${n} point${n === 1 ? '' : 's'}. They have not been addressed, so read the passages they point to with care.`;

  const card = (f) => h('div', { class: `finding ${f.severity === 'blocking' ? 'serious' : ''}` },
    h('div', { class: 'finding-kind', text: f.kind_label }),
    h('p', { class: 'finding-problem', text: f.problem }),
    f.quote ? h('div', { class: 'finding-quote', text: `“${f.quote}”` }) : null,
    h('div', { class: 'finding-meta' }, 'About ',
      f.place.section
        ? h('a', { href: `#s-${f.place.section}`, onclick: jump(`s-${f.place.section}`),
                   text: f.place.label })
        : f.place.label, '.'),
    f.answer ? h('div', { class: 'finding-answer' },
      h('b', { class: f.answer.disposition === 'fixed' ? 'fixed' : 'refused',
               text: `${FIXED_WORDS[f.answer.disposition] || f.answer.disposition}. ` }),
      f.answer.note) : null);

  const group = (sev, label) => {
    const items = findings.filter(f => f.severity === sev);
    if (!items.length) return null;
    return [h('div', { class: `review-group ${sev === 'blocking' ? 'serious' : ''}`, text: label }),
            items.map(card)];
  };
  const minor = findings.filter(f => f.severity === 'minor');

  return h('section', { class: 'sec', id: 's-review' },
    h('h2', { text: 'What the check found' }),
    h('div', { class: 'body' },
      h('p', { class: 'review-intro', text: intro }),
      // In the checker's own words — since the method's twelfth version this
      // includes how many formulas it checked in the typeset companion.
      review.summary ? h('details', { class: 'review-summary' },
        h('summary', { text: 'The checker’s own summary' }),
        h('p', { text: review.summary })) : null,
      group('blocking', 'Serious'),
      group('worth-fixing', 'Worth fixing'),
      minor.length ? h('details', { class: 'minor' },
        h('summary', { text: `Minor (${minor.length})` }), minor.map(card)) : null));
}

// ------------------------------------------------------------ the paper

const TOUCH = matchMedia('(hover: none) and (pointer: coarse)').matches;
const HINT = TOUCH
  ? 'Select any word or passage, then tap Explain — or Ask… to put your own question.'
  : 'Select any word or passage and right-click it: Explain, or ask your own question.';

async function renderSource(slug, page) {
  stopPolling();
  state.slug = slug;
  let status;
  try { status = await api('GET', `/api/papers/${slug}${query({ level: readingOf(slug)?.level })}`); }
  catch (err) { toast(err.message, 'err'); go(null); return; }
  const kind = noteKind(status.paper);
  // Several papers are listed, each opening as the paper it is; a search has
  // no paper of its own.
  if (kind === 'search') { go(slug); return; }

  const back = readingOf(slug);
  const main = back && back.run && back.run === status.companion_run ? 'Companion'
    : back?.run ? 'Reading'
      : status.companion && !status.run?.running ? 'Companion'
        : status.run ? 'Reading' : 'Back';
  if (status.companion) state.withCompanion.add(slug);
  barFor(slug, 'paper', main, 'paper');
  notes.attach(slug);
  if (kind === 'collection') { renderMembers(slug, status); return; }

  const v = viewerFor(slug);
  const where = h('span', { class: 'viewer-page' });
  const bar = h('div', { class: 'viewer-bar' },
    h('span', { class: 'viewer-title', text: status.paper.title }),
    where,
    zoomControls(v),
    h('span', { class: 'viewer-hint', text: HINT }),
    h('a', { href: `/api/papers/${slug}/file/source.pdf`, target: '_blank', rel: 'noopener',
             text: 'The PDF file' }));
  const view = h('section', { class: 'viewer' }, bar, v.body);
  // Its height too, measured as the bar's is: a page brought into view
  // (scrollToPage) starts below both, not under them.
  new ResizeObserver(() => view.style.setProperty('--viewer-bar-h', `${bar.offsetHeight}px`))
    .observe(bar);
  mount($('#view'), view);
  firstAsk.at('paper', { running: !!status.run?.running, companion: !!status.companion });
  state.view = `paper:${slug}`;
  hints.show('paper');

  const opened = await v.ready;
  if (!opened || !document.body.contains(view)) return;
  opened.setScroller(null);             // back from a pane, if it was in one
  const count = () => { where.textContent = `page ${opened.current()} of ${opened.pages}`; };
  let ticking = false;
  const onScroll = () => {
    if (!document.body.contains(view)) { window.removeEventListener('scroll', onScroll); return; }
    if (!ticking) { ticking = true; requestAnimationFrame(() => { ticking = false; count(); }); }
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  if (page) opened.scrollToPage(page);
  else restoreScroll(state.view);
  count();
}

// The papers read together: each is a paper of its own, opened as one.
function renderMembers(slug, status) {
  const members = status.paper.members || [];
  const view = h('section', { class: 'stage runs-page members-page' },
    h('div', { class: 'runs-head' },
      h('div', {},
        h('h1', { text: status.paper.title }),
        h('p', { text: 'The papers read together. Each is also a paper of its own: open it to '
                       + 'read it, to select and ask about it, or to read it on its own.' }))),
    h('div', { class: 'runs-list' },
      h('h2', { text: `${members.length} papers` }),
      members.map((m, i) => h('div', { class: 'run-card member' },
        h('span', { class: 'run-main' },
          h('span', { class: 'run-title', text: m.title }),
          h('span', { class: 'run-meta', text: m.here === false
            ? `Paper ${i + 1} of ${members.length} · no longer among your papers; its PDF is kept here`
            : `Paper ${i + 1} of ${members.length}` })),
        h('span', { class: 'member-actions' },
          m.here === false ? null
            : h('a', { class: 'ghost', href: `#/p/${m.slug}/paper`, text: 'Open it' }),
          h('a', { class: 'linkish', href: `/api/papers/${slug}/member/${i}`, target: '_blank',
                   rel: 'noopener', text: 'PDF' }))))));
  mount($('#view'), view);
  state.view = `papers:${slug}`;
  firstAsk.at('members', { running: !!status.run?.running });
  restoreScroll(state.view);
}

// A search's papers, where its guide lists them: a section headed by a group
// of results keeps its own words, and has its papers as cards in place of its
// list. Groups the guide does not head come first.
function placeResults(secs, doc, slug, picker = null) {
  const norm = (t) => String(t || '').toLowerCase().replace(/[*_`]/g, '').replace(/\s+/g, ' ').trim();
  const groups = new Map();
  doc.results.forEach((r, i) => {
    const name = r.group || 'Papers found';
    (groups.get(name) || groups.set(name, []).get(name)).push([r, i]);
  });
  const placed = new Set();
  for (const sec of secs) {
    const label = norm(sec.querySelector('h2')?.textContent);
    const name = [...groups.keys()].find(g => norm(g) === label);
    if (!name) continue;
    placed.add(name);
    const body = sec.querySelector('.body');
    body.querySelectorAll(':scope > ul, :scope > ol').forEach(el => el.remove());
    body.append(resultList(groups.get(name), doc, slug, picker));
  }
  [...groups.keys()].filter(g => !placed.has(g)).reverse().forEach((name, i) => {
    secs.unshift(h('section', { class: 'sec', id: `s-found-${i}` },
      h('h2', { text: name }), h('div', { class: 'body' }, resultList(groups.get(name), doc, slug, picker))));
  });
}

function resultList(items, doc, slug, picker = null) {
  return h('ol', { class: 'results' }, items.map(([r, i]) => resultCard(r, i, doc, slug, picker)));
}

// One paper a search found: what it is, why it answers, and — where its PDF is
// open — a button that adds it to the library, to read here.
function resultCard(r, index, doc, slug, picker = null) {
  const authors = r.authors || [];
  const who = authors.length > 4 ? `${authors.slice(0, 3).join(', ')} et al.` : authors.join(', ');
  const venue = r.venue && !(r.arxiv && /arxiv/i.test(r.venue)) ? r.venue : '';
  const where = [r.arxiv ? `arXiv:${r.arxiv}` : '', venue, r.doi ? `doi:${r.doi}` : '']
    .filter(Boolean).join(' · ');
  const out = (href, text) => h('a', { href, target: '_blank', rel: 'noopener' }, mathText(text));
  let add = null;
  if (r.pdf && doc.run_id) {
    add = h('button', { class: 'ghost result-add', type: 'button', text: 'Add to my papers' });
    add.addEventListener('click', async () => {
      add.disabled = true;
      mount(add, h('span', { class: 'spin' }), ' Fetching…');
      try {
        const got = await api('POST', `/api/papers/${slug}/fetch`, { run: doc.run_id, index });
        picker?.remember(index, got);
        add.replaceWith(h('a', { class: 'ghost result-add', href: `#/p/${got.slug}/runs`,
                                 text: got.existing ? 'You have it — open it' : 'Added — read it' }));
        toast(got.existing ? 'You have this paper already.' : `Added to your papers: ${got.title}`);
      } catch (err) {
        toast(err.message, 'err');
        add.disabled = false;
        add.textContent = 'Add to my papers';
      }
    });
  }
  return h('li', { class: 'result' },
    picker?.checkbox(index),
    h('div', { class: 'result-title' }, r.url ? out(r.url, r.title) : mathText(r.title)),
    h('div', { class: 'result-meta', text: [who, r.year, where].filter(Boolean).join(' · ') }),
    r.summary ? h('p', { class: 'result-summary' }, mathText(r.summary)) : null,
    r.why ? h('p', { class: 'result-why' }, h('span', { class: 'result-why-k', text: 'Why it is here: ' }),
                    mathText(r.why)) : null,
    h('div', { class: 'result-actions' },
      add || h('span', { class: 'result-none', text: 'No open PDF found' }),
      r.pdf ? out(r.pdf, 'PDF') : null,
      r.url ? out(r.url, r.arxiv ? 'arXiv page' : 'Its page') : null));
}

// What the reader said they know, before a Non-expert reading (questions.js),
// and the background they gave in Advanced settings, as the reading had them.
async function showAnswers(slug, run) {
  let data;
  try { data = await api('GET', `/api/papers/${slug}/answers?run=${encodeURIComponent(run)}`); }
  catch (err) { toast(err.message, 'err'); return; }
  const names = data.names || {};
  const asked = data.questions || [];
  const both = data.cv && asked.length;
  const dialog = h('dialog', { class: 'settings answers', 'aria-labelledby': 'answers-title' },
    h('div', { class: 'settings-form' },
      h('h2', { id: 'answers-title',
                text: both ? 'Who it was written for' : asked.length ? 'Your answers' : 'Your background' }),
      h('p', { class: 'set-intro', text: both
        ? 'The background you gave, and what you said you already know, as this reading had them. '
          + 'The companion was written for both.'
        : asked.length ? 'What you said you already know, before this reading began. The '
          + 'companion was written for these answers.'
          : 'The mathematical background you gave in Advanced settings, as this reading had it. '
            + 'The companion was written for it.' }),
      data.cv ? [both ? h('h3', { class: 'answers-h', text: 'Your mathematical background' }) : null,
                 h('div', { class: 'answers-cv', text: data.cv })] : null,
      both ? h('h3', { class: 'answers-h', text: 'Your answers' }) : null,
      asked.length ? h('ol', { class: 'answers-list' }, asked.map(q => h('li', {
        class: q.parent ? 'answers-follow' : '' },
        h('span', { class: 'answers-q', text: q.question }),
        h('b', { class: `answers-a a-${data.answers?.[q.id] || 'none'}`,
                 text: names[data.answers?.[q.id]] || 'Not answered' })))) : null,
      h('div', { class: 'set-foot' }, h('span'),
        h('button', { class: 'primary', type: 'button', text: 'Close',
                      onclick: () => dialog.close() }))));
  dialog.addEventListener('close', () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

// The paper, drawn once per paper and kept: going back to the companion and
// returning should not redraw every page. The same drawing is shown by the
// Paper view and in the side-by-side view's right pane.
//
// Kept only while it works. A paper that could not be opened, or whose drawing
// stopped working, is opened afresh the next time it is shown — and at once,
// from "Try again" — rather than kept broken until the page is reloaded,
// which is what used to make the paper unviewable until unpacman was
// restarted (2026-10-08).
function viewerFor(slug) {
  if (state.viewer?.slug === slug && !state.viewer.broken) return state.viewer;
  dropViewer();
  const file = `/api/papers/${slug}/file/source.pdf`;
  const body = h('div', { class: 'viewer-body' },
    h('p', { class: 'viewer-wait' }, h('span', { class: 'spin' }), ' Opening the paper…'));
  const v = { slug, body, api: null, broken: false, stop: new AbortController() };
  const again = () => h('button', { class: 'linkish', type: 'button', text: 'Try again',
                                    onclick: () => redrawPaper(v) });
  const fileLink = () => h('a', { href: file, target: '_blank', rel: 'noopener',
                                  text: 'open the PDF file' });
  const broken = (why) => {
    if (v.broken) return;
    v.broken = true;
    diag.note('viewer', `the paper stopped drawing: ${why}`);
    body.prepend(h('div', { class: 'notice warn viewer-broken', role: 'alert' },
      'The paper stopped drawing here. ', again(), ', or ', fileLink(), ' instead.'));
  };
  v.ready = openViewer(body, file, { onBroken: broken, signal: v.stop.signal })
    .then((opened) => { v.api = opened; body.querySelector('.viewer-wait')?.remove(); return opened; })
    .catch((err) => {
      v.broken = true;
      if (v.stop.signal.aborted) return null;
      const why = String(err?.message || err);
      diag.note('viewer', `the paper could not be opened: ${why}`);
      mount(body, h('p', { class: 'viewer-wait' },
        `The paper could not be drawn here (${why}). `, again(), ', or ', fileLink(), ' instead.'));
      return null;
    });
  state.viewer = v;
  return v;
}

function dropViewer() {
  const v = state.viewer;
  if (!v) return;
  state.viewer = null;
  v.stop.abort();
  v.api?.destroy();
}

// The paper opened again, wherever it is being shown.
function redrawPaper(v) {
  if (state.viewer === v) dropViewer();
  route();
}

// Back to the page after a while away (another tab, another app, the night):
// the paper's drawing is asked whether it still answers, and drawn again if
// not — before the reader finds it blank.
let hiddenSince = 0;
document.addEventListener('visibilitychange', async () => {
  if (document.visibilityState === 'hidden') { hiddenSince = Date.now(); return; }
  const v = state.viewer;
  if (!v?.api || Date.now() - hiddenSince < 60_000) return;
  if (!(await v.api.check()) && state.viewer === v && v.body.isConnected) redrawPaper(v);
});

// Whether the paper is on screen now: the Paper view, or the side-by-side
// view's right pane.
function paperShown(r) {
  return (r.view === 'paper' || r.view === 'side') && state.viewer?.slug === r.slug
    && state.viewer.api && state.viewer.body.isConnected;
}

// What the notes ask the page to do.
async function openPage(n) {
  const r = parseRoute();
  if (!r) return;
  if (paperShown(r)) {
    state.viewer.api.scrollToPage(n, { smooth: true });
    return;
  }
  await navigate(`#/p/${r.slug}/paper/${n}`);
}

async function showInText(anchor) {
  const r = parseRoute();
  if (!r) return;
  if (anchor.kind === 'paper') {
    if (!paperShown(r)) await navigate(`#/p/${r.slug}/paper/${anchor.page || 1}`);
    const opened = state.viewer?.api;
    if (!opened || !anchor.page) return;
    opened.scrollToPage(anchor.page);
    const layer = await opened.textOf(anchor.page);
    if (!notes.flashIn(layer, anchor.find)) toast('That passage could not be found on its page again.');
  } else {
    // Back to the companion the question was asked in: each reading has its own.
    const at = anchor.reading;
    const here = readingOf(r.slug);
    const same = (r.view === 'main' || r.view === 'side') && $('#paper')
      && (!at || (here && here.level === at.level && (!at.run || here.run === at.run)));
    // Side by side, the other reading is shown side by side too.
    if (!same) {
      await navigate(r.view === 'side' ? sideHash(r.slug, at?.level, at?.run)
        : at ? levelHash(r.slug, at.level, at.run) : `#/p/${r.slug}`);
    }
    const root = (anchor.section && document.getElementById(`s-${anchor.section}`)) || $('#paper');
    if (!notes.flashIn(root, anchor.find)) root?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

// ----------------------------------------------------------------- boot

// Where the view being left was scrolled to — the window, or, side by side,
// each pane — so that coming back returns the reader to the same line.
function saveScroll() {
  if (!state.view) return;
  const left = $('.side-left');
  const right = $('.side-right');
  state.scrolls[state.view] = left ? left.scrollTop : window.scrollY;
  if (right && state.slug) state.scrolls[`side-paper:${state.slug}`] = right.scrollTop;
}

// The first visit of a session goes to Getting ready while something a
// reading needs is missing; "Look around first" lets the reader past.
const LOOKED = 'unpacman.lookedAround';

function lookedAround() {
  try { return sessionStorage.getItem(LOOKED) === '1'; } catch { return false; }
}

async function renderReadyView() {
  stopPolling();
  state.slug = null;
  state.file = null;
  state.view = 'ready';
  notes.detach();
  mount($('#bar-right'), about.button(), feedback.button(), settingsButton());
  const root = h('div', { class: 'ready-root' });
  mount($('#view'), root);
  window.scrollTo(0, 0);
  await ready.renderReady(root, {
    onDone: () => {
      try { sessionStorage.setItem(LOOKED, '1'); } catch { /* the page still moves on */ }
      go(null);
    },
  });
}

async function route() {
  state.searchPicker?.dispose();
  state.searchPicker = null;
  saveScroll();
  hints.hide();
  firstAsk.leaving();
  const r = parseRoute();
  if (r?.view === 'ready') await renderReadyView();
  else if (!r) {
    const now = lookedAround() ? ready.known() : await ready.state();
    if (now && !now.ok && !lookedAround()) await renderReadyView();
    else await renderDrop();
  } else if (r.view === 'paper') await renderSource(r.slug, r.page);
  else if (r.view === 'side') await renderSide(r.slug, r.level, r.run);
  else if (r.view === 'runs') await renderRuns(r.slug);
  else await renderPaper(r.slug, r.level, r.run);
  firstAsk.settle();
  const done = state.afterRender;
  state.afterRender = null;
  done?.();
}

notes.init({ openPage, showInText, reading: () => readingOf(currentSlug()),
             kind: (slug) => state.kinds.get(slug) || 'paper' });
glossary.init({ openPage });
firstAsk.load();

window.addEventListener('hashchange', route);
// Runs deleted from Advanced settings: nothing remembered of them, and the
// page beneath drawn again from what is left.
document.addEventListener('unpacman:deleted', () => {
  state.reading = null;
  state.lastCompanion = null;
  state.searchDraft = null; // A selected import may have just been deleted.
  route();
});
document.addEventListener('click', (e) => {
  if (e.target.closest('[data-home]')) { e.preventDefault(); go(null); }
});
window.addEventListener('scroll', () => {
  $('.bar').classList.toggle('scrolled', window.scrollY > 4);
}, { passive: true });
// The bar's height, for the side-by-side view to fill exactly what is below it.
new ResizeObserver(() => {
  document.documentElement.style.setProperty('--bar-h', `${$('.bar').offsetHeight}px`);
}).observe($('.bar'));

route();
