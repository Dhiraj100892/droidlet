"""
Copyright (c) Facebook, Inc. and its affiliates.
"""
import unittest

import craftassist.agent.shapes as shapes
from base_craftassist_test_case import BaseCraftassistTestCase
from base_agent.dialogue_stack import DialogueStack
from craftassist.agent.dialogue_objects import DummyInterpreter
import all_test_commands


def add_many_objects(test):
    # loc, size, material, time (in world steps~secs)
    cube_data = [
        [(-5, 63, 4), 3, (57, 0), 1],
        [(10, 63, 9), 5, (41, 0), 5],
        [(5, 63, 4), 4, (41, 0), 10],
    ]
    cube_triples = {"has_name": "cube", "has_shape": "cube"}
    test.shapes = [
        list(
            test.agent.add_object_ff_time(
                cd[3],
                xyzbms=shapes.cube(size=cd[1], bid=cd[2]),
                origin=cd[0],
                relations=cube_triples,
            ).blocks.items()
        )
        for cd in cube_data
    ]
    test.set_looking_at(test.shapes[0][0][0])


class FiltersTest(BaseCraftassistTestCase):
    def setUp(self):
        super().setUp()
        dummy_dialogue_stack = DialogueStack(self.agent, self.agent.memory)
        self.dummy_interpreter = DummyInterpreter(
            "SPEAKER",
            agent=self.agent,
            memory=self.agent.memory,
            dialogue_stack=dummy_dialogue_stack,
        )

        add_many_objects(self)

    def test_first_and_last(self):
        f = all_test_commands.FILTERS["number of blocks in the first thing built"]
        l = all_test_commands.FILTERS["number of blocks in the last thing built"]
        DI = self.dummy_interpreter

        b = DI.subinterpret["filters"](DI, "SPEAKER", f)
        mems, vals = b()
        self.assertEqual(len(vals), 1)
        self.assertEqual(vals[0], 27)

        b = DI.subinterpret["filters"](DI, "SPEAKER", l)
        mems, vals = b()
        self.assertEqual(len(vals), 1)
        self.assertEqual(vals[0], 64)


if __name__ == "__main__":
    unittest.main()
