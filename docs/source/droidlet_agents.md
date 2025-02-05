```eval_rst
.. _droidlet_agents:
```
# Agents
We instantiate a droidlet [agent](https://github.com/fairinternal/minecraft/blob/master/locobot/agent) on a [Locobot](http://www.locobot.org/) and an [agent](https://github.com/fairinternal/minecraft/blob/master/craftassist/agent) in Minecraft using the [Craftassist](https://arxiv.org/abs/1907.08584) framework (the droidlet project evolved from Craftassist).  

## Locobot ##

### Locobot Perception ###
```eval_rst
 .. autoclass:: locobot.agent.perception.Perception
   :members: perceive, setup_vision_handlers, log
 .. autoclass:: locobot.agent.perception.SlowPerception
   :members: perceive, setup_vision_handlers
```

### Locobot PyRobot interface ###
```eval_rst
 .. autoclass:: locobot.robot.RemoteLocobot
   :members: get_pcd_data, go_to_absolute, go_to_relative, get_base_state, set_joint_positions, set_joint_velocities, set_ee_pose, move_ee_xyz, open_gripper, close_gripper, get_gripper_state, get_end_eff_pose, get_joint_positions, get_joint_velocities, get_depth, get_depth_bytes, get_intrinsics, get_rgb, get_rgb_bytes, transform_pose, get_current_pcd, pix_to_3dpt, dip_pix_to_3dpt, get_transform, get_pan, get_camera_state, get_tilt, reset, set_pan, set_pan_tilt, set_tilt, grasp, explore
```

## Craftassist ##
Details for setting up and running the Cuberite server and Craftassist agent are [here](https://github.com/fairinternal/minecraft/blob/master/craftassist/README.md)

The craftassist perception modules are mostly heuristic.  A model for semantic segmentation is [here](https://github.com/fairinternal/minecraft/tree/master/craftassist/agent/voxel_models/detection-transformer/)
