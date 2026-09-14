"""Jobs and terminals: processes the agent starts now and checks on later.

Concept: a dev server, a watch build or a long test run would hold a bash call
until it times out. bash with background "true" starts the command as a job
and returns at once; job_output, job_list and job_kill check on it later. A
terminal is a shell that stays open between calls, so cd, environment
variables and an activated virtualenv persist from one terminal_send to the next.

Design rules:
  * Every process runs in the working directory with bash's shell, and bash's
    policy applies: terminal_send's input is a "command" argument, so the
    deny patterns and network classification see it.
  * Job output goes to .zill/jobs/job-<id>.log, so nothing is lost and a
    result shows only a bounded tail.
  * A finished job is announced once, on the next tool result, so the model
    hears about it without polling.
  * terminal_send follows each command with a sentinel echo, so it returns
    that command's output and exit code, or the output so far when the
    command is still running after the wait.
  * close() kills every process tree this harness started.
"""

import codecs
import itertools
import os
import re
import signal
import subprocess
import threading
import time

from .tools import SHELL, tool

JOB_DIR = ".zill/jobs"
TAIL_CHARS = 8_000
KEEP_CHARS = 200_000  # a terminal's buffer; older output is dropped
MARK = "__ZILL_END_{}__"
MARK_LINE = re.compile(r"^ *__ZILL_END_\d+__ (-?\d+) *$", re.M)
GROUP = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
         else {"start_new_session": True})


def _kill(proc):
    """Kill proc and every process it started."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    for stream in (proc.stdin, proc.stdout):
        if stream:
            stream.close()


def _tail(text, chars=TAIL_CHARS):
    """Return the end of text, noting how much was left out."""
    return text if len(text) <= chars else f"... [{len(text) - chars} earlier chars]\n{text[-chars:]}"


class Processes:
    """The background jobs and terminals of one harness."""

    def __init__(self, workdir):
        self.workdir = workdir
        self.jobs, self.terminals, self._announced = {}, {}, set()
        self._ids = itertools.count(1)

    def start_job(self, command):
        """Start command in the background; return how to check on it."""
        job_id = str(next(self._ids))
        log = os.path.join(self.workdir, JOB_DIR, f"job-{job_id}.log")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "wb") as handle:
            proc = subprocess.Popen(command, shell=True, cwd=self.workdir, stdin=subprocess.DEVNULL,
                                    stdout=handle, stderr=subprocess.STDOUT, **GROUP)
        self.jobs[job_id] = {"command": command, "proc": proc, "log": log, "started": time.time()}
        return f"Started background job {job_id}: {command}\nCheck it with job_output {job_id}."

    def job(self, job_id):
        """Return the job named job_id, or raise ValueError."""
        job = self.jobs.get(str(job_id).strip())
        if job is None:
            raise ValueError(f"no job {job_id}; job_list shows the jobs")
        return job

    def terminal(self, term_id):
        """Return the terminal named term_id, or raise ValueError."""
        term = self.terminals.get(str(term_id).strip())
        if term is None:
            raise ValueError(f"no terminal {term_id}; terminal_open starts one")
        return term

    @staticmethod
    def status(item):
        code = item["proc"].poll()
        return "running" if code is None else f"exited {code}"

    def rows(self):
        """Describe every job and terminal, newest last."""
        now = time.time()
        return ([{"id": i, "kind": "job", "command": j["command"], "status": self.status(j),
                  "seconds": round(now - j["started"])} for i, j in self.jobs.items()]
                + [{"id": i, "kind": "terminal", "command": t["last"], "status": self.status(t),
                    "seconds": round(now - t["started"])} for i, t in self.terminals.items()])

    def notices(self):
        """Return a line for each job that finished since the last notice."""
        lines = []
        for job_id, job in self.jobs.items():
            if job_id not in self._announced and job["proc"].poll() is not None:
                self._announced.add(job_id)
                lines.append(f"[ZILL: background job {job_id} ({job['command'][:60]}) "
                             f"{self.status(job)}; job_output {job_id} shows its output]")
        return lines

    def open_terminal(self):
        """Start a persistent shell and a thread that collects its output; return its id."""
        term_id = f"t{next(self._ids)}"
        command = [SHELL, "/Q", "/K"] if os.name == "nt" else [SHELL]
        proc = subprocess.Popen(command, cwd=self.workdir, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env={**os.environ, "PROMPT": "$S"}, **GROUP)
        term = {"proc": proc, "text": "", "base": 0, "read": 0, "sent": 0, "last": "",
                "started": time.time(), "lock": threading.Lock()}

        def pump():
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            try:
                for chunk in iter(lambda: proc.stdout.read1(4096), b""):
                    with term["lock"]:
                        term["text"] += decoder.decode(chunk)
                        if len(term["text"]) > KEEP_CHARS:
                            cut = len(term["text"]) - KEEP_CHARS
                            term["text"], term["base"] = term["text"][cut:], term["base"] + cut
            except (OSError, ValueError):  # the terminal was closed under us
                pass

        threading.Thread(target=pump, daemon=True).start()
        self.terminals[term_id] = term
        return term_id

    def _since(self, term, start):
        """Return a terminal's output from absolute offset start, sentinels shown as exit codes."""
        with term["lock"]:
            text = term["text"][max(0, start - term["base"]):]
            term["read"] = term["base"] + len(term["text"])
        text = MARK_LINE.sub(lambda m: f"[exit code: {m.group(1)}]", text.replace("\r", ""))
        return "\n".join(line for line in text.split("\n") if line.strip())

    def send(self, term_id, command, wait):
        """Run command in a terminal; return its output once done, or so far after wait seconds."""
        term = self.terminal(term_id)
        term["sent"] += 1
        term["last"] = command
        mark = MARK.format(term["sent"])
        with term["lock"]:
            start = term["base"] + len(term["text"])
        end, code = ("\r\n", "%errorlevel%") if os.name == "nt" else ("\n", "$?")
        try:
            term["proc"].stdin.write(f"{command}{end}echo {mark} {code}{end}".encode("utf-8"))
            term["proc"].stdin.flush()
        except OSError:
            return f"ERROR: terminal {term_id} has closed; open a new one"
        deadline, done = time.monotonic() + max(0.0, wait), False
        while not done and time.monotonic() < deadline and term["proc"].poll() is None:
            time.sleep(0.05)
            with term["lock"]:
                done = mark in term["text"][max(0, start - term["base"]):]
        output = _tail(self._since(term, start))
        if term["proc"].poll() is not None:
            return f"{output}\n[the shell exited with code {term['proc'].returncode}]".strip()
        if not done:
            return f"{output}\n[still running after {wait:g}s; terminal_read {term_id} shows more]"
        return output or "(no output)"

    def close(self):
        """Kill every job and terminal this harness started."""
        for item in list(self.jobs.values()) + list(self.terminals.values()):
            _kill(item["proc"])


def process_tools(processes):
    """Return the job and terminal tools over processes."""
    @tool("Show a background job's status and the end of its output.", risk="read",
          job="Job id from bash(background) or job_list",
          chars=f"Most trailing characters to show (default {TAIL_CHARS})")
    def job_output(job, chars=str(TAIL_CHARS)):
        entry = processes.job(job)
        if entry["proc"].poll() is not None:
            processes._announced.add(str(job).strip())
        with open(entry["log"], encoding="utf-8", errors="replace") as f:
            text = _tail(f.read(), int(chars))
        return f"job {job}: {processes.status(entry)}\n{text or '(no output yet)'}"

    @tool("List background jobs and open terminals with their status.", risk="read")
    def job_list():
        return "\n".join(f"{r['id']}  {r['kind']:<8} {r['status']:<10} {r['seconds']:>5}s  "
                         f"{r['command']}" for r in processes.rows()) or "(no jobs or terminals)"

    @tool("Stop a background job and everything it started.", risk="write",
          job="Job id from job_list")
    def job_kill(job):
        entry = processes.job(job)
        _kill(entry["proc"])
        return f"job {job}: {processes.status(entry)}"

    @tool("Open a persistent shell. Directory changes and environment variables carry over "
          "between terminal_send calls. Returns the terminal id.", risk="execute")
    def terminal_open():
        return f"Opened terminal {processes.open_terminal()}"

    @tool("Run a command in a persistent terminal and return its output and exit code.",
          terminal="Terminal id from terminal_open", command="The shell command to run",
          wait="Seconds to wait for it to finish before returning (default 30, at most 600)")
    def terminal_send(terminal, command, wait="30"):
        return processes.send(terminal, command, min(float(wait), 600))

    @tool("Show a terminal's output since the last send or read.", risk="read",
          terminal="Terminal id")
    def terminal_read(terminal):
        term = processes.terminal(terminal)
        return _tail(processes._since(term, term["read"])) or "(no new output)"

    @tool("Close a persistent terminal.", risk="write", terminal="Terminal id")
    def terminal_close(terminal):
        _kill(processes.terminal(terminal)["proc"])
        return f"Closed terminal {terminal}"

    return [job_output, job_list, job_kill, terminal_open, terminal_send, terminal_read,
            terminal_close]
