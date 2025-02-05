"""
Copyright (c) Facebook, Inc. and its affiliates.
"""
import json
import logging
import os
import re
import spacy
from typing import Tuple, Dict, Optional
from glob import glob

import sentry_sdk

import preprocess

from base_agent.memory_nodes import ProgramNode
from base_agent.dialogue_manager import DialogueManager
from base_agent.dialogue_objects import (
    BotCapabilities,
    BotGreet,
    DialogueObject,
    Say,
    coref_resolve,
    process_spans,
)
from dlevent import sio
dirname = os.path.dirname(__file__)

from base_util import hash_user

sp = spacy.load("en_core_web_sm")


class NSPDialogueManager(DialogueManager):
    """Dialogue manager driven by neural network.

    Attributes:
        dialogue_objects (dict): Dictionary specifying the DialogueObject
            class for each dialogue type. Keys are dialogue types. Values are
            corresponding class names. Example dialogue objects:
            {'interpreter': MCInterpreter,
            'get_memory': GetMemoryHandler,
            'put_memory': ...
            }
        botCapabilityQuery (List[str]): Set of commands that trigger scripted responses.
        safety_words (List[str]): Set of blacklisted words or phrases. Commands
            containing these are automatically filtered out.
        botGreetings (dict): Different types of greetings that trigger
            scripted responses. Example:
            { "hello": ["hi bot", "hello"] }
        model (TTADBertModel): Semantic Parsing model that takes text as
            input and outputs a logical form.
            To use a new model here, ensure that the subfolder directory structure
            mirrors the current model/dataset directories.
            See :class:`TTADBertModel`.
        ground_truth_actions (dict): A key-value with ground truth logical forms.
            These will be queried first (via exact string match), before running the model.
        dialogue_object_parameters (dict): Set the parameters for dialogue objects.
            Sets the agent, memory and dialogue stack.

    Args:
        agent: a droidlet agent, eg. ``CraftAssistAgent``
        dialogue_object_classes (dict): Dictionary specifying the DialogueObject
            class for each dialogue type. See ``dialogue_objects`` definition above.
        opts (argparse.Namespace): Parsed command line arguments specifying parameters in agent.

            Args:
                --nsp_models_dir: Path to directory containing all files necessary to
                    load and run the model, including args, tree mappings and the checkpointed model.
                    Semantic parsing models used by current project are in ``ttad_bert_updated``.
                    eg. semantic parsing model is ``ttad_bert_updated/caip_test_model.pth``
                --nsp_data_dir: Path to directory containing all datasets used by the NSP model.
                    Note that this data is not used in inference, rather we load from the ground truth
                    data directory.
                --ground_truth_data_dir: Path to directory containing ground truth datasets
                    loaded by agent at runtime. Option to include a file for blacklisted words ``safety.txt``,
                    a class for greetings ``greetings.json`` and .txt files with text, logical_form pairs in
                    ``datasets/``.

            See :class:`ArgumentParser` for full list of command line options.

    """

    def __init__(self, agent, dialogue_object_classes, opts):
        super(NSPDialogueManager, self).__init__(agent, None)
        self.dialogue_objects = dialogue_object_classes
        self.botCapabilityQuery = [
            "what can you do",
            "what else can you do",
            "what do you know",
            "tell me what you can do",
            "what things can you do",
            "what are your capabilities",
            "show me what you can do",
            "what are you capable of",
            "help me",
            "help",
            "do something",
        ]
        safety_words_path = opts.ground_truth_data_dir + "safety.txt"
        if os.path.isfile(safety_words_path):
            self.safety_words = self.get_safety_words(safety_words_path)
        else:
            self.safety_words = []
        # Load bot greetings
        greetings_path = opts.ground_truth_data_dir + "greetings.json"
        if os.path.isfile(greetings_path):
            with open(greetings_path) as fd:
                self.botGreetings = json.load(fd)
        else:
            self.botGreetings = {"hello": ["hi", "hello", "hey"], "goodbye": ["bye"]}

        models_dir = opts.nsp_models_dir
        # Instantiate the main model
        ttad_model_dir = models_dir + "ttad_bert_updated/"
        logging.info("using model_dir={}".format(ttad_model_dir))
        if os.path.isdir(opts.nsp_data_dir) and os.path.isdir(ttad_model_dir):
            from ttad.ttad_transformer_model.query_model import TTADBertModel as Model

            self.model = Model(model_dir=ttad_model_dir, data_dir=opts.nsp_data_dir)
        self.debug_mode = False

        self.ground_truth_actions = {}
        if not opts.no_ground_truth:
            if os.path.isdir(opts.ground_truth_data_dir):
                files = glob(opts.ground_truth_data_dir + "datasets/*.txt")
                for dataset in files:
                    with open(dataset) as f:
                        for line in f.readlines():
                            text, logical_form = line.strip().split("|")
                            clean_text = text.strip('"')
                            self.ground_truth_actions[clean_text] = json.loads(logical_form)

        self.dialogue_object_parameters = {
            "agent": self.agent,
            "memory": self.agent.memory,
            "dialogue_stack": self.dialogue_stack,
        }

        @sio.on("queryParser")
        def query_parser(sid, data):
            logging.info("inside query parser.....")
            logging.info(data)
            x = self.get_logical_form(s=data["chat"], model=self.model)
            logging.info(x)
            payload = {"action_dict": x}
            sio.emit("renderActionDict", payload)

    def maybe_get_dialogue_obj(self, chat: Tuple[str, str]) -> Optional[DialogueObject]:
        """Process a chat and maybe modify the dialogue stack.

        Args:
            chat (Tuple[str, str]): Incoming chat from a player.
                Format is (speaker, chat), eg. ("player1", "build a red house")

        Returns:
            DialogueObject or empty if no action is needed.

        """

        if len(self.dialogue_stack) > 0 and self.dialogue_stack[-1].awaiting_response:
            return None

        # chat is a single line command
        speaker, chatstr = chat
        preprocessed_chatstrs = preprocess.preprocess_chat(chatstr)

        # Push appropriate DialogueObjects to stack if incoming chat
        # is one of the scripted ones
        if any([chat in self.botCapabilityQuery for chat in preprocessed_chatstrs]):
            return BotCapabilities(**self.dialogue_object_parameters)
        for greeting_type in self.botGreetings:
            if any([chat in self.botGreetings[greeting_type] for chat in preprocessed_chatstrs]):
                return BotGreet(greeting_type, **self.dialogue_object_parameters)

        # NOTE: preprocessing in model code is different, this shouldn't break anything
        logical_form = self.get_logical_form(s=preprocessed_chatstrs[0], model=self.model)
        return self.handle_logical_form(speaker, logical_form, preprocessed_chatstrs[0])

    def handle_logical_form(self, speaker: str, d: Dict, chatstr: str) -> Optional[DialogueObject]:
        """Return the appropriate DialogueObject to handle an action dict d
        d should have spans filled (via process_spans).
        """
        coref_resolve(self.agent.memory, d, chatstr)
        logging.info('logical form post-coref "{}" -> {}'.format(hash_user(speaker), d))
        ProgramNode.create(self.agent.memory, d)

        if d["dialogue_type"] == "NOOP":
            return Say("I don't know how to answer that.", **self.dialogue_object_parameters)
        elif d["dialogue_type"] == "HUMAN_GIVE_COMMAND":
            return self.dialogue_objects["interpreter"](
                speaker, d, **self.dialogue_object_parameters
            )
        elif d["dialogue_type"] == "PUT_MEMORY":
            return self.dialogue_objects["put_memory"](
                speaker, d, **self.dialogue_object_parameters
            )
        elif d["dialogue_type"] == "GET_MEMORY":
            logging.info("this model out: %r" % (d))
            return self.dialogue_objects["get_memory"](
                speaker, d, **self.dialogue_object_parameters
            )
        else:
            raise ValueError("Bad dialogue_type={}".format(d["dialogue_type"]))

    def get_logical_form(self, s: str, model, chat_as_list=False) -> Dict:
        """Get logical form output for a given chat command.
        First check the ground truth file for the chat string. If not
        in ground truth, query semantic parsing model to get the output.

        Args:
            s (str): Input chat provided by the user.
            model (TTADBertModel): Semantic parsing model, pre-trained and loaded
                by agent

        Return:
            Dict: Logical form representation of the task. See paper for more
                in depth explanation of logical forms:
                https://arxiv.org/abs/1907.08584

        Examples:
            >>> get_logical_form("destroy this", model)
            {
                "dialogue_type": "HUMAN_GIVE_COMMAND",
                "action_sequence": [{
                    "action_type": "DESTROY",
                    "reference_object": {
                        "filters": {"contains_coreference": "yes"},
                        "text_span": [0, [1, 1]]
                    }
                }]
            }
        """
        if s in self.ground_truth_actions:
            d = self.ground_truth_actions[s]
            logging.info('Found gt action for "{}"'.format(s))
        else:
            logging.info("Querying the semantic parsing model")
            if chat_as_list:
                d = model.parse([s])
            else:
                d = model.parse(chat=s)  # self.ttad_model.parse(chat=s)

        # perform lemmatization on the chat
        logging.info('chat before lemmatization "{}"'.format(s))
        lemmatized_chat = sp(s)
        chat = " ".join(str(word.lemma_) for word in lemmatized_chat)
        logging.info('chat after lemmatization "{}"'.format(chat))

        # Get the words from indices in spans
        process_spans(d, re.split(r" +", s), re.split(r" +", chat))
        logging.info('ttad pre-coref "{}" -> {}'.format(chat, d))

        # log to sentry
        sentry_sdk.capture_message(
            json.dumps({"type": "ttad_pre_coref", "in_original": s, "out": d})
        )
        sentry_sdk.capture_message(
            json.dumps({"type": "ttad_pre_coref", "in_lemmatized": chat, "out": d})
        )

        logging.info('logical form before grammar update "{}'.format(d))
        logging.info('logical form after grammar fix "{}"'.format(d))

        return d
