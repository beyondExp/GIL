import * as Cesium from 'cesium';
import { StyleManager } from './ui.js';
import { flyToAustin } from './camera.js';
import { DataLayerManager } from './data/manager.js';
import flightsLayer from './data/flights.js';
import militaryFlightsLayer from './data/militaryFlights.js';
import earthquakesLayer from './data/earthquakes.js';
import satellitesLayer from './data/satellites.js';
import rocketLaunchesLayer from './data/rocketLaunches.js';
import trafficLayer from './data/traffic.js';
import cctvLayer from './data/cctv.js';
import radioLayer from './data/radio.js';
import bikeshareLayer from './data/bikeshare.js';
import aisLiveVesselsLayer from './data/aisLiveVessels.js';
import militaryInstallationsLayer from './data/militaryInstallations.js';
import militaryAwarenessLayer from './data/militaryAwareness.js';
import localDataLayers from './data/localLayers.js';
import { LAYER_STATE_REGISTRY } from './data/layerState.js';
import { registerDataCredits } from './data/dataCredits.js';
import { SceneDirector } from './scenes/director.js';
import { initGevVoiceCommands } from './voice/gevRealtime.js';
import { MapStackController } from './mapStackController.js';
import { initAnnotations } from './annotations/index.js';
import { initLogoGaze } from './logoGaze.js';
import { initCockpitCloudEffects } from './cockpitCloudEffects.js';
import {
  installRenderGovernor,
  getRenderGovernorDiagnostics,
  governorRequestRender,
  holdContinuousRender,
  releaseContinuousRender,
} from './renderGovernor.js';
import { installScopeMask } from './scopeMask.js';
import { initFirstRunExperience } from './firstRunExperience.js';
import { initStreetViewPip } from './streetViewPip.js';
import { initGilRobotInspector } from './gilRobotInspector.js';
import { initGevMcpBridge } from './gevMcpBridge.js';
import { initCustom360Library } from './custom360.js';

initLogoGaze();

function getBodyMode() {
  try {
    const mode = (new URL(window.location.href).searchParams.get('body') || 'earth').toLowerCase();
    return mode === 'moon' ? 'moon' : 'earth';
  } catch {
    return 'earth';
  }
}

function navigateToBodyMode(mode) {
  const next = (mode || 'earth').toLowerCase() === 'moon' ? 'moon' : 'earth';
  const url = new URL(window.location.href);
  if (next === 'moon') url.searchParams.set('body', 'moon');
  else url.searchParams.delete('body');
  window.location.href = url.toString();
}

/**
 * Extract a human-readable error message from any thrown value.
 * Handles Error objects, strings, and plain objects with message/error fields.
 * @param {*} error — caught exception value
 * @returns {string} best-effort error description
 */
function describeError(error) {
  if (!error) return 'Unknown initialization error';
  if (error instanceof Error) {
    if (error.message && error.message.trim()) return error.message.trim();
    return error.name || 'Initialization error';
  }
  if (typeof error === 'string' && error.trim()) return error.trim();
  if (typeof error === 'object') {
    const maybeMessage = String(error.message || error.error || '').trim();
    if (maybeMessage) return maybeMessage;
    try {
      const serialized = JSON.stringify(error);
      if (serialized && serialized !== '{}') return serialized;
    } catch {
      // ignore serialization error
    }
  }
  return String(error);
}

/**
 * B-BOT GIL — Inspector UI entry point
 * Initializes CesiumJS with Google Photorealistic 3D Tiles,
 * style system, HUD overlays, location presets, and share links.
 */
async function init() {
  const loadingScreen = document.getElementById('loading-screen');
  const loaderStatus = loadingScreen.querySelector('.loader-status');
  const bodyMode = getBodyMode();

  // Globe body toggle (Earth <-> Moon). Implemented as a full reload because Cesium's
  // default ellipsoid must be set before creating the viewer for correct cartographic math.
  const moonToggle = document.getElementById('moon-toggle');
  if (moonToggle) {
    const sync = () => {
      const on = bodyMode === 'moon';
      moonToggle.setAttribute('aria-pressed', on ? 'true' : 'false');
      moonToggle.textContent = on ? 'MOON' : 'MOON (OFF)';
    };
    moonToggle.addEventListener('click', () => {
      navigateToBodyMode(bodyMode === 'moon' ? 'earth' : 'moon');
    });
    sync();
  }

  try {
    loaderStatus.textContent = 'Configuring viewer...';

    // Set Cesium Ion token for World Terrain
    const cesiumToken = import.meta.env.CESIUM_ION_TOKEN;
    if (cesiumToken) {
      Cesium.Ion.defaultAccessToken = cesiumToken;
    }

    // Moon mode: load Cesium Moon (3D Tiles) from Cesium ion.
    // This intentionally bypasses Google Photorealistic 3D Tiles + Earth-only data layers.
    if (bodyMode === 'moon') {
      if (!cesiumToken) {
        throw new Error('CESIUM_ION_TOKEN not found. Moon mode requires Cesium ion access (Cesium Moon asset).');
      }

      // Must be set BEFORE constructing the Viewer (Cesium docs).
      Cesium.Ellipsoid.default = Cesium.Ellipsoid.MOON;

      const viewer = new Cesium.Viewer('cesiumContainer', {
        timeline: false,
        animation: false,
        baseLayerPicker: false,
        geocoder: false,
        homeButton: false,
        sceneModePicker: false,
        navigationHelpButton: false,
        fullscreenButton: false,
        vrButton: false,
        selectionIndicator: false,
        infoBox: false,
        baseLayer: false,
        creditContainer: (() => {
          const el = document.createElement('div');
          el.id = 'cesium-credits';
          document.body.appendChild(el);
          return el;
        })(),
        msaaSamples: 4,
        contextOptions: {
          webgl: {
            preserveDrawingBuffer: true,
          },
        },
      });

      viewer.targetFrameRate = 60;
      viewer.scene.globe.show = false;
      viewer.scene.skyAtmosphere.show = false;
      viewer.scene.backgroundColor = Cesium.Color.BLACK;
      try {
        viewer.scene.sun.show = true;
        viewer.scene.moon.show = false;
      } catch {
        // ignore
      }

      loaderStatus.textContent = 'Loading Cesium Moon...';
      const moonTileset = await Cesium.Cesium3DTileset.fromIonAssetId(2684829);
      viewer.scene.primitives.add(moonTileset);
      await viewer.zoomTo(moonTileset);

      // Hide Earth-specific UI panels (they depend on Earth services).
      try {
        const dataPanel = document.getElementById('data-panel');
        if (dataPanel) dataPanel.style.display = 'none';
        const cctvPanel = document.getElementById('cctv-panel');
        if (cctvPanel) cctvPanel.style.display = 'none';
      } catch {
        // ignore
      }

      loadingScreen.classList.add('hidden');
      return;
    }

    // Set Google Maps API key for 3D Tiles
    const googleApiKey = import.meta.env.GOOGLE_MAPS_API_KEY;
    if (!googleApiKey) {
      throw new Error('GOOGLE_MAPS_API_KEY not found. Set it as an environment variable.');
    }
    Cesium.GoogleMaps.defaultApiKey = googleApiKey;

    // Expose API key globally for geocoding in locations.js
    window.__GOOGLE_MAPS_API_KEY__ = googleApiKey;

    // Create the Cesium viewer with minimal chrome
    const viewer = new Cesium.Viewer('cesiumContainer', {
      timeline: false,
      animation: false,
      baseLayerPicker: false,
      geocoder: false,
      homeButton: false,
      sceneModePicker: false,
      navigationHelpButton: false,
      fullscreenButton: false,
      vrButton: false,
      selectionIndicator: false,
      infoBox: false,
      baseLayer: false,
      // Visible attribution container — Google Maps / 3D Tiles credits are
      // required by Google's Terms of Service, so they must be shown (styled
      // subtly via #cesium-credits). The credit line stays visible in
      // clean-view AND recording modes too (ToS requires attribution while the
      // content is displayed — those are the exact modes used to record
      // demos), including the "Data attribution" link that opens the per-layer
      // license popover.
      creditContainer: (() => {
        const el = document.createElement('div');
        el.id = 'cesium-credits';
        document.body.appendChild(el);
        return el;
      })(),
      msaaSamples: 4,
      contextOptions: {
        webgl: {
          preserveDrawingBuffer: true,
        },
      },
    });

    // Cap the default render loop at 60 fps. Cesium's loop otherwise runs at
    // the display's refresh rate — 120 Hz on ProMotion panels — doubling GPU
    // and CPU burn for zero visual benefit in a map app whose animation
    // cadences (poll interpolation, trail fades, style crossfades) are all
    // designed against wall-clock time, not frame count. Measured on the
    // 2026-08-05 perf investigation as a strict halving of idle burn on
    // 120 Hz hardware; a no-op on 60 Hz displays. (perf item 2)
    viewer.targetFrameRate = 60;

    // Register per-layer data attribution into the "Data attribution" popover.
    // Required by each source's license (ODbL, CC BY-NC-SA, NASA FIRMS, etc.);
    // strings are verbatim from DATA_SOURCES.md. Static + always-present in the
    // expandable bottom-left credit lightbox (showOnScreen=false), so they never
    // clutter the on-globe attribution line.
    registerDataCredits(viewer);

    // Hide Cesium's default globe — Google Photorealistic 3D Tiles provide their own
    // globe at all LODs (street level → orbital). The default globe's 2D imagery
    // clips through 3D tile buildings at close range.
    viewer.scene.globe.show = false;

    // Keep a sky behind Google 3D Tiles, but soften Cesium's high-intensity
    // default atmosphere. With the globe hidden its bright limb otherwise
    // reads as a hard cyan seam where distant photoreal tiles meet the sky.
    viewer.scene.skyAtmosphere.show = true;
    viewer.scene.skyAtmosphere.atmosphereLightIntensity = 18;
    viewer.scene.skyAtmosphere.saturationShift = -0.12;
    viewer.scene.skyAtmosphere.brightnessShift = -0.08;

    loaderStatus.textContent = 'Loading Google 3D Tiles...';
    let tileset = null;
    try {
      // Load Google Photorealistic 3D Tiles
      tileset = await Cesium.createGooglePhotorealistic3DTileset({
        onlyUsingWithGoogleGeocoder: true,
      });
      viewer.scene.primitives.add(tileset);
      // NOTE: Cesium World Terrain intentionally disabled — conflicts with Google 3D Tiles at high zoom.
      // Google Photorealistic 3D Tiles provide their own terrain/elevation.
      viewer.scene.globe.show = false;
    } catch (tileError) {
      console.warn('[Init] Google 3D Tiles unavailable, falling back to Cesium globe:', tileError);
      const tileErrorDetail = describeError(tileError);
      loaderStatus.textContent = `Google 3D Tiles unavailable (${tileErrorDetail}). Continuing in fallback mode...`;
      // Keep Cesium globe visible as fallback instead of aborting the app.
      viewer.scene.globe.show = true;
    }

    loaderStatus.textContent = 'Initializing systems...';

    const mapStackController = new MapStackController(viewer, {
      googleTileset: tileset,
      cesiumToken,
      initialStack: tileset ? 'photoreal' : 'osm',
      // Task 5 (height-datum fix): rebroadcast stack changes as a window
      // CustomEvent so data layers (CCTV per-regime ground resolution) can
      // react without coupling MapStackController to layer modules. Fires on
      // 'switching'/'ready'/'error'; listeners derive the surface regime from
      // live scene state, so intermediate emissions are harmless.
      onChange: (state) => {
        window.dispatchEvent(new CustomEvent('gev:map-stack-changed', { detail: state }));
      },
      onError: (message) => console.warn('[MapStack]', message),
    });
    await mapStackController.setStack(tileset ? 'photoreal' : 'osm', { silent: true });

    // Initialize the style manager (post-processing, HUD, locations, share links)
    const styleManager = new StyleManager(viewer, { mapStackController });
    // The previous multi-canvas weather compositor remains disabled. Cockpit
    // clouds use a separate, capped low-resolution GPU pass that never attaches
    // Cesium fog or post-process stages and is fully stopped in map mode.
    const weatherEffects = null;
    const cockpitCloudEffects = initCockpitCloudEffects(viewer);

    // If no share link state, do default fly-to Austin
    if (!styleManager.hasShareState) {
      loaderStatus.textContent = 'Flying to Austin, TX...';
      flyToAustin(viewer);
    } else {
      loaderStatus.textContent = 'Restoring shared view...';
    }

    // Initialize data layer manager
    const dataManager = new DataLayerManager(viewer, {
      allowQaRegistration: import.meta.env.DEV,
    });
    dataManager.register(flightsLayer);
    dataManager.register(militaryFlightsLayer);
    dataManager.register(earthquakesLayer);
    dataManager.register(satellitesLayer);
    dataManager.register(rocketLaunchesLayer);
    rocketLaunchesLayer.attachDataManager(dataManager);
    dataManager.register(trafficLayer);
    dataManager.register(cctvLayer);
    dataManager.register(radioLayer);
    dataManager.register(bikeshareLayer);
    dataManager.register(aisLiveVesselsLayer);
    dataManager.register(militaryInstallationsLayer);
    dataManager.register(militaryAwarenessLayer);
    militaryAwarenessLayer.attachDataManager(dataManager);
    for (const layer of localDataLayers) {
      dataManager.register(layer);
    }
    // Restoration starts only after the complete production registry is sealed.
    dataManager.finalizeRegistrations(LAYER_STATE_REGISTRY);
    if (import.meta.env.DEV) {
      window.__gevQaRegisterLayer = (targetManager, layerModule) => {
        if (targetManager !== dataManager) throw new Error('QA layer manager mismatch');
        return dataManager.registerForQa(layerModule);
      };
      window.__gevQaUnregisterLayer = (targetManager, layerId) => {
        if (targetManager !== dataManager) throw new Error('QA layer manager mismatch');
        return dataManager.unregisterForQa(layerId);
      };
    }
    dataManager.buildTogglePanel(document.getElementById('data-toggles'));
    styleManager.attachDataManager(dataManager);

    // Initialize deterministic scene playback for social clip capture
    const sceneDirector = new SceneDirector(viewer, styleManager, dataManager);

    // Initialize the voice "whiteboard" annotation engine (world-space renderer)
    const annotations = initAnnotations({ viewer, tileset });

    // Keep startup chrome truthful: a share is not restored until camera,
    // visual/map/panel lanes, and every requested layer have terminated.
    void Promise.all([
      styleManager.initialRestorePromise,
      new Promise((resolve) => setTimeout(resolve, 1000)),
    ]).finally(() => {
      loadingScreen.classList.add('hidden');
      // Reveal only after the loading cover has yielded. transitionend can be
      // absent under reduced motion, so a bounded fallback makes this reliable.
      let firstRunRevealed = false;
      const revealFirstRun = () => {
        if (firstRunRevealed) return;
        firstRunRevealed = true;
        // dataManager is passed explicitly: the globe missions enable bundled
        // keyless layers through it, and reaching for styleManager._dataManager
        // would make a private field part of this feature's contract.
        initFirstRunExperience({ styleManager, dataManager });
      };
      loadingScreen.addEventListener('transitionend', revealFirstRun, { once: true });
      setTimeout(revealFirstRun, 900);
    });

    // Expose for debugging
    // Idle render governor: flips the scene into requestRenderMode whenever
    // nothing animates per frame. Installed AFTER every module above has had
    // its chance to register pre-install holds. (perf wave 2)
    installRenderGovernor(viewer);

    // The explicit scope mask replaces the emergent six-pass artifact —
    // see src/scopeMask.js. Installed before the UI so the DISPLAY-rail
    // toggle finds it live.
    installScopeMask(viewer);

    // Zoomed-in Street View preview (server-side proxy keeps API key private).
    const streetViewInitialEnabled = (() => {
      try {
        const raw = window.localStorage?.getItem('gevStreetViewPreviewEnabled');
        if (raw === null) return false;
        return raw === '1' || raw === 'true';
      } catch {
        return false;
      }
    })();
    const streetViewPip = initStreetViewPip(viewer, { defaultEnabled: streetViewInitialEnabled });
    if (streetViewPip) {
      window.__godsEyeView = window.__godsEyeView || {};
      window.__godsEyeView.streetViewPip = streetViewPip;
    }
    const streetViewToggle = document.getElementById('streetview-preview-toggle');
    if (streetViewToggle && streetViewPip) {
      const syncToggle = () => {
        const on = streetViewPip.isEnabled();
        streetViewToggle.setAttribute('aria-pressed', on ? 'true' : 'false');
        streetViewToggle.textContent = on ? 'STREET VIEW PREVIEW' : 'STREET VIEW PREVIEW (OFF)';
      };
      streetViewToggle.addEventListener('click', () => {
        streetViewPip.setEnabled(!streetViewPip.isEnabled());
        try { window.localStorage?.setItem('gevStreetViewPreviewEnabled', streetViewPip.isEnabled() ? '1' : '0'); } catch {}
        syncToggle();
      });
      syncToggle();
    }

    // Custom 360° pins (local manifest + fullscreen viewer).
    const custom360InitialEnabled = (() => {
      try {
        const raw = window.localStorage?.getItem('gevCustom360Enabled');
        if (raw === null) return false;
        return raw === '1' || raw === 'true';
      } catch {
        return false;
      }
    })();
    const custom360 = initCustom360Library(viewer, { defaultEnabled: custom360InitialEnabled });
    if (custom360) {
      window.__godsEyeView = window.__godsEyeView || {};
      window.__godsEyeView.custom360 = custom360;
    }
    const custom360Toggle = document.getElementById('custom360-toggle');
    if (custom360Toggle && custom360) {
      const sync = () => {
        const on = custom360.isEnabled();
        custom360Toggle.setAttribute('aria-pressed', on ? 'true' : 'false');
        custom360Toggle.textContent = on ? 'CUSTOM 360' : 'CUSTOM 360 (OFF)';
      };
      custom360Toggle.addEventListener('click', () => {
        const next = !custom360.isEnabled();
        custom360.setEnabled(next);
        try { window.localStorage?.setItem('gevCustom360Enabled', next ? '1' : '0'); } catch {}
        sync();
      });
      sync();
    }

    // Live GIL robot inspector (gil_controls REST bridge).
    const gilRobotInitialEnabled = (() => {
      try {
        const raw = window.localStorage?.getItem('gevGilRobotEnabled');
        if (raw === null) return false;
        return raw === '1' || raw === 'true';
      } catch {
        return false;
      }
    })();
    const gilRobotInspector = initGilRobotInspector(viewer, { defaultEnabled: gilRobotInitialEnabled });
    if (gilRobotInspector) {
      window.__godsEyeView = window.__godsEyeView || {};
      window.__godsEyeView.gilRobotInspector = gilRobotInspector;
    }
    const gilRobotToggle = document.getElementById('gil-robot-toggle');
    if (gilRobotToggle && gilRobotInspector) {
      const syncGilRobotToggle = () => {
        const on = gilRobotInspector.isEnabled();
        gilRobotToggle.setAttribute('aria-pressed', on ? 'true' : 'false');
        gilRobotToggle.textContent = on ? 'GIL ROBOT' : 'GIL ROBOT (OFF)';
      };
      gilRobotToggle.addEventListener('click', () => {
        gilRobotInspector.setEnabled(!gilRobotInspector.isEnabled());
        syncGilRobotToggle();
      });
      syncGilRobotToggle();
    }

    // MCP bridge: expose UI tools to external agents via a local MCP server.
    const gevMcpBridge = initGevMcpBridge({
      viewer,
      styleManager,
      dataManager,
      mapStackController,
      gilRobotInspector,
    });
    if (gevMcpBridge) {
      window.__godsEyeView = window.__godsEyeView || {};
      window.__godsEyeView.mcpBridge = gevMcpBridge;
    }

    // Cesium OSM Buildings (ion-backed, worldwide where OSM has coverage).
    const osmBuildingsToggle = document.getElementById('osm-buildings-toggle');
    let osmBuildingsTileset = null;
    let osmBuildingsLoading = null;
    const osmBuildingsEnabled = (() => {
      try {
        const raw = window.localStorage?.getItem('gevOsmBuildingsEnabled');
        if (raw === null) return false;
        return raw === '1' || raw === 'true';
      } catch {
        return false;
      }
    })();

    const setOsmBuildingsUi = (on) => {
      if (!osmBuildingsToggle) return;
      osmBuildingsToggle.setAttribute('aria-pressed', on ? 'true' : 'false');
      osmBuildingsToggle.textContent = on ? 'OSM BUILDINGS' : 'OSM BUILDINGS (OFF)';
    };

    const ensureOsmBuildingsLoaded = async () => {
      if (osmBuildingsTileset) return osmBuildingsTileset;
      if (osmBuildingsLoading) return await osmBuildingsLoading;
      osmBuildingsLoading = (async () => {
        const tileset = await Cesium.createOsmBuildingsAsync();
        tileset.show = true;
        viewer.scene.primitives.add(tileset);
        osmBuildingsTileset = tileset;
        governorRequestRender('osm-buildings-loaded');
        return tileset;
      })();
      try {
        return await osmBuildingsLoading;
      } finally {
        osmBuildingsLoading = null;
      }
    };

    const applyOsmBuildingsVisibility = async () => {
      const enabled = (() => {
        try {
          const raw = window.localStorage?.getItem('gevOsmBuildingsEnabled');
          return raw === '1' || raw === 'true';
        } catch {
          return false;
        }
      })();
      const activeStackId = mapStackController.getActiveId();
      const shouldShow = enabled && activeStackId === 'osm';
      if (!shouldShow) {
        if (osmBuildingsTileset) osmBuildingsTileset.show = false;
        governorRequestRender('osm-buildings-hide');
        return;
      }
      try {
        const t = await ensureOsmBuildingsLoaded();
        t.show = true;
        governorRequestRender('osm-buildings-show');
      } catch (e) {
        console.warn('[OSM Buildings] Failed to load:', e);
        try { window.localStorage?.setItem('gevOsmBuildingsEnabled', '0'); } catch {}
        setOsmBuildingsUi(false);
      }
    };

    if (osmBuildingsToggle) {
      const ionOk = Boolean(cesiumToken);
      if (!ionOk) {
        osmBuildingsToggle.disabled = true;
        osmBuildingsToggle.title = 'Cesium ion token required for OSM Buildings';
      } else {
        osmBuildingsToggle.addEventListener('click', async () => {
          const current = (() => {
            try { return (window.localStorage?.getItem('gevOsmBuildingsEnabled') || '') === '1'; } catch { return false; }
          })();
          const next = !current;
          try { window.localStorage?.setItem('gevOsmBuildingsEnabled', next ? '1' : '0'); } catch {}
          setOsmBuildingsUi(next);
          // OSM Buildings are designed for the globe stack; switch to it.
          if (next && mapStackController.getActiveId() !== 'osm') {
            await mapStackController.setStack('osm');
          }
          await applyOsmBuildingsVisibility();
        });
      }
      setOsmBuildingsUi(osmBuildingsEnabled);
      // Initial apply: if enabled, ensure stack is OSM then load buildings.
      if (osmBuildingsEnabled && ionOk) {
        (async () => {
          if (mapStackController.getActiveId() !== 'osm') {
            await mapStackController.setStack('osm', { silent: true });
          }
          await applyOsmBuildingsVisibility();
        })();
      }
      window.addEventListener('gev:map-stack-changed', () => {
        void applyOsmBuildingsVisibility();
      });
    }

    // The follow camera recomputes the tracked target's dead-reckon position
    // every frame — tracking anything is a per-frame animation. (perf wave 2)
    viewer.trackedEntityChanged.addEventListener(() => {
      if (viewer.trackedEntity) holdContinuousRender('tracked-entity');
      else releaseContinuousRender('tracked-entity');
    });

    // Hidden-state suspension (perf wave 2): when the window/tab is hidden,
    // stop the default render loop outright — a hidden canvas repaints for
    // nobody, and browser rAF throttling still lets throttled frames burn
    // GPU. Holder/data state is untouched, so return is seamless: restore
    // the loop, refresh the one DOM surface we gated, render a frame.
    const syncVisibilitySuspension = () => {
      const hidden = document.hidden;
      viewer.useDefaultRenderLoop = !hidden;
      cockpitCloudEffects?.setSuspended?.(hidden);
      if (!hidden) {
        if (dataManager._panelRefreshPendingOnVisible) {
          dataManager._panelRefreshPendingOnVisible = false;
          dataManager._refreshTogglePanel();
        }
        governorRequestRender('visibility-restore');
      }
    };
    document.addEventListener('visibilitychange', syncVisibilitySuspension);
    // Apply the CURRENT state too — bootstrap can complete while the tab is
    // already hidden, and waiting for the next transition would leave the
    // loop burning behind a hidden tab. (perf wave 2 fix)
    syncVisibilitySuspension();

    window.__godsEyeView = {
      ...(window.__godsEyeView || {}),
      viewer,
      styleManager,
      tileset,
      dataManager,
      sceneDirector,
      mapStackController,
      annotations,
      weatherEffects,
      cockpitCloudEffects,
      getRenderGovernorDiagnostics,
      requestRender: governorRequestRender,
    };
    window.__godsEyeView.voiceCommands = initGevVoiceCommands({ viewer, styleManager, dataManager, sceneDirector, annotations });

  } catch (error) {
    console.error('B-BOT GIL initialization failed:', error);
    loaderStatus.textContent = `Error: ${describeError(error)}`;
    loaderStatus.style.color = '#ff4444';
  }
}

init();
