// The image viewer: a click on an image of the overview or of a thread shows it over the whole window, and the left
// and right arrows step through the other images of the same place that the reader can see, going round at the ends.
(function imageViewer() {
  const box = document.createElement('dialog');
  box.id = 'viewer'; box.className = 'modal'; box.autofocus = true;
  box.innerHTML = '<img alt=""><div class="bar card panel"><span class="count"></span><button class="ghost close" aria-label="Close">✕</button></div>';
  document.body.append(box);
  const img = box.querySelector('img'), count = box.querySelector('.count');
  let images = [], at = 0;
  const show = (i) => {
    at = (i + images.length) % images.length;
    // Without the old picture on screen while the next one loads.
    img.removeAttribute('src');
    img.src = images[at].src; img.alt = images[at].alt;
    count.textContent = `${at + 1} / ${images.length}`;
    count.hidden = images.length < 2;
  };
  // The file a URL names, through the server's /image as well.
  const file = (url) => {
    const u = new URL(url, location.href);
    return (u.origin === location.origin && u.pathname === '/image' ? new URL(u.searchParams.get('src')) : u).pathname.split('/').pop();
  };
  // A click on an image of a summary opens its details, and one on an image in a link to another file follows the
  // link. GitHub wraps the images of a body in links to the same files.
  const viewable = (i) => {
    const a = i.closest('a[href]');
    return !i.closest('summary') && (!a || file(a.href) === file(i.src));
  };
  // The capture on the window comes before the shell's, which would open the link around the image.
  window.addEventListener('click', (e) => {
    const clicked = e.target.closest && e.target.closest('#overview .prbody img, #overview .cbody img, .thread .cbody img');
    if (!clicked || !viewable(clicked) || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault(); e.stopPropagation();
    const all = [...clicked.closest('#overview, .thread').querySelectorAll('.prbody img, .cbody img')]
      .filter(i => i === clicked || (i.getClientRects().length && viewable(i)));
    images = all.map(i => ({ src: i.currentSrc || i.src, alt: i.alt }));
    show(all.indexOf(clicked));
    box.showModal();
  }, true);
  // A click on the dialog itself, outside the image, is a click on the backdrop.
  box.addEventListener('click', (e) => { if (e.target === box || e.target.closest('.close')) box.close(); });
  // The shell cancels every Escape, which stops the dialog from closing on its own, so the viewer closes it. The
  // shell's own chords come first, and the page's keys wait until the viewer closes. The capture on the document
  // hears the keys wherever WebKit leaves the focus.
  document.addEventListener('keydown', (e) => {
    if (!box.open) return;
    e.stopPropagation();
    if (e.key === 'Escape') { e.preventDefault(); box.close(); return; }
    if (e.metaKey || e.ctrlKey || e.altKey || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
    e.preventDefault();
    show(at + (e.key === 'ArrowLeft' ? -1 : 1));
  }, true);
})();
