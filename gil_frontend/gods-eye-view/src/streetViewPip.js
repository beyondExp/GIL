import * as Cesium from 'cesium';
import { hudAccentCss } from './uiTokens.js';

function clamp(n, lo, hi) {
  if (!Number.isFinite(n)) return lo;
  return Math.max(lo, Math.min(hi, n));
}

function normHeadingDeg(deg) {
  if (!Number.isFinite(deg)) return 0;
  let v = deg % 360;
  if (v < 0) v += 360;
  return v;
}

function approxDistanceM(a, b) {
  if (!a || !b) return Number.POSITIVE_INFINITY;
  const dLat = (b.lat - a.lat) * 111_320;
  const dLon = (b.lon - a.lon) * 111_320 * Math.cos((a.lat * Math.PI) / 180);
  return Math.hypot(dLat, dLon);
}

function centerGroundCartographic(viewer) {
  const scene = viewer.scene;
  const canvas = scene.canvas;
  const center = new Cesium.Cartesian2(
    Math.max(0, (canvas.clientWidth || canvas.width || 0) / 2),
    Math.max(0, (canvas.clientHeight || canvas.height || 0) / 2),
  );

  let p = null;
  if (scene.pickPositionSupported) {
    try { p = scene.pickPosition(center); } catch { p = null; }
  }
  if (!p) {
    try {
      // Works even when globe is hidden: it uses the ellipsoid.
      p = viewer.camera.pickEllipsoid(center, scene.globe?.ellipsoid || Cesium.Ellipsoid.WGS84);
    } catch {
      p = null;
    }
  }
  if (!p) return null;
  try { return Cesium.Cartographic.fromCartesian(p); } catch { return null; }
}

function makeTextTagBillboardImage(text, {
  fg = '#ffffff',
  bg = 'rgba(0,0,0,0.35)',
  font = '600 12px sans-serif',
  paddingX = 6,
  paddingY = 4,
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

export function initStreetViewPip(viewer, {
  defaultEnabled = false,
  // Cesium camera height is in meters above ellipsoid; 220m is *very* close and
  // made Street View feel "broken" unless you zoomed to near-building scale.
  minCameraHeightM = 5000,
  updateMinIntervalMs = 1200,
  minMoveM = 10,
  minYawDeg = 12,
} = {}) {
  if (typeof window === 'undefined' || typeof document === 'undefined') return null;
  if (!viewer?.scene?.canvas) return null;

  const googleMapsKey = (() => {
    try { return String(import.meta.env.GOOGLE_MAPS_API_KEY || '').trim(); } catch { return ''; }
  })();

  const root = document.createElement('section');
  root.className = 'streetview-pip streetview-pip--hidden';
  root.innerHTML = `
    <div class="streetview-pip__header">
      <div class="streetview-pip__title">Street View</div>
      <button class="streetview-pip__fullscreen" type="button" aria-label="Open Street View in fullscreen 360" title="Open fullscreen 360">⛶</button>
      <button class="streetview-pip__close" type="button" aria-label="Hide Street View">✕</button>
    </div>
    <div class="streetview-pip__body">
      <div class="streetview-pip__status" data-status>Zoom in to load street imagery</div>
      <img class="streetview-pip__img" data-img alt="Street View preview" loading="eager" decoding="async" />
    </div>
  `;
  document.body.appendChild(root);

  const img = root.querySelector('[data-img]');
  const status = root.querySelector('[data-status]');
  const close = root.querySelector('.streetview-pip__close');
  const fullscreenBtn = root.querySelector('.streetview-pip__fullscreen');

  // Fullscreen 360 overlay (interactive panorama via Google Maps Embed API).
  const overlay = document.createElement('section');
  overlay.className = 'streetview-360 streetview-360--hidden';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-label', 'Street View 360');
  overlay.innerHTML = `
    <div class="streetview-360__backdrop" data-backdrop></div>
    <div class="streetview-360__panel" role="document">
      <div class="streetview-360__bar">
        <div class="streetview-360__title">Street View · 360</div>
        <div class="streetview-360__bar-actions">
          <button class="streetview-360__close" type="button" aria-label="Close Street View 360" title="Close (Esc)">✕</button>
        </div>
      </div>
      <div class="streetview-360__body">
        <div class="streetview-360__status" data-360-status hidden></div>
        <iframe class="streetview-360__frame" data-360-frame title="Street View panorama" loading="lazy" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  const overlayBackdrop = overlay.querySelector('[data-backdrop]');
  const overlayClose = overlay.querySelector('.streetview-360__close');
  const overlayStatus = overlay.querySelector('[data-360-status]');
  const overlayFrame = overlay.querySelector('[data-360-frame]');

  let enabled = Boolean(defaultEnabled);
  let dismissed = false;
  close?.addEventListener('click', () => {
    dismissed = true;
    root.classList.add('streetview-pip--hidden');
  });

  // Optional: show where the preview is sampled (center-of-screen ground pick).
  let markerError = null;
  const marker = (() => {
    try {
      const scene = viewer.scene;
      const accent = Cesium.Color.fromCssColorString(hudAccentCss()).withAlpha(0.95);

      const billboards = new Cesium.BillboardCollection({ scene });
      scene.primitives.add(billboards);

      const bb = billboards.add({
        image: '/pin.svg',
        color: accent,
        scale: 1.35,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        scaleByDistance: new Cesium.NearFarScalar(60, 1.25, 6000, 0.55),
        show: false,
      });

      const tagImage = makeTextTagBillboardImage('SV', {
        fg: hudAccentCss(),
        bg: 'rgba(0,0,0,0.28)',
        font: '700 12px Geist, Inter, -apple-system, BlinkMacSystemFont, sans-serif',
      });
      const tag = billboards.add({
        image: tagImage,
        color: Cesium.Color.WHITE.withAlpha(0.92),
        pixelOffset: new Cesium.Cartesian2(0, -44),
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        show: false,
      });

      return {
        setPosition(pos) {
          bb.position = pos;
          tag.position = pos;
        },
        setShow(on) {
          bb.show = Boolean(on);
          tag.show = Boolean(on);
        },
        destroy() {
          try { scene.primitives.remove(billboards); } catch {}
        },
      };
    } catch (e) {
      markerError = e?.message || String(e);
      return null;
    }
  })();

  let lastReqAt = 0;
  let lastKey = '';
  let lastPose = null; // {lat,lon,heading}
  let everShown = false;
  let forceVisible = Boolean(defaultEnabled);
  let lastSample = null; // {lat, lon, headingDeg, pitchDeg, fovDeg}

  const setStatus = (text) => {
    if (status) status.textContent = text;
    if (img) img.style.visibility = text ? 'hidden' : 'visible';
  };

  const show = () => root.classList.remove('streetview-pip--hidden');
  const hide = () => root.classList.add('streetview-pip--hidden');

  const open360 = () => {
    if (!overlayFrame || !overlayStatus) return;
    if (!lastSample || !Number.isFinite(lastSample.lat) || !Number.isFinite(lastSample.lon)) {
      overlayStatus.hidden = false;
      overlayStatus.textContent = 'No sampling point yet — move/zoom the camera to pick a ground point.';
      overlayFrame.setAttribute('src', 'about:blank');
      overlay.classList.remove('streetview-360--hidden');
      return;
    }
    if (!googleMapsKey) {
      overlayStatus.hidden = false;
      overlayStatus.textContent = 'GOOGLE_MAPS_API_KEY is not available to the client bundle.';
      overlayFrame.setAttribute('src', 'about:blank');
      overlay.classList.remove('streetview-360--hidden');
      return;
    }
    overlayStatus.hidden = true;
    overlayStatus.textContent = '';

    const u = new URL('https://www.google.com/maps/embed/v1/streetview');
    u.searchParams.set('key', googleMapsKey);
    u.searchParams.set('location', `${lastSample.lat},${lastSample.lon}`);
    u.searchParams.set('heading', String(lastSample.headingDeg));
    u.searchParams.set('pitch', String(clamp(lastSample.pitchDeg, -40, 20)));
    u.searchParams.set('fov', String(clamp(lastSample.fovDeg, 20, 120)));
    overlayFrame.setAttribute('src', u.toString());
    overlay.classList.remove('streetview-360--hidden');
    try { overlayClose?.focus?.(); } catch {}
  };

  const close360 = () => {
    overlay.classList.add('streetview-360--hidden');
    try { overlayFrame?.setAttribute('src', 'about:blank'); } catch {}
  };

  const onKeyDown = (e) => {
    if (e.key === 'Escape' && !overlay.classList.contains('streetview-360--hidden')) {
      e.preventDefault();
      close360();
    }
  };
  window.addEventListener('keydown', onKeyDown);
  overlayBackdrop?.addEventListener('click', close360);
  overlayClose?.addEventListener('click', close360);
  fullscreenBtn?.addEventListener('click', open360);
  img?.addEventListener('click', () => {
    if (enabled) open360();
  });

  const updateMarkerFromCartographic = (c) => {
    if (!marker) return;
    if (!c) {
      marker.setShow(false);
      return;
    }
    try {
      const lat = Cesium.Math.toDegrees(c.latitude);
      const lon = Cesium.Math.toDegrees(c.longitude);
      marker.setPosition(Cesium.Cartesian3.fromDegrees(lon, lat, 0));
      marker.setShow(true);
      viewer.scene?.requestRender?.();
    } catch {
      marker.setShow(false);
    }
  };

  const update = () => {
    if (!enabled) {
      hide();
      if (marker) marker.setShow(false);
      return;
    }
    if (dismissed) return;
    const cam = viewer.camera;
    const camH = cam?.positionCartographic?.height ?? Number.POSITIVE_INFINITY;
    const zoomedOut = camH > minCameraHeightM;
    if (camH < minCameraHeightM) {
      everShown = true;
    }

    // Visible behavior:
    // - by default: stay hidden until the user zooms in once (everShown)
    // - after explicit enable (forceVisible): keep visible even when zoomed out
    if (!forceVisible && !everShown) {
      hide();
      if (marker) marker.setShow(false);
      return;
    }

    show();
    if (zoomedOut) {
      // Still fetch, but at a slower cadence to avoid burning quota when the
      // user is at global/regional zoom.
      setStatus(`Loading Street View… (zoom in for faster/steadier updates; alt ~${Math.round(camH)}m)`);
    }

    const now = Date.now();
    const minInterval = zoomedOut ? Math.max(updateMinIntervalMs, 4000) : updateMinIntervalMs;
    if (now - lastReqAt < minInterval) return;

    const c = centerGroundCartographic(viewer);
    if (!c) {
      show();
      setStatus('No ground pick available');
      if (marker) marker.setShow(false);
      return;
    }

    const lat = Cesium.Math.toDegrees(c.latitude);
    const lon = Cesium.Math.toDegrees(c.longitude);
    const headingDeg = normHeadingDeg(Cesium.Math.toDegrees(cam.heading));
    const pitchDeg = Cesium.Math.toDegrees(cam.pitch);
    const fovDeg = Cesium.Math.toDegrees(cam.frustum?.fov ?? (cam.frustum?.fovy ?? (Math.PI / 3)));
    lastSample = { lat, lon, headingDeg, pitchDeg, fovDeg };

    const pose = { lat, lon, heading: headingDeg };
    if (lastPose) {
      const movedM = approxDistanceM(lastPose, pose);
      const dyaw = Math.abs(((pose.heading - lastPose.heading + 540) % 360) - 180);
      if (movedM < minMoveM && dyaw < minYawDeg) return;
    }

    const w = zoomedOut ? 400 : 480;
    const h = zoomedOut ? 225 : 270;
    const key = `${lat.toFixed(5)},${lon.toFixed(5)},${headingDeg.toFixed(0)},${pitchDeg.toFixed(0)},${fovDeg.toFixed(0)},${w}x${h}`;
    if (key === lastKey) return;
    lastKey = key;
    lastPose = pose;
    lastReqAt = now;

    show();
    setStatus('Loading Street View…');

    updateMarkerFromCartographic(c);

    const url = new URL('/api/google/streetview', window.location.origin);
    url.searchParams.set('lat', String(lat));
    url.searchParams.set('lon', String(lon));
    url.searchParams.set('heading', String(headingDeg));
    url.searchParams.set('pitch', String(clamp(pitchDeg, -40, 20)));
    url.searchParams.set('fov', String(clamp(fovDeg, 20, 120)));
    url.searchParams.set('w', String(w));
    url.searchParams.set('h', String(h));
    url.searchParams.set('t', String(now)); // cache-bust on changes

    if (img) {
      img.onload = () => {
        setStatus('');
        show();
        viewer.scene?.requestRender?.();
      };
      img.onerror = () => {
        show();
        setStatus('Street View unavailable here');
        if (marker) marker.setShow(false);
        viewer.scene?.requestRender?.();
      };
      img.src = url.toString();
    }
  };

  const onMoveEnd = () => update();
  viewer.camera.moveEnd.addEventListener(onMoveEnd);
  window.addEventListener('resize', update);
  // Startup retries: during "restoring shared view" the camera height can be
  // temporarily wrong and no moveEnd fires, so we re-sample a few times.
  for (let i = 0; i < 8; i++) {
    setTimeout(() => {
      try { update(); } catch { /* ignored */ }
    }, 800 + i * 650);
  }

  return {
    update,
    setEnabled(next) {
      enabled = Boolean(next);
      dismissed = false;
      if (!enabled) {
        forceVisible = false;
        hide();
        if (marker) marker.setShow(false);
        if (img) img.removeAttribute('src');
        close360();
      } else {
        forceVisible = true;
        show();
        setStatus('Zoom in to load street imagery');
        setTimeout(update, 0);
      }
    },
    isEnabled() {
      return enabled;
    },
    __debug: {
      get markerOk() { return Boolean(marker); },
      get markerError() { return markerError; },
    },
    destroy() {
      try { viewer.camera.moveEnd.removeEventListener(onMoveEnd); } catch {}
      try { window.removeEventListener('resize', update); } catch {}
      try { window.removeEventListener('keydown', onKeyDown); } catch {}
      try { marker?.destroy?.(); } catch {}
      try { root.remove(); } catch {}
      try { overlay.remove(); } catch {}
    },
  };
}

