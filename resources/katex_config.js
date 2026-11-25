(() => {
  const KATEX = 'https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/';
  let loading = null;

  // KaTeX is about 300 KB, so pages without formulas never fetch it.
  const addStyles = (doc) => {
    if (doc.querySelector('link[href*="katex"]')) return;
    const link = doc.createElement('link');
    link.rel = 'stylesheet';
    link.href = KATEX + 'katex.min.css';
    link.integrity = 'sha384-n8MVd4RsNIU0tAv4ct0nTaAbDJwPJzDEaqSD1odI+WdtXRGWt2kTvGFasHpSy3SV';
    link.crossOrigin = 'anonymous';
    doc.head.appendChild(link);
  };

  const load = () => {
    if (window.katex) return Promise.resolve();
    if (!loading) {
      loading = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = KATEX + 'katex.min.js';
        script.integrity = 'sha384-XjKyOOlGwcjNTAIQHIpgOno0Hl1YQqzUOEleOLALmuqehneUG+vnGctmUb0ZY0l8';
        script.crossOrigin = 'anonymous';
        script.onload = resolve;
        script.onerror = () => {
          loading = null;
          script.remove();
          reject(new Error('KaTeX could not be loaded'));
        };
        document.head.appendChild(script);
      });
    }
    return loading;
  };

  const render = (maths) => {
    maths.forEach((node) => {
      const tex = node.textContent || node.innerText;
      const inline = tex.startsWith('\\(') && tex.endsWith('\\)');
      const display = tex.startsWith('\\[') && tex.endsWith('\\]');
      if (!inline && !display) return;
      window.katex.render(tex.slice(2, -2), node, {
        'displayMode': display,
        'throwOnError': false,
        'strict': false,
      });
    });
  };

  window.renderKatex = (elem = document.body) => {
    if (!elem) return;
    const maths = elem.querySelectorAll('.arithmatex');
    if (!maths.length) return;
    addStyles(elem.ownerDocument || elem);
    if (window.katex) {
      render(maths);
    } else {
      load().then(() => render(maths)).catch((error) => console.warn(error.message));
    }
  };
})();
