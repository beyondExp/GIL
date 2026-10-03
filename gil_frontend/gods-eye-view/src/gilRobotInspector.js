import * as Cesium from 'cesium';
import { hudAccentCss } from './uiTokens.js';

const LS_ENABLED = 'gevGilRobotEnabled';
const LS_ANCHOR = 'gevGilRobotAnchorLLA';
const GIL_BASE = (() => {
  try {
    const raw = String(import.meta.env.VITE_GIL_CONTROLS_BASE || '').trim();
    if (raw) return raw.replace(/\/+$/, '');
  } catch {
    // ignore
  }
  return 'http://127.0.0.1:6769';
})();

function readJson(key, fallback) {
  try {
    const raw = window.localStorage?.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function writeJson(key, value) {
  try {
    window.localStorage?.setItem(key, JSON.stringify(value));
  } catch {
    // ignore
  }
}

function getEnabledDefault() {
  try {
    const raw = window.localStorage?.getItem(LS_ENABLED);
    if (raw === null) return false;
    return raw === '1' || raw === 'true';
  } catch {
    return false;
  }
}

function snapshotAnchorFromCamera(viewer) {
  const carto = viewer.camera.positionCartographic;
  return {
    lon: Cesium.Math.toDegrees(carto.longitude),
    lat: Cesium.Math.toDegrees(carto.latitude),
    // IMPORTANT: anchor should be a *world* reference point, not the camera altitude.
    // Using camera height can place the robot marker hundreds/thousands of meters above ground.
    height: 0,
    source: 'camera',
    atMs: Date.now(),
  };
}

function localToWorldCartesian(anchor, local) {
  if (!anchor || typeof anchor.lon !== 'number' || typeof anchor.lat !== 'number') return null;
  const origin = Cesium.Cartesian3.fromDegrees(anchor.lon, anchor.lat, Number(anchor.height || 0));
  const enu = Cesium.Transforms.eastNorthUpToFixedFrame(origin);
  const offset = new Cesium.Cartesian3(Number(local.x || 0), Number(local.y || 0), Number(local.z || 0));
  return Cesium.Matrix4.multiplyByPoint(enu, offset, new Cesium.Cartesian3());
}

function safeText(value, fallback = '—') {
  const s = String(value ?? '').trim();
  return s ? s : fallback;
}

function _round5(x) {
  return Math.round(Number(x || 0) * 1e5) / 1e5;
}

async function fetchInspectorSnapshot(signal) {
  const res = await fetch(`${GIL_BASE}/api/inspector`, { signal });
  if (!res.ok) throw new Error(`inspector HTTP ${res.status}`);
  return await res.json();
}

export function initGilRobotInspector(viewer, { defaultEnabled } = {}) {
  if (!viewer) return null;

  let enabled = typeof defaultEnabled === 'boolean' ? defaultEnabled : getEnabledDefault();
  let running = false;
  let last = null;
  let pollAbort = null;

  const root = document.createElement('section');
  root.className = 'gil-robot-pip gil-robot-pip--hidden';
  root.innerHTML = `
    <div class="gil-robot-pip__header">
      <div class="gil-robot-pip__title">GIL Robot</div>
      <div class="gil-robot-pip__actions">
        <button class="gil-robot-pip__spawn" type="button" title="Click the globe to spawn robot here">SPAWN</button>
        <button class="gil-robot-pip__pov" type="button" title="Follow robot POV in main view">POV</button>
        <button class="gil-robot-pip__focus" type="button" title="Fly camera to robot">FOCUS</button>
        <button class="gil-robot-pip__anchor" type="button" title="Anchor local robot frame to current camera location">ANCHOR</button>
        <button class="gil-robot-pip__close" type="button" aria-label="Hide GIL robot inspector" title="Hide">✕</button>
      </div>
    </div>
    <div class="gil-robot-pip__body">
      <div class="gil-robot-pip__status" data-status>Waiting for gil_controls...</div>
      <img class="gil-robot-pip__img" data-img alt="Robot FPV" loading="eager" decoding="async" />
      <div class="gil-robot-pip__meta" data-meta></div>
      <div class="gil-robot-pip__cmdrow">
        <button class="gil-robot-pip__btn" data-action="dream" type="button" title="Run steer (dream-only)">DREAM</button>
        <button class="gil-robot-pip__btn gil-robot-pip__btn--primary" data-action="commit" type="button" title="Run steer(commit=true)">COMMIT</button>
        <button class="gil-robot-pip__btn" data-action="stop" type="button" title="Immediate stop">STOP</button>
        <button class="gil-robot-pip__btn" data-action="reset" type="button" title="Reset episode">RESET</button>
        <button class="gil-robot-pip__btn" data-action="record" type="button" title="Toggle episode recording (frames+pose)">REC</button>
      </div>
    </div>
  `;
  document.body.appendChild(root);

  const statusEl = root.querySelector('[data-status]');
  const metaEl = root.querySelector('[data-meta]');
  const imgEl = root.querySelector('[data-img]');
  const closeBtn = root.querySelector('.gil-robot-pip__close');
  const spawnBtn = root.querySelector('.gil-robot-pip__spawn');
  const povBtn = root.querySelector('.gil-robot-pip__pov');
  const focusBtn = root.querySelector('.gil-robot-pip__focus');
  const anchorBtn = root.querySelector('.gil-robot-pip__anchor');
  const cmdRow = root.querySelector('.gil-robot-pip__cmdrow');

  const entities = viewer.entities;
  const accent = hudAccentCss();
  let robotEntity = null;
  let pathEntity = null;
  let headingEntity = null;
  let povEnabled = false;
  let spawnArmed = false;
  let clampPending = false;
  let lastClampKey = '';
  let lastClampAtMs = 0;
  let lastClampHeightM = null; // cached surface height (meters)
  let lastRobotKey = '';
  let lastRobotClampedPos = null;
  let pathClampPending = false;
  let lastPathKey = '';
  let lastPathPositions = [];
  const screenHandler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);

  const ensureEntities = () => {
    if (!robotEntity) {
      robotEntity = entities.add({
        id: 'gev-gil-robot',
        position: Cesium.Cartesian3.ZERO,
        point: {
          pixelSize: 14,
          color: Cesium.Color.fromCssColorString(accent),
          outlineColor: Cesium.Color.BLACK.withAlpha(0.6),
          outlineWidth: 2,
          heightReference: Cesium.HeightReference.NONE,
          // Always visible even when inside photoreal tiles / buildings.
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
        cylinder: {
          length: 1.65,
          topRadius: 0.22,
          bottomRadius: 0.22,
          material: Cesium.Color.fromCssColorString(accent).withAlpha(0.20),
          // Keep it visible even when intersecting meshes.
          heightReference: Cesium.HeightReference.NONE,
        },
        label: {
          text: 'ROBOT',
          font: '12px Geist, Inter, system-ui, sans-serif',
          fillColor: Cesium.Color.WHITE.withAlpha(0.92),
          outlineColor: Cesium.Color.BLACK.withAlpha(0.55),
          outlineWidth: 3,
          pixelOffset: new Cesium.Cartesian2(0, -18),
          showBackground: true,
          backgroundColor: Cesium.Color.BLACK.withAlpha(0.35),
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
      });
    }
    if (!pathEntity) {
      pathEntity = entities.add({
        id: 'gev-gil-robot-path',
        polyline: {
          positions: [],
          width: 3,
          material: Cesium.Color.fromCssColorString(accent).withAlpha(0.8),
          clampToGround: false,
        },
      });
    }
    if (!headingEntity) {
      headingEntity = entities.add({
        id: 'gev-gil-robot-heading',
        polyline: {
          positions: [],
          width: 2,
          material: Cesium.Color.fromCssColorString(accent).withAlpha(0.95),
          clampToGround: false,
        },
      });
    }
  };

  const clearEntities = () => {
    if (robotEntity) entities.remove(robotEntity);
    if (pathEntity) entities.remove(pathEntity);
    if (headingEntity) entities.remove(headingEntity);
    robotEntity = null;
    pathEntity = null;
    headingEntity = null;
  };

  const setVisible = (on) => {
    root.classList.toggle('gil-robot-pip--hidden', !on);
  };

  const setStatus = (text, { dim = false } = {}) => {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.classList.toggle('gil-robot-pip__status--dim', Boolean(dim));
  };

  const setMeta = (html) => {
    if (!metaEl) return;
    metaEl.innerHTML = html || '';
  };

  const setImage = (dataUrl) => {
    if (!imgEl) return;
    if (dataUrl && typeof dataUrl === 'string' && dataUrl.startsWith('data:image')) {
      imgEl.style.visibility = 'visible';
      imgEl.src = dataUrl;
      return;
    }
    imgEl.style.visibility = 'hidden';
    imgEl.removeAttribute('src');
  };

  const readAnchor = () => readJson(LS_ANCHOR, null);
  const writeAnchor = (a) => writeJson(LS_ANCHOR, a);

  const readAnchorSafe = () => {
    const a = readAnchor();
    if (!a || typeof a !== 'object') return null;
    const h = Number(a.height || 0);
    // If a previous session anchored at camera altitude, force to ground.
    if (Number.isFinite(h) && h > 20) {
      const fixed = { ...a, height: 0, source: String(a.source || 'camera') || 'camera', atMs: Date.now() };
      writeAnchor(fixed);
      return fixed;
    }
    return a;
  };

  const anchorFromSnapshot = (snap) => {
    const a = snap?.observation?.anchor;
    if (a && typeof a === 'object' && Number.isFinite(Number(a.lon)) && Number.isFinite(Number(a.lat))) {
      return {
        lon: Number(a.lon),
        lat: Number(a.lat),
        height: Number(a.height || 0),
        source: String(a.source || 'backend'),
        atMs: Number(a.at_ms || a.atMs || Date.now()),
      };
    }
    const wa = snap?.observation?.state?.world_anchor;
    if (wa && typeof wa === 'object' && Number.isFinite(Number(wa.lon)) && Number.isFinite(Number(wa.lat))) {
      return {
        lon: Number(wa.lon),
        lat: Number(wa.lat),
        height: Number(wa.height || 0),
        source: String(wa.source || 'backend'),
        atMs: Number(wa.at_ms || wa.atMs || Date.now()),
      };
    }
    return readAnchorSafe();
  };

  const focusRobot = async () => {
    try {
      if (!robotEntity) ensureEntities();
      if (!robotEntity) return { ok: false, error: 'no_robot_entity' };
      // Put the camera at a deterministic offset above the robot (much easier to "see" than flyTo()).
      const pos = robotEntity.position;
      if (!pos) {
        await viewer.flyTo(robotEntity, { duration: 0.9 });
        return { ok: true };
      }
      const p = typeof pos.getValue === 'function' ? pos.getValue(viewer.clock.currentTime) : pos;
      const carto = Cesium.Cartographic.fromCartesian(p);
      const lon = Cesium.Math.toDegrees(carto.longitude);
      const lat = Cesium.Math.toDegrees(carto.latitude);
      const height = Math.max(60, Number(carto.height || 0) + 80);
      await viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(lon, lat, height),
        orientation: {
          heading: viewer.camera.heading,
          pitch: Cesium.Math.toRadians(-45),
          roll: 0,
        },
        duration: 0.9,
      });
      return { ok: true };
    } catch (e) {
      return { ok: false, error: safeText(e?.message, 'focus_error') };
    }
  };

  const setPovEnabled = (on) => {
    povEnabled = Boolean(on);
    try { povBtn?.classList?.toggle?.('gil-robot-pip__pov--on', povEnabled); } catch {}
    try { povBtn?.setAttribute?.('aria-pressed', povEnabled ? 'true' : 'false'); } catch {}
    try { povBtn && (povBtn.textContent = povEnabled ? 'POV*' : 'POV'); } catch {}
    if (!povEnabled) {
      try {
        // Release lookAt transform back to normal camera controls.
        viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
      } catch {
        // ignore
      }
    }
  };

  const setSpawnArmed = (on) => {
    spawnArmed = Boolean(on);
    try { spawnBtn?.classList?.toggle?.('gil-robot-pip__spawn--on', spawnArmed); } catch {}
    try { spawnBtn?.setAttribute?.('aria-pressed', spawnArmed ? 'true' : 'false'); } catch {}
    try { spawnBtn && (spawnBtn.textContent = spawnArmed ? 'SPAWN*' : 'SPAWN'); } catch {}

    if (!spawnArmed) {
      try { screenHandler.removeInputAction(Cesium.ScreenSpaceEventType.LEFT_CLICK); } catch {}
      return;
    }

    setStatus('spawn mode: click on the globe to place robot', { dim: true });
    screenHandler.setInputAction((movement) => {
      void (async () => {
        try {
          const scene = viewer.scene;
          const winPos = movement?.position;
          if (!winPos) return;

          let cart = null;
          if (scene.pickPositionSupported) {
            try { cart = scene.pickPosition(winPos); } catch { cart = null; }
          }
          if (!cart) {
            try { cart = viewer.camera.pickEllipsoid(winPos, scene.globe.ellipsoid); } catch { cart = null; }
          }
          if (!cart) throw new Error('could not pick globe position');

          const carto = Cesium.Cartographic.fromCartesian(cart);
          // Ask backend to own the anchor + reset local pose.
          await fetch(`${GIL_BASE}/api/spawn_world`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              robot_kind: 'humanoid',
              lon: Cesium.Math.toDegrees(carto.longitude),
              lat: Cesium.Math.toDegrees(carto.latitude),
              height: 0,
              yaw: 0,
              z: 1.05,
            }),
          }).catch(() => {});

          setSpawnArmed(false);
          void focusRobot();
        } catch (e) {
          setStatus(`spawn failed (${safeText(e?.message, 'error')})`, { dim: true });
          setSpawnArmed(false);
        }
      })();
    }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
  };

  const clampToSurface = async (cartesian, zOffset = 1.0) => {
    try {
      const scene = viewer?.scene;
      if (!scene) return cartesian;
      const carto0 = Cesium.Cartographic.fromCartesian(cartesian);
      const lon = carto0.longitude;
      const lat = carto0.latitude;
      const baseZ = Math.max(0.0, Number(zOffset || 0));

      // Cache key: small movement/heading changes won't resample.
      const key = `${_round5(Cesium.Math.toDegrees(lon))}:${_round5(Cesium.Math.toDegrees(lat))}`;
      const now = Date.now();
      if (key === lastClampKey && (now - lastClampAtMs) < 1200 && Number.isFinite(Number(lastClampHeightM))) {
        return Cesium.Cartesian3.fromRadians(lon, lat, Math.max(0, Number(lastClampHeightM)) + baseZ);
      }

      // Prefer 3D tiles clamping (photoreal) when available.
      if (typeof scene.clampToHeightMostDetailed === 'function') {
        const base = Cesium.Cartesian3.fromRadians(lon, lat, 0);
        const res = await scene.clampToHeightMostDetailed([base]);
        const p = Array.isArray(res) ? res[0] : null;
        if (p) {
          const cc = Cesium.Cartographic.fromCartesian(p);
          const h = Number(cc?.height);
          if (Number.isFinite(h)) {
            lastClampKey = key;
            lastClampAtMs = now;
            lastClampHeightM = h;
            return Cesium.Cartesian3.fromRadians(lon, lat, Math.max(0, h) + baseZ);
          }
        }
      }

      // Fallback: sample height from terrain and some tiles (best-effort).
      if (typeof scene.sampleHeightMostDetailed === 'function') {
        const res = await scene.sampleHeightMostDetailed([new Cesium.Cartographic(lon, lat, 0)]);
        const h = Number(res?.[0]?.height);
        if (Number.isFinite(h)) {
          lastClampKey = key;
          lastClampAtMs = now;
          lastClampHeightM = h;
          return Cesium.Cartesian3.fromRadians(lon, lat, Math.max(0, h) + baseZ);
        }
      }
      // Fallback: if we have any cached height, reuse it to avoid popping.
      if (Number.isFinite(Number(lastClampHeightM))) {
        return Cesium.Cartesian3.fromRadians(lon, lat, Math.max(0, Number(lastClampHeightM)) + baseZ);
      }
      return Cesium.Cartesian3.fromRadians(lon, lat, baseZ);
    } catch {
      return cartesian;
    }
  };

  const clampManyToSurface = async (cartesians, zOffset = 0.15) => {
    try {
      const scene = viewer?.scene;
      if (!scene || !Array.isArray(cartesians) || cartesians.length === 0) return cartesians || [];

      const cartos = cartesians.map((c) => Cesium.Cartographic.fromCartesian(c));
      const baseZ = Math.max(0.0, Number(zOffset || 0));

      // Try photoreal 3D tiles first (best for Google Photorealistic).
      const bases = cartos.map((c) => Cesium.Cartesian3.fromRadians(c.longitude, c.latitude, 0));
      let tileHeights = null;
      if (typeof scene.clampToHeightMostDetailed === 'function') {
        const res = await scene.clampToHeightMostDetailed(bases);
        if (Array.isArray(res) && res.length === bases.length) {
          tileHeights = res.map((p) => {
            try {
              if (!p) return null;
              const cc = Cesium.Cartographic.fromCartesian(p);
              const h = Number(cc?.height);
              return Number.isFinite(h) ? h : null;
            } catch {
              return null;
            }
          });
        }
      }

      // Fallback heights (terrain / whatever is available).
      let sampled = null;
      if (typeof scene.sampleHeightMostDetailed === 'function') {
        const res = await scene.sampleHeightMostDetailed(cartos.map((c) => new Cesium.Cartographic(c.longitude, c.latitude, 0)));
        if (Array.isArray(res) && res.length === cartos.length) {
          sampled = res.map((c) => {
            const h = Number(c?.height);
            return Number.isFinite(h) ? h : 0;
          });
        }
      }

      const out = [];
      for (let i = 0; i < cartos.length; i += 1) {
        const c = cartos[i];
        const hTile = tileHeights ? tileHeights[i] : null;
        const h = Number.isFinite(hTile) ? hTile : (sampled ? sampled[i] : Number(c.height || 0));
        out.push(Cesium.Cartesian3.fromRadians(c.longitude, c.latitude, Math.max(0, h) + baseZ));
      }
      return out;
    } catch {
      return Array.isArray(cartesians) ? cartesians : [];
    }
  };

  const applySnapshot = (snap) => {
    last = snap;
    const obs = snap?.observation || {};
    const state = obs?.state || {};
    const base = state?.base || {};
    const gate = snap?.gate || {};
    const director = snap?.director || {};
    const dream = snap?.dream || {};
    const controls = snap?.controls || {};
    const health = controls?.health || {};
    const preflight = controls?.preflight || health?.preflight || {};
    const cam = controls?.camera || {};

    const x = Number(base?.x || 0);
    const y = Number(base?.y || 0);
    const z = Number(base?.z || 0);
    const yaw = Number(base?.yaw || 0);

    setImage(obs?.image || null);
    const decision = safeText(director?.decision, '—');
    const reason = safeText(director?.reason, '');
    const gateHasInfo = Boolean(gate?.gate_id) || Boolean((gate?.reason || '').trim());
    const preOk = Boolean(preflight?.ok);
    const backend = safeText(health?.backend, '');

    let status = '';
    if (!gateHasInfo && (decision === '—' || decision === '')) {
      status = `ready=${preOk ? 'yes' : 'no'}${backend ? ` · backend=${backend}` : ''} · no dream yet (click DREAM)`;
    } else if (!gateHasInfo && decision !== '—') {
      status = `decision=${decision}${reason ? ` · ${reason}` : ''} · ready=${preOk ? 'yes' : 'no'}`;
    } else {
      status = `gate=${gate?.ok ? 'ok' : 'blocked'} · ${safeText(gate?.reason, '—')} · decision=${decision}`;
    }
    setStatus(status, { dim: !preOk });
    const camOk = Boolean(cam?.ok);
    const camPicked = safeText(cam?.picked, '');
    const camReason = safeText(cam?.reason, '');
    setMeta(
      `<div><b>pose</b> x=${x.toFixed(2)} y=${y.toFixed(2)} z=${z.toFixed(2)} yaw=${yaw.toFixed(2)}</div>` +
      `<div><b>controls</b> ready=${preOk ? 'yes' : 'no'} backend=${safeText(health?.backend, '--')} mode=${safeText(health?.mode, '--')}</div>` +
      `<div><b>camera</b> ok=${camOk ? 'yes' : 'no'}${camPicked ? ` picked=${camPicked}` : ''}${camReason ? ` reason=${camReason}` : ''}</div>` +
      `<div><b>belief</b> calibrated=${Boolean(snap?.belief?.calibrated)} coverage=${Number(snap?.belief?.coverage || 0).toFixed(2)} occ=${Number(snap?.belief?.occupied_cells || 0)}</div>` +
      `<div><b>dream</b> kept=${Number(dream?.kept || 0)} gate_id=${safeText(gate?.gate_id, '')}</div>`
    );

    ensureEntities();
    const anchor = anchorFromSnapshot(snap);
    const visZ = Math.max(1.0, Number.isFinite(z) ? z : 1.0);
    const rough = localToWorldCartesian(anchor, { x, y, z: 0 });
    const pos = rough ? Cesium.Cartesian3.clone(rough) : null;
    if (pos && robotEntity) {
      // Avoid flicker: prefer last clamped position while new clamp resolves.
      const carto = Cesium.Cartographic.fromCartesian(pos);
      const key = `${_round5(Cesium.Math.toDegrees(carto.longitude))}:${_round5(Cesium.Math.toDegrees(carto.latitude))}`;

      let place = null;
      if (lastRobotClampedPos && key === lastRobotKey) place = lastRobotClampedPos;
      else if (clampPending && lastRobotClampedPos) place = lastRobotClampedPos;
      else if (Number.isFinite(Number(lastClampHeightM))) {
        place = Cesium.Cartesian3.fromRadians(carto.longitude, carto.latitude, Math.max(0, Number(lastClampHeightM)) + visZ);
      } else {
        place = pos;
      }

      robotEntity.position = place;
      const hpr = new Cesium.HeadingPitchRoll(Number(yaw || 0), 0, 0);
      robotEntity.orientation = Cesium.Transforms.headingPitchRollQuaternion(place, hpr);
      robotEntity.label.text = `ROBOT · ${safeText(director?.embodiment, 'humanoid')}`;

      // Heading indicator (3m).
      if (headingEntity?.polyline) {
        const ahead = localToWorldCartesian(anchor, { x: x + 3 * Math.cos(yaw), y: y + 3 * Math.sin(yaw), z: 0 });
        headingEntity.polyline.positions = ahead ? [place, ahead] : [];
      }

      if (!clampPending) {
        clampPending = true;
        void clampToSurface(pos, visZ).then((clamped) => {
          try {
            if (!robotEntity) return;
            lastRobotClampedPos = clamped;
            lastRobotKey = key;
            robotEntity.position = clamped;
            if (povEnabled) {
              const heading = Number(yaw || 0);
              const pitch = Cesium.Math.toRadians(-18);
              const range = 35;
              viewer.camera.lookAt(clamped, new Cesium.HeadingPitchRange(heading, pitch, range));
            }
          } finally {
            clampPending = false;
          }
        });
      }

      if (povEnabled) {
        try {
          // Chase-style view relative to the robot in the main Cesium viewport.
          const heading = Number(yaw || 0);
          const pitch = Cesium.Math.toRadians(-18);
          const range = 35; // meters behind/above
          viewer.camera.lookAt(place, new Cesium.HeadingPitchRange(heading, pitch, range));
        } catch {
          // ignore
        }
      }
    }

    const path = Array.isArray(dream?.predicted_path_local) ? dream.predicted_path_local : [];
    if (anchor && pathEntity && pathEntity.polyline) {
      // Downsample to keep clamping fast.
      const maxPts = 140;
      const stride = Math.max(1, Math.ceil((path.length || 1) / maxPts));
      const roughPositions = [];
      let idx = 0;
      for (const p of path) {
        if (idx % stride === 0) {
          const w = localToWorldCartesian(anchor, { x: p?.x, y: p?.y, z: 0 });
          if (w) roughPositions.push(w);
        }
        idx += 1;
        if (roughPositions.length >= maxPts) break;
      }

      // Show rough immediately, then clamp asynchronously to the visible surface.
      pathEntity.polyline.positions = roughPositions;
      const tail = roughPositions.length ? Cesium.Cartographic.fromCartesian(roughPositions[roughPositions.length - 1]) : null;
      const key = `${_round5(anchor.lon)}:${_round5(anchor.lat)}:${roughPositions.length}:${tail ? _round5(Cesium.Math.toDegrees(tail.longitude)) : 0}:${tail ? _round5(Cesium.Math.toDegrees(tail.latitude)) : 0}`;
      if (!pathClampPending && key !== lastPathKey && roughPositions.length >= 2) {
        pathClampPending = true;
        lastPathKey = key;
        void clampManyToSurface(roughPositions, 0.12).then((clamped) => {
          try {
            if (!pathEntity?.polyline) return;
            lastPathPositions = clamped;
            pathEntity.polyline.positions = clamped;
          } finally {
            pathClampPending = false;
          }
        });
      } else if (!pathClampPending && lastPathPositions?.length) {
        // Keep using last clamped path while waiting for next update.
        pathEntity.polyline.positions = lastPathPositions;
      }
    }
  };

  const pollLoop = async () => {
    if (running) return;
    running = true;
    while (enabled) {
      pollAbort?.abort?.();
      pollAbort = new AbortController();
      try {
        const snap = await fetchInspectorSnapshot(pollAbort.signal);
        applySnapshot(snap);
      } catch (e) {
        setStatus(`gil_controls unavailable (${safeText(e?.message, 'error')})`, { dim: true });
      }
      await new Promise((r) => setTimeout(r, 250));
    }
    running = false;
  };

  const anchorToCamera = () => {
    const a = snapshotAnchorFromCamera(viewer);
    writeAnchor(a);
    setStatus('anchored local robot frame to camera', { dim: true });
    // Make it obvious something happened.
    void focusRobot();
    return a;
  };

  const runAction = async (action) => {
    const url = (() => {
      if (action === 'dream' || action === 'commit') return `${GIL_BASE}/api/steer`;
      if (action === 'stop') return `${GIL_BASE}/api/stop`;
      // Scenario reset: re-apply the current world anchor + reset local pose at the anchor origin.
      // This is more reliable/visible than a pure local reset across different sim environments.
      if (action === 'reset') return `${GIL_BASE}/api/spawn_world`;
      if (action === 'record_status') return `${GIL_BASE}/api/record/status`;
      if (action === 'record_start') return `${GIL_BASE}/api/record/start`;
      if (action === 'record_stop') return `${GIL_BASE}/api/record/stop`;
      return '';
    })();
    if (!url) return { ok: false, error: 'unknown_action' };

    const payload = (() => {
      if (action === 'dream') return { instruction: 'escape the maze', commit: false };
      if (action === 'commit') return { instruction: 'escape the maze', commit: true };
      if (action === 'reset') {
        const a0 = anchorFromSnapshot(last) || readAnchorSafe() || snapshotAnchorFromCamera(viewer);
        const a = a0 && typeof a0 === 'object' ? a0 : snapshotAnchorFromCamera(viewer);
        return {
          robot_kind: 'humanoid',
          lon: Number(a.lon),
          lat: Number(a.lat),
          height: Number(a.height || 0),
          yaw: 0,
          z: 1.05,
        };
      }
      if (action === 'record_start') return { robot_kind: 'humanoid' };
      return {};
    })();

    try {
      setStatus(`sending ${action}...`, { dim: true });
      const isGet = action === 'record_status';
      const res = await fetch(url, isGet ? { method: 'GET' } : {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
      // Immediately refresh after any action.
      setStatus(`${action} ok`, { dim: true });
      return { ok: true, result: data };
    } catch (e) {
      setStatus(`${action} failed (${safeText(e?.message, 'error')})`, { dim: true });
      return { ok: false, error: safeText(e?.message, 'error') };
    }
  };

  closeBtn?.addEventListener('click', () => setEnabled(false));
  spawnBtn?.addEventListener('click', () => {
    setSpawnArmed(!spawnArmed);
  });
  povBtn?.addEventListener('click', () => {
    setPovEnabled(!povEnabled);
    if (povEnabled) {
      // Immediately apply POV if we already have a robot pose.
      try {
        const snap = last;
        if (snap) applySnapshot(snap);
      } catch {
        // ignore
      }
    }
  });
  focusBtn?.addEventListener('click', () => {
    void focusRobot();
  });
  anchorBtn?.addEventListener('click', () => {
    anchorToCamera();
  });
  cmdRow?.addEventListener('click', (ev) => {
    const btn = ev.target?.closest?.('[data-action]');
    const action = btn?.getAttribute?.('data-action');
    if (!action) return;
    if (action === 'record') {
      void (async () => {
        const st = await runAction('record_status');
        const active = Boolean(st?.result?.active);
        const next = active ? 'record_stop' : 'record_start';
        const res = await runAction(next);
        const nowActive = Boolean(res?.result?.active);
        try { btn.textContent = nowActive ? 'REC*' : 'REC'; } catch {}
      })();
      return;
    }
    void runAction(action);
  });

  const setEnabled = (on) => {
    enabled = Boolean(on);
    try { window.localStorage?.setItem(LS_ENABLED, enabled ? '1' : '0'); } catch {}
    if (enabled) {
      setVisible(true);
      ensureEntities();
      // Backwards-compat fallback: if backend doesn't provide an anchor yet.
      if (!readAnchorSafe()) writeAnchor(snapshotAnchorFromCamera(viewer));
      // If the robot is already placed, jump to it once so operators don't hunt.
      setTimeout(() => { void focusRobot(); }, 350);
      void pollLoop();
    } else {
      setVisible(false);
      setSpawnArmed(false);
      setPovEnabled(false);
      pollAbort?.abort?.();
      clearEntities();
    }
  };

  // Initial apply.
  setEnabled(enabled);

  return {
    isEnabled: () => enabled,
    setEnabled,
    getLastSnapshot: () => last,
    anchorToCamera,
    focus: () => focusRobot(),
    dream: () => runAction('dream'),
    commit: () => runAction('commit'),
    stop: () => runAction('stop'),
    reset: () => runAction('reset'),
  };
}

