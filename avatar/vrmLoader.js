// Loads a .vrm (VRM 0.x or 1.0, e.g. a VRoid Studio export) into a three-vrm VRM instance.
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";

export async function loadVRM(url) {
  const loader = new GLTFLoader();
  loader.register(parser => new VRMLoaderPlugin(parser));
  const gltf = await loader.loadAsync(url);
  const vrm = gltf.userData.vrm;
  if (!vrm) throw new Error(`${url} has no VRM data (is it a .vrm file?)`);

  // fewer vertices and one skeleton: less work per frame
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.combineSkeletons(gltf.scene);
  // VRM 0.x models face away from the camera; this turns them round and does nothing for VRM 1.0
  VRMUtils.rotateVRM0(vrm);
  // hair and clothes move outside their original bounds, so the default culling would make them flicker
  vrm.scene.traverse(obj => { obj.frustumCulled = false; });
  return vrm;
}

export function disposeVRM(vrm) {
  vrm.scene.removeFromParent();
  VRMUtils.deepDispose(vrm.scene);
}
