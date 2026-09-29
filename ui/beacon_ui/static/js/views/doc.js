// Document viewer: a module document (course map, activity, assignment…) with line
// numbers and its problems marked on the lines they refer to. Read-only.
import { api, h, levelChip, mount } from '../core.js';

export function docView(main, path, line) {
  const module = path.split('/')[0];
  (async () => {
    let text, diags;
    try {
      const res = await fetch(`/files/${path.split('/').map(encodeURIComponent).join('/')}?view=1`);
      if (!res.ok) throw new Error(res.status === 404 ? `${path} does not exist.` : `${res.status} ${res.statusText}`);
      text = await res.text();
      const d = await api(`/api/diagnostics?path=${encodeURIComponent(module)}`);
      diags = (d.outstanding || []).filter(x => x.file === path);
    } catch (e) {
      mount(main, h('div', { class: 'crumbs' }, h('a', { href: `#/module/${module}` }, `← ${module}`)), h('div', { class: 'banner error' }, e.message));
      return;
    }
    const byLine = new Map();
    for (const d of diags) {
      if (!byLine.has(d.line)) byLine.set(d.line, []);
      byLine.get(d.line).push(d);
    }
    const whole = byLine.get(null) || byLine.get(undefined) || [];
    const lines = text.replace(/\r\n/g, '\n').split('\n');
    const worst = (ds) => ds.some(d => d.level === 'error') ? 'error' : 'warn';
    mount(main,
      h('div', { class: 'page-head' }, h('div', {},
        h('div', { class: 'crumbs' }, h('a', { href: '#/' }, 'Programme'), ' / ', h('a', { href: `#/module/${module}` }, module), ' / ', path.slice(module.length + 1)),
        h('h1', {}, path),
        h('div', { class: 'sub' }, diags.length ? `${diags.length} problem${diags.length === 1 ? '' : 's'}` : 'No problems found.')),
        h('a', { class: 'btn', href: `/files/${path}?view=1`, target: '_blank' }, 'Open raw')),
      whole.length || diags.length ? h('div', { class: 'panel', style: { marginBottom: '16px' } },
        h('ul', { class: 'diaglist' }, diags.map(d => h('li', {}, h('div', {}, levelChip(d.level)),
          h('div', {}, d.line ? h('a', { href: '#', onclick: (e) => { e.preventDefault(); document.getElementById(`L${d.line}`)?.scrollIntoView({ block: 'center' }); } }, `line ${d.line}: `) : null,
            d.message, h('div', { class: 'loc' }, d.code), d.hint ? h('div', { class: 'hint' }, d.hint) : null))))) : null,
      h('div', { class: 'panel doc' }, h('table', { class: 'doclines' }, h('tbody', {}, lines.map((l, i) => {
        const n = i + 1;
        const ds = byLine.get(n);
        return [
          h('tr', { id: `L${n}`, class: ds ? `flag-${worst(ds)}` : '' }, h('td', { class: 'ln' }, n), h('td', { class: 'code' }, l || ' ')),
          ds ? h('tr', { class: 'note' }, h('td', {}), h('td', {}, ds.map(d => h('div', {}, levelChip(d.level), ' ', d.message)))) : null,
        ];
      })))));
    if (line) document.getElementById(`L${line}`)?.scrollIntoView({ block: 'center' });
  })();
  return null;
}
