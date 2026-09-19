from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings

WORLD_DIR = Path(settings.BASE_DIR) / "worlds"


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _load_world(case_id: str) -> dict:
    fp = WORLD_DIR / f"{case_id}.json"
    if not fp.exists():
        raise FileNotFoundError(f"World file not found: {fp}")
    return json.loads(fp.read_text(encoding="utf-8"))


def _default_state(case_id: str) -> dict:
    w = _load_world(case_id)

    item_locs = {}
    for room_id, room in w.get("rooms", {}).items():
        for item_id in room.get("items", []):
            item_locs[item_id] = room_id

    return {
        "case_id": case_id,
        "loc": w["start_room"],
        "inv": [],
        "item_locs": item_locs,
        "flags": dict(w.get("flags_default", {})),
        "moves": 0,
        "log": ["BOOT OK. TYPE 'HELP'.", ""],
        "last_out": "",
    }


def get_state(session, case_id: str = "case001") -> dict:
    st = session.get("sp_state")
    if not isinstance(st, dict) or st.get("case_id") != case_id:
        st = _default_state(case_id)
        session["sp_state"] = st
        session.modified = True
    return st


def save_state(session, st: dict) -> None:
    session["sp_state"] = st
    session.modified = True


def _room(world: dict, room_id: str) -> dict:
    return world["rooms"][room_id]


def _item(world: dict, item_id: str) -> dict:
    return world["items"][item_id]


def _find_item_by_name(world: dict, name: str) -> Optional[str]:
    n = _norm(name)
    if not n:
        return None
    # exact ID match
    if n in world["items"]:
        return n
    # fuzzy name contains
    for iid, meta in world["items"].items():
        if n in _norm(meta.get("name", "")):
            return iid
    return None


def _describe_room(world: dict, st: dict) -> str:
    r = _room(world, st["loc"])
    title = r["name"].upper()
    out = [title, "-" * len(title), r["desc"].rstrip(), ""]

    item_locs = st.setdefault("item_locs", {})
    items_here = [
        item_id
        for item_id, room_id in item_locs.items()
        if room_id == st["loc"]
    ]

    if items_here:
        out.append("YOU SEE: " + ", ".join(_item(world, i)["name"].upper() for i in items_here))

    exits = ", ".join(sorted(r.get("exits", {}).keys()))
    out.append("EXITS: " + (exits.upper() if exits else "NONE"))
    return "\n".join(out)

def _inv_text(world: dict, st: dict) -> str:
    inv = st["inv"]
    if not inv:
        return "YOU ARE CARRYING: NOTHING."
    return "YOU ARE CARRYING: " + ", ".join(_item(world, i)["name"].upper() for i in inv)


def _locked(world: dict, st: dict, direction: str) -> Tuple[bool, str]:
    r = _room(world, st["loc"])
    exits = r.get("exits", {})
    if direction not in exits:
        return True, "YOU CAN'T GO THAT WAY."
    locks = r.get("locks", {})
    if direction in locks:
        flag = locks[direction]
        if not bool(st["flags"].get(flag)):
            msg = r.get("lock_messages", {}).get(direction) or "THAT WAY IS LOCKED."
            return True, msg
    return False, ""


def _move(world: dict, st: dict, direction: str) -> str:
    blocked, msg = _locked(world, st, direction)
    if blocked:
        return msg
    cur = _room(world, st["loc"])
    st["loc"] = cur["exits"][direction]
    st["moves"] += 1
    return _describe_room(world, st)


def _take(world: dict, st: dict, item_name: str) -> str:
    iid = _find_item_by_name(world, item_name)
    if not iid:
        return "TAKE WHAT?"

    item_locs = st.setdefault("item_locs", {})

    if iid in st["inv"]:
        return "YOU ARE ALREADY CARRYING THAT."

    if item_locs.get(iid) != st["loc"]:
        return "THAT IS NOT HERE."

    item_locs[iid] = None
    st["inv"].append(iid)
    st["moves"] += 1
    return f"TAKEN: {_item(world, iid)['name'].upper()}"


def _drop(world: dict, st: dict, item_name: str) -> str:
    iid = _find_item_by_name(world, item_name)
    if not iid:
        return "DROP WHAT?"

    if iid not in st["inv"]:
        return "YOU ARE NOT CARRYING THAT."

    item_locs = st.setdefault("item_locs", {})
    st["inv"].remove(iid)
    item_locs[iid] = st["loc"]

    st["moves"] += 1
    return f"DROPPED: {_item(world, iid)['name'].upper()}"


def _examine(world: dict, st: dict, item_name: str) -> str:
    iid = _find_item_by_name(world, item_name)
    if not iid:
        return "EXAMINE WHAT?"

    item_locs = st.setdefault("item_locs", {})

    if iid not in st["inv"] and item_locs.get(iid) != st["loc"]:
        return "YOU DON'T SEE THAT HERE."

    meta = _item(world, iid)
    return f"{meta['name'].upper()}: {meta.get('desc','').strip()}"


def _help() -> str:
    return (
        "COMMANDS:\n"
        "  LOOK / L\n"
        "  GO <N|S|E|W>   (ALSO: N,S,E,W)\n"
        "  TAKE <ITEM>\n"
        "  DROP <ITEM>\n"
        "  INVENTORY / I\n"
        "  EXAMINE <ITEM>\n"
        "  USE <ITEM> [ON <THING>]\n"
        "  LOG            (SHOW CASE LOG)\n"
        "  CLS\n"
        "  RESTART\n"
        "  HELP\n"
    )


def _apply_use(world: dict, st: dict, item_id: str, target: Optional[str]) -> str:
    # Generic rule: look up a "use" mapping in JSON.
    # Each use rule can:
    # - require location
    # - require flags/inventory
    # - set flags
    # - produce output
    uses = world.get("uses", [])
    loc = st["loc"]

    for rule in uses:
        if rule.get("item") != item_id:
            continue
        if "location" in rule and rule["location"] != loc:
            continue
        if "target" in rule and _norm(rule["target"]) != _norm(target or ""):
            continue

        # requirements
        for req_item in rule.get("requires_inventory", []):
            if req_item not in st["inv"]:
                return rule.get("fail_out") or "YOU'RE MISSING SOMETHING."
        for flag, val in rule.get("requires_flags", {}).items():
            if bool(st["flags"].get(flag)) != bool(val):
                return rule.get("fail_out") or "THAT DOESN'T WORK."

        # effects
        for flag, val in rule.get("set_flags", {}).items():
            st["flags"][flag] = val

        add_log = rule.get("add_log")
        if add_log:
            st["log"].append(add_log)

        # win?
        if rule.get("win"):
            st["flags"]["won"] = True

        st["moves"] += 1
        return rule.get("out") or "DONE."

    st["moves"] += 1
    return "NOTHING HAPPENS."


def process(session, raw: str, case_id: str = "case001") -> Tuple[str, dict]:
    world = _load_world(case_id)
    st = get_state(session, case_id=case_id)
    cmd = _norm(raw)

    if not cmd:
        return "", st

    # core commands
    if cmd in {"help", "?"}:
        out = _help()
        st["last_out"] = out
        save_state(session, st)
        return out, st

    if cmd in {"look", "l"}:
        out = _describe_room(world, st)
        st["last_out"] = out
        save_state(session, st)
        return out, st

    if cmd in {"inventory", "inv", "i"}:
        out = _inv_text(world, st)
        st["last_out"] = out
        save_state(session, st)
        return out, st

    if cmd == "log":
        out = "\n".join(st["log"][-30:]) if st["log"] else "(NO LOG.)"
        st["last_out"] = out
        save_state(session, st)
        return out, st

    if cmd in {"cls", "clear"}:
        st["last_out"] = "__CLS__"
        save_state(session, st)
        return "__CLS__", st

    if cmd in {"restart", "reset"}:
        st = _default_state(case_id)
        session["sp_state"] = st
        session.modified = True
        out = _describe_room(_load_world(case_id), st)
        st["last_out"] = out
        save_state(session, st)
        return out, st

    # movement
    dirmap = {"n": "north", "north": "north", "s": "south", "south": "south",
              "e": "east", "east": "east", "w": "west", "west": "west"}
    if cmd in dirmap:
        out = _move(world, st, dirmap[cmd])
        st["last_out"] = out
        save_state(session, st)
        return out, st

    if cmd.startswith("go "):
        d = _norm(cmd[3:])
        if d in dirmap:
            out = _move(world, st, dirmap[d])
        else:
            out = "GO WHERE? (N/S/E/W)"
        st["last_out"] = out
        save_state(session, st)
        return out, st

    # take/drop/examine
    if cmd.startswith("take "):
        out = _take(world, st, cmd[5:])
        st["last_out"] = out
        save_state(session, st)
        return out, st

    if cmd.startswith("drop "):
        out = _drop(world, st, cmd[5:])
        st["last_out"] = out
        save_state(session, st)
        return out, st

    if cmd.startswith("examine "):
        out = _examine(world, st, cmd[8:])
        st["last_out"] = out
        save_state(session, st)
        return out, st

    # use
    if cmd.startswith("use "):
        rest = cmd[4:]
        target = None
        if " on " in rest:
            item_part, target_part = rest.split(" on ", 1)
            rest, target = item_part, target_part

        iid = _find_item_by_name(world, rest)
        if not iid:
            out = "USE WHAT?"
        elif iid not in st["inv"]:
            out = "YOU ARE NOT CARRYING THAT."
        else:
            out = _apply_use(world, st, iid, target)

        st["last_out"] = out
        save_state(session, st)
        return out, st

    # flavor
    if cmd in {"ver", "whoami"}:
        out = "SQUATCHPATROL TERMINAL v0.1 (C) TORCHLIGHT ADVENTURES. FIELD-ISSUED. DO NOT TAUNT."
        st["last_out"] = out
        save_state(session, st)
        return out, st

    out = "I DON'T UNDERSTAND THAT. TYPE 'HELP'."
    st["last_out"] = out
    save_state(session, st)
    return out, st
