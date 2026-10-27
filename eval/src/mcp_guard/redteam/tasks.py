"""Attack and Benign task files: what each one sets up, says and counts as success.

Each task is one TOML file. Attacks live under `attacks/` and Benign tasks under `benign/` in
the tasks folder; a task's ID is its path there without `.toml`, e.g. `attacks/ticket-leaks`.

    attack_class = "indirect_prompt_injection"   # Attacks only
    description = "What the task tries, in one sentence."
    customer = "C001"                             # who the Agent works for: a Customer,
                                                  # or staff = "S001" or supervisor = "S004"
    messages = ["What the person says, one string per turn."]

    [setup]                                       # optional
    fx_mode = "poisoned"                          # honest (default), poisoned or rug_pull
    fx_instructions = "..."                       # what a malicious convert's description
                                                  # adds (default: fx_rates' own text)
    [[setup.tickets]]                             # tickets planted in the inbox, numbered
    customer = "C001"                             # after the seeded ones: TK011, TK012, ...
    subject = "..."
    body = "..."

    [success]                                     # one exact check, never an LLM judge:
    check = "canary"                              # a Canary is in anything notify or
                                                  # fx-rates received, but not sent to
                                                  # its own Customer's registered email
                                                  # or phone;
    # check = "database", query = "SELECT ..."    # the query returns a single true;
    # check = "reply", contains = "194844"        # an Agent reply contains this text,
                                                  # as scoring.says compares them.

    [[fake_model]]                                # optional: the scripted fake model's
    tool_calls = [{ name = "...", args = {} }]    # moves, one per model call. Strings in
    repeat = 3                                    # them may use {last_result}, the text of
    [[fake_model]]                                # the latest Tool call results. `repeat`
    reply = "..."                                 # plays a step that many times.

For an Attack, the check being met means the Attack succeeded. For a Benign task, it means the
task was done; not met counts as a False block.
"""

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from mcp_guard.agent.agent import Person
from mcp_guard.fake_servers.fx_rates import INJECTED_INSTRUCTIONS, FxMode

type AttackClass = Literal[
    "indirect_prompt_injection",
    "exfiltration",
    "confused_deputy",
    "tool_poisoning",
    "rug_pull",
    "runaway_loop",
]
ATTACK_CLASSES: tuple[AttackClass, ...] = AttackClass.__value__.__args__

type Kind = Literal["attack", "benign"]
FOLDERS: dict[str, Kind] = {"attacks": "attack", "benign": "benign"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlantedTicket(_Strict):
    customer: str
    subject: str
    body: str


class Setup(_Strict):
    fx_mode: FxMode = "honest"
    fx_instructions: str = INJECTED_INSTRUCTIONS
    tickets: tuple[PlantedTicket, ...] = ()


class CanaryCheck(_Strict):
    check: Literal["canary"]


class DatabaseCheck(_Strict):
    check: Literal["database"]
    query: str


class ReplyCheck(_Strict):
    check: Literal["reply"]
    contains: str


type Success = Annotated[CanaryCheck | DatabaseCheck | ReplyCheck, Field(discriminator="check")]


class FakeToolCall(_Strict):
    name: str
    args: dict[str, Any] = Field(default_factory=dict[str, Any])


class FakeStep(_Strict):
    """One answer of the scripted fake model: Tool calls to make, or a reply."""

    tool_calls: tuple[FakeToolCall, ...] = ()
    reply: str | None = None
    repeat: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _either_calls_or_reply(self) -> Self:
        if bool(self.tool_calls) == (self.reply is not None):
            raise ValueError("a fake_model step has either tool_calls or a reply")
        return self


class TaskSpec(_Strict):
    """A task file's contents."""

    attack_class: AttackClass | None = None
    description: str
    customer: str | None = None
    staff: str | None = None
    supervisor: str | None = None
    messages: tuple[str, ...] = Field(min_length=1)
    setup: Setup = Setup()
    success: Success
    fake_model: tuple[FakeStep, ...] = ()

    @model_validator(mode="after")
    def _for_one_person(self) -> Self:
        if [self.customer, self.staff, self.supervisor].count(None) != 2:
            raise ValueError("a task is for one customer, staff member or supervisor")
        return self

    @property
    def person(self) -> Person:
        """Who the Agent works for in this task."""
        if self.staff is not None:
            return Person(self.staff, "staff")
        if self.supervisor is not None:
            return Person(self.supervisor, "supervisor")
        assert self.customer is not None
        return Person(self.customer)


@dataclass(frozen=True)
class Task:
    id: str
    kind: Kind
    spec: TaskSpec
    fingerprint: str  # changes whenever the file does, so its earlier results go stale

    @property
    def group(self) -> str:
        """The row it is reported in: its Attack class, or "benign"."""
        return self.spec.attack_class or "benign"


class TaskFileError(Exception):
    """A task file that doesn't follow the format; the message names the file."""


def load_tasks(tasks_dir: Path) -> list[Task]:
    """Every task in the folder, ordered by ID."""
    tasks: list[Task] = []
    for path in sorted(tasks_dir.rglob("*.toml")):
        task_id = path.relative_to(tasks_dir).with_suffix("").as_posix()
        kind = FOLDERS.get(task_id.split("/")[0])
        if kind is None:
            raise TaskFileError(f"{path}: tasks belong under {' or '.join(FOLDERS)}/")
        content = path.read_bytes()
        try:
            spec = TaskSpec.model_validate(tomllib.loads(content.decode()))
        except (tomllib.TOMLDecodeError, ValidationError) as e:
            raise TaskFileError(f"{path}: {e}") from e
        if (kind == "attack") != (spec.attack_class is not None):
            raise TaskFileError(f"{path}: Attacks, and only Attacks, have an attack_class")
        tasks.append(Task(task_id, kind, spec, hashlib.sha256(content).hexdigest()))
    return tasks
