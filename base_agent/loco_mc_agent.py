"""
Copyright (c) Facebook, Inc. and its affiliates.
"""
import sys

import logging
import random
import re
import time
import numpy as np

from core import BaseAgent
from dlevent import sio

from base_util import hash_user
from save_and_fetch_commands import *

random.seed(0)

DATABASE_FILE_FOR_DASHBOARD = "dashboard_data.db"
DEFAULT_BEHAVIOUR_TIMEOUT = 20
# a BaseAgent with:
# 1: a controller that is (mostly) a dialogue manager, and the dialogue manager
#      is powered by a neural semantic parser.
# 2: has a turnable head, can point, and has basic locomotion
# 3: can send and receive chats

# this name is pathetic please help
class LocoMCAgent(BaseAgent):
    def __init__(self, opts, name=None):
        logging.info("Agent.__init__ started")
        self.name = name or default_agent_name()
        self.opts = opts
        self.init_physical_interfaces()
        super(LocoMCAgent, self).__init__(opts, name=self.name)
        self.uncaught_error_count = 0
        self.last_chat_time = 0
        self.last_task_memid = None
        self.areas_to_perceive = []
        self.perceive_during_step = False
        self.dashboard_memory = {
            "objects": [],
            "humans": [],
            "chatResponse": {},
            "chats": [
                {"msg": "", "failed": False},
                {"msg": "", "failed": False},
                {"msg": "", "failed": False},
                {"msg": "", "failed": False},
                {"msg": "", "failed": False},
            ],
        }

    def init_event_handlers(self):
        ## emit event from statemanager and send dashboard memory from here
        # create a connection to database file
        logging.info("creating the connection to db file: %r" % (DATABASE_FILE_FOR_DASHBOARD))
        self.conn = create_connection(DATABASE_FILE_FOR_DASHBOARD)
        # create all tables if they don't already exist
        logging.info("creating all tables for Visual programming and error annotation ...")
        create_all_tables(self.conn)

        @sio.on("saveCommand")
        def save_command_to_db(sid, postData):
            print("in save_command_to_db, got postData: %r" % (postData))
            # save the command and fetch all
            out = saveAndFetchCommands(self.conn, postData)
            if out == "DUPLICATE":
                print("Duplicate command not saved.")
            else:
                print("Saved successfully")
            payload = {"commandList": out}
            sio.emit("updateSearchList", payload)

        @sio.on("fetchCommand")
        def get_cmds_from_db(sid, postData):
            print("in get_cmds_from_db, got postData: %r" % (postData))
            out = onlyFetchCommands(self.conn, postData["query"])
            payload = {"commandList": out}
            sio.emit("updateSearchList", payload)

        @sio.on("saveErrorDetailsToDb")
        def save_error_details_to_db(sid, postData):
            logging.info("in save_error_details_to_db, got PostData: %r" % (postData))
            # save the details to table
            saveAnnotatedErrorToDb(self.conn, postData)

        @sio.on("saveSurveyInfo")
        def save_survey_info_to_db(sid, postData):
            logging.info("in save_survey_info_to_db, got PostData: %r" % (postData))
            # save details to survey table
            saveSurveyResultsToDb(self.conn, postData)

        @sio.on("saveObjectAnnotation")
        def save_object_annotation_to_db(sid, postData):
            logging.info("in save_object_annotation_to_db, got postData: %r" % (postData))
            saveObjectAnnotationsToDb(self.conn, postData)


    def init_physical_interfaces(self):
        """
        should define or otherwise set up
        (at least):
        self.send_chat(),
        movement primitives, including
        self.look_at(x, y, z):
        self.set_look(look):
        self.point_at(...),
        self.relative_head_pitch(angle)
        ...
        """
        raise NotImplementedError

    def init_perception(self):
        """
        should define (at least):
        self.get_pos()
        self.get_incoming_chats()
        and the perceptual modules that write to memory
        all modules that should write to memory on a perceive() call
        should be registered in self.perception_modules, and have
        their own .perceive() fn
        """
        raise NotImplementedError

    def init_memory(self):
        """ something like:
        self.memory = memory.AgentMemory(
            db_file=os.environ.get("DB_FILE", ":memory:"),
            db_log_path="agent_memory.{}.log".format(self.name),
        )
        """
        raise NotImplementedError

    def init_controller(self):
        """
        dialogue_object_classes["interpreter"] = ....
        dialogue_object_classes["get_memory"] = ....
        dialogue_object_classes["put_memory"] = ....
        self.dialogue_manager = NSPDialogueManager(self,
                                                   dialogue_object_classes,
                                                   self.opts)
        logging.info("Initialized DialogueManager")
        """
        raise NotImplementedError

    def handle_exception(self, e):
        logging.exception(
            "Default handler caught exception, db_log_idx={}".format(self.memory.get_db_log_idx())
        )
        self.send_chat("Oops! I got confused and wasn't able to complete my last task :(")
        self.memory.task_stack_clear()
        self.dialogue_manager.dialogue_stack.clear()
        self.uncaught_error_count += 1
        if self.uncaught_error_count >= 100:
            sys.exit(1)

    def step(self):
        if self.count == 0:
            logging.info("First top-level step()")
        super().step()

    def task_step(self, sleep_time=0.25):
        # Clean finished tasks
        while (
            self.memory.task_stack_peek() and self.memory.task_stack_peek().task.check_finished()
        ):
            self.memory.task_stack_pop()

        # If nothing to do, wait a moment
        if self.memory.task_stack_peek() is None:
            time.sleep(sleep_time)
            return

        # If something to do, step the topmost task
        task_mem = self.memory.task_stack_peek()
        if task_mem.memid != self.last_task_memid:
            logging.info("Starting task {}".format(task_mem.task))
            self.last_task_memid = task_mem.memid
        task_mem.task.step(self)
        self.memory.task_stack_update_task(task_mem.memid, task_mem.task)

    def get_time(self):
        # round to 100th of second, return as
        # n hundreth of seconds since agent init
        return self.memory.get_time()

    def perceive(self, force=False):
        for v in self.perception_modules.values():
            v.perceive(force=force)

    def controller_step(self):
        """Process incoming chats and modify task stack"""
        raw_incoming_chats = self.get_incoming_chats()
        if raw_incoming_chats:
            # force to get objects
            self.perceive(force=True)
            logging.info("Incoming chats: {}".format(raw_incoming_chats))

        incoming_chats = []
        for raw_chat in raw_incoming_chats:
            match = re.search("^<([^>]+)> (.*)", raw_chat)
            if match is None:
                logging.info("Ignoring chat: {}".format(raw_chat))
                continue

            speaker, chat = match.group(1), match.group(2)
            speaker_hash = hash_user(speaker)
            logging.info("Incoming chat: ['{}' -> {}]".format(speaker_hash, chat))
            if chat.startswith("/"):
                continue
            incoming_chats.append((speaker, chat))
            self.memory.add_chat(self.memory.get_player_by_name(speaker).memid, chat)

        if len(incoming_chats) > 0:
            # force to get objects, speaker info
            if self.perceive_during_step:
                self.perceive(force=True)
            # change this to memory.get_time() format?
            self.last_chat_time = time.time()
            # for now just process the first incoming chat
            self.dialogue_manager.step(incoming_chats[0])
        else:
            # Maybe add default task
            if not self.no_default_behavior:
                self.maybe_run_slow_defaults()
            self.dialogue_manager.step((None, ""))

    def maybe_run_slow_defaults(self):
        """Pick a default task task to run
        with a low probability"""
        if self.memory.task_stack_peek() or len(self.dialogue_manager.dialogue_stack) > 0:
            return

        # default behaviors of the agent not visible in the game
        invisible_defaults = []

        defaults = (
            self.visible_defaults + invisible_defaults
            if time.time() - self.last_chat_time > DEFAULT_BEHAVIOUR_TIMEOUT
            else invisible_defaults
        )

        defaults = [(p, f) for (p, f) in defaults if f not in self.memory.banned_default_behaviors]

        def noop(*args):
            pass

        defaults.append((1 - sum(p for p, _ in defaults), noop))  # noop with remaining prob

        # weighted random choice of functions
        p, fns = zip(*defaults)
        fn = np.random.choice(fns, p=p)
        if fn != noop:
            logging.info("Default behavior: {}".format(fn))
        fn(self)


def default_agent_name():
    """Use a unique name based on timestamp"""
    return "bot.{}".format(str(time.time())[3:13])
