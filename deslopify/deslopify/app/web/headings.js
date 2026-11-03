// Number the displayed companion without changing its saved prose or anchors.
const NUMBER_PREFIX = /^\s*\d+(?:\.\d+)*[.)]?\s+/;

function removeNumber(heading) {
  const prefix = heading.textContent.match(NUMBER_PREFIX)?.[0];
  if (!prefix) return;
  const walker = document.createTreeWalker(heading, NodeFilter.SHOW_TEXT);
  let remaining = prefix.length;
  while (remaining) {
    const node = walker.nextNode();
    if (!node) break;
    const taken = Math.min(remaining, node.textContent.length);
    node.textContent = node.textContent.slice(taken);
    remaining -= taken;
  }
}

export function numberSection(section, number) {
  const title = section.querySelector(':scope > h2');
  // Keep the title's first text node intact: the contents rail reads it, and
  // review flags appended after it must remain interactive.
  title.firstChild.textContent = `${number}. ${title.firstChild.textContent.replace(NUMBER_PREFIX, '')}`;
  const headings = [...section.querySelectorAll('.body h1, .body h2, .body h3, .body h4, .body h5, .body h6')];
  const shallowest = Math.min(...headings.map(h => Number(h.tagName.slice(1))));
  const counters = [];
  for (const heading of headings) {
    const rawDepth = Number(heading.tagName.slice(1)) - shallowest + 1;
    // A skipped Markdown level still gets the next sensible depth, never 1.0.1.
    const depth = Math.min(rawDepth, counters.length + 1);
    counters.length = depth;
    counters[depth - 1] = (counters[depth - 1] || 0) + 1;
    removeNumber(heading);
    const normalized = document.createElement(`h${Math.min(depth + 2, 6)}`);
    for (const attr of heading.attributes) normalized.setAttribute(attr.name, attr.value);
    normalized.append(`${number}.${counters.join('.')} `, ...heading.childNodes);
    heading.replaceWith(normalized);
  }
}
