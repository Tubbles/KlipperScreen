from datetime import datetime, timezone

import pytest

from ks_includes import github_slicing as slicing

# Shaped like the answers of auto-slicer2's agent methods.
OPTIONS = {
    "dimensions": [
        {"name": "material", "options": ["PLA", "PETG", "TPU"], "default": "PLA"},
        {"name": "support", "options": ["No support", "Support", "Vase"], "default": "No support"},
        {"name": "adhesion", "options": ["Skirt", "Brim"], "default": "Skirt"},
        {
            "name": "quality",
            "options": ["Draft", "Standard", "Fine", "Strong"],
            "default": "Standard",
        },
    ],
    "rules": {"tough": {"description": "PETG, thick walls", "settings": {"wall_line_count": "4"}}},
    "matrix": True,
}
DEFAULT_CELL = {
    "material": "PLA",
    "support": "No support",
    "adhesion": "Skirt",
    "quality": "Standard",
}
JOB = {
    "id": "a1b2c3",
    "artifact_id": 42,
    "model": "parts/bracket.stl",
    "selection": DEFAULT_CELL,
    "rule": None,
    "state": "queued",
    "message": "Queued",
    "path": None,
    "printer_path": None,
    "created_at": 1.0,
    "finished_at": None,
}


def result_entry(selection=None, rule=None):
    return {"selection": selection, "rule": rule, "path": "p.gcode", "printer_path": "gh/p.gcode"}


def job(**changes):
    return slicing.parse_job({**JOB, **changes})


@pytest.fixture
def options():
    return slicing.parse_options(OPTIONS)


@pytest.fixture
def choice(options):
    return slicing.initial_choice(options)


class TestRequests:
    def test_agent_request_names_agent_and_method(self):
        assert slicing.agent_request("models", {"artifact_id": 7}) == {
            "agent": "autoslice",
            "method": "autoslice.models",
            "arguments": {"artifact_id": 7},
        }

    def test_slice_arguments_with_a_selection(self):
        part = {"selection": DEFAULT_CELL}
        assert slicing.slice_arguments(7, "a.stl", part) == {
            "artifact_id": 7,
            "model": "a.stl",
            "selection": DEFAULT_CELL,
        }

    def test_slice_arguments_with_a_rule(self):
        arguments = slicing.slice_arguments(7, "a.stl", {"rule": "tough"})
        assert arguments == {"artifact_id": 7, "model": "a.stl", "rule": "tough"}


class TestResponseError:
    def test_result_is_no_error(self):
        assert slicing.response_error({"jsonrpc": "2.0", "result": [], "id": 3}) is None

    @pytest.mark.parametrize(
        "error",
        [
            {"code": 400, "message": "Agent autoslice not connected"},
            {"code": 500, "message": "Client Socket Disconnected"},
        ],
    )
    def test_missing_agent_reads_as_slicer_not_connected(self, error):
        assert slicing.response_error({"error": error}) == "Slicer not connected"

    def test_agent_error_shows_the_agents_message(self):
        error = {
            "code": 424,
            "message": "Agent autoslice RPC error",
            "data": {"code": 404, "message": "Unknown model: x.stl"},
        }
        assert slicing.response_error({"error": error}) == "Unknown model: x.stl"

    def test_agent_error_without_data_shows_moonrakers_message(self):
        error = {"code": 424, "message": "Agent autoslice RPC error", "data": None}
        assert slicing.response_error({"error": error}) == "Agent autoslice RPC error"

    def test_other_error_shows_its_message(self):
        error = {"code": -32601, "message": "Method not found"}
        assert slicing.response_error({"error": error}) == "Method not found"


class TestParsing:
    def test_projects(self):
        entry = {
            "repo": "Tubbles/model-workshop",
            "artifact_id": 4321,
            "name": "brackets",
            "commit": "0123abc",
            "created_at": "2026-10-30T06:36:07Z",
            "size": 2048,
        }
        assert slicing.parse_projects([entry]) == [
            slicing.Project(4321, "brackets", "0123abc", "2026-10-30T06:36:07Z", 2048)
        ]

    def test_models(self):
        result = [{"name": "parts/bracket.stl", "size": 100}]
        assert slicing.parse_models(result) == [slicing.Model("parts/bracket.stl", 100)]

    def test_options(self, options):
        assert [dimension.name for dimension in options.dimensions] == [
            "material",
            "support",
            "adhesion",
            "quality",
        ]
        assert options.dimensions[3].options == ("Draft", "Standard", "Fine", "Strong")
        assert options.rules == (slicing.Rule("tough", "PETG, thick walls"),)
        assert options.matrix

    def test_rule_without_description(self):
        options = slicing.parse_options({**OPTIONS, "rules": {"plain": {"settings": {}}}})
        assert options.rules == (slicing.Rule("plain", ""),)

    def test_results(self):
        results = slicing.parse_results([result_entry(rule="tough")])
        assert results == [slicing.Result(None, "tough")]

    def test_job(self):
        assert job() == slicing.Job("a1b2c3", "queued", "Queued", None, DEFAULT_CELL, None)


class TestEventJob:
    def event(self, **changes):
        return {"agent": "autoslice", "event": "autoslice.job", "data": JOB, **changes}

    def test_job_event_for_the_followed_job(self):
        assert slicing.event_job(self.event(), "a1b2c3") == job()

    def test_other_job(self):
        assert slicing.event_job(self.event(), "ffffff") is None

    def test_other_agent(self):
        assert slicing.event_job(self.event(agent="moonagent"), "a1b2c3") is None

    def test_connection_event(self):
        event = {"agent": "autoslice", "event": "disconnected"}
        assert slicing.event_job(event, "a1b2c3") is None


class TestThumbnail:
    def test_decodes_base64(self):
        assert slicing.decode_thumbnail({"png": "iVBORw0KGgo="}) == b"\x89PNG\r\n\x1a\n"

    @pytest.mark.parametrize("result", [{"png": "not base64!"}, {}, {"png": None}])
    def test_invalid(self, result):
        assert slicing.decode_thumbnail(result) is None


class TestChoice:
    def test_starts_on_the_defaults(self, choice):
        assert choice == slicing.Choice(DEFAULT_CELL, None)

    def test_option_replaces_one_dimension(self, choice):
        changed = slicing.choose_option(choice, "material", "PETG")
        assert changed.selection == {**DEFAULT_CELL, "material": "PETG"}

    def test_rule_deactivates_the_matrix(self, choice):
        ruled = slicing.choose_rule(choice, "tough")
        assert ruled.rule == "tough"
        assert not slicing.option_active(ruled, "material", "PLA")

    def test_option_deselects_the_rule_and_keeps_the_cell(self, choice):
        ruled = slicing.choose_rule(slicing.choose_option(choice, "quality", "Fine"), "tough")
        back = slicing.choose_option(ruled, "material", "TPU")
        assert back.rule is None
        assert back.selection == {**DEFAULT_CELL, "material": "TPU", "quality": "Fine"}

    def test_one_active_option_per_dimension(self, choice):
        active = [
            option
            for option in ("PLA", "PETG", "TPU")
            if slicing.option_active(choice, "material", option)
        ]
        assert active == ["PLA"]

    def test_slice_choice_of_the_matrix(self, choice, options):
        assert slicing.slice_choice(choice, options) == {"selection": DEFAULT_CELL}

    def test_slice_choice_of_a_rule(self, choice, options):
        ruled = slicing.choose_rule(choice, "tough")
        assert slicing.slice_choice(ruled, options) == {"rule": "tough"}

    def test_rules_only_project_needs_a_rule(self, choice):
        options = slicing.parse_options({**OPTIONS, "matrix": False})
        assert slicing.slice_choice(choice, options) is None
        ruled = slicing.choose_rule(choice, "tough")
        assert slicing.slice_choice(ruled, options) == {"rule": "tough"}


class TestSlicedMarks:
    def test_option_marked_with_the_other_current_options(self, choice):
        results = slicing.parse_results([result_entry({**DEFAULT_CELL, "material": "PETG"})])
        assert slicing.option_sliced(choice, results, "material", "PETG")
        assert not slicing.option_sliced(choice, results, "material", "PLA")
        finer = slicing.choose_option(choice, "quality", "Fine")
        assert not slicing.option_sliced(finer, results, "material", "PETG")

    def test_rule_marked(self):
        results = slicing.parse_results([result_entry(rule="tough")])
        assert slicing.rule_sliced("tough", results)
        assert not slicing.rule_sliced("other", results)

    def test_current_cell_sliced(self, choice, options):
        results = slicing.parse_results([result_entry(DEFAULT_CELL)])
        assert slicing.choice_sliced(choice, options, results)
        assert not slicing.choice_sliced(slicing.choose_rule(choice, "tough"), options, results)

    def test_cell_of_a_rules_only_project_is_never_current(self, choice):
        options = slicing.parse_options({**OPTIONS, "matrix": False})
        results = slicing.parse_results([result_entry(DEFAULT_CELL)])
        assert not slicing.choice_sliced(choice, options, results)

    def test_marked_text(self):
        assert slicing.marked("PLA", True) == "PLA ✓"
        assert slicing.marked("PLA", False) == "PLA"

    def test_slice_label(self):
        assert slicing.slice_label(True) == "Slice again"
        assert slicing.slice_label(False) == "Slice"


class TestJob:
    def test_late_answer_does_not_undo_an_event(self):
        current = job(state="done")
        assert slicing.advance(current, job(state="slicing")) == current

    def test_newer_state_wins(self):
        assert slicing.advance(job(), job(state="slicing")).state == "slicing"

    def test_same_state_takes_the_newer_report(self):
        newer = job(state="slicing", message="Slicing")
        assert slicing.advance(job(state="slicing"), newer) == newer

    def test_waits_for_the_file_after_done(self):
        done = job(state="done", printer_path="gh/a/b.gcode")
        assert slicing.waiting_for_file(done, False)
        assert not slicing.can_print(done, False)
        assert not slicing.waiting_for_file(done, True)
        assert slicing.can_print(done, True)

    def test_error_neither_waits_nor_prints(self):
        failed = job(state="error", message="CuraEngine error")
        assert slicing.is_finished(failed)
        assert not slicing.waiting_for_file(failed, False)
        assert not slicing.can_print(failed, True)

    @pytest.mark.parametrize(
        "state, file_ready, text",
        [
            ("queued", False, "Queued"),
            ("slicing", False, "Slicing"),
            ("error", False, "Slicing failed"),
            ("done", False, "Waiting for the file to reach the printer"),
            ("done", True, "Ready to print"),
        ],
    )
    def test_status(self, state, file_ready, text):
        assert slicing.job_status(job(state=state), file_ready) == text

    def test_choice_text_of_a_cell(self):
        assert slicing.job_choice_text(job()) == "PLA, No support, Skirt, Standard"

    def test_choice_text_of_a_rule(self):
        assert slicing.job_choice_text(job(selection=None, rule="tough")) == "tough"


class TestFormatting:
    NOW = datetime(2026, 10, 30, 8, 0, 0, tzinfo=timezone.utc)

    def test_short_commit(self):
        assert slicing.short_commit("0123456789abcdef") == "0123456"

    def test_age_seconds(self):
        assert slicing.age_seconds("2026-10-30T06:36:07Z", self.NOW) == 5033

    def test_age_of_a_clock_ahead_is_zero(self):
        assert slicing.age_seconds("2026-10-30T08:00:30Z", self.NOW) == 0

    @pytest.mark.parametrize(
        "seconds, text",
        [
            (59, "just now"),
            (60, "1 minute ago"),
            (3599, "59 minutes ago"),
            (7200, "2 hours ago"),
            (86400, "1 day ago"),
            (3 * 86400 + 5, "3 days ago"),
        ],
    )
    def test_format_age(self, seconds, text):
        assert slicing.format_age(seconds) == text
