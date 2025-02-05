"""
Copyright (c) Facebook, Inc. and its affiliates.
"""
import json
import numpy as np
import random
import re
import ast

from os.path import isfile, isdir
from os.path import join as pjoin

import torch
from torch.utils.data import Dataset

#########
# Node typing: checking the type of a specific sub-tree (dict value)
#########
def is_span(val):
    try:
        a, (b, c) = val
        return all([type(v) == int for v in [a, b, c]])
    except (ValueError, TypeError):
        return False


def is_span_list(val):
    res = type(val) == list and len(val) > 0 and all([is_span(v) for v in val])
    return res


def is_cat(val):
    return type(val) == str or val is True or val is False


def is_cat_list(val):
    res = (type(val) == list) and len(val) > 0 and all([is_cat(v) for v in val])
    return res


def is_int(val):
    return type(val) == dict


def is_int_list(val):
    res = (type(val) == list) and len(val) > 0 and all([is_int(v) for v in val])
    return res


def add_tree(full_tree, new_tree, vocounts, nw=1):
    """Make grammar from dataset. 
    
    Starts with empty full_tree, then add all nodes found in the dataset.
    If new_tree is outside of what the grammar can handle, modifies grammar.
    Also counts number of occurence of each node.
    """
    for k, v in new_tree.items():
        if k not in full_tree:
            full_tree[k] = {"name": k, "children": {}, "values": {}, "count": 0}
        full_tree[k]["count"] += nw
        if is_cat(v):
            full_tree[k]["values"][v] = full_tree[k]["values"].get(v, 0) + nw
            w = "C:" + k + "|" + str(v)
            vocounts[w] = vocounts.get(w, 0) + nw
        elif is_int(v):
            ws = "IB:" + k
            we = "IE:" + k
            vocounts[ws] = vocounts.get(ws, 0) + nw
            vocounts[we] = vocounts.get(we, 0) + nw
            add_tree(full_tree[k]["children"], v, vocounts, nw)
        elif is_int_list(v):
            ws = "ILB:" + k
            wi = "IL&:" + k
            we = "ILE:" + k
            vocounts[ws] = vocounts.get(ws, 0) + nw
            vocounts[wi] = vocounts.get(wi, 0) + nw
            vocounts[we] = vocounts.get(we, 0) + nw
            for c in v:
                add_tree(full_tree[k]["children"], c, vocounts, nw)
        elif is_span(v) or is_span_list(v):
            # Treating text spans differently because of separate head predicting text spans
            if k == "text_span":
                w = "TS:" + k
                ws = "TBE:" + k
                vocounts[w] = vocounts.get(w, 0) + nw
                vocounts[ws] = vocounts.get(ws, 0) + nw
            w = "S:" + k
            ws = "BE:" + k
            vocounts[w] = vocounts.get(w, 0) + nw
            vocounts[ws] = vocounts.get(ws, 0) + nw


def make_full_tree(trees_weight_ls):
    """Starts with an empty grammar and adds trees from the dataset.
    """
    res = {}
    vocounts = {}
    for trees, weight in trees_weight_ls:
        try:
            for tree in trees:
                dlg, tr = tree
                add_tree(res, tr, vocounts, weight)
        except ValueError as e:
            print(e)
            print(tree)
    tree_i2w = [k for k, v in sorted(vocounts.items(), key=lambda x: x[1], reverse=True)] + [
        "BE:span"
    ]
    return res, tree_i2w


def process_txt_data(filepath: str):
    """Converts a txt file format to the tree format needed to construct grammar
    """
    samples = open(filepath, "r").readlines()
    split_lines = [line.split("|") for line in samples]
    # Format of each sample is [text, action_dict]
    formatted_data = [[text, ast.literal_eval(action_dict)] for text, action_dict in split_lines]
    return formatted_data


def tree_to_seq(full_tree, tree, idx_map=None):
    """Linearize and de-linearize trees.

    Transforms tree into sequence of (token, span_start, span_end, text_span_start, text_span_end)
    idx_map maps the span ids before and after tokenization

    """
    res = []
    sorted_keys = sorted(
        [k for k in tree.keys() if k in full_tree],
        key=lambda x: full_tree[x]["count"],
        reverse=True,
    ) + sorted([k for k, v in tree.items() if k not in full_tree])
    try:
        for k in sorted_keys:
            if is_cat(tree[k]):
                res += [("C:" + k + "|" + str(tree[k]), -1, -1, -1, -1)]
            elif is_span(tree[k]):
                if k == "text_span":
                    a, (b, c) = tree[k]
                    res += [("TS:" + k, -1, -1, -1, -1)]
                    res += [("TBE:" + k, -1, -1, idx_map[a][b][0], idx_map[a][c][1])]
                else:
                    a, (b, c) = tree[k]
                    res += [("S:" + k, -1, -1, -1, -1)]
                    res += [("BE:" + k, idx_map[a][b][0], idx_map[a][c][1], -1, -1)]
            elif is_int(tree[k]):
                res += (
                    [("IB:" + k, -1, -1, -1, -1)]
                    + tree_to_seq(full_tree.get(k, {"children": {}})["children"], tree[k], idx_map)
                    + [("IE:" + k, -1, -1, -1, -1)]
                )
            elif is_int_list(tree[k]):
                res += [("ILB:" + k, -1, -1, -1, -1)]
                for c in tree[k]:
                    res += tree_to_seq(full_tree.get(k, {"children": {}})["children"], c, idx_map) + [
                        ("IL&:" + k, -1, -1, -1, -1)
                    ]
                res = res[:-1] + [("ILE:" + k, -1, -1, -1, -1)]
            else:
                raise NotImplementedError
    except IndexError as e:
        raise e
    return res


def select_spans(seq):
    """Selects sub-tree in (span in the output sequence) so we can apply recursively seq_to_tree
    """
    spans = [-1 for _ in seq]
    active = {}
    unopened = False
    for i, (w, b_id, e_id, text_span_b_id, text_span_e_id) in enumerate(seq):
        if w.startswith("IB:") or w.startswith("ILB:"):
            active[w] = active.get(w, {})
            active[w][i] = 0
            for s_idx in active[w]:
                active[w][s_idx] += 1
        elif w.startswith("IE:") or w.startswith("ILE:"):
            ws = w.replace("E:", "B:")
            if ws not in active:
                # closing an unopened bracket
                unopened = True
            else:
                closed = []
                for s_idx in active[ws]:
                    active[ws][s_idx] -= 1
                    if active[ws][s_idx] <= 0:
                        closed += [s_idx]
                        spans[s_idx] = i
                for s_idx in closed:
                    del active[ws][s_idx]
    # check whether all brackets have been closed
    well_formed = (sum([len(ctr_dict) for ws, ctr_dict in active.items()]) == 0) and not unopened
    for ws in active:
        for s_idx in active[ws]:
            spans[s_idx] = len(seq)
    # create a dictionary of left bracket > right bracket
    span_dict = {}
    for s_idx, e_idx in enumerate(spans):
        if e_idx > 0:
            span_dict[s_idx] = e_idx
    return (span_dict, well_formed)


def seq_to_tree(full_tree, seq, idx_rev_map=None, span_dct=None, start_id=0):
    """Transforms sequence back into tree of nested dictionaries
    
    span_dict identifies the sub-sequences corresponding to sub-trees
    """
    res = {}
    if span_dct is None:
        span_dict, well_formed = select_spans(seq)
    else:
        span_dict = span_dct
        well_formed = True
    idx = 0
    while idx < len(seq):
        if ":" not in seq[idx][0]:
            idx += 1
            continue
        t, w = seq[idx][0].split(":")
        # categorical node
        if t == "C":
            cat, val = w.split("|")
            res[cat] = val
            idx += 1
        # span node
        elif t == "S":
            if idx + 1 < len(seq):
                b_pre = seq[idx + 1][1]
                e_pre = seq[idx + 1][2]
                l_idx, b_idx = idx_rev_map[b_pre]
                _, e_idx = idx_rev_map[e_pre]
                res[w] = [l_idx, [b_idx, e_idx]]
            else:
                res[w] = [-1, [-1, -1]]
            # idx     += 1
            idx += 2
        elif t == "TS":
            if idx + 1 < len(seq):
                text_span_start = seq[idx + 1][3]
                text_span_end = seq[idx + 1][4]
                list_idx, start_idx = idx_rev_map[text_span_start]
                _, end_idx = idx_rev_map[text_span_end]
                res[w] = [list_idx, [start_idx, end_idx]]
            else:
                res[w] = [-1, [-1, -1]]
            idx += 2
        # internal node
        elif t == "IB":
            sub_full_tree = full_tree.get(w, {"children": {}})["children"]
            sub_span = (idx + 1, span_dict[start_id + idx] - start_id)
            sub_seq = seq[sub_span[0] : sub_span[1]]
            res[w] = seq_to_tree(
                sub_full_tree, sub_seq, idx_rev_map, span_dict, start_id=start_id + sub_span[0]
            )[0]
            idx = sub_span[1]
        # internal node list
        elif t == "ILB":
            sub_full_tree = full_tree.get(w, {"children": {}})["children"]
            sub_span = (idx + 1, span_dict[start_id + idx] - start_id)
            pre_sub_seq = seq[sub_span[0] : sub_span[1]]
            # split sub-sequence by list items
            sub_seq_ls_idx = (
                [-1]
                + [i for i, sw in enumerate(pre_sub_seq) if sw[0] == "IL&:" + w]
                + [len(pre_sub_seq)]
            )
            sub_span_ls = [
                (sub_span[0] + sub_seq_ls_idx[i] + 1, sub_span[0] + sub_seq_ls_idx[i + 1])
                for i in range(len(sub_seq_ls_idx) - 1)
            ]
            # read sub-trees
            res[w] = []
            for s_sub_span in sub_span_ls:
                sub_seq = seq[s_sub_span[0] : s_sub_span[1]]
                res[w] += [
                    seq_to_tree(
                        sub_full_tree,
                        sub_seq,
                        idx_rev_map,
                        span_dict,
                        start_id=start_id + s_sub_span[0],
                    )[0]
                ]
            idx = sub_span[1]
        # failure case??? TODO: raise error
        else:
            idx += 1
    return (res, well_formed)


def compare_tree(ta, tb):
    """Returns empty tree if ta and tb are the same tree
    """
    res = {}
    # internal node
    if is_int(ta) or is_int_list(ta):
        if is_int_list(ta):
            ta = ta[0]
            tb = tb[0]
        for a in ta:
            if a in tb:
                comp = compare_tree(ta[a], tb[a])
                if len(comp) > 0:
                    res[a] = comp
            else:
                res[a] = (ta[a], "")
        for b in tb:
            if b not in ta:
                res[b] = ("", tb[b])
    elif ta != tb:
        res = (ta, tb)
    return res


def align_post_tok(pre_tok, post_tok, seen_toks=0):
    """Helper function to align word indices before and after applying BPE
    """
    i, j, ci, cj = [0] * 4
    idx_map = [[seen_toks, seen_toks] for _ in range(len(pre_tok.split()))]
    while ci < len(pre_tok) and cj < len(post_tok):
        if pre_tok[ci] == post_tok[cj]:
            if pre_tok[ci] == " ":
                i += 1
                j += 1
                if i > 0:
                    idx_map[i - 1][1] = j - 1 + seen_toks
                idx_map[i][0] = j + seen_toks
            ci += 1
            cj += 1
        elif post_tok[cj] == " ":
            j += 1
            cj += 1
        elif pre_tok[ci] == " ":
            i += 1
            if i > 0:
                idx_map[i - 1][0] = j - 1 + seen_toks
            idx_map[i][1] = j + seen_toks
            ci += 1
        else:
            cj += 1
    idx_map[i][-1] = j + seen_toks
    return idx_map


def tokenize_mapidx(text, tokenizer):
    """Applies BPE to input and creates mapping of span indices before and after BPE
    """
    # re-order lines: last chat in multi-chat is first in the list
    # rev_lines = [line.strip() for line in text.split('<SEP>')]
    # text_lines = [rev_lines[i - 1] for i in range(len(rev_lines), 0, -1)]
    text_lines = [line.strip() for line in text.split("<SEP>")]
    # tokenize text and linearize tree
    seen_toks = 1
    idx_maps = [[] for _ in text_lines]
    res_toks = ["[CLS]"]
    for lid, line in enumerate(text_lines):
        tok_line = tokenizer.tokenize(line)
        tok_join = " ".join(tok_line)
        idx_maps[-1 - lid] = align_post_tok(line, tok_join, seen_toks)
        res_toks += tok_line[:] + ["[SEP]"]
        seen_toks += len(tok_line) + 1
    return (" ".join(res_toks), idx_maps)


def tokenize_linearize(text, tree, tokenizer, full_tree, word_noise=0.0):
    """Takes raw text and tree, returns BPE-ed text and linearized tree
    """
    tok_text, idx_maps = tokenize_mapidx(text, tokenizer)
    tokenized = " ".join(
        [
            "[UNK]" if w not in ["[CLS]", "[SEP]"] and random.random() < word_noise else w
            for w in tok_text.split()
        ]
    )
    try:
        lin_tree = tree_to_seq(full_tree, tree, idx_maps)
    except IndexError as e:
        raise e
    return (tokenized, lin_tree)


class CAIPDataset(Dataset):
    """Torch Dataset for the CAIP format, applies BPE and linearizes trees on-the-fly

    CAIP: CraftAssist Instruction Parsing 

    Args:
        tokenizer: pre-trained tokenizer for input
        sampling: Whether to sample hard examples
        word_noise: Word noise
        sample_probas: Sampling probabilities for different data types
        dataset_length: Size of dataset
        tree_voc: Tree vocabulary file
        tree_idxs: Tree dictionary
        data: Dictionary containing datasets loaded into memory.
        
    """

    def __init__(
        self,
        tokenizer,
        args,
        prefix="train",
        dtype="templated",
        sampling=False,
        word_noise=0.0,
        full_tree_voc=None,
    ):
        assert isdir(args.data_dir)
        self.tokenizer = tokenizer
        self.tree_to_text = args.tree_to_text

        # We load the (input, tree) pairs for all data types and
        # initialize the hard examples buffer
        self.data = {}
        self.sampling = sampling
        self.word_noise = word_noise
        dtype_samples = json.loads(args.dtype_samples)
        self.dtype = dtype
        self.dtypes = [t for t, p in dtype_samples]
        self.sample_probas = np.array([p for t, p in dtype_samples])
        self.sample_probas /= self.sample_probas.sum()
        if prefix == "train":
            for k in self.dtypes:
                fname = pjoin(args.data_dir, prefix, k + ".txt")
                print(fname)
                assert isfile(fname)
                print("loading {}".format(fname))
                self.data[k] = process_txt_data(fname)
            self.hard_buffer_size = 1024
            self.hard_buffer_counter = 0
        else:
            fname = pjoin(args.data_dir, prefix, dtype + ".txt")
            if isfile(fname):
                print("loading {}".format(fname))
                self.data[dtype] = process_txt_data(fname)
            else:
                print("could not find dataset {}".format(fname))
                self.data[dtype] = []

        # load meta-tree and tree vocabulary
        if full_tree_voc is None:
            print("making tree")
            ftr, tr_i2w = make_full_tree(
                [
                    (self.data["humanbot"], 3e5),
                    (self.data["prompts"], 1e5),
                    (self.data["templated"][:100000], 1),
                ]
            )
            self.full_tree = ftr
        else:
            full_tree, tr_i2w = full_tree_voc
            self.full_tree = full_tree
        spec_tokens = ["[PAD]", "unused", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "<S>", "</S>"]
        self.tree_voc = spec_tokens[:] + tr_i2w
        self.tree_idxs = dict([(w, i) for i, w in enumerate(self.tree_voc)])

        self.dataset_length = max([len(v) for v in self.data.values()])
        if args.examples_per_epoch > 0:
            self.dataset_length = min(self.dataset_length, args.examples_per_epoch)

    def _contains_span_indices(self, token_idx_list: list):
        return token_idx_list[1] >= 0 and token_idx_list[2] >= 0

    def __len__(self):
        return self.dataset_length

    def __getitem__(self, idx):
        """Sample data type and get example
        """
        if self.sampling:
            dtype = np.random.choice(self.dtypes, p=self.sample_probas)
            if len(self.data[dtype]) == 0:
                dtype = self.dtype
        else:
            dtype = self.dtype
        try:
            t = self.data[dtype][idx % len(self.data[dtype])]
            p_text, p_tree = t
        except ValueError as e:
            print(e)
        text, tree = tokenize_linearize(
            p_text, p_tree, self.tokenizer, self.full_tree, self.word_noise
        )
        text_idx_ls = [self.tokenizer._convert_token_to_id(w) for w in text.split()]
        tree_idx_ls = [
            [self.tree_idxs[w], bi, ei, text_span_start, text_span_end]
            for w, bi, ei, text_span_start, text_span_end in [("<S>", -1, -1, -1, -1)]
            + tree
            + [("</S>", -1, -1, -1, -1)]
        ]
        if self.tree_to_text:
            stripped_tree_tokens = []
            for w, bi, ei in tree:
                tree_node = w.lower()
                tree_node_processed = re.sub("[^0-9a-zA-Z]+", " ", tree_node)
                tree_tokens = tree_node_processed.split(" ")
                stripped_tree_tokens += [x for x in tree_tokens if x != ""]

            extended_tree = ["[CLS]"] + stripped_tree_tokens + ["[SEP]"]
            tree_idx_ls = [self.tokenizer._convert_token_to_id(w) for w in extended_tree]
        return (text_idx_ls, tree_idx_ls, (text, p_text, p_tree))

    def add_hard_example(self, exple):
        """Add a given example for resampling.
        """
        if self.hard_buffer_counter < self.hard_buffer_size:
            self.data["hard"] += [exple]
        else:
            self.data["hard"][self.hard_buffer_counter % self.hard_buffer_size] = exple
        self.hard_buffer_counter += 1


def caip_collate(batch, tokenizer, tree_to_text=False):
    """Applies padding and makes batch tensors

    Args:
        batch (tuple): tuple containinig raw text and tree pairs
        tokenizer: tokenization method

    Returns:
        Tuple containing the tokenized and tensorized input, output and masks

    """
    # keep track of examples
    pre_examples = [(p_text, p_tree) for x, y, (_, p_text, p_tree) in batch]
    # input: text
    batch_x_ls = [x for x, y, _ in batch]
    x_len = max([len(x) for x in batch_x_ls])
    x_mask_ls = [[1] * len(x) + [0] * (x_len - len(x)) for x in batch_x_ls]
    batch_x_pad_ls = [x + [tokenizer.pad_token_id] * (x_len - len(x)) for x in batch_x_ls]
    # output: linearized trees
    batch_y_ls = [y for x, y, _ in batch]
    y_len = max([len(y) for y in batch_y_ls])
    y_mask_ls = [[1] * len(y) + [0] * (y_len - len(y)) for y in batch_y_ls]
    if tree_to_text:
        batch_y_pad_ls = [
            y + [tokenizer.pad_token_id] * (y_len - len(y)) for y in batch_y_ls
        ]  # 0 as padding idx
    else:
        batch_y_pad_ls = [
            y + [[0, -1, -1, -1, -1]] * (y_len - len(y)) for y in batch_y_ls
        ]  # 0 as padding idx
    # tensorize
    x = torch.tensor(batch_x_pad_ls)
    x_mask = torch.tensor(x_mask_ls)
    y = torch.tensor(batch_y_pad_ls)
    y_mask = torch.tensor(y_mask_ls)
    return (x, x_mask, y, y_mask, pre_examples)
