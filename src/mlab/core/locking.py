"""Short process-shared locks for editable records and task-wide admission."""
import contextlib
import fcntl
from functools import wraps


@contextlib.contextmanager
def file_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def budget_locked(function):
    """Serialize budget checks and run creation across attempts at the same task."""
    @wraps(function)
    def wrapped(lab, *args, **kwargs):
        course = lab.root.parent if (lab.root.parent / "course.json").exists() else lab.root
        with file_lock(course / ".mlab" / f"budget-{lab.task['id']}.lock"):
            return function(lab, *args, **kwargs)
    return wrapped
