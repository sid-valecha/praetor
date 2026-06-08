from praetor.models import Task, TaskStatus


def compute_ready_set(tasks: list[Task]) -> list[Task]:
    statuses = {task.id: task.status for task in tasks}
    return [
        task
        for task in tasks
        if task.status is TaskStatus.pending
        and all(statuses.get(dependency) is TaskStatus.done for dependency in task.depends_on)
    ]


def detect_cycles(tasks: list[Task]) -> list[list[str]]:
    graph = {task.id: task.depends_on for task in tasks}
    state: dict[str, str] = {}
    path: list[str] = []
    cycles: list[list[str]] = []

    def visit(task_id: str) -> None:
        marker = state.get(task_id)
        if marker == "visiting":
            cycles.append(path[path.index(task_id) :])
        if marker:
            return
        state[task_id] = "visiting"
        path.append(task_id)
        for dependency in graph.get(task_id, []):
            visit(dependency)
        path.pop()
        state[task_id] = "done"

    for task_id in graph:
        visit(task_id)

    return cycles


def propagate_blocked(tasks: list[Task]) -> list[str]:
    blocking_ids = {
        task.id for task in tasks if task.status in {TaskStatus.failed, TaskStatus.blocked}
    }
    pending_tasks = [task for task in tasks if task.status is TaskStatus.pending]
    propagated: set[str] = set()

    def depends_on_blocked(task: Task) -> bool:
        return any(
            dependency in blocking_ids or dependency in propagated for dependency in task.depends_on
        )

    while True:
        before = len(propagated)
        propagated.update(task.id for task in pending_tasks if depends_on_blocked(task))
        if len(propagated) == before:
            break
    return [task.id for task in tasks if task.id in propagated]
