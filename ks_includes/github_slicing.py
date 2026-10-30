"""Pure logic of the GitHub panel, which slices CI artifacts of a CAD repository on demand.

The slicing runs on another machine, connected to Moonraker as the agent "autoslice". Requests go
through Moonraker's server.extensions.request, and job changes come back as notify_agent_event.
"""

import base64
import binascii
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional

AGENT = "autoslice"
JOB_EVENT = "autoslice.job"
# What Moonraker answers when the agent is not connected, or when it drops a request it was given.
NOT_CONNECTED_ERRORS = ((400, f"Agent {AGENT} not connected"), (500, "Client Socket Disconnected"))
# An error the agent itself answered. Moonraker puts the agent's {code, message} in data.
AGENT_ERROR_CODE = 424
# Jobs only move forward: queued, slicing, then done or error.
STATE_RANKS = {"queued": 0, "slicing": 1, "done": 2, "error": 2}
SLICED_MARK = "✓"


@dataclass(frozen=True)
class Project:
    artifact_id: int
    name: str
    commit: str
    created_at: str
    size: int


@dataclass(frozen=True)
class Model:
    name: str
    size: int


@dataclass(frozen=True)
class Dimension:
    name: str
    options: tuple
    default: str


@dataclass(frozen=True)
class Rule:
    name: str
    description: str


@dataclass(frozen=True)
class Options:
    dimensions: tuple
    rules: tuple
    matrix: bool


@dataclass(frozen=True)
class Result:
    """A gcode already sliced for a model, from a matrix selection or from a rule."""

    selection: Optional[dict]
    rule: Optional[str]


@dataclass(frozen=True)
class Job:
    id: str
    state: str
    message: str
    printer_path: Optional[str]
    selection: Optional[dict]
    rule: Optional[str]


@dataclass(frozen=True)
class Choice:
    """The matrix cell picked in the options, and the rule that replaces it while one is picked."""

    selection: dict
    rule: Optional[str]


# Requests


def agent_request(method, arguments):
    """Parameters of server.extensions.request for one of the agent's methods."""
    return {"agent": AGENT, "method": f"{AGENT}.{method}", "arguments": arguments}


def model_arguments(artifact_id, model):
    return {"artifact_id": artifact_id, "model": model}


def slice_arguments(artifact_id, model, choice_part):
    return {**model_arguments(artifact_id, model), **choice_part}


# Responses


def response_error(response):
    """None when a Moonraker response holds a result, otherwise the message to show."""
    if "result" in response:
        return None
    error = response.get("error") or {}
    code, message = error.get("code"), error.get("message", "")
    if (code, message) in NOT_CONNECTED_ERRORS:
        return _("Slicer not connected")
    data = error.get("data")
    if code == AGENT_ERROR_CODE and isinstance(data, dict) and data.get("message"):
        return data["message"]
    return message


def parse_projects(result):
    return [
        Project(
            entry["artifact_id"], entry["name"], entry["commit"], entry["created_at"], entry["size"]
        )
        for entry in result
    ]


def parse_models(result):
    return [Model(entry["name"], entry["size"]) for entry in result]


def parse_dimension(entry):
    return Dimension(entry["name"], tuple(entry["options"]), entry["default"])


def parse_options(result):
    return Options(
        tuple(parse_dimension(entry) for entry in result["dimensions"]),
        tuple(Rule(name, rule.get("description", "")) for name, rule in result["rules"].items()),
        bool(result["matrix"]),
    )


def parse_results(result):
    return [Result(entry["selection"], entry["rule"]) for entry in result]


def parse_job(result):
    return Job(
        result["id"],
        result["state"],
        result.get("message") or "",
        result.get("printer_path"),
        result.get("selection"),
        result.get("rule"),
    )


def event_job(data, job_id):
    """The job in a notify_agent_event, None when the event is not about job_id."""
    if data.get("agent") != AGENT or data.get("event") != JOB_EVENT:
        return None
    job = parse_job(data["data"])
    return job if job.id == job_id else None


def decode_thumbnail(result):
    """PNG bytes of a thumbnail answer, None when it holds no valid base64."""
    try:
        return base64.b64decode(result["png"], validate=True)
    except (KeyError, TypeError, binascii.Error):
        return None


# Options


def initial_choice(options):
    return Choice({dimension.name: dimension.default for dimension in options.dimensions}, None)


def choose_option(choice, dimension, option):
    """Picking an option returns to the matrix."""
    return Choice({**choice.selection, dimension: option}, None)


def choose_rule(choice, rule):
    """A rule replaces the matrix, which keeps its cell for when an option is picked again."""
    return replace(choice, rule=rule)


def option_active(choice, dimension, option):
    return choice.rule is None and choice.selection.get(dimension) == option


def slice_choice(choice, options):
    """The selection or rule part of a slice request, None while nothing can be sliced."""
    if choice.rule is not None:
        return {"rule": choice.rule}
    if options.matrix:
        return {"selection": dict(choice.selection)}
    return None


# Results already sliced


def selection_sliced(selection, results):
    return any(result.selection == selection for result in results)


def option_sliced(choice, results, dimension, option):
    """Whether the cell of this option and the other dimensions' current options is sliced."""
    return selection_sliced({**choice.selection, dimension: option}, results)


def rule_sliced(rule, results):
    return any(result.rule == rule for result in results)


def choice_sliced(choice, options, results):
    if choice.rule is not None:
        return rule_sliced(choice.rule, results)
    return options.matrix and selection_sliced(choice.selection, results)


def marked(text, sliced):
    return f"{text} {SLICED_MARK}" if sliced else text


def slice_label(sliced):
    return _("Slice again") if sliced else _("Slice")


# Jobs


def advance(current, incoming):
    """The newer of two reports of one job. Events and answers can arrive out of order."""
    if STATE_RANKS.get(incoming.state, 0) < STATE_RANKS.get(current.state, 0):
        return current
    return incoming


def is_finished(job):
    return job.state in ("done", "error")


def waiting_for_file(job, file_ready):
    """Whether a finished gcode is still on its way to the printer."""
    return job.state == "done" and bool(job.printer_path) and not file_ready


def can_print(job, file_ready):
    return job.state == "done" and file_ready


def job_choice_text(job):
    if job.rule is not None:
        return job.rule
    return ", ".join((job.selection or {}).values())


def job_status(job, file_ready):
    if job.state == "queued":
        return _("Queued")
    if job.state == "slicing":
        return _("Slicing")
    if job.state == "error":
        return _("Slicing failed")
    if job.state != "done":
        return job.state
    return _("Ready to print") if file_ready else _("Waiting for the file to reach the printer")


# Formatting


def short_commit(commit):
    return commit[:7]


def age_seconds(created_at, now):
    """Seconds from an ISO 8601 time such as 2026-10-30T06:36:07Z to now, an aware datetime."""
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return max(0, int((now - created).total_seconds()))


def format_age(seconds):
    if seconds < 60:
        return _("just now")
    if seconds < 3600:
        count = seconds // 60
        unit = ngettext("minute", "minutes", count)
    elif seconds < 86400:
        count = seconds // 3600
        unit = ngettext("hour", "hours", count)
    else:
        count = seconds // 86400
        unit = ngettext("day", "days", count)
    return _("%s ago") % f"{count} {unit}"
