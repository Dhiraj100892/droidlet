"""
Copyright (c) Facebook, Inc. and its affiliates.
"""

from typing import Tuple, Dict, Any, Optional

from base_agent.memory_nodes import PlayerNode
from base_agent.dialogue_objects import (
    AGENTPOS,
    ConditionInterpreter,
    interpret_point_target,
    get_repeat_num,
    Interpreter,
    AttributeInterpreter,
)

from base_agent.base_util import ErrorWithResponse
from .spatial_reasoning import ComputeLocations
from .facing_helper import FacingInterpreter

import dance
import tasks


def post_process_loc(loc, interpreter):
    return (loc[0], loc[2], interpreter.agent.mover.get_base_pos()[2])


def add_default_locs(interpreter):
    interpreter.default_frame = "AGENT"
    interpreter.default_loc = AGENTPOS


class LocoInterpreter(Interpreter):
    """This class handles processes incoming chats and modifies the task stack.

    Handlers should add/remove/reorder tasks on the stack, but not
    execute them.
    """

    def __init__(self, speaker: str, action_dict: Dict, **kwargs):
        super().__init__(speaker, action_dict, **kwargs)
        self.speaker = speaker
        self.action_dict = action_dict
        self.provisional: Dict = {}
        self.action_dict_frozen = False
        self.loop_data = None
        self.archived_loop_data = None
        self.default_debug_path = "debug_interpreter.txt"
        self.post_process_loc = post_process_loc
        add_default_locs(self)

        # FIXME!
        self.workspace_memory_prio = []  # noqa

        self.subinterpret["attribute"] = AttributeInterpreter()
        self.subinterpret["condition"] = ConditionInterpreter()
        self.subinterpret["specify_locations"] = ComputeLocations()
        self.subinterpret["facing"] = FacingInterpreter()

        self.action_handlers["DANCE"] = self.handle_dance
        self.action_handlers["GET"] = self.handle_get
        self.action_handlers["DROP"] = self.handle_drop

        self.task_objects = {
            "move": tasks.Move,
            "look": tasks.Look,
            "dance": tasks.Dance,
            "point": tasks.Point,
            "turn": tasks.Turn,
            "autograsp": tasks.AutoGrasp,
            "loop": tasks.Loop,
            "get": tasks.Get,
            "drop": tasks.Drop,
        }

    def handle_get(self, speaker, d) -> Tuple[Optional[str], Any]:
        default_ref_d = {"filters": {"location": AGENTPOS}}
        ref_d = d.get("reference_object", default_ref_d)
        objs = self.subinterpret["reference_objects"](
            self, speaker, ref_d, extra_tags=["_physical_object"]
        )
        if len(objs) == 0:
            raise ErrorWithResponse("I don't know what you want me to get.")

        if all(isinstance(obj, PlayerNode) for obj in objs):
            raise ErrorWithResponse("I can't get a person, sorry!")
        objs = [obj for obj in objs if not isinstance(obj, PlayerNode)]

        if d.get("receiver") is None:
            receiver_d = None
        else:
            receiver_d = d.get("receiver").get("reference_object")
        receiver = None
        if receiver_d:
            receiver = self.subinterpret["reference_objects"](self, speaker, receiver_d)
            if len(receiver) == 0:
                raise ErrorWithResponse("I don't know where you want me to take it")
            receiver = receiver[0].memid

        num_get_tasks = 0
        for obj in objs:
            task_data = {"get_target": obj.memid, "give_target": receiver, "action_dict": d}
            self.append_new_task(self.task_objects["get"], task_data)
            num_get_tasks += 1
        #        logging.info("Added {} Get tasks to stack".format(num_get_tasks))
        self.finished = True
        return None, None

    def handle_dance(self, speaker, d) -> Tuple[Optional[str], Any]:
        def new_tasks():
            repeat = get_repeat_num(d)
            tasks_to_do = []
            # only go around the x has "around"; FIXME allow other kinds of dances
            location_d = d.get("location")
            if location_d is not None:
                rd = location_d.get("relative_direction")
                if rd is not None and (
                    rd == "AROUND" or rd == "CLOCKWISE" or rd == "ANTICLOCKWISE"
                ):
                    ref_obj = None
                    location_reference_object = location_d.get("reference_object")
                    if location_reference_object:
                        objmems = self.subinterpret["reference_objects"](
                            self, speaker, location_reference_object
                        )
                        if len(objmems) == 0:
                            raise ErrorWithResponse("I don't understand where you want me to go.")
                        ref_obj = objmems[0]
                    for i in range(repeat):
                        refmove = dance.RefObjMovement(
                            self.agent,
                            ref_object=ref_obj,
                            relative_direction=location_d["relative_direction"],
                        )
                        t = self.task_objects["dance"](self.agent, {"movement": refmove})
                        tasks_to_do.append(t)
                    return list(reversed(tasks_to_do))

            dance_type = d.get("dance_type", {"dance_type_name": "dance"})
            if dance_type.get("point"):
                target = interpret_point_target(self, speaker, dance_type["point"])
                for i in range(repeat):
                    t = self.task_objects["point"](self.agent, {"target": target})
                    tasks_to_do.append(t)
            elif dance_type.get("look_turn") or dance_type.get("body_turn"):
                lt = dance_type.get("look_turn")
                if lt:
                    f = self.subinterpret["facing"](self, speaker, lt, head_or_body="head")
                    T = self.task_objects["look"]
                else:
                    bt = dance_type.get("body_turn")
                    f = self.subinterpret["facing"](self, speaker, bt, head_or_body="body")
                    T = self.task_objects["turn"]
                for i in range(repeat):
                    tasks_to_do.append(T(self.agent, f))
            elif dance_type["dance_type_name"] == "wave":
                new_task = self.task_objects["dance"](self.agent, {"movement_type": "wave"})
                tasks_to_do.append(new_task)
            else:
                # FIXME ! merge dances, refactor.  search by name in sql
                raise ErrorWithResponse("I don't know how to do that dance yet!")
            return list(reversed(tasks_to_do))

        if "stop_condition" in d:
            condition = self.subinterpret["stop_condition"](self, speaker, d["stop_condition"])
            self.append_new_task(
                self.task_objects["loop"],
                {"new_tasks_fn": new_tasks, "conditions": condition, "action_dict": d},
            )
        else:
            for t in new_tasks():
                self.append_new_task(t)

        self.finished = True
        return None, None

    def handle_drop(self, speaker, d) -> Tuple[Optional[str], Any]:
        """
        Drops whatever object in hand
        """
        task_data = {"action_dict": d}
        self.append_new_task(self.task_objects["drop"], task_data)
        self.finished = True
        return None, None
