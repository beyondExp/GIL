import { createServer } from 'node:http';
import process from 'node:process';
import { WebSocketServer } from 'ws';
import { z } from 'zod';

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';

const BRIDGE_PORT = Number(process.env.GEV_MCP_BRIDGE_PORT || '4190');
const BRIDGE_HOST = String(process.env.GEV_MCP_BRIDGE_HOST || '127.0.0.1');
const REQUEST_TIMEOUT_MS = Number(process.env.GEV_MCP_REQUEST_TIMEOUT_MS || '15000');

function nowMs() {
  return Date.now();
}

/** @type {WebSocket|null} */
let activeUi = null;
/** @type {any} */
let lastUiState = null;
/** @type {Map<string, {resolve:Function, reject:Function, deadline:number}>} */
const pending = new Map();
/** @type {WeakMap<any, Set<string>>} */
const uiSupports = new WeakMap();

function makeId() {
  return `${nowMs()}-${Math.random().toString(16).slice(2)}`;
}

function sendUi(msg) {
  if (!activeUi || activeUi.readyState !== 1) return false;
  try {
    activeUi.send(JSON.stringify(msg));
    return true;
  } catch {
    return false;
  }
}

async function callUi(method, params = {}) {
  if (!activeUi || activeUi.readyState !== 1) {
    throw new Error('gods-eye-view UI not connected to bridge websocket');
  }
  const id = makeId();
  const deadline = nowMs() + REQUEST_TIMEOUT_MS;
  const p = new Promise((resolve, reject) => pending.set(id, { resolve, reject, deadline }));
  if (!sendUi({ id, method, params })) {
    pending.delete(id);
    throw new Error('failed to send to UI');
  }
  return await p;
}

function prunePending() {
  const t = nowMs();
  for (const [id, rec] of pending.entries()) {
    if (t > rec.deadline) {
      pending.delete(id);
      try { rec.reject(new Error('UI request timeout')); } catch {}
    }
  }
}

// WebSocket bridge server (UI connects here).
const httpServer = createServer((req, res) => {
  res.statusCode = 200;
  res.setHeader('Content-Type', 'text/plain; charset=utf-8');
  res.end('gods-eye-view MCP bridge\n');
});
const wss = new WebSocketServer({ server: httpServer });
wss.on('connection', (ws) => {
  // Don't immediately select; wait for ui_hello so we can prefer newer builds.
  // eslint-disable-next-line no-console
  console.error('[gev-mcp] UI websocket connected');
  ws.on('message', (data) => {
    const text = typeof data === 'string' ? data : data.toString('utf-8');
    let msg = null;
    try { msg = JSON.parse(text); } catch { msg = null; }
    if (!msg) return;

    // State streaming.
    if (msg.type === 'ui_state') {
      lastUiState = msg.state || null;
      return;
    }
    if (msg.type === 'ui_hello') {
      const supports = new Set(Array.isArray(msg.supports) ? msg.supports.map((s) => String(s)) : []);
      uiSupports.set(ws, supports);
      const ver = String(msg.bridgeVersion || '');
      // Prefer UIs that advertise support for the new drive-distance method.
      const wants = supports.has('gev_gil_drive_distance');
      const activeSupports = activeUi ? (uiSupports.get(activeUi) || new Set()) : new Set();
      const activeWants = activeSupports.has('gev_gil_drive_distance');
      if (!activeUi) {
        activeUi = ws;
      } else if (wants && !activeWants) {
        // Upgrade active UI to one that supports drive-distance.
        activeUi = ws;
      }
      // eslint-disable-next-line no-console
      console.error(`[gev-mcp] UI hello: ${msg.product || 'unknown'} ${ver ? `(${ver})` : ''}`);
      return;
    }

    // Response to a tool call.
    if (msg.id && typeof msg.id === 'string' && 'ok' in msg) {
      const rec = pending.get(msg.id);
      if (!rec) return;
      pending.delete(msg.id);
      if (msg.ok) rec.resolve(msg.result);
      else rec.reject(new Error(String(msg.error || 'UI error')));
    }
  });
  ws.on('close', () => {
    if (activeUi === ws) activeUi = null;
    // eslint-disable-next-line no-console
    console.error('[gev-mcp] UI websocket disconnected');
  });
});

httpServer.listen(BRIDGE_PORT, BRIDGE_HOST, () => {
  // Keep output minimal; MCP client UI will show server name.
  // eslint-disable-next-line no-console
  console.error(`[gev-mcp] bridge ws listening on ws://${BRIDGE_HOST}:${BRIDGE_PORT}`);
});
setInterval(prunePending, 750);

// MCP server (stdio).
const mcp = new McpServer(
  { name: 'gods-eye-view', version: '0.1.0' },
  { capabilities: { tools: {} } },
);

const ToolResponse = (obj) => ({
  content: [{ type: 'text', text: JSON.stringify(obj) }],
});

mcp.registerTool(
  'gev_ping',
  {
    description: 'Ping gods-eye-view bridge and UI.',
    inputSchema: z.object({}),
  },
  async () => {
    const hasUi = Boolean(activeUi && activeUi.readyState === 1);
    return ToolResponse({ ok: true, hasUi, lastUiState: lastUiState || null });
  },
);

mcp.registerTool(
  'gev_get_state',
  {
    description: 'Get current gods-eye-view UI state (camera, map stack, enabled layers).',
    inputSchema: z.object({}),
  },
  async () => {
    const state = await callUi('gev_get_state', {});
    return ToolResponse({ ok: true, state });
  },
);

mcp.registerTool(
  'gev_set_camera',
  {
    description: 'Set Cesium camera view.',
    inputSchema: z.object({
      lon: z.number(),
      lat: z.number(),
      height: z.number().optional(),
      heading: z.number().optional(),
      pitch: z.number().optional(),
      roll: z.number().optional(),
    }),
  },
  async (args) => {
    const result = await callUi('gev_set_camera', args);
    return ToolResponse({ ok: true, result });
  },
);

mcp.registerTool(
  'gev_set_map_stack',
  {
    description: 'Switch the active map stack (photoreal|osm|...).',
    inputSchema: z.object({ stack: z.string().min(1) }),
  },
  async (args) => {
    const result = await callUi('gev_set_map_stack', args);
    return ToolResponse({ ok: true, result });
  },
);

mcp.registerTool(
  'gev_set_layer_enabled',
  {
    description: 'Enable or disable a data layer by id.',
    inputSchema: z.object({ layerId: z.string().min(1), enabled: z.boolean() }),
  },
  async (args) => {
    const result = await callUi('gev_set_layer_enabled', args);
    return ToolResponse({ ok: true, result });
  },
);

mcp.registerTool(
  'gev_gil_robot_set_enabled',
  {
    description: 'Show/hide the GIL Robot inspector overlay.',
    inputSchema: z.object({ enabled: z.boolean() }),
  },
  async (args) => {
    const result = await callUi('gev_gil_robot_set_enabled', args);
    return ToolResponse({ ok: true, result });
  },
);

mcp.registerTool(
  'gev_gil_robot_anchor_to_camera',
  {
    description: 'Anchor the local robot frame to the current globe camera location.',
    inputSchema: z.object({}),
  },
  async () => {
    const result = await callUi('gev_gil_robot_anchor_to_camera', {});
    return ToolResponse({ ok: true, result });
  },
);

mcp.registerTool(
  'gev_gil_robot_focus',
  {
    description: 'Fly the camera to the ghost robot marker.',
    inputSchema: z.object({}),
  },
  async () => {
    const result = await callUi('gev_gil_robot_focus', {});
    return ToolResponse({ ok: true, result });
  },
);

mcp.registerTool(
  'gev_gil_robot_dream',
  {
    description: 'Run GIL robot DREAM (steer commit=false) via the UI bridge.',
    inputSchema: z.object({}),
  },
  async () => {
    const result = await callUi('gev_gil_robot_dream', {});
    return ToolResponse({ ok: true, result });
  },
);

mcp.registerTool(
  'gev_gil_robot_commit',
  {
    description: 'Run GIL robot COMMIT (steer commit=true) via the UI bridge.',
    inputSchema: z.object({}),
  },
  async () => {
    const result = await callUi('gev_gil_robot_commit', {});
    return ToolResponse({ ok: true, result });
  },
);

mcp.registerTool(
  'gev_gil_robot_stop',
  {
    description: 'Stop the robot immediately via the UI bridge.',
    inputSchema: z.object({}),
  },
  async () => {
    const result = await callUi('gev_gil_robot_stop', {});
    return ToolResponse({ ok: true, result });
  },
);

mcp.registerTool(
  'gev_gil_robot_reset',
  {
    description: 'Reset the robot episode via the UI bridge.',
    inputSchema: z.object({}),
  },
  async () => {
    const result = await callUi('gev_gil_robot_reset', {});
    return ToolResponse({ ok: true, result });
  },
);

mcp.registerTool(
  'gev_gil_drive_distance',
  {
    description: 'Drive the robot forward for a target distance in meters (demo helper).',
    inputSchema: z.object({
      distance_m: z.number().optional(),
      vx: z.number().optional(),
    }),
  },
  async (args) => {
    const result = await callUi('gev_gil_drive_distance', args || {});
    return ToolResponse({ ok: true, result });
  },
);

// Start stdio transport.
await mcp.connect(new StdioServerTransport());

