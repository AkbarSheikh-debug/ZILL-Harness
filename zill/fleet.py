"""Day 5 — The fleet: many harnesses, many directories, one call.

Concept: a Harness is bound to one working directory and shares nothing with
another, so independent jobs run side by side on threads. The model call is
network wait, not CPU, which is exactly what a thread pool is good at.

Design rules:
  * The factory is injected: run_fleet never decides model, policy or tools,
    it only calls make_harness(workdir) once per job.
  * One job's failure is a result, not a crash. Exceptions become ok=False
    reports and every other job keeps running.
  * Results come back in the order the jobs went in, however they finish.
"""

from concurrent.futures import ThreadPoolExecutor


def run_fleet(jobs, make_harness, max_workers=4):
    """Run each {name, workdir, task} job in its own harness; return results in order.

    Each result is {"name", "ok", "report"}: the final text on success, or
    "<ExceptionType>: <message>" on failure.
    """
    def run_one(job):
        try:
            report = make_harness(job["workdir"]).run(job["task"])
        except Exception as err:  # reported, so the other jobs are unaffected
            return {"name": job["name"], "ok": False, "report": f"{type(err).__name__}: {err}"}
        return {"name": job["name"], "ok": True, "report": report}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(run_one, jobs))
