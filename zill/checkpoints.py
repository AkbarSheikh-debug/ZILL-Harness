"""Checkpoints: every change the agent makes can be taken back.

Concept: before a state-changing tool runs, the harness snapshots the
working directory into a shadow git repository at .zill/checkpoints. It has
its own git dir and index and shares only the work tree, so the user's own
.git, index and history are never touched. Restoring moves the files back.

Design rules:
  * An unchanged tree is never recommitted, so consecutive snapshots always
    differ and undo walks back one change at a time, in time order: it finds
    the snapshot matching the work tree and steps to the one before it.
  * Restoring never loses anything: the current state is snapshotted first,
    so every restore can itself be restored.
  * Rollback happens only when the user asks (zill undo, /undo). The agent
    has no tool for it.
  * Ignored: .zill/, .git/, dependency and cache folders, and whatever the
    project's .gitignore files ignore. GIT_* variables from the environment
    are dropped, so an inherited GIT_INDEX_FILE cannot redirect writes.
  * Needs git on PATH. Without it, checkpoints are simply unavailable.
"""

import os
import shutil
import subprocess

CHECKPOINT_DIR = ".zill/checkpoints"
EXCLUDES = [".zill/", "node_modules/", ".venv/", "__pycache__/"]
TOOL_PREFIX = "tool: "
GIT_TIMEOUT = 60


class Checkpoints:
    """Snapshots of one working directory in a shadow git repository."""

    def __init__(self, workdir):
        self.workdir = os.path.realpath(workdir)
        self.git_dir = os.path.join(self.workdir, CHECKPOINT_DIR)
        self.available = shutil.which("git") is not None

    def _git(self, *args, check=True):
        """Run one git command against the shadow repository."""
        command = ["git", f"--git-dir={self.git_dir}", f"--work-tree={self.workdir}",
                   "-c", "core.autocrlf=false", "-c", "core.safecrlf=false",
                   "-c", "commit.gpgsign=false", "-c", "user.name=ZILL",
                   "-c", "user.email=zill@localhost",
                   "-c", f"core.hooksPath={os.path.join(self.git_dir, 'no-hooks')}", *args]
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        try:
            proc = subprocess.run(command, cwd=self.workdir, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", env=env,
                                  timeout=GIT_TIMEOUT)
        except subprocess.TimeoutExpired as err:
            raise RuntimeError(f"git {args[0]} timed out after {GIT_TIMEOUT}s") from err
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {proc.stderr.strip()[:300]}")
        return proc

    def _ensure(self):
        """Create the shadow repository on first use."""
        if not self.available:
            raise RuntimeError("checkpoints need git on PATH")
        if not os.path.isdir(os.path.join(self.git_dir, "objects")):
            os.makedirs(self.git_dir, exist_ok=True)
            self._git("init", "-q")
            os.makedirs(os.path.join(self.git_dir, "info"), exist_ok=True)
            with open(os.path.join(self.git_dir, "info", "exclude"), "a", encoding="utf-8") as f:
                f.write("\n".join(EXCLUDES) + "\n")

    def _tree(self):
        """Stage the whole work tree and return its tree hash."""
        self._git("add", "-A")
        return self._git("write-tree").stdout.strip()

    def snapshot(self, label):
        """Commit the work tree under label; return the checkpoint id."""
        self._ensure()
        tree = self._tree()
        head = self._git("rev-parse", "-q", "--verify", "HEAD^{tree}", check=False).stdout.strip()
        if tree != head:
            self._git("commit", "-q", "--no-verify", "--allow-empty", "-m", label)
        return self._git("rev-parse", "--short", "HEAD").stdout.strip()

    def list(self, limit=20):
        """Return [(id, age, label)], newest first."""
        if not self.available or not os.path.isdir(os.path.join(self.git_dir, "objects")):
            return []
        log = self._git("log", f"-n{limit}", "--format=%h%x09%cr%x09%s", check=False).stdout
        return [tuple(line.split("\t", 2)) for line in log.splitlines() if line]

    def restore(self, ref=None):
        """Move the work tree to checkpoint ref, or undo the latest change; return the id."""
        self._ensure()
        if ref is None:
            current = self._tree()
            log = self._git("log", "--format=%h %T %s", check=False).stdout.splitlines()
            snapshots = [(parts[0], parts[1]) for parts in (line.split(" ", 2) for line in log)
                         if parts[2].startswith(TOOL_PREFIX)]  # newest first
            # Step one snapshot older than where the work tree is now; if the tree
            # has changed since the newest snapshot, undo to that snapshot.
            here = next((i for i, (_, tree) in enumerate(snapshots) if tree == current), None)
            older = snapshots if here is None else snapshots[here + 1:]
            if not older:
                raise RuntimeError("nothing to undo")
            ref = older[0][0]
        target = self._git("rev-parse", "-q", "--verify", f"{ref}^{{commit}}",
                           check=False).stdout.strip()
        if not target:
            raise RuntimeError(f"no checkpoint named {ref}")
        self.snapshot(f"restore: before restoring {ref}")
        self._git("read-tree", "-u", "--reset", target)
        return ref
