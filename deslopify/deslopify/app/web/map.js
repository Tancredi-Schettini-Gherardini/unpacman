/* The proof map, drawn in the page.
 *
 * The same graph the PDF map is drawn from, but something you can ask
 * questions of. Every box already carries a description and a verbatim quotation
 * from the paper; clicking one shows them, together with what the step rests on
 * and what it is used for — which is "where is this used?" and "what does this
 * need?" answered by pointing rather than by reading.
 *
 * Boxes are HTML, not SVG text: labels wrap properly, carry typeset
 * mathematics, take keyboard focus, and follow the page's theme. Arrows are an
 * SVG layer underneath, routed around the rows they skip rather than through
 * them. */

const BOX_MAX = 232, BOX_MIN = 150, GAP_X = 26, GAP_Y = 54, SIDE = 44;

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function svg(tag, attrs = {}) {
  const e = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}

export function drawMap(root, data, { typeset = () => {} } = {}) {
  const nodes = data.nodes;
  const edges = data.edges;
  const parents = new Map(), children = new Map();
  for (const e of edges) {
    if (!(e.source in nodes) || !(e.target in nodes)) continue;
    (parents.get(e.target) || parents.set(e.target, []).get(e.target)).push(e);
    (children.get(e.source) || children.set(e.source, []).get(e.source)).push(e);
  }

  root.replaceChildren();
  const stage = el('div', 'graph');
  const layer = svg('svg', { class: 'graph-edges', 'aria-hidden': 'true' });
  const defs = svg('defs');
  for (const [id, cls] of [['arrow', 'tip'], ['arrow-hot', 'tip hot'], ['arrow-bad', 'tip bad']]) {
    const m = svg('marker', { id: `${id}-${root.id || 'm'}`, viewBox: '0 0 10 10', refX: '9', refY: '5',
                              markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse' });
    m.append(svg('path', { d: 'M 0 1 L 9 5 L 0 9 z', class: cls }));
    defs.append(m);
  }
  layer.append(defs);
  stage.append(layer);

  const detail = el('div', 'graph-detail');
  detail.hidden = true;
  const hint = el('div', 'graph-hint',
    'Select a step to see what it rests on, what it is used for, and the passage in the paper.');

  // --- boxes ---------------------------------------------------------------
  const boxes = new Map();
  const make = (id) => {
    const n = nodes[id];
    const box = el('button', `gnode k-${n.kind === '!' ? 'gap' : n.kind}${n.crux ? ' crux' : ''}${n.uncertain ? ' unsure' : ''}`);
    box.type = 'button';
    box.dataset.id = id;
    const role = el('span', 'gnode-role', n.crux ? `${n.role} · key step` : n.role);
    const label = el('span', 'gnode-label', n.label);
    box.append(role, label);
    box.setAttribute('aria-label', `${n.role}: ${n.label}`);
    box.addEventListener('click', (ev) => { ev.stopPropagation(); select(id); });
    stage.append(box);
    boxes.set(id, box);
    return box;
  };
  data.rows.flat().forEach(make);
  data.gaps.forEach(make);

  let gapRule = null, gapTitle = null;
  if (data.gaps.length) {
    gapRule = el('div', 'graph-gaprule');
    gapTitle = el('div', 'graph-gaptitle', 'Where the paper skips work');
    stage.append(gapRule, gapTitle);
  }

  root.append(stage, hint, detail);
  typeset(stage);

  // --- layout --------------------------------------------------------------
  const rows = [...data.rows, ...(data.gaps.length ? [data.gaps] : [])];
  const pos = new Map();

  function layout() {
    const width = Math.max(320, root.clientWidth);
    // Stay inside the text column when the map fits there; spill into the
    // margin only as far as a wide row actually needs. Centred on the whole
    // wide area instead, a small map floats off to the right of its heading.
    const column = Math.min(width, root.parentElement?.clientWidth || width);
    const widest = Math.max(1, ...rows.map(r => r.length));
    const fit = (room) => (room - 2 * SIDE - (widest - 1) * GAP_X) / widest;
    const boxW = fit(column) >= BOX_MIN
      ? Math.min(BOX_MAX, fit(column))
      : Math.max(BOX_MIN, Math.min(BOX_MAX, fit(width)));
    const block = widest * boxW + (widest - 1) * GAP_X + 2 * SIDE;
    const area = block <= column ? column : Math.min(width, block);
    boxes.forEach(b => { b.style.width = `${boxW}px`; });
    detail.style.maxWidth = `${column}px`;
    hint.style.maxWidth = `${column}px`;

    let y = 6;
    rows.forEach((row, r) => {
      const isGaps = data.gaps.length && r === rows.length - 1;
      if (isGaps) {
        y += 10;
        gapRule.style.top = `${y}px`;
        gapRule.style.width = `${area}px`;
        gapTitle.style.top = `${y + 8}px`;
        y += 34;
      }
      const heights = row.map(id => boxes.get(id).offsetHeight);
      const rowH = Math.max(...heights);
      const total = row.length * boxW + (row.length - 1) * GAP_X;
      let x = (area - total) / 2;
      row.forEach((id, i) => {
        const b = boxes.get(id);
        const top = y + (rowH - heights[i]) / 2;
        b.style.left = `${x}px`;
        b.style.top = `${top}px`;
        pos.set(id, { x, y: top, w: boxW, h: heights[i], row: r, cx: x + boxW / 2 });
        x += boxW + GAP_X;
      });
      y += rowH + GAP_Y;
    });
    const height = y - GAP_Y + 8;
    stage.style.height = `${height}px`;
    layer.setAttribute('width', width);
    layer.setAttribute('height', height);
    layer.setAttribute('viewBox', `0 0 ${width} ${height}`);
    drawEdges(width);
  }

  // --- arrows --------------------------------------------------------------
  const paths = [];
  function drawEdges(width) {
    paths.splice(0).forEach(p => p.remove());
    for (const e of edges) {
      const a = pos.get(e.source), b = pos.get(e.target);
      if (!a || !b) continue;
      const sx = a.cx, sy = a.y + a.h, tx = b.cx, ty = b.y;
      let d;
      if (b.row - a.row <= 1) {
        const dy = Math.max(18, (ty - sy) / 2);
        d = `M ${sx} ${sy} C ${sx} ${sy + dy}, ${tx} ${ty - dy}, ${tx} ${ty}`;
      } else {
        // Skip the rows in between by going round them, on whichever side the
        // arrow is already heading — a straight line would cut through boxes
        // that have nothing to do with it.
        const between = [...pos.values()].filter(p => p.row > a.row && p.row < b.row);
        const right = sx >= tx;
        const lane = right
          ? Math.min(width - 10, Math.max(sx, tx, ...between.map(p => p.x + p.w)) + 18)
          : Math.max(10, Math.min(sx, tx, ...between.map(p => p.x)) - 18);
        const r = 16;
        d = `M ${sx} ${sy} C ${sx} ${sy + r * 1.4}, ${lane} ${sy + r * 0.6}, ${lane} ${sy + r * 2}`
          + ` L ${lane} ${ty - r * 2}`
          + ` C ${lane} ${ty - r * 0.6}, ${tx} ${ty - r * 1.4}, ${tx} ${ty}`;
      }
      const kind = e.type === 'CONTRADICTS' ? 'bad' : e.type === 'MOTIVATES' ? 'soft' : '';
      const path = svg('path', {
        d, class: `edge ${kind}${e.uncertain ? ' unsure' : ''}`,
        'marker-end': `url(#${e.type === 'CONTRADICTS' ? 'arrow-bad' : 'arrow'}-${root.id || 'm'})`,
      });
      path.dataset.source = e.source;
      path.dataset.target = e.target;
      layer.append(path);
      paths.push(path);
    }
    if (current) mark(current);
  }

  // --- selection -----------------------------------------------------------
  let current = null;

  function mark(id) {
    const ins = new Set((parents.get(id) || []).map(e => e.source));
    const outs = new Set((children.get(id) || []).map(e => e.target));
    stage.classList.toggle('focus', !!id);
    boxes.forEach((b, k) => {
      b.classList.toggle('sel', k === id);
      b.classList.toggle('rel-in', ins.has(k));
      b.classList.toggle('rel-out', outs.has(k));
      b.setAttribute('aria-pressed', k === id ? 'true' : 'false');
    });
    paths.forEach(p => {
      const hot = id && (p.dataset.source === id || p.dataset.target === id);
      p.classList.toggle('hot', !!hot);
      if (!p.classList.contains('bad')) {
        p.setAttribute('marker-end', `url(#${hot ? 'arrow-hot' : 'arrow'}-${root.id || 'm'})`);
      }
    });
  }

  function select(id) {
    if (current === id) { clear(); return; }
    current = id;
    mark(id);
    const n = nodes[id];
    detail.replaceChildren();

    const head = el('div', 'gd-head');
    head.append(el('span', `gd-role k-${n.kind === '!' ? 'gap' : n.kind}`, n.role), el('span', 'gd-id', id));
    detail.append(head, el('div', 'gd-label', n.label));
    if (n.description) detail.append(el('p', 'gd-desc', n.description));
    if (n.quote) {
      const q = el('blockquote', 'gd-quote');
      q.append(el('span', 'gd-quote-k', 'In the paper'), el('span', 'gd-quote-t', n.quote));
      detail.append(q);
    }
    if (n.uncertain) {
      detail.append(el('p', 'gd-note', 'The reading marked this step as an interpretation rather than something the paper states outright.'));
    }
    const list = (title, items, other) => {
      if (!items.length) return;
      detail.append(el('div', 'gd-k', title));
      const ul = el('ul', 'gd-list');
      for (const e of items) {
        const target = e[other];
        const li = el('li');
        const link = el('button', 'gd-link', nodes[target]?.label || target);
        link.type = 'button';
        link.addEventListener('click', (ev) => { ev.stopPropagation(); select(target); boxes.get(target)?.focus(); });
        li.append(link);
        if (e.justification) li.append(el('span', 'gd-why', e.justification));
        ul.append(li);
      }
      detail.append(ul);
    };
    list('Rests on', parents.get(id) || [], 'source');
    list('Used for', children.get(id) || [], 'target');
    if (n.kind === '!') {
      detail.append(el('p', 'gd-note', 'A gap is not a step in the argument: it marks a place where the paper asserts work it does not show.'));
    } else if (!(parents.get(id) || []).length && !(children.get(id) || []).length) {
      detail.append(el('p', 'gd-note', 'Nothing on the map connects to this step.'));
    }

    hint.hidden = true;
    detail.hidden = false;
    typeset(detail);
  }

  function clear() {
    current = null;
    mark(null);
    detail.hidden = true;
    hint.hidden = false;
  }

  stage.addEventListener('click', clear);
  root.addEventListener('keydown', (e) => { if (e.key === 'Escape') clear(); });

  layout();
  // Fonts and KaTeX change box heights after first paint; lay out again once
  // they settle, and whenever the column changes width.
  if (document.fonts?.ready) document.fonts.ready.then(layout);
  let last = root.clientWidth;
  new ResizeObserver(() => {
    if (Math.abs(root.clientWidth - last) > 2) { last = root.clientWidth; layout(); }
  }).observe(root);

  return { select, clear, layout };
}
