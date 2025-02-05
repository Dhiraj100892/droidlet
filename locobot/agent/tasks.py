"""
Copyright (c) Facebook, Inc. and its affiliates.
"""

import numpy as np
import logging
from base_agent.task import Task
from locobot.agent.dance import DanceMovement
from rotation import yaw_pitch
import time

# from locobot_mover_utils import CAMERA_HEIGHT


# tasks should be interruptible; that is, if they
# store state, stopping the task and doing something
# else should not mess up their state and just the
# current state should be enough to do the task from
# any ob


class Dance(Task):
    def __init__(self, agent, task_data, featurizer=None):
        super(Dance, self).__init__()
        # movement should be a Movement object from dance.py
        self.movement = DanceMovement(agent, None)
        self.movement_type = task_data.get("movement_type", None)

    def step(self, agent):
        self.interrupted = False

        if self.movement_type == "wave":
            self.movement.wave()

        elif not self.movement:  # default move
            mv = Move(agent, {"target": [-1000, -1000, -1000], "approx": 2})
            agent.memory.task_stack_push(mv, parent_memid=self.memid)


#### TODO, FIXME!:
#### merge Look, Point, Turn into dancemove; on mc side too
class Look(Task):
    def __init__(self, agent, task_data):
        super(Look, self).__init__()
        self.target = task_data.get("target")
        self.yaw = task_data.get("yaw")
        self.pitch = task_data.get("pitch")
        assert self.yaw or self.pitch or self.target
        self.command_sent = False

    def step(self, agent):
        self.finished = False
        self.interrupted = False
        if self.target:
            logging.info(f"calling bot to look at location {self.target}")
        if self.pitch:
            logging.info(f"calling bot to shift pitch {self.pitch}")
        if self.yaw:
            logging.info(f"calling bot to shift yaw {self.yaw}")
        if not self.command_sent:
            status = agent.mover.look_at(self.target, self.yaw, self.pitch)
            self.command_sent = True
            if status == "finished":
                self.finished = True
        else:
            self.finished = agent.mover.bot_step()

    def __repr__(self):
        if self.target:
            return "<Look at {} {} {}>".format(self.target[0], self.target[1], self.target[2])
        else:
            return "<Look at {} {}>".format(self.pitch, self.yaw)


class Point(Task):
    def __init__(self, agent, task_data):
        super(Point, self).__init__()
        self.target = np.array(task_data["target"])

    def step(self, agent):
        self.interrupted = False
        logging.info(f"calling bot to look at a point {self.target.tolist()}")
        status = agent.mover.point_at([self.target.tolist()])
        if status == "finished":
            self.finished = True

    def __repr__(self):
        return "<Point at {} ±{}>".format(self.target, self.approx)


class Move(Task):
    def __init__(self, agent, task_data, featurizer=None):
        super(Move, self).__init__()
        self.target = np.array(task_data["target"])
        self.is_relative = task_data.get("is_relative", 0)
        self.path = None
        self.command_sent = False

    def step(self, agent):
        self.interrupted = False
        self.finished = False
        if not self.command_sent:
            logging.info("calling move with : %r" % (self.target.tolist()))
            self.command_sent = True
            if self.is_relative:
                agent.mover.move_relative([self.target.tolist()])
            else:
                agent.mover.move_absolute([self.target.tolist()])

        else:
            self.finished = agent.mover.bot_step()

    # FIXME FOR HACKATHON
    def handle_no_path(self, agent):
        pass

    def __repr__(self):
        return "<Move {}>".format(self.target)


class Loop(Task):
    def __init__(self, agent, task_data):
        super(Loop, self).__init__()
        self.new_tasks_fn = task_data["new_tasks_fn"]
        self.stop_condition = task_data["stop_condition"]

    def step(self, agent):
        if self.stop_condition.check():
            self.finished = True
            return
        else:
            for t in self.new_tasks_fn():
                agent.memory.task_stack_push(t, parent_memid=self.memid)


class Turn(Task):
    def __init__(self, agent, task_data):
        super(Turn, self).__init__()
        self.yaw = task_data["yaw"]
        self.command_sent = False

    def step(self, agent):
        self.interrupted = False
        self.finished = False
        if not self.command_sent:
            self.command_sent = True
            agent.mover.turn(self.yaw)
        else:
            self.finished = agent.mover.bot_step()

    def __repr__(self):
        return "<Turn {} degrees>".format(self.yaw)


# TODO handle case where agent already has item in inventory (pure give)
class Get(Task):
    def __init__(self, agent, task_data):
        super().__init__()
        # get target should be a ReferenceObjectNode memid
        self.get_target = task_data["get_target"]
        self.give_target = task_data["give_target"]
        # steps take values "not_started", "started", "complete"
        if not self.give_target:
            # TODO all movements simultaneous- change look while driving
            # approach_pickup, look_at_object, grab
            self.steps = ["not_started"] * 3
        else:
            # approach_pickup, look_at_object, grab, approach_dropoff, give/drop
            self.steps = ["not_started"] * 5

    def get_mv_target(self, agent, get_or_give="get", end_distance=0.35):
        """figure out the location where agent should move to in order to get or give object in global frame
        all units are in metric unit

        Args:
            get_or_give (str, optional): whether to get or give object. Defaults to "get".
            end_distance (float, optional): stand end_distance away from the goal in meter. Defaults to 0.35.

        Returns:
            [tuple]: (x,y,theta) location the agent should move to, in global co-ordinate system
        """
        agent_pos = np.array(agent.mover.get_base_pos())[:2]
        if get_or_give == "get":
            target_memid = self.get_target
        else:
            target_memid = self.give_target
        target_pos = agent.memory.get_mem_by_id(target_memid).get_pos()
        target_pos = np.array((target_pos[0], target_pos[2]))
        diff = target_pos - agent_pos
        distance = np.linalg.norm(diff)
        # FIXME add a check to make sure not already there
        xz = agent_pos + (distance - end_distance) * diff / distance
        # TODO: Check if yaw s right
        target_yaw = np.arctan2(diff[1], diff[0])
        received_yaw = False
        while not received_yaw:
            try:
                target_yaw += agent.mover.get_base_pos()[2]
                received_yaw = True
            except:
                time.sleep(0.1)
        return (xz[0], xz[1], target_yaw)

    def step(self, agent):
        self.interrupted = False
        self.finished = False
        # move to object to be picked up
        if self.steps[0] == "not_started":
            # check if already holding target object for pure give, when object is grasped
            # its added to memory with tag "_in_inventory"
            if self.get_target in agent.memory.get_memids_by_tag("_in_inventory"):
                self.steps[0] = "finished"
                self.steps[1] = "finished"
                self.steps[2] = "finished"
            else:
                target = self.get_mv_target(agent, get_or_give="get")
                self.add_child_task(Move(agent, {"target": target}), agent)
                # TODO a loop?  otherwise check location/graspability instead of just assuming?
                self.steps[0] = "finished"
            return
        # look at the object directly
        if self.steps[0] == "finished" and self.steps[1] == "not_started":
            target_pos = agent.memory.get_mem_by_id(self.get_target).get_pos()
            self.add_child_task(Look(agent, {"target": target_pos}), agent)
            self.steps[1] = "finished"
            return
        # grab it
        if self.steps[1] == "finished" and self.steps[2] == "not_started":
            self.add_child_task(AutoGrasp(agent, {"target": self.get_target}), agent)
            self.steps[2] = "finished"
            return
        if len(self.steps) == 3:
            self.finished = True
            return
        # go to the place where you are supposed to drop off the item
        if self.steps[3] == "not_started":
            target = self.get_mv_target(agent, get_or_give="give")
            self.add_child_task(Move(agent, {"target": target}), agent)
            # TODO a loop?  otherwise check location/graspability instead of just assuming?
            self.steps[3] = "finished"
            return
        # drop it
        if self.steps[3] == "finished":
            self.add_child_task(Drop(agent, {"object": self.get_target}), agent)
            self.finished = True
            return

    def __repr__(self):
        return "<get {}>".format(self.get_target)


class AutoGrasp(Task):
    """thin wrapper for Dhiraj' grasping routine."""

    def __init__(self, agent, task_data):
        super().__init__()
        # this is a ref object memid
        self.target = task_data["target"]
        self.command_sent = False

    def step(self, agent):
        self.interrupted = False
        self.finished = False
        if not self.command_sent:
            self.command_sent = True
            agent.mover.grab_nearby_object()
        else:
            self.finished = agent.mover.bot_step()
            # TODO check that the object in the gripper is actually the object we meant to pick up
            # TODO deal with failure cases
            # TODO tag this in the grip task, not here
            if self.finished:
                if agent.mover.is_object_in_gripper():
                    agent.memory.tag(self.target, "_in_inventory")


class Drop(Task):
    """drop whatever is in hand."""

    def __init__(self, agent, task_data):
        super().__init__()
        # currently unused, we can expand this soon?
        self.object_to_drop = task_data.get("object", None)
        self.command_sent = False

    def step(self, agent):
        self.interrupted = False
        self.finished = False
        if not self.command_sent:
            logging.info("Dropping the object in hand")
            self.command_sent = True
            agent.mover.drop()
        else:
            self.finished = agent.mover.bot_step() and not agent.mover.is_object_in_gripper()
            if self.finished:
                agent.memory.untag(self.object_to_drop, "_in_inventory")
                if self.object_to_drop is None:
                    # assumed there is only one object with tag "_in_inventory"
                    for mmid in agent.memory.get_memids_by_tag("_in_inventory"):
                        agent.memory.untag(mmid, "_in_inventory")


class Explore(Task):
    """use slam to explore environemt    """

    def __init__(self, agent, task_data):
        super().__init__()
        self.command_sent = False

    def step(self, agent):
        self.interrupted = False
        self.finished = False
        if not self.command_sent:
            self.command_sent = True
            agent.mover.explore()
        else:
            self.finished = agent.mover.bot_step()
