'use strict';
const catalogue = window.PORTRAITS ?? [];
const $ = id => document.getElementById(id);
const UNRECORDED = '****';
const label = value => value === UNRECORDED ? 'Unspecified' : value;
if (!window.PORTRAITS) $('empty').textContent = 'No catalogue was generated. Run python tools/GeneratePortraitViewer.py, then reload this page.';
const compare = (a, b) => a.localeCompare(b, undefined, {numeric: true});
let matches = [], shown = 0;
for (const field of ['race', 'sex']) {
  for (const value of [...new Set(catalogue.map(p => p[field]))].sort(compare)) {
    $(field).add(new Option(label(value), value));
  }
}
function details(p) {
  $('large').hidden = !p.image;
  if (p.image) $('large').src = p.image;
  else $('large').removeAttribute('src');
  $('large').alt = p.name;
  $('name').textContent = p.name;
  $('metadata').textContent = `${p.race} · Sex: ${label(p.sex)} · ${p.id === null ? 'Not listed in portraits.2da' : `Table ID: ${p.id}`}`;
  $('resource').value = p.name;
  $('detail').showModal();
}
$('resource').addEventListener('click', event => event.target.select());
function more() {
  const batch = matches.slice(shown, shown + 120);
  const fragment = document.createDocumentFragment();
  for (const p of batch) {
    const card = document.createElement('button');
    card.type = 'button'; card.className = 'card';
    card.setAttribute('aria-label', `Open ${p.name}, ${p.race}`);
    if (p.image) {
      const image = document.createElement('img');
      image.src = p.image; image.alt = ''; image.loading = 'lazy'; image.width = 128; image.height = 200;
      card.append(image);
    } else {
      const placeholder = document.createElement('span');
      placeholder.className = 'placeholder'; placeholder.textContent = 'Missing artwork'; card.append(placeholder);
    }
    const caption = document.createElement('span'); caption.className = 'caption';
    const name = document.createElement('strong'); name.textContent = p.name;
    const meta = document.createElement('small'); meta.textContent = p.id === null ? p.race : `${p.race} · #${p.id}`;
    caption.append(name, meta); card.append(caption);
    card.addEventListener('click', () => details(p)); fragment.append(card);
  }
  $('grid').append(fragment); shown += batch.length;
  $('count').textContent = `${matches.length.toLocaleString()} matches · ${shown.toLocaleString()} shown`;
  $('more').hidden = shown >= matches.length;
}
function render() {
  const terms = $('query').value.toLowerCase().trim().split(/\s+/).filter(Boolean);
  matches = catalogue.filter(p => (!$('race').value || p.race === $('race').value)
    && (!$('sex').value || p.sex === $('sex').value)
    && ($('art').value === 'all' || Boolean(p.image) === ($('art').value === 'local'))
    && terms.every(term => `${p.name} ${p.race} ${p.id ?? ''}`.toLowerCase().includes(term)));
  matches.sort((a, b) => {
    if ($('sort').value === 'id') return (a.id === null ? Infinity : Number(a.id)) - (b.id === null ? Infinity : Number(b.id)) || compare(a.name, b.name);
    if ($('sort').value === 'race') return compare(a.race, b.race) || compare(a.name, b.name);
    return compare(a.name, b.name) * ($('sort').value === 'reverse' ? -1 : 1);
  });
  shown = 0; $('grid').replaceChildren(); $('empty').hidden = matches.length !== 0; more();
}
$('controls').addEventListener('submit', event => event.preventDefault());
$('controls').addEventListener('input', render);
// The form clears its fields after the reset event, so filter on the next tick.
$('controls').addEventListener('reset', () => setTimeout(render, 0));
$('more').addEventListener('click', more);
render();
