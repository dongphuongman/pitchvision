"""WORKER_ROLE dispatch for the worker image entrypoint (no broker needed)."""

from __future__ import annotations

import pytest
from pitchvision_worker.celery_app import QUEUES, create_app
from pitchvision_worker.entrypoints.__main__ import (
    EXIT_BAD_ROLE,
    EXIT_NOT_IMPLEMENTED,
    EXIT_OK,
    EXIT_UNHEALTHY,
    Role,
    UnknownRoleError,
    celery_worker_argv,
    main,
    parse_role,
)


class Recorder:
    def __init__(self) -> None:
        self.argv: list[str] | None = None

    def __call__(self, argv: list[str]) -> int:
        self.argv = argv
        return EXIT_OK


@pytest.mark.parametrize(
    ("role", "queue"),
    [("orchestrator", "orchestrate"), ("cpu", "cpu"), ("gpu", "gpu"), ("  CPU ", "cpu")],
)
def test_celery_roles_start_worker_on_their_queue(role: str, queue: str) -> None:
    run = Recorder()
    assert main([], {"WORKER_ROLE": role}, run_celery=run) == EXIT_OK
    assert run.argv is not None
    assert run.argv[0] == "worker"
    assert f"--queues={queue}" in run.argv
    assert f"--hostname={role.strip().lower()}@%h" in run.argv


def test_gpu_defaults_to_one_process_and_env_overrides() -> None:
    assert "--concurrency=1" in celery_worker_argv(Role.GPU, {})
    assert not any(a.startswith("--concurrency") for a in celery_worker_argv(Role.CPU, {}))
    assert "--concurrency=4" in celery_worker_argv(Role.CPU, {"WORKER_CONCURRENCY": "4"})


@pytest.mark.parametrize("env", [{}, {"WORKER_ROLE": ""}, {"WORKER_ROLE": "trainer"}])
def test_missing_or_unknown_role_errors(env: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    run = Recorder()
    assert main([], env, run_celery=run) == EXIT_BAD_ROLE
    assert run.argv is None
    assert "orchestrator, cpu, gpu, live, runpod" in capsys.readouterr().err
    with pytest.raises(UnknownRoleError):
        parse_role(env)


@pytest.mark.parametrize("role", ["live", "runpod"])
def test_reserved_roles_are_not_implemented_yet(role: str) -> None:
    run = Recorder()
    assert main([], {"WORKER_ROLE": role}, run_celery=run) == EXIT_NOT_IMPLEMENTED
    assert run.argv is None


def test_healthcheck_reports_ping_result() -> None:
    env = {"WORKER_ROLE": "cpu"}
    assert main(["--healthcheck"], env, celery_ping=lambda role: True) == EXIT_OK
    assert main(["--healthcheck"], env, celery_ping=lambda role: False) == EXIT_UNHEALTHY


def test_celery_app_declares_queues_and_ping_task() -> None:
    app = create_app("memory://")
    assert {q.name for q in app.conf.task_queues} == set(QUEUES) == {"orchestrate", "cpu", "gpu"}
    app.loader.import_default_modules()
    from pitchvision_worker.tasks import ping

    assert ping.name == "pitchvision_worker.ping"
    assert ping.run() == "pong"
