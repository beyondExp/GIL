import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls';

export class RobotScene {
  constructor(container, onStateChange, onStatusChange) {
    this.container = container;
    this.onStateChange = onStateChange;
    this.onStatusChange = onStatusChange;
    this.ws = null;
    this.stateInterval = null;
    
    // Robot Joint Angles (Degrees)
    // Initial "Ready" Pose: Adjusted to look at cubes (X=1.2)
    // Target Tip: (1.2, 0.3, 0.0) -> Wrist: (1.2, 0.5, 0.0)
    // Calc: Sh=48.6, El=97.2, Wr=34.2 (Points down)
    this.angles = { 
      base: 90, 
      shoulder: 48, 
      elbow: 97, 
      wristPitch: 35, 
      wristRoll: 0, 
      gripper: 0 
    };
    this.targetAngles = { ...this.angles };
    this.gripperOpen = true;
    this.basePose = { x: 0, y: 0, z: 0, yaw: 0 }; // reserved for future mobile base support
    this.heldObject = null; // THREE.Mesh when grasped
    this.objects = { bin: null, cubes: [] };
    this.graspRadius = 0.20; // training-friendly default, can be overridden by reset_scene
    this.cubeJitter = 0.15; // meters, can be overridden by reset_scene
    this.binJitter = 0.10; // meters, deterministic with seed
    this.randomizeColors = true;
    // Keep randomized objects within a conservative reachable region (avoid IK edge cases).
    this.cubeXMin = 0.90;
    this.cubeXMax = 1.50;
    this.cubeZMin = -0.65;
    this.cubeZMax = 0.65;
    this._lastMoveTarget = null; // {x,y,z} used for bin side-entry blocking
    
    // Arm Dimensions (meters)
    this.L1 = 0.5; // Base height
    this.L2 = 0.8; // Shoulder to Elbow
    this.L3 = 0.8; // Elbow to Wrist
    this.L4 = 0.2; // Wrist length (Gripper)

    this.init();
    this.createEnvironment();
    this.createRobot();
    this.setupWebSocket();
    this.startStateStreaming();
    this.animate();
  }

  // Deterministic RNG for seeded resets (Mulberry32)
  static mulberry32(seed) {
    let a = (seed >>> 0);
    return function() {
      a |= 0;
      a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  static randRange(rngFn, lo, hi) {
    return lo + (hi - lo) * rngFn();
  }

  init() {
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x2a2a2a);

    this.camera = new THREE.PerspectiveCamera(60, this.container.clientWidth / this.container.clientHeight, 0.1, 100);
    this.camera.position.set(3, 3, 3);
    this.camera.lookAt(0, 1, 0);

    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
    this.renderer.shadowMap.enabled = true;
    this.container.appendChild(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.target.set(0, 1, 0);

    window.addEventListener('resize', () => this.onWindowResize());
  }

  onWindowResize() {
    if (!this.container) return;
    this.camera.aspect = this.container.clientWidth / this.container.clientHeight;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
  }

  createEnvironment() {
    // Lights
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
    this.scene.add(ambientLight);

    const dirLight = new THREE.DirectionalLight(0xffffff, 1);
    dirLight.position.set(5, 10, 5);
    dirLight.castShadow = true;
    this.scene.add(dirLight);

    // Floor
    const grid = new THREE.GridHelper(10, 10, 0x444444, 0x222222);
    this.scene.add(grid);
    
    const plane = new THREE.Mesh(
        new THREE.PlaneGeometry(10, 10),
        new THREE.MeshStandardMaterial({ color: 0x1a1a1a })
    );
    plane.rotation.x = -Math.PI / 2;
    plane.receiveShadow = true;
    this.scene.add(plane);

    // Objects
    this.createObjects();
  }

  createObjects() {
    // Bin (open-top box) - prevents "visual cheating" and makes TPV cues clearer.
    const BIN_W = 0.6, BIN_H = 0.3, BIN_D = 0.6;
    const WALL_T = 0.04, FLOOR_T = 0.02;
    const binGroup = new THREE.Group();
    binGroup.name = 'bin';
    // Default pose (can be randomized in reset()).
    binGroup.position.set(1.40, 0.15, 0.00);

    const binMat = new THREE.MeshStandardMaterial({ color: 0xffff00 });
    binGroup.userData.binDims = { w: BIN_W, h: BIN_H, d: BIN_D, wall: WALL_T, floor: FLOOR_T };
    binGroup.userData.binMat = binMat;

    // Bottom
    const bottom = new THREE.Mesh(new THREE.BoxGeometry(BIN_W, FLOOR_T, BIN_D), binMat);
    bottom.position.set(0, -BIN_H / 2 + FLOOR_T / 2, 0);
    bottom.castShadow = true;
    binGroup.add(bottom);
    // Walls
    const wallLong = new THREE.BoxGeometry(BIN_W, BIN_H, WALL_T);
    const wallShort = new THREE.BoxGeometry(WALL_T, BIN_H, BIN_D);
    const wall1 = new THREE.Mesh(wallLong, binMat);
    wall1.position.set(0, 0, BIN_D / 2 - WALL_T / 2);
    const wall2 = new THREE.Mesh(wallLong, binMat);
    wall2.position.set(0, 0, -BIN_D / 2 + WALL_T / 2);
    const wall3 = new THREE.Mesh(wallShort, binMat);
    wall3.position.set(BIN_W / 2 - WALL_T / 2, 0, 0);
    const wall4 = new THREE.Mesh(wallShort, binMat);
    wall4.position.set(-BIN_W / 2 + WALL_T / 2, 0, 0);
    for (const w of [wall1, wall2, wall3, wall4]) {
      w.castShadow = true;
      binGroup.add(w);
    }

    this.scene.add(binGroup);
    this.objects.bin = binGroup;

    // Cubes
    const cubeGeo = new THREE.BoxGeometry(0.15, 0.15, 0.15);
    
    const redCube = new THREE.Mesh(cubeGeo, new THREE.MeshStandardMaterial({ color: 0xff0000 }));
    redCube.position.set(1.5, 0.075, 0.5);
    redCube.name = 'cube_red';
    redCube.userData.objectType = 'cube';
    redCube.userData.held = false;
    redCube.userData.initialPosition = { x: redCube.position.x, y: redCube.position.y, z: redCube.position.z };
    this.scene.add(redCube);

    const greenCube = new THREE.Mesh(cubeGeo, new THREE.MeshStandardMaterial({ color: 0x00ff00 }));
    // Keep green cube clearly away from the bin region at reset.
    greenCube.position.set(1.2, 0.075, -0.7);
    greenCube.name = 'cube_green';
    greenCube.userData.objectType = 'cube';
    greenCube.userData.held = false;
    greenCube.userData.initialPosition = { x: greenCube.position.x, y: greenCube.position.y, z: greenCube.position.z };
    this.scene.add(greenCube);

    const blueCube = new THREE.Mesh(cubeGeo, new THREE.MeshStandardMaterial({ color: 0x0000ff }));
    blueCube.position.set(0.9, 0.075, 0.2);
    blueCube.name = 'cube_blue';
    blueCube.userData.objectType = 'cube';
    blueCube.userData.held = false;
    blueCube.userData.initialPosition = { x: blueCube.position.x, y: blueCube.position.y, z: blueCube.position.z };
    this.scene.add(blueCube);

    this.objects.cubes = [redCube, greenCube, blueCube];
  }

  _binParams() {
    const bin = this.objects.bin;
    if (!bin) return null;
    const dims = bin.userData && bin.userData.binDims ? bin.userData.binDims : { w: 0.6, h: 0.3, d: 0.6, wall: 0.04, floor: 0.02 };
    const center = new THREE.Vector3();
    bin.getWorldPosition(center);
    const inv = new THREE.Matrix4();
    inv.copy(bin.matrixWorld).invert();
    const hx = dims.w / 2;
    const hz = dims.d / 2;
    const rimY = center.y + dims.h / 2; // top edge in world-space
    return { center, hx, hz, rimY, inv };
  }

  _insideBinXZ(x, z) {
    const p = this._binParams();
    if (!p) return false;
    const v = new THREE.Vector3(x, p.center.y, z);
    v.applyMatrix4(p.inv);
    return (Math.abs(v.x) <= p.hx) && (Math.abs(v.z) <= p.hz);
  }

  _applyBinSideEntryBlock(target) {
    // Prevent "going through bin walls": entering the bin interior at low Y must come from above (inside XZ already).
    const p = this._binParams();
    if (!p) return target;
    const inside = this._insideBinXZ(target.x, target.z);
    if (!inside) return target;
    if (target.y >= p.rimY) return target;
    const prev = this._lastMoveTarget;
    const prevInside = prev ? this._insideBinXZ(prev.x, prev.z) : false;
    if (!prevInside) {
      // Block side-entry by clamping to rim height; descent can continue once inside.
      return { x: target.x, y: p.rimY, z: target.z };
    }
    return target;
  }

  _applyBinSideExitBlock(target) {
    // Symmetric: prevent exiting the bin through walls below rim height.
    const p = this._binParams();
    if (!p) return target;
    const prev = this._lastMoveTarget;
    if (!prev) return target;
    const prevInside = this._insideBinXZ(prev.x, prev.z);
    const inside = this._insideBinXZ(target.x, target.z);
    if (prevInside && !inside && target.y < p.rimY) {
      return { x: target.x, y: p.rimY, z: target.z };
    }
    return target;
  }

  createRobot() {
    const material = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.2, metalness: 0.5 });
    const jointMat = new THREE.MeshStandardMaterial({ color: 0x333333 });

    // 1. Base
    this.baseGroup = new THREE.Group();
    this.scene.add(this.baseGroup);
    
    const baseMesh = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.4, this.L1, 32), jointMat);
    baseMesh.position.y = this.L1 / 2;
    baseMesh.castShadow = true;
    this.baseGroup.add(baseMesh);

    // 2. Shoulder
    this.shoulderGroup = new THREE.Group();
    this.shoulderGroup.position.y = this.L1;
    this.baseGroup.add(this.shoulderGroup);

    const shoulderMesh = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.3, 0.3), material);
    shoulderMesh.castShadow = true;
    this.shoulderGroup.add(shoulderMesh);

    const armMesh = new THREE.Mesh(new THREE.BoxGeometry(0.15, this.L2, 0.15), material);
    armMesh.position.y = this.L2 / 2;
    armMesh.castShadow = true;
    this.shoulderGroup.add(armMesh);

    // 3. Elbow
    this.elbowGroup = new THREE.Group();
    this.elbowGroup.position.y = this.L2;
    this.shoulderGroup.add(this.elbowGroup);

    const elbowJoint = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 0.25, 16), jointMat);
    elbowJoint.rotation.z = Math.PI / 2;
    elbowJoint.castShadow = true;
    this.elbowGroup.add(elbowJoint);

    const forearmMesh = new THREE.Mesh(new THREE.BoxGeometry(0.12, this.L3, 0.12), material);
    forearmMesh.position.y = this.L3 / 2;
    forearmMesh.castShadow = true;
    this.elbowGroup.add(forearmMesh);

    // 4. Wrist
    this.wristGroup = new THREE.Group();
    this.wristGroup.position.y = this.L3;
    this.elbowGroup.add(this.wristGroup);

    const wristMesh = new THREE.Mesh(new THREE.BoxGeometry(0.1, this.L4, 0.1), jointMat);
    wristMesh.position.y = this.L4 / 2;
    wristMesh.castShadow = true;
    this.wristGroup.add(wristMesh);

    // 5. End Effector / Gripper
    this.gripperGroup = new THREE.Group();
    this.gripperGroup.position.y = this.L4;
    this.wristGroup.add(this.gripperGroup);

    // Gripper fingers
    const fingerGeo = new THREE.BoxGeometry(0.02, 0.1, 0.02);
    this.leftFinger = new THREE.Mesh(fingerGeo, material);
    this.leftFinger.position.set(-0.03, 0.05, 0);
    this.gripperGroup.add(this.leftFinger);

    this.rightFinger = new THREE.Mesh(fingerGeo, material);
    this.rightFinger.position.set(0.03, 0.05, 0);
    this.gripperGroup.add(this.rightFinger);
    
    // Add Camera (visual representation + functional logic would be here)
    const camGeo = new THREE.BoxGeometry(0.05, 0.02, 0.05);
    const camMat = new THREE.MeshStandardMaterial({ color: 0x0000ff });
    this.wristCam = new THREE.Mesh(camGeo, camMat);
    this.wristCam.position.set(0, -0.02, 0.05); // Mounted on wrist
    this.wristGroup.add(this.wristCam);

    // AI Vision Cameras (Attached to Wrist)
    // 1. Left Eye
    this.cameraLeft = new THREE.PerspectiveCamera(75, 1.0, 0.1, 10);
    this.cameraLeft.position.set(-0.05, 0, 0); // 5cm left
    this.cameraLeft.lookAt(0, 1, 0); // Look Towards Fingers (+Y)
    this.wristCam.add(this.cameraLeft); // Attach to wrist cam mesh

    // 2. Right Eye
    this.cameraRight = new THREE.PerspectiveCamera(75, 1.0, 0.1, 10);
    this.cameraRight.position.set(0.05, 0, 0); // 5cm right
    this.cameraRight.lookAt(0, 1, 0); // Look Towards Fingers (+Y)
    this.wristCam.add(this.cameraRight);

    // 3. Wide Context
    this.cameraWide = new THREE.PerspectiveCamera(120, 1.0, 0.1, 10);
    this.cameraWide.position.set(0, 0, 0);
    this.cameraWide.lookAt(0, 1, 0); // Look Towards Fingers (+Y)
    this.wristCam.add(this.cameraWide);

    // Off-screen RenderTarget for AI Vision
    this.aiRenderTarget = new THREE.WebGLRenderTarget(640, 480);

    // Debug Target Marker
    const targetGeo = new THREE.SphereGeometry(0.05, 16, 16);
    const targetMat = new THREE.MeshBasicMaterial({ color: 0xff00ff, transparent: true, opacity: 0.5 });
    this.targetMarker = new THREE.Mesh(targetGeo, targetMat);
    this.scene.add(this.targetMarker);
  }

  updateRobotAngles() {
    // Interpolate current angles to target
    const lerp = 0.1;
    this.angles.base += (this.targetAngles.base - this.angles.base) * lerp;
    this.angles.shoulder += (this.targetAngles.shoulder - this.angles.shoulder) * lerp;
    this.angles.elbow += (this.targetAngles.elbow - this.angles.elbow) * lerp;
    
    // Independent interpolation for Wrist
    this.angles.wristPitch += (this.targetAngles.wristPitch - this.angles.wristPitch) * lerp;

    // Apply to meshes
    this.baseGroup.rotation.y = THREE.MathUtils.degToRad(this.angles.base);
    this.shoulderGroup.rotation.x = THREE.MathUtils.degToRad(this.angles.shoulder);
    this.elbowGroup.rotation.x = THREE.MathUtils.degToRad(this.angles.elbow);
    
    // Ensure wrist group exists and rotate it
    if (this.wristGroup) {
        this.wristGroup.rotation.x = THREE.MathUtils.degToRad(this.angles.wristPitch);
    }
    
    // Gripper animation
    const gripperTarget = this.gripperOpen ? 0.03 : 0.005;
    this.leftFinger.position.x += (-gripperTarget - this.leftFinger.position.x) * 0.2;
    this.rightFinger.position.x += (gripperTarget - this.rightFinger.position.x) * 0.2;
  }

  // Inverse Kinematics: Calculate angles for target (x, y, z)
  solveIK(x, y, z) {
    // CONSTRAINT: Floor Collision Prevention
    const MIN_HEIGHT = 0.05; // 5cm above ground
    if (y < MIN_HEIGHT) {
        console.warn(`[IK] Target Y=${y.toFixed(2)} is too low! Clamping to ${MIN_HEIGHT}m.`);
        y = MIN_HEIGHT;
    }

    // 1. Base Rotation (Theta 1)
    const theta1 = Math.atan2(x, z); 
    
    // Calculate horizontal distance from base to target
    let r = Math.sqrt(x*x + z*z);

    // ENGINEERING CONSTRAINT: Minimum Radius (Donut Workspace)
    const MIN_RADIUS = 0.3; // 30cm from center
    if (r < MIN_RADIUS) {
        console.warn(`[IK] Target too close to base (r=${r.toFixed(2)} < ${MIN_RADIUS}). Clamping.`);
        r = MIN_RADIUS;
        x = r * Math.sin(theta1);
        z = r * Math.cos(theta1);
        if (this.targetMarker) this.targetMarker.position.set(x, y, z);
    }
    
    // Effective height and reach for the 2-link planar arm (Shoulder+Elbow)
    // We want the GRIPPER TIP at (x,y,z) pointing DOWN.
    // So the WRIST PIVOT must be at (x, y + L4, z).
    // Target Y relative to Shoulder Pivot (L1 height):
    const dy = (y + this.L4) - this.L1; 
    const dr = r; 
    
    const h = Math.sqrt(dr*dr + dy*dy); // Distance from shoulder to wrist pivot
    
    let effectiveH = h;
    // Check reach
    if (h > (this.L2 + this.L3)) {
        console.warn("Target out of reach (" + h.toFixed(2) + "m > " + (this.L2 + this.L3) + "m) - Clamping");
        effectiveH = (this.L2 + this.L3) * 0.999; // Clamp to just inside max reach
    }
    
    // Cosine Rule for Shoulder (Theta 2) and Elbow (Theta 3)
    // Triangle sides: L2, L3, h
    
    // Angle at shoulder between L2 and h
    const alpha = Math.acos( (this.L2*this.L2 + effectiveH*effectiveH - this.L3*this.L3) / (2 * this.L2 * effectiveH) );
    // Angle of h relative to horizon
    const beta = Math.atan2(dy, dr);
    
    const theta2 = (Math.PI / 2) - (beta + alpha); // Relative to vertical
    
    // Angle at elbow between L2 and L3
    const gamma = Math.acos( (this.L2*this.L2 + this.L3*this.L3 - effectiveH*effectiveH) / (2 * this.L2 * this.L3) );
    const theta3 = Math.PI - gamma;
    
    this.targetAngles.base = THREE.MathUtils.radToDeg(theta1);
    this.targetAngles.shoulder = THREE.MathUtils.radToDeg(theta2);
    this.targetAngles.elbow = THREE.MathUtils.radToDeg(theta3);

    // ENGINEERING CONSTRAINT: Joint Limits
    // Clamp angles to realistic physical limits
    this.targetAngles.shoulder = Math.max(-90, Math.min(160, this.targetAngles.shoulder));
    this.targetAngles.elbow = Math.max(0, Math.min(160, this.targetAngles.elbow)); // Elbow can't bend backwards < 0
    
    // Keep wrist parallel to ground (optional)
    // Correction: Keep gripper pointing DOWN (180 degrees global)
    // Global Pitch = Sh + El + Wr = 180
    // Wr = 180 - Sh - El
    this.targetAngles.wristPitch = 180 - this.targetAngles.shoulder - this.targetAngles.elbow;
  }

  setupWebSocket() {
    if (this.ws) {
        // Prevent multiple connections from same instance
        this.ws.close();
        this.ws = null;
    }

    this.ws = new WebSocket('ws://127.0.0.1:8766');
    
    this.ws.onopen = () => {
      console.log('[WS] Connected');
      this.onStatusChange('Connected');
    };
    
    this.ws.onclose = () => {
      console.log('[WS] Disconnected');
      this.onStatusChange('Disconnected');
      // Only reconnect if we haven't been disposed
      if (!this.isDisposed) {
        this.reconnectTimer = setTimeout(() => this.setupWebSocket(), 3000);
      }
    };
    
    this.ws.onmessage = (event) => {
      console.log('[WS] Raw Message:', event.data);
      try {
        const data = JSON.parse(event.data);
        this.handleCommand(data);
      } catch (e) {
        console.error('WS Message Error', e);
      }
    };
  }

  startStateStreaming() {
    // Lightweight state stream for RL/control (no images). 20Hz default.
    if (this.stateInterval) clearInterval(this.stateInterval);
    this.stateInterval = setInterval(() => {
      this.sendLightStateUpdate();
    }, 50);
  }

  sendLightStateUpdate() {
    if (!(this.ws && this.ws.readyState === WebSocket.OPEN)) return;
    // End effector position (world)
    const eePos = new THREE.Vector3();
    if (this.gripperGroup) {
      this.gripperGroup.getWorldPosition(eePos);
    } else {
      eePos.set(0, 0, 0);
    }
    this.ws.send(JSON.stringify({
      type: 'scene_state',
      joint_positions: this.angles,
      end_effector: { x: eePos.x, y: eePos.y, z: eePos.z },
      base: this.basePose,
      gripper_open: this.gripperOpen,
      objects: this._getObjectStates()
    }));
  }

  _getObjectStates() {
    const out = {};
    if (this.objects.bin) {
      const p = new THREE.Vector3();
      this.objects.bin.getWorldPosition(p);
      out.bin = { x: p.x, y: p.y, z: p.z };
    }
    out.cubes = (this.objects.cubes || []).map((c) => {
      const p = new THREE.Vector3();
      c.getWorldPosition(p);
      return { name: c.name, x: p.x, y: p.y, z: p.z, held: !!c.userData.held };
    });
    out.held_object = this.heldObject ? this.heldObject.name : null;
    return out;
  }

  _tryGraspNearestCube() {
    if (!this.gripperGroup) return;
    if (this.heldObject) return;
    const eePos = new THREE.Vector3();
    this.gripperGroup.getWorldPosition(eePos);
    let best = null;
    let bestDist = Infinity;
    for (const cube of (this.objects.cubes || [])) {
      if (cube.userData.held) continue;
      const p = new THREE.Vector3();
      cube.getWorldPosition(p);
      const d = p.distanceTo(eePos);
      if (d < bestDist) {
        bestDist = d;
        best = cube;
      }
    }
    const graspRadius = (typeof this.graspRadius === 'number') ? this.graspRadius : 0.20;
    if (best && bestDist < graspRadius) {
      // Reparent to gripper keeping world transform
      this.gripperGroup.attach(best);
      best.position.set(0, 0.06, 0);
      best.userData.held = true;
      this.heldObject = best;
      console.log('[GRASP] Attached', best.name, 'dist', bestDist.toFixed(3));
    }
  }

  _releaseHeldObject() {
    if (!this.heldObject) return;
    const obj = this.heldObject;
    this.scene.attach(obj);
    obj.userData.held = false;
    this.heldObject = null;
    console.log('[GRASP] Released', obj.name);
  }

  handleCommand(data) {
    if (data.type === 'move_robot') {
      let tx = data.x, ty = data.y, tz = data.z;
      let adjusted = this._applyBinSideEntryBlock({ x: tx, y: ty, z: tz });
      adjusted = this._applyBinSideExitBlock(adjusted);
      tx = adjusted.x; ty = adjusted.y; tz = adjusted.z;
      this._lastMoveTarget = { x: tx, y: ty, z: tz };
      console.log('[CMD] Move to', tx, ty, tz);
      this.targetMarker.position.set(tx, ty, tz);
      this.solveIK(tx, ty, tz);
    } else if (data.type === 'set_angles') {
      console.log('[CMD] Restore Angles', data.angles);
      this.angles = { ...data.angles };
      this.targetAngles = { ...data.angles };
      // Force update visual immediately
      this.updateRobotAngles();
    } else if (data.type === 'gripper') {
      console.log('[CMD] Gripper', data.open);
      this.gripperOpen = data.open;
      if (!this.gripperOpen) {
        this._tryGraspNearestCube();
      } else {
        this._releaseHeldObject();
      }
    } else if (data.type === 'reset_scene') {
      console.log('[CMD] Reset Scene');
      const seed = (typeof data.seed === 'number') ? data.seed : null;
      if (typeof data.grasp_radius === 'number') this.graspRadius = data.grasp_radius;
      if (typeof data.cube_jitter === 'number') this.cubeJitter = data.cube_jitter;
      if (typeof data.bin_jitter === 'number') this.binJitter = data.bin_jitter;
      if (typeof data.randomize_colors === 'boolean') this.randomizeColors = data.randomize_colors;
      if (typeof data.cube_x_min === 'number') this.cubeXMin = data.cube_x_min;
      if (typeof data.cube_x_max === 'number') this.cubeXMax = data.cube_x_max;
      if (typeof data.cube_z_min === 'number') this.cubeZMin = data.cube_z_min;
      if (typeof data.cube_z_max === 'number') this.cubeZMax = data.cube_z_max;
      this.reset(seed);
    } else if (data.type === 'capture_image') {
      // On-demand image capture for dataset collection / perception tools.
      // This sends a 'scene_image' message immediately.
      this.sendStateUpdate();
    } else if (data.type === 'cmd_vel') {
      // Placeholder for future mobile base simulation.
      // We track the requested base pose locally so it can be logged back to controls.
      const dt = typeof data.dt === 'number' ? data.dt : 0.2;
      const vx = typeof data.vx === 'number' ? data.vx : 0;
      const vy = typeof data.vy === 'number' ? data.vy : 0;
      const wz = typeof data.wz === 'number' ? data.wz : 0;

      this.basePose.yaw += wz * dt;
      // Simple planar integration in world frame (not body-frame accurate; placeholder only)
      this.basePose.x += vx * dt;
      this.basePose.z += vy * dt;
    }
    
    // Send state back to UI/Controls
    this.onStateChange({
        angles: this.angles,
        gripper: this.gripperOpen ? 'OPEN' : 'CLOSED'
    });

    // After every command, push a lightweight state update so RL/control can step deterministically
    this.sendLightStateUpdate();
  }

  captureVisionData() {
    // Save current canvas size and target
    const currentSize = new THREE.Vector2();
    this.renderer.getSize(currentSize);
    const currentTarget = this.renderer.getRenderTarget();

    // Set size for AI vision (standardized)
    this.renderer.setSize(640, 480, false); // false = don't update style (prevents flicker)
    
    // Left Eye Capture
    this.renderer.setRenderTarget(null); 
    
    // 1. Left Eye -> RenderTarget
    const rtLeft = new THREE.WebGLRenderTarget(640, 480);
    this.renderer.setRenderTarget(rtLeft);
    this.renderer.render(this.scene, this.cameraLeft);
    
    // Read pixels
    const bufferLeft = new Uint8Array(640 * 480 * 4);
    this.renderer.readRenderTargetPixels(rtLeft, 0, 0, 640, 480, bufferLeft);
    
    // 2. Right Eye -> RenderTarget
    const rtRight = new THREE.WebGLRenderTarget(640, 480);
    this.renderer.setRenderTarget(rtRight);
    this.renderer.render(this.scene, this.cameraRight);
    
    const bufferRight = new Uint8Array(640 * 480 * 4);
    this.renderer.readRenderTargetPixels(rtRight, 0, 0, 640, 480, bufferRight);

    // 3. Wide / third-person-ish camera (use cameraWide, already attached to wrist)
    // Note: for true TPV, we may later switch this to a world-fixed camera; for now it provides
    // an additional perspective without changing scene setup.
    const rtWide = new THREE.WebGLRenderTarget(640, 480);
    this.renderer.setRenderTarget(rtWide);
    this.renderer.render(this.scene, this.cameraWide);
    const bufferWide = new Uint8Array(640 * 480 * 4);
    this.renderer.readRenderTargetPixels(rtWide, 0, 0, 640, 480, bufferWide);

    // Restore Main View
    this.renderer.setRenderTarget(currentTarget);
    this.renderer.setSize(currentSize.x, currentSize.y, true); // Restore style
    
    // Convert raw pixels to Data URL (Heavy, but necessary for current architecture)
    // We use a helper canvas that is never added to DOM
    if (!this.helperCanvas) {
        this.helperCanvas = document.createElement('canvas');
        this.helperCanvas.width = 640;
        this.helperCanvas.height = 480;
    }
    const ctx = this.helperCanvas.getContext('2d');
    
    // Process Left
    const imgDataLeft = ctx.createImageData(640, 480);
    // WebGL flips Y, we need to flip it back or handle it.
    // readRenderTargetPixels returns bottom-up. ImageData expects top-down.
    this.flipY(bufferLeft, imgDataLeft.data, 640, 480);
    ctx.putImageData(imgDataLeft, 0, 0);
    const imgLeft = this.helperCanvas.toDataURL('image/jpeg', 0.8);
    
    // Process Right
    const imgDataRight = ctx.createImageData(640, 480);
    this.flipY(bufferRight, imgDataRight.data, 640, 480);
    ctx.putImageData(imgDataRight, 0, 0);
    const imgRight = this.helperCanvas.toDataURL('image/jpeg', 0.8);

    // Process Wide
    const imgDataWide = ctx.createImageData(640, 480);
    this.flipY(bufferWide, imgDataWide.data, 640, 480);
    ctx.putImageData(imgDataWide, 0, 0);
    const imgWide = this.helperCanvas.toDataURL('image/jpeg', 0.8);
    
    // Cleanup Targets
    rtLeft.dispose();
    rtRight.dispose();
    rtWide.dispose();

    // Camera Info (World Space)
    const camPos = new THREE.Vector3();
    this.cameraLeft.getWorldPosition(camPos);
    const camPosWide = new THREE.Vector3();
    this.cameraWide.getWorldPosition(camPosWide);
    
    return {
        image: imgLeft, 
        image_left: imgLeft,
        image_right: imgRight,
        image_wide: imgWide,
        camera_info: {
            position: { x: camPos.x, y: camPos.y, z: camPos.z }
        },
        camera_info_wide: {
            position: { x: camPosWide.x, y: camPosWide.y, z: camPosWide.z }
        }
    };
  }

  flipY(src, dst, width, height) {
    for (let y = 0; y < height; y++) {
        const srcRow = (height - 1 - y) * width * 4;
        const dstRow = y * width * 4;
        for (let i = 0; i < width * 4; i++) {
            dst[dstRow + i] = src[srcRow + i];
        }
    }
  }

  sendStateUpdate() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        const vision = this.captureVisionData();
        // End effector position (world)
        const eePos = new THREE.Vector3();
        if (this.gripperGroup) {
          this.gripperGroup.getWorldPosition(eePos);
        } else {
          eePos.set(0, 0, 0);
        }
        
        // Update UI with vision data
        this.onStateChange({
            angles: this.angles,
            gripper: this.gripperOpen ? 'OPEN' : 'CLOSED',
            vision: vision
        });

        this.ws.send(JSON.stringify({
            type: 'scene_image',
            joint_positions: this.angles,
            end_effector: { x: eePos.x, y: eePos.y, z: eePos.z },
            base: this.basePose,
            gripper_open: this.gripperOpen,
            objects: this._getObjectStates(),
            image: vision.image,
            image_left: vision.image_left,
            image_right: vision.image_right,
            image_wide: vision.image_wide,
            camera_info: vision.camera_info,
            camera_info_wide: vision.camera_info_wide
        }));
    }
  }

  animate() {
    // Stop animation if disposed
    if (this.isDisposed) return;

    requestAnimationFrame(() => this.animate());
    
    this.updateRobotAngles();

    // If gripper is closed but we aren't holding anything yet, keep trying to grasp.
    // This avoids a common failure mode where we close "slightly too early" and never re-attempt.
    if (!this.gripperOpen && !this.heldObject) {
      this._tryGraspNearestCube();
    }

    this.controls.update();
    this.renderer.render(this.scene, this.camera);
    
    // Send updates occasionally
    if (Math.random() < 0.02) {
        this.sendStateUpdate();
    }
  }
  
  reset(seed = null) {
    // Reset to Ready Pose
    this.angles = { 
      base: 90, 
      shoulder: 48, 
      elbow: 97, 
      wristPitch: 35, 
      wristRoll: 0, 
      gripper: 0 
    };
    this.targetAngles = { ...this.angles };
    this.gripperOpen = true;
    this._releaseHeldObject();
    this._lastMoveTarget = null;

    // Deterministic randomization source
    const rng = (typeof seed === 'number') ? RobotScene.mulberry32(seed) : Math.random;

    // Randomize bin pose (keep reachable)
    if (this.objects.bin) {
      const bj = (typeof this.binJitter === 'number') ? this.binJitter : 0.10;
      const base = { x: 1.40, y: 0.15, z: 0.00 };
      const x = Math.min(1.55, Math.max(1.10, RobotScene.randRange(rng, base.x - bj, base.x + bj)));
      const z = Math.min(0.40, Math.max(-0.40, RobotScene.randRange(rng, base.z - bj, base.z + bj)));
      this.objects.bin.position.set(x, base.y, z);
      this.objects.bin.rotation.y = RobotScene.randRange(rng, -Math.PI, Math.PI);
      // Bin color randomization (deterministic)
      if (this.randomizeColors && this.objects.bin.userData && this.objects.bin.userData.binMat) {
        const palette = [0xffff00, 0xffcc00, 0xff8800, 0x00ffff, 0xff00ff, 0xffffff];
        const c = palette[Math.floor(RobotScene.randRange(rng, 0, palette.length - 1 + 0.999))];
        this.objects.bin.userData.binMat.color.setHex(c);
      }
    }

    // Reset cubes to initial positions
    const binPos = (this.objects.bin) ? this.objects.bin.position : { x: 1.4, y: 0.15, z: 0.0 };
    const jitter = (typeof this.cubeJitter === 'number') ? this.cubeJitter : 0.15;
    for (const cube of (this.objects.cubes || [])) {
      const ip = cube.userData.initialPosition;
      if (ip) {
        this.scene.attach(cube);
        // Small deterministic jitter for training robustness (and multi-seed eval).
        // Keep within reachable workspace and avoid starting inside the bin region.
        let x = ip.x;
        let z = ip.z;
        for (let k = 0; k < 20; k++) {
          x = RobotScene.randRange(rng, ip.x - jitter, ip.x + jitter);
          z = RobotScene.randRange(rng, ip.z - jitter, ip.z + jitter);
          // Clamp to conservative reachable workspace in this sim
          const xMin = (typeof this.cubeXMin === 'number') ? this.cubeXMin : 0.90;
          const xMax = (typeof this.cubeXMax === 'number') ? this.cubeXMax : 1.50;
          const zMin = (typeof this.cubeZMin === 'number') ? this.cubeZMin : -0.65;
          const zMax = (typeof this.cubeZMax === 'number') ? this.cubeZMax : 0.65;
          x = Math.min(xMax, Math.max(xMin, x));
          z = Math.min(zMax, Math.max(zMin, z));
          // Avoid bin overlap (conservative)
          const dx = Math.abs(x - binPos.x);
          const dz = Math.abs(z - binPos.z);
          if (!(dx <= 0.35 && dz <= 0.35)) break;
        }
        cube.position.set(x, ip.y, z);
        cube.userData.held = false;
      }
      // Randomize cube colors deterministically (do NOT change names)
      if (this.randomizeColors && cube.material && cube.material.color) {
        const palette = [0xff0000, 0x00ff00, 0x0000ff, 0xff00ff, 0x00ffff, 0xffffff, 0xff8800];
        const c = palette[Math.floor(RobotScene.randRange(rng, 0, palette.length - 1 + 0.999))];
        cube.material.color.setHex(c);
      }
    }
    
    // Reset visual marker
    if (this.targetMarker) {
        this.targetMarker.position.set(0, 0, 0); // Hide or center
    }
    
    // Force update
    this.updateRobotAngles();
    
    // Notify backend (optional, but good for sync)
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.sendStateUpdate();
    }
  }

  dispose() {
    this.isDisposed = true; 
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.stateInterval) clearInterval(this.stateInterval);

    if (this.ws) {
        this.ws.onclose = null;
        this.ws.close();
        this.ws = null;
    }
    this.renderer.dispose();
    this.container.innerHTML = '';
    window.removeEventListener('resize', this.onWindowResize);
    this.helperCanvas = null;
  }
}
