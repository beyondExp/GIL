import * as Cesium from 'cesium';
import { hudAccentCss } from './uiTokens.js';

function clamp(n, lo, hi) {
  if (!Number.isFinite(n)) return lo;
  return Math.max(lo, Math.min(hi, n));
}

function makeTextTagBillboardImage(text, {
  fg = '#ffffff',
  bg = 'rgba(0,0,0,0.35)',
  font = '600 12px sans-serif',
  paddingX = 6,
} = {}) {
  try {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.font = font;
    const m = ctx.measureText(text);
    const w = Math.ceil(m.width + paddingX * 2);
    const h = 22;
    canvas.width = w;
    canvas.height = h;
    ctx.font = font;
    ctx.fillStyle = bg;
    ctx.strokeStyle = 'rgba(255,255,255,0.18)';
    ctx.lineWidth = 1;
    const r = 8;
    ctx.beginPath();
    ctx.moveTo(r, 0);
    ctx.arcTo(w, 0, w, h, r);
    ctx.arcTo(w, h, 0, h, r);
    ctx.arcTo(0, h, 0, 0, r);
    ctx.arcTo(0, 0, w, 0, r);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = fg;
    ctx.textBaseline = 'middle';
    ctx.fillText(text, paddingX, Math.floor(h / 2));
    return canvas.toDataURL('image/png');
  } catch {
    return null;
  }
}

function normalizeItem(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const id = String(raw.id || '').trim();
  const lat = Number(raw.lat);
  const lon = Number(raw.lon);
  if (!id || !Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  const height = Number.isFinite(Number(raw.height)) ? Number(raw.height) : 0;
  const yawDeg = Number.isFinite(Number(raw.yawDeg)) ? Number(raw.yawDeg) : 0;
  const title = String(raw.title || id);
  const path = String(raw.path || '').trim();
  if (!path) return null;
  const tags = Array.isArray(raw.tags) ? raw.tags.map((t) => String(t)) : [];
  return { id, lat, lon, height, yawDeg, title, path, tags };
}

function resolvePath(path) {
  const p = String(path || '').trim();
  if (!p) return '';
  if (p.startsWith('http://') || p.startsWith('https://')) return p;
  if (p.startsWith('/')) return p;
  // relative to public/custom360/
  return `/custom360/${p.replace(/^\.?\//, '')}`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Minimal WebGL panorama viewer (equirectangular → inside-sphere texture)
// ─────────────────────────────────────────────────────────────────────────────

function mat4Identity() {
  return new Float32Array([1, 0, 0, 0,
                           0, 1, 0, 0,
                           0, 0, 1, 0,
                           0, 0, 0, 1]);
}

function mat4Mul(a, b) {
  const o = new Float32Array(16);
  for (let r = 0; r < 4; r++) {
    for (let c = 0; c < 4; c++) {
      o[r * 4 + c] =
        a[r * 4 + 0] * b[0 * 4 + c] +
        a[r * 4 + 1] * b[1 * 4 + c] +
        a[r * 4 + 2] * b[2 * 4 + c] +
        a[r * 4 + 3] * b[3 * 4 + c];
    }
  }
  return o;
}

function mat4Perspective(fovYRad, aspect, near, far) {
  const f = 1.0 / Math.tan(fovYRad / 2);
  const nf = 1 / (near - far);
  const o = new Float32Array(16);
  o[0] = f / aspect;
  o[5] = f;
  o[10] = (far + near) * nf;
  o[11] = -1;
  o[14] = 2 * far * near * nf;
  return o;
}

function mat4RotateY(yawRad) {
  const c = Math.cos(yawRad);
  const s = Math.sin(yawRad);
  return new Float32Array([ c, 0, -s, 0,
                            0, 1,  0, 0,
                            s, 0,  c, 0,
                            0, 0,  0, 1 ]);
}

function mat4RotateX(pitchRad) {
  const c = Math.cos(pitchRad);
  const s = Math.sin(pitchRad);
  return new Float32Array([ 1, 0,  0, 0,
                            0, c,  s, 0,
                            0, -s, c, 0,
                            0, 0,  0, 1 ]);
}

function buildSphereMesh(latBands = 40, lonBands = 80) {
  const positions = [];
  const uvs = [];
  const indices = [];
  for (let lat = 0; lat <= latBands; lat++) {
    const v = lat / latBands;
    const theta = v * Math.PI;
    const sinT = Math.sin(theta);
    const cosT = Math.cos(theta);
    for (let lon = 0; lon <= lonBands; lon++) {
      const u = lon / lonBands;
      const phi = u * Math.PI * 2;
      const sinP = Math.sin(phi);
      const cosP = Math.cos(phi);
      // Unit sphere; will render inside, so keep as-is and flip winding via indices.
      const x = sinT * cosP;
      const y = cosT;
      const z = sinT * sinP;
      positions.push(x, y, z);
      uvs.push(u, 1 - v);
    }
  }
  for (let lat = 0; lat < latBands; lat++) {
    for (let lon = 0; lon < lonBands; lon++) {
      const first = lat * (lonBands + 1) + lon;
      const second = first + lonBands + 1;
      // Flip winding to render inside.
      indices.push(first, second, first + 1);
      indices.push(second, second + 1, first + 1);
    }
  }
  return {
    positions: new Float32Array(positions),
    uvs: new Float32Array(uvs),
    indices: new Uint16Array(indices),
  };
}

function createPanoramaCanvasOverlay() {
  const overlay = document.createElement('section');
  overlay.className = 'custom360-360 custom360-360--hidden';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-label', 'Custom 360 Viewer');
  overlay.innerHTML = `
    <div class="custom360-360__backdrop" data-backdrop></div>
    <div class="custom360-360__panel" role="document">
      <div class="custom360-360__bar">
        <div class="custom360-360__title" data-title>360</div>
        <div class="custom360-360__bar-actions">
          <button class="custom360-360__copy" type="button" title="Copy current 360 metadata as JSON">COPY JSON</button>
          <button class="custom360-360__close" type="button" aria-label="Close 360 viewer" title="Close (Esc)">✕</button>
        </div>
      </div>
      <div class="custom360-360__body">
        <div class="custom360-360__status" data-status hidden></div>
        <canvas class="custom360-360__canvas" data-canvas></canvas>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  return overlay;
}

function initPanoramaViewer(overlay) {
  const canvas = overlay.querySelector('[data-canvas]');
  const status = overlay.querySelector('[data-status]');
  const titleEl = overlay.querySelector('[data-title]');
  const closeBtn = overlay.querySelector('.custom360-360__close');
  const copyBtn = overlay.querySelector('.custom360-360__copy');
  const backdrop = overlay.querySelector('[data-backdrop]');

  let gl = null;
  let raf = null;
  let active = false;
  let tex = null;
  let prog = null;
  let mesh = null;
  let bufPos = null;
  let bufUv = null;
  let bufIdx = null;
  let uMvp = null;
  let currentItem = null;

  let yaw = 0;
  let pitch = 0;
  let fovDeg = 70;
  let dragging = false;
  let lastX = 0;
  let lastY = 0;

  const setStatus = (txt) => {
    if (!status) return;
    if (!txt) {
      status.hidden = true;
      status.textContent = '';
      return;
    }
    status.hidden = false;
    status.textContent = txt;
  };

  function resize() {
    if (!canvas || !gl) return;
    const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    const w = Math.max(2, Math.floor((canvas.clientWidth || 0) * dpr));
    const h = Math.max(2, Math.floor((canvas.clientHeight || 0) * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
      gl.viewport(0, 0, w, h);
    }
  }

  function compileShader(type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      const msg = gl.getShaderInfoLog(s) || 'shader compile failed';
      gl.deleteShader(s);
      throw new Error(msg);
    }
    return s;
  }

  function initGl() {
    if (!canvas) return;
    gl = canvas.getContext('webgl', { alpha: false, antialias: true });
    if (!gl) throw new Error('WebGL not available');

    const vs = `
      attribute vec3 aPos;
      attribute vec2 aUv;
      uniform mat4 uMvp;
      varying vec2 vUv;
      void main() {
        vUv = aUv;
        gl_Position = uMvp * vec4(aPos, 1.0);
      }
    `;
    const fs = `
      precision mediump float;
      varying vec2 vUv;
      uniform sampler2D uTex;
      void main() {
        gl_FragColor = texture2D(uTex, vUv);
      }
    `;
    const shV = compileShader(gl.VERTEX_SHADER, vs);
    const shF = compileShader(gl.FRAGMENT_SHADER, fs);
    prog = gl.createProgram();
    gl.attachShader(prog, shV);
    gl.attachShader(prog, shF);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      const msg = gl.getProgramInfoLog(prog) || 'program link failed';
      throw new Error(msg);
    }
    gl.useProgram(prog);

    uMvp = gl.getUniformLocation(prog, 'uMvp');
    const aPos = gl.getAttribLocation(prog, 'aPos');
    const aUv = gl.getAttribLocation(prog, 'aUv');
    gl.uniform1i(gl.getUniformLocation(prog, 'uTex'), 0);

    mesh = buildSphereMesh(48, 96);
    bufPos = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, bufPos);
    gl.bufferData(gl.ARRAY_BUFFER, mesh.positions, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 3, gl.FLOAT, false, 0, 0);

    bufUv = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, bufUv);
    gl.bufferData(gl.ARRAY_BUFFER, mesh.uvs, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(aUv);
    gl.vertexAttribPointer(aUv, 2, gl.FLOAT, false, 0, 0);

    bufIdx = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, bufIdx);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.indices, gl.STATIC_DRAW);

    gl.disable(gl.CULL_FACE);
    gl.enable(gl.DEPTH_TEST);
    gl.clearColor(0, 0, 0, 1);
    resize();
  }

  function draw() {
    if (!active || !gl || !prog || !tex) return;
    resize();
    const w = canvas.width || 2;
    const h = canvas.height || 2;
    const aspect = w / h;
    const proj = mat4Perspective((clamp(fovDeg, 30, 110) * Math.PI) / 180, aspect, 0.1, 10.0);
    const ry = mat4RotateY(yaw);
    const rx = mat4RotateX(pitch);
    const view = mat4Mul(ry, rx);
    const mvp = mat4Mul(proj, view);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.uniformMatrix4fv(uMvp, false, mvp);
    gl.drawElements(gl.TRIANGLES, mesh.indices.length, gl.UNSIGNED_SHORT, 0);
    raf = requestAnimationFrame(draw);
  }

  async function setImage(url) {
    if (!gl) initGl();
    if (!gl) return;
    setStatus('Loading image…');
    const img = new Image();
    img.crossOrigin = 'anonymous';
    const loaded = await new Promise((resolve, reject) => {
      img.onload = () => resolve(true);
      img.onerror = () => reject(new Error('Failed to load panorama image'));
      img.src = url;
    });
    if (!loaded) return;

    if (!tex) tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, 0);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    setStatus('');
  }

  function open(item, url) {
    currentItem = item || null;
    if (titleEl) titleEl.textContent = String(item?.title || '360');
    // Seed yaw (Cesium heading is degrees clockwise from north; our yaw is radians).
    const y = Number(item?.yawDeg || 0);
    yaw = (y * Math.PI) / 180;
    pitch = 0;
    fovDeg = 70;

    overlay.classList.remove('custom360-360--hidden');
    active = true;
    void setImage(url).then(() => {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(draw);
    }).catch((e) => {
      setStatus(e?.message || String(e));
    });
    try { closeBtn?.focus?.(); } catch {}
  }

  function close() {
    overlay.classList.add('custom360-360--hidden');
    active = false;
    if (raf) cancelAnimationFrame(raf);
    raf = null;
    setStatus('');
  }

  function onPointerDown(e) {
    if (!active) return;
    dragging = true;
    lastX = e.clientX;
    lastY = e.clientY;
  }
  function onPointerMove(e) {
    if (!active || !dragging) return;
    const dx = e.clientX - lastX;
    const dy = e.clientY - lastY;
    lastX = e.clientX;
    lastY = e.clientY;
    yaw += dx * 0.004;
    pitch = clamp(pitch + dy * 0.004, -1.35, 1.35);
  }
  function onPointerUp() {
    dragging = false;
  }
  function onWheel(e) {
    if (!active) return;
    e.preventDefault();
    fovDeg = clamp(fovDeg + (e.deltaY > 0 ? 4 : -4), 30, 110);
  }

  copyBtn?.addEventListener('click', async () => {
    if (!currentItem) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(currentItem, null, 2));
      setStatus('Copied JSON to clipboard.');
      setTimeout(() => setStatus(''), 900);
    } catch {
      setStatus('Copy failed (clipboard permission).');
      setTimeout(() => setStatus(''), 1200);
    }
  });

  closeBtn?.addEventListener('click', close);
  backdrop?.addEventListener('click', close);
  window.addEventListener('keydown', (e) => {
    if (!active) return;
    if (e.key === 'Escape') close();
  });

  canvas?.addEventListener('pointerdown', onPointerDown);
  window.addEventListener('pointermove', onPointerMove);
  window.addEventListener('pointerup', onPointerUp);
  canvas?.addEventListener('wheel', onWheel, { passive: false });

  window.addEventListener('resize', () => resize());

  return { open, close, isOpen: () => active };
}

export function initCustom360Library(viewer, { defaultEnabled = false } = {}) {
  if (typeof window === 'undefined' || typeof document === 'undefined') return null;
  if (!viewer?.scene) return null;

  const overlay = createPanoramaCanvasOverlay();
  const pano = initPanoramaViewer(overlay);

  let enabled = Boolean(defaultEnabled);
  let billboards = null;
  let handler = null;
  let items = [];

  async function loadManifest() {
    const res = await fetch('/custom360/manifest.json', { cache: 'no-store' });
    if (!res.ok) throw new Error(`manifest.json HTTP ${res.status}`);
    const js = await res.json();
    const raw = Array.isArray(js.items) ? js.items : [];
    const norm = raw.map(normalizeItem).filter(Boolean);
    items = norm;
    return norm;
  }

  function clearPins() {
    try {
      if (billboards) viewer.scene.primitives.remove(billboards);
    } catch {
      // ignore
    }
    billboards = null;
  }

  function installPins(list) {
    clearPins();
    const accent = Cesium.Color.fromCssColorString(hudAccentCss()).withAlpha(0.95);
    billboards = new Cesium.BillboardCollection({ scene: viewer.scene });
    viewer.scene.primitives.add(billboards);

    const tagImage = makeTextTagBillboardImage('360', {
      fg: hudAccentCss(),
      bg: 'rgba(0,0,0,0.28)',
      font: '700 12px Geist, Inter, -apple-system, BlinkMacSystemFont, sans-serif',
    });

    for (const it of list) {
      const pos = Cesium.Cartesian3.fromDegrees(it.lon, it.lat, it.height || 0);
      const bb = billboards.add({
        image: '/pin.svg',
        color: accent,
        scale: 1.35,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        scaleByDistance: new Cesium.NearFarScalar(60, 1.25, 6000, 0.55),
        position: pos,
      });
      bb.id = { kind: 'custom360', id: it.id };

      if (tagImage) {
        const tag = billboards.add({
          image: tagImage,
          color: Cesium.Color.WHITE.withAlpha(0.92),
          pixelOffset: new Cesium.Cartesian2(0, -44),
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
          position: pos,
        });
        tag.id = { kind: 'custom360', id: it.id };
      }
    }
  }

  function setEnabled(on) {
    enabled = Boolean(on);
    if (!enabled) {
      clearPins();
      try { handler?.destroy?.(); } catch {}
      handler = null;
      return;
    }

    void loadManifest()
      .then((list) => {
        installPins(list);
        if (handler) return;
        handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
        handler.setInputAction((movement) => {
          try {
            const picked = viewer.scene.pick(movement.position);
            const pid = picked?.id || picked?.primitive?.id || null;
            if (!pid || pid.kind !== 'custom360') return;
            const it = items.find((x) => x.id === pid.id);
            if (!it) return;
            pano.open(it, resolvePath(it.path));
          } catch {
            // ignore
          }
        }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
      })
      .catch((e) => {
        console.warn('[Custom360] Failed to load manifest:', e);
      });
  }

  setEnabled(enabled);

  return {
    isEnabled: () => enabled,
    setEnabled,
    reload: async () => {
      if (!enabled) return;
      const list = await loadManifest();
      installPins(list);
    },
  };
}

