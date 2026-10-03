import process from 'node:process';

import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

const transport = new StdioClientTransport({
  command: 'node',
  args: ['src/server.js'],
  cwd: process.cwd(),
  env: {
    ...process.env,
    GEV_MCP_BRIDGE_HOST: process.env.GEV_MCP_BRIDGE_HOST || '127.0.0.1',
    GEV_MCP_BRIDGE_PORT: process.env.GEV_MCP_BRIDGE_PORT || '4190',
    GEV_MCP_REQUEST_TIMEOUT_MS: process.env.GEV_MCP_REQUEST_TIMEOUT_MS || '45000',
  },
  stderr: 'inherit',
});

const client = new Client({ name: 'gev-drive30', version: '0.1.0' }, { capabilities: {} });
await client.connect(transport, { timeout: 15_000 });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const parseTextJson = (text) => {
  try { return JSON.parse(text); } catch { return { ok: false, error: text }; }
};

const call = async (name, args = {}) => {
  const res = await client.callTool({ name, arguments: args });
  const text = res?.content?.[0]?.type === 'text' ? res.content[0].text : '{}';
  return parseTextJson(text);
};

// Wait for UI to connect to the websocket bridge.
let ping = null;
for (let i = 0; i < 16; i += 1) {
  ping = await call('gev_ping', {});
  if (ping?.hasUi) break;
  await sleep(500);
}
if (!ping?.hasUi) {
  throw new Error('UI not connected to bridge websocket (open gods-eye-view at http://127.0.0.1:4174)');
}

// Place camera somewhere recognizable (Austin).
await call('gev_set_camera', { lon: -97.7431, lat: 30.2672, height: 1200, heading: 0, pitch: -35, roll: 0 });
await call('gev_gil_robot_set_enabled', { enabled: true });
await call('gev_gil_robot_anchor_to_camera', {});
await call('gev_gil_robot_focus', {});

// Drive 30 meters (best-effort). Requires dummy sim or Isaac connected.
const driveRes = await fetch('http://127.0.0.1:6769/api/drive_distance', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ distance_m: 30, vx: 0.4 }),
});
const drive = await driveRes.json().catch(() => ({}));

// eslint-disable-next-line no-console
console.log(JSON.stringify({ ok: true, ping, drive }, null, 2));

await transport.close();

