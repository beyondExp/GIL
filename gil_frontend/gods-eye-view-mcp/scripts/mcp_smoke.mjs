import process from 'node:process';

import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const transport = new StdioClientTransport({
  command: 'node',
  args: ['src/server.js'],
  cwd: process.cwd(),
  env: {
    ...process.env,
    GEV_MCP_BRIDGE_HOST: process.env.GEV_MCP_BRIDGE_HOST || '127.0.0.1',
    GEV_MCP_BRIDGE_PORT: process.env.GEV_MCP_BRIDGE_PORT || '4190',
    GEV_MCP_REQUEST_TIMEOUT_MS: process.env.GEV_MCP_REQUEST_TIMEOUT_MS || '15000',
  },
  stderr: 'inherit',
});

const client = new Client(
  { name: 'gev-mcp-smoke', version: '0.1.0' },
  { capabilities: {} },
);

await client.connect(transport, { timeout: 15_000 });

const tools = await client.listTools();
const names = new Set(tools.tools.map((t) => t.name));
for (const required of ['gev_ping', 'gev_get_state', 'gev_set_camera']) {
  if (!names.has(required)) {
    throw new Error(`missing tool: ${required}`);
  }
}

let ping = null;
for (let i = 0; i < 12; i += 1) {
  const res = await client.callTool({ name: 'gev_ping', arguments: {} });
  const text = res?.content?.[0]?.type === 'text' ? res.content[0].text : '{}';
  ping = JSON.parse(text);
  if (ping?.hasUi) break;
  await sleep(500);
}
if (!ping?.hasUi) {
  throw new Error('UI did not connect to websocket bridge (hasUi=false)');
}

const stateRes = await client.callTool({ name: 'gev_get_state', arguments: {} });
const stateTxt = stateRes?.content?.[0]?.type === 'text' ? stateRes.content[0].text : '{}';
const state = JSON.parse(stateTxt);

const camRes = await client.callTool({
  name: 'gev_set_camera',
  arguments: { lon: -97.7431, lat: 30.2672, height: 1500, heading: 0, pitch: -35, roll: 0 },
});
const camTxt = camRes?.content?.[0]?.type === 'text' ? camRes.content[0].text : '{}';
const cam = JSON.parse(camTxt);

const layerToggleRes = await client.callTool({
  name: 'gev_set_layer_enabled',
  arguments: { layerId: 'local-datacenters', enabled: true },
});
const layerToggleTxt = layerToggleRes?.content?.[0]?.type === 'text' ? layerToggleRes.content[0].text : '{}';
const layerToggle = JSON.parse(layerToggleTxt);

const state2Res = await client.callTool({ name: 'gev_get_state', arguments: {} });
const state2Txt = state2Res?.content?.[0]?.type === 'text' ? state2Res.content[0].text : '{}';
const state2 = JSON.parse(state2Txt);

const gilEnableRes = await client.callTool({
  name: 'gev_gil_robot_set_enabled',
  arguments: { enabled: true },
});
const gilEnableTxt = gilEnableRes?.content?.[0]?.type === 'text' ? gilEnableRes.content[0].text : '{}';
const gilEnable = JSON.parse(gilEnableTxt);

const gilAnchorRes = await client.callTool({ name: 'gev_gil_robot_anchor_to_camera', arguments: {} });
const gilAnchorTxt = gilAnchorRes?.content?.[0]?.type === 'text' ? gilAnchorRes.content[0].text : '{}';
const gilAnchor = JSON.parse(gilAnchorTxt);

const gilDreamRes = await client.callTool({ name: 'gev_gil_robot_dream', arguments: {} });
const gilDreamTxt = gilDreamRes?.content?.[0]?.type === 'text' ? gilDreamRes.content[0].text : '{}';
const gilDream = JSON.parse(gilDreamTxt);

// eslint-disable-next-line no-console
console.log(JSON.stringify({ ok: true, ping, state: state?.state || state, camera: cam?.result || cam }, null, 2));
// eslint-disable-next-line no-console
console.log(JSON.stringify({ layerToggle, state2: state2?.state || state2, gilEnable, gilAnchor, gilDream }, null, 2));

await transport.close();

