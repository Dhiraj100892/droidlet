"""
Copyright (c) Facebook, Inc. and its affiliates.
"""
import os
import sys
import numpy as np

from mc_util import XYZ, IDM, to_block_pos, pos_to_np, euclid_dist, diag_adjacent
from typing import Tuple, List
from block_data import BORING_BLOCKS

BASE_AGENT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.append(BASE_AGENT_ROOT)

from base_agent.memory_nodes import PlayerNode, AttentionNode
from mc_memory_nodes import BlockObjectNode


def capped_line_of_sight(agent, player_struct, cap=20):
    """Return the block directly in the entity's line of sight, or a point in the distance"""
    xsect = agent.get_player_line_of_sight(player_struct)
    if xsect is not None and euclid_dist(pos_to_np(xsect), pos_to_np(player_struct.pos)) <= cap:
        return pos_to_np(xsect)

    # default to cap blocks in front of entity
    vec = agent.coordinate_transforms.look_vec(player_struct.look.yaw, player_struct.look.pitch)
    return cap * np.array(vec) + to_block_pos(pos_to_np(player_struct.pos))


class LowLevelMCPerception:
    def __init__(self, agent, perceive_freq=5):
        self.agent = agent
        self.memory = agent.memory
        self.pending_agent_placed_blocks = set()
        self.perceive_freq = perceive_freq

    def perceive(self, force=False):
        """Every n seconds (defined by perceive_freq), update location
        of all mobs, item position in stack and update state of the
        world, in agent's memory"""
        # FIXME (low pri) remove these in code, get from sql
        self.agent.pos = to_block_pos(pos_to_np(self.agent.get_player().pos))

        if self.agent.count % self.perceive_freq == 0 or force:
            for mob in self.agent.get_mobs():
                if euclid_dist(self.agent.pos, pos_to_np(mob.pos)) < self.memory.perception_range:
                    self.memory.set_mob_position(mob)
            item_stack_set = set()
            for item_stack in self.agent.get_item_stacks():
                item_stack_set.add(item_stack.entityId)
                if (
                    euclid_dist(self.agent.pos, pos_to_np(item_stack.pos))
                    < self.memory.perception_range
                ):
                    self.memory.set_item_stack_position(item_stack)
            old_item_stacks = self.memory.get_all_item_stacks()
            if old_item_stacks:
                for old_item_stack in old_item_stacks:
                    memid = old_item_stack[0]
                    eid = old_item_stack[1]
                    if eid not in item_stack_set:
                        self.memory.untag(memid, "_on_ground")
                    else:
                        self.memory.tag(memid, "_on_ground")

        # note: no "force"; these run on every perceive call.  assumed to be fast
        self.update_self_memory()
        self.update_other_players(self.agent.get_other_players())

        # use safe_get_changed_blocks to deal with pointing
        for (xyz, idm) in self.agent.safe_get_changed_blocks():
            self.on_block_changed(xyz, idm)

    def update_self_memory(self):
        """Update agent's current position and attributes in memory"""
        p = self.agent.get_player()
        memid = self.memory.get_player_by_eid(p.entityId).memid
        cmd = "UPDATE ReferenceObjects SET eid=?, name=?, x=?,  y=?, z=?, pitch=?, yaw=? WHERE "
        cmd = cmd + "uuid=?"
        self.memory._db_write(
            cmd, p.entityId, p.name, p.pos.x, p.pos.y, p.pos.z, p.look.pitch, p.look.yaw, memid
        )

    def update_other_players(self, player_list: List, force=False):
        """Update other in-game players in agen't memory
        Args:
            a list of player_structs from agent
        """
        for p in player_list:
            mem = self.memory.get_player_by_eid(p.entityId)
            if mem is None:
                memid = PlayerNode.create(self.memory, p)
            else:
                memid = mem.memid
            cmd = (
                "UPDATE ReferenceObjects SET eid=?, name=?, x=?,  y=?, z=?, pitch=?, yaw=? WHERE "
            )
            cmd = cmd + "uuid=?"
            self.memory._db_write(
                cmd, p.entityId, p.name, p.pos.x, p.pos.y, p.pos.z, p.look.pitch, p.look.yaw, memid
            )
            loc = capped_line_of_sight(self.agent, p)
            loc[1] += 1
            memids = self.memory._db_read_one(
                'SELECT uuid FROM ReferenceObjects WHERE ref_type="attention" AND type_name=?',
                p.entityId,
            )
            if memids:
                self.memory._db_write(
                    "UPDATE ReferenceObjects SET x=?, y=?, z=? WHERE uuid=?",
                    loc[0],
                    loc[1],
                    loc[2],
                    memids[0],
                )
            else:
                AttentionNode.create(self.memory, loc, attender=p.entityId)

    # TODO replace name by eid everywhere
    def get_player_struct_by_name(self, name):
        """Get the raw player struct by player name
        Returns:
            a raw player struct, e.g. to use in agent.get_player_line_of_sight
        """
        for p in self.agent.get_other_players():
            if p.name == name:
                return p
        return None

    def on_block_changed(self, xyz: XYZ, idm: IDM):
        """Update the state of the world when a block is changed."""
        # TODO don't need to do this for far away blocks if this is slowing down bot
        self.maybe_remove_inst_seg(xyz)
        self.maybe_remove_block_from_memory(xyz, idm)
        self.maybe_add_block_to_memory(xyz, idm)

    def clear_air_surrounded_negatives(self):
        pass

    def maybe_remove_inst_seg(self, xyz):
        """Update instance segmentation label of blocks"""
        # get all associated instseg nodes
        info = self.memory.get_instseg_object_ids_by_xyz(xyz)
        if not info or len(info) == 0:
            pass

        # first delete the InstSeg info on the loc of this block
        self.memory._db_write(
            'DELETE FROM VoxelObjects WHERE ref_type="inst_seg" AND x=? AND y=? AND z=?', *xyz
        )

        # then for each InstSeg, check if all blocks of same InstSeg node has
        # already been deleted. if so, delete the InstSeg node entirely
        for memid in info:
            memid = memid[0]
            xyzs = self.memory._db_read(
                'SELECT x, y, z FROM VoxelObjects WHERE ref_type="inst_seg" AND uuid=?', memid
            )
            all_deleted = True
            for xyz in xyzs:
                r = self.memory._db_read(
                    'SELECT * FROM VoxelObjects WHERE ref_type="inst_seg" AND uuid=? AND x=? AND y=? AND z=?',
                    memid,
                    *xyz
                )
                if bool(r):
                    all_deleted = False
            if all_deleted:
                # TODO make an archive.
                self.memory._db_write("DELETE FROM Memories WHERE uuid=?", memid)

    # clean all this up...
    # eventually some conditions for not committing air/negative blocks
    def maybe_add_block_to_memory(self, xyz: XYZ, idm: IDM, agent_placed=False):
        """Update blocks to memory when any change in the environment
        is caused either by agent or player"""
        if not agent_placed:
            interesting, player_placed, agent_placed = self.is_placed_block_interesting(
                xyz, idm[0]
            )
        else:
            interesting = True
            player_placed = False
        if not interesting:
            return

        # TODO remove this, clean up
        if agent_placed:
            try:
                self.pending_agent_placed_blocks.remove(xyz)
            except:
                pass

        adjacent = [
            self.memory.get_object_info_by_xyz(a, "BlockObjects", just_memid=False)
            for a in diag_adjacent(xyz)
        ]

        if idm[0] == 0:
            # block removed / air block added
            adjacent_memids = [a[0][0] for a in adjacent if len(a) > 0 and a[0][1] == 0]
        else:
            # normal block added
            adjacent_memids = [a[0][0] for a in adjacent if len(a) > 0 and a[0][1] > 0]
        adjacent_memids = list(set(adjacent_memids))
        if len(adjacent_memids) == 0:
            # new block object
            BlockObjectNode.create(self.agent.memory, [(xyz, idm)])
        elif len(adjacent_memids) == 1:
            # update block object
            memid = adjacent_memids[0]
            self.memory.upsert_block(
                (xyz, idm), memid, "BlockObjects", player_placed, agent_placed
            )
            self.memory.set_memory_updated_time(memid)
            self.memory.set_memory_attended_time(memid)
        else:
            chosen_memid = adjacent_memids[0]
            self.memory.set_memory_updated_time(chosen_memid)
            self.memory.set_memory_attended_time(chosen_memid)

            # merge tags
            where = " OR ".join(["subj=?"] * len(adjacent_memids))
            self.memory._db_write(
                "UPDATE Triples SET subj=? WHERE " + where, chosen_memid, *adjacent_memids
            )

            # merge multiple block objects (will delete old ones)
            where = " OR ".join(["uuid=?"] * len(adjacent_memids))
            cmd = "UPDATE VoxelObjects SET uuid=? WHERE "
            self.memory._db_write(cmd + where, chosen_memid, *adjacent_memids)

            # insert new block
            self.memory.upsert_block(
                (xyz, idm), chosen_memid, "BlockObjects", player_placed, agent_placed
            )

    def maybe_remove_block_from_memory(self, xyz: XYZ, idm: IDM):
        """Update agent's memory with blocks that have been destroyed."""
        tables = ["BlockObjects"]
        for table in tables:
            info = self.memory.get_object_info_by_xyz(xyz, table, just_memid=False)
            if not info or len(info) == 0:
                continue
            assert len(info) == 1
            memid, b, m = info[0]
            delete = (b == 0 and idm[0] > 0) or (b > 0 and idm[0] == 0)
            if delete:
                self.memory.remove_voxel(*xyz, table)
                self.agent.areas_to_perceive.append((xyz, 3))

    # FIXME move removal of block to parent
    def is_placed_block_interesting(self, xyz: XYZ, bid: int) -> Tuple[bool, bool, bool]:
        """Return three values:
        - bool: is the placed block interesting?
        - bool: is it interesting because it was placed by a player?
        - bool: is it interesting because it was placed by the agent?
        """
        interesting = False
        player_placed = False
        agent_placed = False
        # TODO record *which* player placed it
        if xyz in self.pending_agent_placed_blocks:
            interesting = True
            agent_placed = True
        for player_struct in self.agent.get_other_players():
            if (
                euclid_dist(pos_to_np(player_struct.pos), xyz) < 5
                and player_struct.mainHand.id == bid
            ):
                interesting = True
                if not agent_placed:
                    player_placed = True
        if bid not in BORING_BLOCKS:
            interesting = True
        return interesting, player_placed, agent_placed
