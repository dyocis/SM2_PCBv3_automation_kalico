#!/usr/bin/env python3
"""Guarded host-file apply/rollback. Never stops services or flashes firmware."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import tempfile

from stage_sm2_config import digest
from verify_sm2_recovery import verify


def require_stopped():
    for service, optional in (("klipper.service", False), ("nevermore-history.service", True)):
        result = subprocess.run(["systemctl", "show", service, "--property=LoadState,ActiveState,SubState"],
                                text=True, capture_output=True, timeout=10)
        state = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if optional and state.get("LoadState") == "not-found":
            continue
        if result.returncode or state != dict(LoadState="loaded", ActiveState="inactive", SubState="dead"):
            raise ValueError("Service must be stopped (inactive/dead): " + service)


def atomic_write(path, data, mode=0o600):
    fd, name = tempfile.mkstemp(prefix=".sm2-write-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as file:
            if os.name == "posix" and path.exists():
                os.fchown(file.fileno(), -1, path.stat().st_gid)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
            os.chmod(temporary, mode)
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_plan(root, recovery, approved_hash):
    root = root.resolve(strict=True)
    recovery = recovery.resolve(strict=True)
    raw = (root / "plan.json").read_bytes()
    if digest(raw) != approved_hash:
        raise ValueError("Plan checksum differs from the reviewed plan")
    plan = json.loads(raw)
    if plan.get("schema") != 1 or plan.get("scope") != "host-file review plan":
        raise ValueError("Unsupported deployment plan")
    saved = verify(recovery)
    if digest((recovery / "manifest.json").read_bytes()) != plan["recovery_manifest_sha256"]:
        raise ValueError("Recovery bundle does not match this plan")
    cfg = json.loads((recovery / saved["config_manifest"]).read_text())
    allowed = {entry["source"]: entry for entry in cfg["files"] + saved["artifacts"]}
    absent = {entry["path"] for entry in saved["module_inventory"] if not entry["originally_present"]}
    seen, payloads = set(), []
    for change in plan["changes"]:
        destination = change["destination"]
        original = allowed.get(destination)
        if original:
            expected = (original["sha256"], original.get("resolved", destination), original.get("mode"), original.get("symlink"))
            actual = (change["before_sha256"], change["resolved_destination"], change["original_mode"], change["original_symlink"])
            if expected != actual or not change["originally_present"]:
                raise ValueError("Plan original-file evidence differs from recovery")
        elif destination not in absent or change["originally_present"] or change["before_sha256"] is not None:
            raise ValueError("Plan destination is not authorized by recovery inventory")
        elif change["resolved_destination"] != destination or change["original_symlink"] is not None:
            raise ValueError("Unexpected path for originally absent module")
        resolved = Path(change["resolved_destination"])
        if not resolved.is_absolute() or str(resolved) in seen:
            raise ValueError("Invalid or duplicate destination")
        seen.add(str(resolved))
        if resolved.is_relative_to(root) or resolved.is_relative_to(recovery):
            raise ValueError("Destination overlaps plan or recovery")
        data = {}
        for name, sha in (("forward", "after_sha256"), ("rollback", "before_sha256")):
            if change[name] is None:
                if name != "rollback" or change["originally_present"]:
                    raise ValueError("Missing deployment payload")
                data[name] = None
                continue
            relative = Path(change[name])
            path = (root / relative).resolve(strict=True)
            if relative.is_absolute() or ".." in relative.parts or not path.is_relative_to(root):
                raise ValueError("Payload escapes plan directory")
            data[name] = path.read_bytes()
            if digest(data[name]) != change[sha]:
                raise ValueError("Deployment payload checksum mismatch")
        payloads.append(data)
    return plan, payloads


def current_state(change):
    alias = Path(change["destination"])
    target = Path(change["resolved_destination"])
    link = os.readlink(alias) if alias.is_symlink() else None
    if link != change["original_symlink"] or alias.resolve() != target:
        raise ValueError("Destination path/link changed: " + str(alias))
    if target.is_symlink():
        raise ValueError("Resolved target became a symlink")
    if not target.exists():
        if change["originally_present"]:
            raise ValueError("Original file is missing: " + str(target))
        return "before"
    mode = change["original_mode"] if change["originally_present"] else 0o644
    if os.name == "posix" and target.stat().st_mode & 0o777 != mode:
        raise ValueError("Destination permissions changed: " + str(target))
    if hasattr(os, "geteuid") and target.stat().st_uid != os.geteuid():
        raise ValueError("Run as the owner of destination files: " + str(target))
    sha = digest(target.read_bytes())
    if change["originally_present"] and sha == change["before_sha256"]:
        return "before"
    if sha == change["after_sha256"]:
        return "after"
    raise ValueError("Destination content changed: " + str(target))


def execute(root, recovery, approved_hash, action, guard=require_stopped):
    """Core executor; CLI holds the Linux process lock around this call."""
    if action not in ("check", "apply", "rollback"):
        raise ValueError("Unknown deployment action")
    plan, payloads = load_plan(root, recovery, approved_hash)
    journal_path = root / "execution.json"
    if journal_path.is_symlink():
        raise ValueError("Execution journal must not be a symlink")
    if journal_path.exists():
        journal = json.loads(journal_path.read_text())
        if journal.get("plan_sha256") != approved_hash:
            raise ValueError("Execution journal belongs to another plan")
    else:
        if action == "rollback":
            raise ValueError("No execution journal; refusing to remove or restore unrecorded files")
        journal = dict(plan_sha256=approved_hash, touched=[], status="prepared")
    if (not isinstance(journal.get("touched"), list)
            or any(type(i) is not int or not 0 <= i < len(plan["changes"]) for i in journal["touched"])
            or len(set(journal["touched"])) != len(journal["touched"])
            or journal.get("status") not in ("prepared", "applying", "applied", "rolling_back", "rolled_back")):
        raise ValueError("Invalid execution journal")
    if action == "apply" and journal["status"] in ("rolling_back", "rolled_back"):
        raise ValueError("Rollback has begun; finish rollback and prepare a new plan")
    guard()
    for index, change in enumerate(plan["changes"]):
        state = current_state(change)
        if state == "after" and index not in journal["touched"]:
            raise ValueError("Unrecorded candidate file; refusing to claim ownership")
    if action == "check":
        return dict(status="checked", changed_files=0)

    def save():
        atomic_write(journal_path, json.dumps(journal, indent=2).encode())

    journal["status"] = "applying" if action == "apply" else "rolling_back"
    save()
    order = range(len(plan["changes"])) if action == "apply" else reversed(journal["touched"])
    for index in order:
        guard()
        change = plan["changes"][index]
        state = current_state(change)
        target = Path(change["resolved_destination"])
        if action == "apply":
            if state == "after" or change["before_sha256"] == change["after_sha256"]:
                continue
            if index not in journal["touched"]:
                journal["touched"].append(index)
                save()  # Record intent durably before touching the destination.
            mode = change["original_mode"] if change["originally_present"] else 0o644
            atomic_write(target, payloads[index]["forward"], mode)
        elif state == "after":
            if change["originally_present"]:
                atomic_write(target, payloads[index]["rollback"], change["original_mode"])
            else:
                target.unlink()  # Only an originally absent, journal-owned, hash-matching module.
                if os.name == "posix":
                    directory = os.open(target.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
        expected = "after" if action == "apply" else "before"
        if current_state(change) != expected:
            raise ValueError("Post-write verification failed")
    journal["status"] = "applied" if action == "apply" else "rolled_back"
    save()
    return journal


@contextmanager
def process_lock(root):
    import fcntl
    # One lock per owning user, shared across different deployment plans.
    path = Path.home() / ".sm2-deployment.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        if os.fstat(lock.fileno()).st_uid != os.geteuid():
            raise ValueError("Deployment lock has another owner")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--recovery", type=Path, required=True)
    parser.add_argument("--reviewed-sha256", required=True)
    parser.add_argument("--action", choices=("check", "apply", "rollback"), default="check")
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() == 0:
        parser.error("Run on the Linux printer host as the owning user, not root")
    with process_lock(args.plan):
        result = execute(args.plan, args.recovery, args.reviewed_sha256, args.action)
    print(json.dumps(result, indent=2))
    print("Services remain stopped. Firmware was not flashed. Attended verification is required.")
