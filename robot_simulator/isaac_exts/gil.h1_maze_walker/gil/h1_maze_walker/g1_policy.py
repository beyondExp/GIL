from typing import Optional

import numpy as np
from isaacsim.core.utils.rotations import quat_to_rot_matrix
from isaacsim.core.utils.types import ArticulationAction
from isaacsim.robot.policy.examples.controllers import PolicyController
from isaacsim.storage.native import get_assets_root_path

_POLICY_CANDIDATES = (
    ("/Isaac/Samples/Policies/G1_Policies/g1_policy.pt", "/Isaac/Samples/Policies/G1_Policies/g1_env.yaml"),
    ("/Isaac/Samples/Policies/G1_Policies/policy.pt", "/Isaac/Samples/Policies/G1_Policies/env.yaml"),
)


class G1FlatTerrainPolicy(PolicyController):
    """Unitree G1 locomotion if Isaac ships a G1 policy on Nucleus (not in all 5.1 builds)."""

    def __init__(
        self,
        prim_path: str,
        root_path: Optional[str] = None,
        name: str = "g1",
        usd_path: Optional[str] = None,
        position: Optional[np.ndarray] = None,
        orientation: Optional[np.ndarray] = None,
    ) -> None:
        assets_root_path = get_assets_root_path()
        if usd_path is None:
            usd_path = assets_root_path + "/Isaac/Robots/Unitree/G1_23dof/g1.usd"
        super().__init__(name, prim_path, root_path, usd_path, position, orientation)
        last_err: Exception | None = None
        loaded = False
        for rel_pt, rel_yaml in _POLICY_CANDIDATES:
            try:
                self.load_policy(assets_root_path + rel_pt, assets_root_path + rel_yaml)
                loaded = True
                break
            except Exception as e:
                last_err = e
        if not loaded:
            raise RuntimeError(f"No G1 Nucleus policy found (last error: {last_err!r})")
        self._action_scale = 0.5
        self._previous_action = np.zeros(1)
        self._policy_counter = 0
        self._n = 1

    def initialize(self):
        out = super().initialize(set_articulation_props=False)
        n = int(np.asarray(self.default_pos).reshape(-1).shape[0])
        self._n = max(1, n)
        self._previous_action = np.zeros(self._n)
        return out

    def _compute_observation(self, command):
        lin_vel_I = self.robot.get_linear_velocity()
        ang_vel_I = self.robot.get_angular_velocity()
        pos_IB, q_IB = self.robot.get_world_pose()
        R_IB = quat_to_rot_matrix(q_IB)
        R_BI = R_IB.transpose()
        lin_vel_b = np.matmul(R_BI, lin_vel_I)
        ang_vel_b = np.matmul(R_BI, ang_vel_I)
        gravity_b = np.matmul(R_BI, np.array([0.0, 0.0, -1.0]))
        n = self._n
        obs = np.zeros(12 + 3 * n)
        obs[:3] = lin_vel_b
        obs[3:6] = ang_vel_b
        obs[6:9] = gravity_b
        obs[9:12] = command
        current_joint_pos = self.robot.get_joint_positions()
        current_joint_vel = self.robot.get_joint_velocities()
        obs[12 : 12 + n] = current_joint_pos[:n] - self.default_pos[:n]
        obs[12 + n : 12 + 2 * n] = current_joint_vel[:n]
        prev = self._previous_action
        obs[12 + 2 * n : 12 + 3 * n] = prev[:n]
        return obs

    def forward(self, dt, command):
        if self._policy_counter % self._decimation == 0:
            obs = self._compute_observation(command)
            self.action = self._compute_action(obs)
            self._previous_action = np.asarray(self.action).reshape(-1)[: self._n].copy()
        action = ArticulationAction(joint_positions=self.default_pos + (self.action * self._action_scale))
        self.robot.apply_action(action)
        self._policy_counter += 1
