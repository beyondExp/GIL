import * as Cesium from 'cesium';

const DEFAULT_WS = 'ws://127.0.0.1:4190';
const GIL_BASE = (() => {
  try {
    const raw = String(import.meta.env.VITE_GIL_CONTROLS_BASE || '').trim();
    if (raw) return raw.replace(/\/+$/, '');
  } catch {
    // ignore
  }
  return 'http://127.0.0.1:6769';
})();
const WS_URL = (() => {
  try {
    const raw = String(import.meta.env.VITE_GEV_MCP_BRIDGE_WS || '').trim();
    if (raw) return raw;
  } catch {
    // ignore
  }
  return DEFAULT_WS;
})();

function safeJsonParse(text) {
  try { return JSON.parse(text); } catch { return null; }
}

function ok(id, result) {
  return JSON.stringify({ id, ok: true, result });
}

function err(id, error) {
  return JSON.stringify({ id, ok: false, error: String(error || 'error') });
}

function toDegrees(rad) {
  return Cesium.Math.toDegrees(Number(rad || 0));
}

function cameraState(viewer) {
  const c = viewer?.camera;
  if (!c) return null;
  const carto = c.positionCartographic;
  return {
    lon: toDegrees(carto.longitude),
    lat: toDegrees(carto.latitude),
    height: Number(carto.height || 0),
    heading: toDegrees(c.heading),
    pitch: toDegrees(c.pitch),
    roll: toDegrees(c.roll),
  };
}

async function setCamera(viewer, params) {
  const lon = Number(params?.lon);
  const lat = Number(params?.lat);
  const height = Number(params?.height ?? 800);
  const heading = Cesium.Math.toRadians(Number(params?.heading ?? 0));
  const pitch = Cesium.Math.toRadians(Number(params?.pitch ?? -35));
  const roll = Cesium.Math.toRadians(Number(params?.roll ?? 0));
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) throw new Error('lon/lat required');
  viewer.camera.setView({
    destination: Cesium.Cartesian3.fromDegrees(lon, lat, height),
    orientation: { heading, pitch, roll },
  });
  return cameraState(viewer);
}

export function initGevMcpBridge({
  viewer,
  styleManager,
  dataManager,
  mapStackController,
  gilRobotInspector,
} = {}) {
  if (!viewer) return null;

  let ws = null;
  let reconnectTimer = null;

  const send = (payload) => {
    try {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(payload);
    } catch {
      // ignore
    }
  };

  const getState = () => {
    const layers = {};
    try {
      for (const [id, entry] of (dataManager?.layers || new Map()).entries()) {
        layers[id] = Boolean(entry?.enabled);
      }
    } catch {
      // ignore
    }
    return {
      bridgeVersion: 'gevMcpBridge@2026-08-31-drive-distance',
      camera: cameraState(viewer),
      mapStack: mapStackController?.getActiveId?.() || null,
      style: styleManager?.activeStyle?.id || styleManager?.activeStyle || null,
      layers,
      gilRobot: {
        enabled: gilRobotInspector?.isEnabled?.() ?? false,
        hasSnapshot: Boolean(gilRobotInspector?.getLastSnapshot?.()),
      },
    };
  };

  const handle = async (msg) => {
    const id = msg?.id ?? null;
    const method = String(msg?.method || '');
    const params = msg?.params || {};
    try {
      if (!id) throw new Error('missing id');
      if (method === 'gev_ping') return send(ok(id, { pong: true, ws: WS_URL }));
      if (method === 'gev_get_state') return send(ok(id, getState()));
      if (method === 'gev_set_camera') return send(ok(id, await setCamera(viewer, params)));
      if (method === 'gev_set_map_stack') {
        const stack = String(params?.stack || '');
        if (!stack) throw new Error('stack required');
        await mapStackController.setStack(stack);
        return send(ok(id, { ok: true, active: mapStackController.getActiveId() }));
      }
      if (method === 'gev_set_layer_enabled') {
        const layerId = String(params?.layerId || '');
        const enabled = Boolean(params?.enabled);
        if (!layerId) throw new Error('layerId required');
        await dataManager.setEnabled(layerId, enabled, { origin: 'tool' });
        return send(ok(id, { ok: true, layerId, enabled }));
      }
      if (method === 'gev_gil_robot_set_enabled') {
        const enabled = Boolean(params?.enabled);
        gilRobotInspector?.setEnabled?.(enabled);
        return send(ok(id, { ok: true, enabled }));
      }
      if (method === 'gev_gil_robot_anchor_to_camera') {
        if (!gilRobotInspector?.anchorToCamera) throw new Error('gilRobotInspector unavailable');
        const anchor = gilRobotInspector.anchorToCamera();
        return send(ok(id, { ok: true, anchor }));
      }
      if (method === 'gev_gil_robot_focus') {
        if (!gilRobotInspector?.focus) throw new Error('gilRobotInspector unavailable');
        const res = await gilRobotInspector.focus();
        return send(ok(id, res));
      }
      if (method === 'gev_gil_robot_dream') {
        if (!gilRobotInspector?.dream) throw new Error('gilRobotInspector unavailable');
        return send(ok(id, await gilRobotInspector.dream()));
      }
      if (method === 'gev_gil_robot_commit') {
        if (!gilRobotInspector?.commit) throw new Error('gilRobotInspector unavailable');
        return send(ok(id, await gilRobotInspector.commit()));
      }
      if (method === 'gev_gil_robot_stop') {
        if (!gilRobotInspector?.stop) throw new Error('gilRobotInspector unavailable');
        return send(ok(id, await gilRobotInspector.stop()));
      }
      if (method === 'gev_gil_robot_reset') {
        if (!gilRobotInspector?.reset) throw new Error('gilRobotInspector unavailable');
        return send(ok(id, await gilRobotInspector.reset()));
      }
      if (method === 'gev_gil_drive_distance') {
        const distance_m = Number(params?.distance_m ?? 30);
        const vx = Number(params?.vx ?? 0.45);
        const res = await fetch(`${GIL_BASE}/api/drive_distance`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ distance_m, vx }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
        return send(ok(id, data));
      }

      return send(err(id, `unknown_method:${method}`));
    } catch (e) {
      return send(err(id, e?.message || String(e)));
    }
  };

  const connect = () => {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    try {
      ws = new WebSocket(WS_URL);
    } catch {
      ws = null;
    }
    if (!ws) return;

    ws.addEventListener('open', () => {
      // Advertise capabilities so the MCP server can prefer the newest UI build.
      send(JSON.stringify({
        type: 'ui_hello',
        product: 'gods-eye-view',
        bridgeVersion: getState().bridgeVersion,
        supports: [
          'gev_set_camera',
          'gev_set_map_stack',
          'gev_set_layer_enabled',
          'gev_gil_robot_set_enabled',
          'gev_gil_robot_anchor_to_camera',
          'gev_gil_robot_focus',
          'gev_gil_robot_dream',
          'gev_gil_robot_commit',
          'gev_gil_robot_stop',
          'gev_gil_robot_reset',
          'gev_gil_drive_distance',
        ],
        atMs: Date.now(),
      }));
    });
    ws.addEventListener('message', (ev) => {
      const msg = typeof ev.data === 'string' ? safeJsonParse(ev.data) : null;
      if (!msg) return;
      void handle(msg);
    });
    ws.addEventListener('close', () => {
      reconnectTimer = setTimeout(connect, 1500);
    });
    ws.addEventListener('error', () => {
      try { ws?.close?.(); } catch {}
    });
  };

  connect();

  // Keep the controller informed of live state.
  const stateTicker = setInterval(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    send(JSON.stringify({ type: 'ui_state', state: getState(), atMs: Date.now() }));
  }, 1000);

  return {
    url: WS_URL,
    close: () => {
      clearInterval(stateTicker);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      try { ws?.close?.(); } catch {}
    },
  };
}

