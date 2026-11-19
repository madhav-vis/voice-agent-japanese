// The one object the page talks to. The voice side gives it cues (what the agent is doing, how it feels,
// which audio track is its voice) and it turns them into bones and expressions on the VRM model.
// Nothing outside this folder touches the model directly.
import * as THREE from "three";
import { loadVRM, disposeVRM } from "./vrmLoader.js";
import { listenToTrack } from "./lipSync.js";

const EMOTIONS = ["happy", "angry", "sad", "relaxed", "surprised"];
const ACTIVITIES = ["idle", "listening", "thinking", "speaking"];
const BONES = ["hips", "spine", "chest", "neck", "head", "leftShoulder", "rightShoulder",
  "leftUpperArm", "rightUpperArm", "leftLowerArm", "rightLowerArm", "leftHand", "rightHand"];

// 30 frames a second is enough for a talking head, and every frame costs CPU the local TTS wants
const FRAME_MS = 1000 / 30;
// framing: from just above the hair down to the chest, never narrower than the shoulders
const VIEW_HEIGHT = 0.62, VIEW_WIDTH = 0.46, HEADROOM = 0.04;
// a VRM loads in a T-pose; this is how far the arms come down (radians)
const ARM_DOWN = 1.24;

// fraction of the way to a goal to move this frame, the same at any frame rate
const ease = (rate, dt) => 1 - Math.exp(-rate * dt);

export class AvatarController {
  constructor(container) {
    this.container = container;
    this.vrm = null;
    this.bones = {};
    this.enabled = true;
    this.disposed = false;

    this.activity = "idle";
    this.weights = { listening: 0, thinking: 0, speaking: 0 };  // each eases towards 1 while it is the activity
    this.emotion = { name: "neutral", strength: 1 };
    this.face = Object.fromEntries(EMOTIONS.map(name => [name, 0]));
    this.mouthTarget = 0;
    this.mouth = 0;
    this.audio = null;            // set by followAudio()
    this.overrides = new Map();   // set by setExpression(), for the test page
    this.blink = { wait: 1.5, at: -1 };
    this.time = 0;
    this.frameMs = 0;             // smoothed cost of one frame, for the test page

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(24, 1, 0.1, 20);
    const light = new THREE.DirectionalLight(0xffffff, Math.PI);
    light.position.set(0.6, 1, 1.2);
    this.scene.add(light);
    this.lookTarget = new THREE.Object3D();   // what the eyes follow
    this.lookGoal = new THREE.Vector3();
    this.scene.add(this.lookTarget);

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });  // throws without WebGL
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
    Object.assign(this.renderer.domElement.style, { width: "100%", height: "100%", display: "block" });
    container.appendChild(this.renderer.domElement);
    this.resizer = new ResizeObserver(() => this.resize());
    this.resizer.observe(container);
  }

  /** Loads (or swaps) the model. Resolves once it is on screen. */
  async load(url) {
    const vrm = await loadVRM(url);
    if (this.disposed) { disposeVRM(vrm); return; }
    if (this.vrm) disposeVRM(this.vrm);
    this.vrm = vrm;
    this.scene.add(vrm.scene);
    this.bones = Object.fromEntries(BONES.map(name => [name, vrm.humanoid.getNormalizedBoneNode(name)]));
    if (vrm.lookAt) vrm.lookAt.target = this.lookTarget;

    // where the top of the head is, for framing
    vrm.scene.updateMatrixWorld(true);
    const head = new THREE.Vector3();
    this.bones.head?.getWorldPosition(head);
    const box = new THREE.Box3().setFromObject(vrm.scene);
    this.top = Number.isFinite(box.max.y) ? Math.min(box.max.y, head.y + 0.4) : head.y + 0.25;

    // take the rest pose, then let hair and clothes settle there instead of swinging down from the T-pose
    this.pose(0, 0);
    vrm.update(0);
    vrm.springBoneManager?.reset();

    this.resize();
    this.lookTarget.position.copy(this.camera.position);
    this.start();
  }

  /** What the agent is doing: idle | listening | thinking | speaking (anything else counts as idle). */
  setActivity(activity) {
    this.activity = ACTIVITIES.includes(activity) ? activity : "idle";
  }

  /** neutral | happy | angry | sad | relaxed | surprised, cross-faded. strength 0..1 scales the expression. */
  setEmotion(name, strength = 1) {
    this.emotion = EMOTIONS.includes(name) ? { name, strength: Math.min(1, Math.max(0, strength)) } : { name: "neutral", strength: 1 };
  }

  /** The mouth follows this audio track (the agent's voice) until it is replaced; null stops following. */
  followAudio(mediaStreamTrack) {
    this.audio?.close();
    this.audio = mediaStreamTrack ? listenToTrack(mediaStreamTrack) : null;
    this.mouthTarget = 0;
  }

  /** Mouth opening 0..1, for when nothing is being followed. */
  setMouthOpen(level) {
    this.mouthTarget = Math.min(1, Math.max(0, level));
  }

  /** Test hook: hold one expression (e.g. "ih", "blinkLeft") at a value; null lets go of it. */
  setExpression(name, value) {
    if (value == null) {
      this.overrides.delete(name);
      this.vrm?.expressionManager?.setValue(name, 0);
    } else {
      this.overrides.set(name, value);
    }
  }

  blinkNow() {
    this.blink.at = 0;
  }

  /** false stops rendering completely (nothing is drawn or computed until it is turned back on). */
  setEnabled(enabled) {
    this.enabled = enabled;
    if (enabled) this.start(); else this.stop();
  }

  dispose() {
    this.disposed = true;
    this.stop();
    this.resizer.disconnect();
    this.audio?.close();
    if (this.vrm) disposeVRM(this.vrm);
    this.vrm = null;
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }

  // ---------- internals ----------

  start() {
    if (this.raf || !this.enabled || !this.vrm || this.disposed) return;
    this.last = performance.now();
    this.raf = requestAnimationFrame(this.tick);
  }

  stop() {
    cancelAnimationFrame(this.raf);
    this.raf = 0;
  }

  tick = (now) => {
    this.raf = requestAnimationFrame(this.tick);
    const since = now - this.last;
    if (since < FRAME_MS - 2) return;             // the display refreshes faster than we draw
    this.last = now;
    if (!this.container.clientWidth) return;      // hidden (e.g. the statistics view): draw nothing
    const dt = Math.min(since / 1000, 0.1);       // after a pause, don't jump
    this.time += dt;
    const began = performance.now();
    this.pose(this.time, dt);
    this.express(dt);
    this.vrm.update(dt);                          // applies bones, eyes, expressions, then hair/cloth physics
    this.renderer.render(this.scene, this.camera);
    this.frameMs += (performance.now() - began - this.frameMs) * 0.1;
  };

  resize() {
    const w = this.container.clientWidth, h = this.container.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    if (this.vrm) {
      const height = Math.max(VIEW_HEIGHT, VIEW_WIDTH / this.camera.aspect);  // a narrow column shows more of the body
      const centre = this.top + HEADROOM - height / 2;
      const distance = height / 2 / Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2));
      this.camera.position.set(0, centre, distance);
      this.camera.lookAt(0, centre, 0);
    }
    this.camera.updateProjectionMatrix();
    if (this.vrm && !this.raf) this.renderer.render(this.scene, this.camera);  // keep a paused avatar in frame
  }

  /** Body: rest pose plus small procedural motion. Rotations are set outright each frame, never accumulated. */
  pose(t, dt) {
    const b = this.bones, w = this.weights;
    for (const name in w) w[name] += ((this.activity === name ? 1 : 0) - w[name]) * ease(5, dt);
    const breath = Math.sin(t * 1.7);

    b.hips?.rotation.set(0, Math.sin(t * 0.37) * 0.02, 0);
    b.spine?.rotation.set(0.035 * w.listening + breath * 0.006, 0, Math.sin(t * 0.43) * 0.012);  // listening: lean in
    b.chest?.rotation.set(breath * 0.014, 0, 0);
    b.neck?.rotation.set(0.02 * w.listening - 0.03 * w.thinking, 0.04 * w.thinking, 0);
    b.head?.rotation.set(
      // idle drift; listening: chin slightly down; thinking: up and away; speaking: moves with the voice
      Math.sin(t * 0.71) * 0.015 + 0.03 * w.listening - 0.07 * w.thinking + (this.mouth * 0.04 + Math.sin(t * 2.1) * 0.012) * w.speaking,
      Math.sin(t * 0.47) * 0.03 + 0.12 * w.thinking,
      Math.sin(t * 0.53) * 0.012 + 0.05 * w.listening + 0.09 * w.thinking,
    );

    // arms down from the T-pose (the model faces +Z, so its left arm points along +X)
    const lift = breath * 0.008;
    b.leftShoulder?.rotation.set(0, 0, -0.05 - lift);
    b.rightShoulder?.rotation.set(0, 0, 0.05 + lift);
    b.leftUpperArm?.rotation.set(0.05, 0, -ARM_DOWN);
    b.rightUpperArm?.rotation.set(0.05, 0, ARM_DOWN);
    b.leftLowerArm?.rotation.set(0, -0.22, 0);
    b.rightLowerArm?.rotation.set(0, 0.22, 0);
    b.leftHand?.rotation.set(0, 0, -0.08);
    b.rightHand?.rotation.set(0, 0, 0.08);

    // eyes: on the viewer, except while thinking
    const cam = this.camera.position;
    this.lookGoal.set(cam.x + 0.9 * w.thinking, cam.y + 0.5 * w.thinking, cam.z);
    this.lookTarget.position.lerp(this.lookGoal, ease(6, dt));
  }

  /** Face: emotion cross-fade, blinking, mouth. */
  express(dt) {
    const face = this.vrm.expressionManager;
    if (!face) return;

    for (const name of EMOTIONS) {
      const goal = this.emotion.name === name ? this.emotion.strength : 0;
      this.face[name] += (goal - this.face[name]) * ease(6, dt);
      face.setValue(name, this.face[name]);
    }

    const blink = this.blink;
    let lids = 0;
    if (blink.at < 0) {
      blink.wait -= dt;
      if (blink.wait <= 0) blink.at = 0;
    } else {
      blink.at += dt / 0.17;  // one blink takes 0.17 s
      if (blink.at >= 1) {
        blink.at = -1;
        blink.wait = Math.random() < 0.15 ? 0.25 : 2 + Math.random() * 4;  // sometimes a double blink
      } else {
        lids = 1 - Math.abs(blink.at * 2 - 1);
      }
    }
    // VRoid's happy face already closes the eyes and this model lets expressions add up, so a blink on top overshoots
    face.setValue("blink", lids * (1 - Math.min(1, this.face.happy * 1.5)));

    if (this.audio) this.mouthTarget = this.audio.level();
    // open fast, close a little slower: follows syllables without chattering
    this.mouth += (this.mouthTarget - this.mouth) * ease(this.mouthTarget > this.mouth ? 32 : 14, dt);
    face.setValue("aa", this.mouth * (1 - 0.5 * this.face.happy));  // the happy face already opens the mouth

    for (const [name, value] of this.overrides) face.setValue(name, value);
  }
}
