"""
Copyright (c) Facebook, Inc. and its affiliates.
"""

"""This file has functions to implement different dances for the agent.
"""
import tasks
import time
import math

# import search
# from base_util import ErrorWithResponse


konami_dance = [
    {"translate": (0, 1, 0)},
    {"translate": (0, 1, 0)},
    {"translate": (0, -1, 0)},
    {"translate": (0, -1, 0)},
    {"translate": (0, 0, -1)},
    {"translate": (0, 0, 1)},
    {"translate": (0, 0, -1)},
    {"translate": (0, 0, 1)},
]

# TODO relative to current
head_bob = [
    {"head_yaw_pitch": (0, 0)},
    {"head_yaw_pitch": (0, 0)},
    {"head_yaw_pitch": (0, 90)},
    {"head_yaw_pitch": (0, 90)},
    {"head_yaw_pitch": (0, 0)},
    {"head_yaw_pitch": (0, 0)},
    {"head_yaw_pitch": (0, 90)},
    {"head_yaw_pitch": (0, 90)},
    {"head_yaw_pitch": (0, 0)},
    {"head_yaw_pitch": (0, 0)},
    {"head_yaw_pitch": (0, 90)},
    {"head_yaw_pitch": (0, 90)},
    {"head_yaw_pitch": (0, 0)},
    {"head_yaw_pitch": (0, 0)},
    {"head_yaw_pitch": (0, 90)},
    {"head_yaw_pitch": (0, 90)},
    {"head_yaw_pitch": (0, 0)},
    {"head_yaw_pitch": (0, 0)},
    {"head_yaw_pitch": (0, 90)},
    {"head_yaw_pitch": (0, 90)},
    {"head_yaw_pitch": (0, 0)},
    {"head_yaw_pitch": (0, 0)},
    {"head_yaw_pitch": (0, 90)},
    {"head_yaw_pitch": (0, 90)},
    {"head_yaw_pitch": (0, 0)},
    {"head_yaw_pitch": (0, 0)},
]


def add_default_dances(memory):
    memory.add_dance(generate_sequential_move_fn(konami_dance), name="konami_dance")
    memory.add_dance(generate_sequential_move_fn(head_bob), name="head_bob")


def generate_sequential_move_fn(sequence):
    def move_fn(danceObj, agent):
        if danceObj.tick >= len(sequence):
            return None
        else:
            if danceObj.dance_location is not None and danceObj.tick == 0:
                mv = tasks.Move(agent, {"target": danceObj.dance_location, "approx": 0})
                danceObj.dance_location = None
            else:
                mv = tasks.DanceMove(agent, sequence[danceObj.tick])
                danceObj.tick += 1
        return mv

    return move_fn


class DanceMovement(object):
    def __init__(self, agent, move_fn, dance_location=None):
        self.agent = agent
        self.bot = agent.mover.bot
        self.move_fn = move_fn
        self.dance_location = dance_location
        self.tick = 0

    def wave(self):
        for _ in range(3):
            self.bot.set_joint_positions([0.4, 0.0, -1, 0.5, -0.1], plan=False)
            while not self.bot.command_finished():
                time.sleep(0.5)
            self.bot.set_joint_positions([-0.4, 0.0, -1, -0.5, -0.1])
            while not self.bot.command_finished():
                time.sleep(0.5)
        self.bot.set_joint_positions([0.0, -math.pi / 4.0, math.pi / 2.0, 0.0, 0.0], plan=False)

    def get_move(self):
        # move_fn should output a tuple (dx, dy, dz) corresponding to a
        # change in Movement or None
        # if None then Movement is finished
        # can output
        return self.move_fn(self, self.agent)


# TODO head bob

# class HeadTurnInstant(Movement):


# TODO: class TimedDance(Movement): !!!!


# # e.g. go around the x
# #     go through the x
# #     go over the x
# #     go across the x
# class RefObjMovement(Movement):
#     def __init__(
#         self,
#         agent,
#         ref_object=None,
#         relative_direction="CLOCKWISE",  # this is the memory of the object
#     ):
#         self.agent = agent
#         self.tick = 0
#         if ref_object is None or ref_object == "AGENT_POS":
#             x, y, z = agent.pos
#             bounds = (x, x, y, y, z, z)
#             center = (x, y, z)
#         else:
#             bounds = ref_object.get_bounds()
#             center = ref_object.get_pos()
#         d = max(bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4])
#         if relative_direction == "CLOCKWISE":
#             offsets = shapes.arrange(
#                 "circle", schematic=None, shapeparams={"encircled_object_radius": d}
#             )
#         elif relative_direction == "ANTICLOCKWISE":
#             offsets = shapes.arrange(
#                 "circle", schematic=None, shapeparams={"encircled_object_radius": d}
#             )
#             offsets = offsets[::-1]
#         else:
#             raise NotImplementedError("TODO other kinds of paths")
#         self.path = [np.round(np.add(center, o)) for o in offsets]
#         self.path.append(self.path[0])

#         # check each offset to find a nearby reachable point, see if a path
#         # is possible now, and error otherwise

#         for i in range(len(self.path) - 1):
#             path = search.astar(agent, self.path[i + 1], approx=2, pos=self.path[i])
#             if path is None:
#                 raise ErrorWithResponse("I cannot find an appropriate path.")

#     def get_move(self):
#         if self.tick >= len(self.path):
#             return None
#         mv = tasks.Move(self.agent, {"target": self.path[self.tick], "approx": 2})
#         self.tick += 1
#         return mv
