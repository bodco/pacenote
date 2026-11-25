"""0.10.0 (Meeting Sheet, phase 1): manual edits survive a rebuild (Refresh uses it, phase 3).

protect_edits(old, new) returns a copy of `new` where:
  - a block the user edited (edited_by_user) is the old block as it was;
  - a fact the user wrote (origin user) or changed (status edited) is the old fact as it was, in the same block:
    it replaces the new fact with the same id, or is added at its old place when the new build has no such fact;
  - a protected fact whose block is gone from the new sheet brings its block back with only the protected facts;
  - PB-100: a fact the user deleted (its id in deleted_facts) stays out together with its older
    "was X" value (superseded_by), and deleted_facts goes on to the new sheet.
Facts of the readiness engine are not protected: it adds them again by itself.
"""
import copy
from typing import Dict, List


def is_protected(fact: dict) -> bool:
    return fact.get("status") == "edited" or fact.get("origin") == "user"


def _key(b: dict) -> str:
    return b.get("id") or b.get("type", "")


def protect_edits(old_sheet: dict, new_sheet: dict) -> dict:
    out = copy.deepcopy(new_sheet)
    blocks: List[dict] = out.setdefault("blocks", [])
    by_id: Dict[str, dict] = {_key(b): b for b in blocks}
    by_type: Dict[str, dict] = {}
    for b in blocks:
        by_type.setdefault(b.get("type", ""), b)
    for ob in old_sheet.get("blocks") or []:
        if ob.get("type") == "verbatim":
            # PB-102: a Verbatim block is never put back from the old sheet (its copy is read again on every build);
            # a move or hide the user made on the sheet page stays
            nb = by_id.get(_key(ob))
            if nb is not None and ob.get("edited_by_user"):
                nb.update({"order": ob.get("order", nb.get("order")), "visible": ob.get("visible", nb.get("visible")),
                           "edited_by_user": True})
            continue
        # PB-101: a custom block is matched by its id only (several per call share the type)
        nb = by_id.get(_key(ob)) or (None if ob.get("type") == "custom" else by_type.get(ob.get("type", "")))
        if ob.get("edited_by_user"):
            keep = copy.deepcopy(ob)
            if nb is None:
                blocks.append(keep)
            else:
                blocks[blocks.index(nb)] = keep
            continue
        old_facts = ob.get("facts") or []
        prot = [(i, f) for i, f in enumerate(old_facts) if is_protected(f)]
        if not prot:
            continue
        if nb is None:
            nb = copy.deepcopy(ob)
            nb["facts"] = []
            blocks.append(nb)
        facts = nb.setdefault("facts", [])
        for i, f in prot:
            keep = copy.deepcopy(f)
            keep.pop("diff", None)
            at = next((j for j, x in enumerate(facts) if x.get("id") == f.get("id")), None)
            if at is not None:
                facts[at] = keep
            else:
                facts.insert(min(i, len(facts)), keep)
    # A protected fact moved into another block by the user must not also stay where the new build put it.
    prot_ids = {f.get("id") for b in blocks for f in b.get("facts") or [] if is_protected(f)}
    deleted = deleted_ids(old_sheet)
    # PB-100: the older "was X" value of a deleted fact goes with it (it is shown only under the newer one). Its own
    # id is not kept in deleted_facts: the rule follows superseded_by, so a later value for the label is not hidden.
    gone = deleted | {f.get("id") for b in blocks for f in b.get("facts") or [] if f.get("superseded_by") in deleted}
    for b in blocks:
        b["facts"] = [f for f in b.get("facts") or [] if (is_protected(f) or f.get("id") not in prot_ids)
                      and f.get("id") not in gone]
    if deleted:
        out["deleted_facts"] = sorted(deleted | deleted_ids(out))
        for b in blocks:                  # a conflict partner left alone is no longer a conflict (as in the app)
            for f in b["facts"]:
                cw = f.get("conflict_with")
                if isinstance(cw, list) and any(x in gone for x in cw):
                    left = [x for x in cw if x not in gone]
                    if left:
                        f["conflict_with"] = left
                    else:
                        f.pop("conflict_with", None)
                        if f.get("status") == "conflict":
                            f["status"] = "needs_check"
    return out


def deleted_ids(sheet: dict) -> set:
    """PB-100: the ids of the facts the user deleted from the sheet (Refresh and auto questions never bring them back)."""
    return {x for x in sheet.get("deleted_facts") or [] if isinstance(x, str)}
