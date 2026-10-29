// The image viewer: a click on an image of the overview or of a thread shows it over the whole window, and the left
// and right arrows step through the other images of the same place, going round at the ends.
(function imageViewer() {
  const box = document.createElement('dialog');
  box.id = 'viewer'; box.autofocus = true;
  box.innerHTML = '<button class="ghost close" aria-label="Close">✕</button><img alt=""><span class="count"></span>';
  document.body.append(box);
  const img = box.querySelector('img'), count = box.querySelector('.count');
  let images = [], at = 0;
  const show = (i) => {
    at = (i + images.length) % images.length;
    img.src = images[at].src; img.alt = images[at].alt;
    count.textContent = `${at + 1} / ${images.length}`;
    count.hidden = images.length < 2;
  };
  // The capture on the window comes before the shell's, which would open the link GitHub wraps around an image.
  window.addEventListener('click', (e) => {
    const clicked = e.target.closest && e.target.closest('#overview .prbody img, #overview .cbody img, .thread .cbody img');
    if (!clicked) return;
    e.preventDefault(); e.stopPropagation();
    const all = [...clicked.closest('#overview, .thread').querySelectorAll('.prbody img, .cbody img')];
    images = all.map(i => ({ src: i.currentSrc || i.src, alt: i.alt }));
    show(all.indexOf(clicked));
    box.showModal();
  }, true);
  // A click on the dialog itself, outside the image, is a click on the backdrop.
  box.addEventListener('click', (e) => { if (e.target === box || e.target.closest('.close')) box.close(); });
  // The shell cancels every Escape, which stops the dialog from closing on its own, so the viewer closes it. The
  // capture on the window hears the keys wherever WebKit leaves the focus. The page's own keys wait until the viewer
  // closes; chords with Cmd still reach the shell.
  window.addEventListener('keydown', (e) => {
    if (!box.open || e.metaKey) return;
    e.stopPropagation();
    if (e.key === 'Escape') { e.preventDefault(); box.close(); return; }
    if (e.ctrlKey || e.altKey || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
    e.preventDefault();
    show(at + (e.key === 'ArrowLeft' ? -1 : 1));
  }, true);
})();
