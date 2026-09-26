"""S-08 — Abstention.

Covers AC-044, AC-045, AC-046, AC-047, AC-048, AC-080.

Abstention is a terminal verdict: it adds an action value and the guarantee that
the action never leads to a rename. AC-080 is the negative boundary the human
settled explicitly — a thin name is better than a half-renamed folder, so low
model confidence on its own is never a reason to decline.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from shotname.abstain import DEFAULT_MIN_ENTROPY, abstain_reason, image_entropy
from shotname.hashing import content_hash
from shotname.model import ModelRequest
from shotname.plan import Action, Reason
from shotname.settings import DEFAULT_MIN_OCR_CHARS, RunSettings
from tests.conftest import Inject
from tests.support.cli import plan_records, records_by_name, run_cli
from tests.support.corpus import build_corpus
from tests.support.fakes import (
    STOP_REASON_END_TURN,
    STOP_REASON_REFUSAL,
    FakeOcr,
    FakeTransport,
    ocr_result,
    suggestion_text,
)
from tests.support.images import write_noise_png, write_solid_png

HIGH_ENTROPY = DEFAULT_MIN_ENTROPY * 10 + 1.0


def _settings(root: Path) -> RunSettings:
    return RunSettings(root=root)


# --------------------------------------------------------------------------- #
# AC-044
# --------------------------------------------------------------------------- #


def test_ac044_min_ocr_chars_default_is_12() -> None:
    """AC-044: the threshold the criterion names."""
    assert DEFAULT_MIN_OCR_CHARS == 12
    assert RunSettings(root=Path(".")).min_ocr_chars == 12


def test_ac044_abstention_needs_both_thin_text_and_low_entropy(
    corpus_root: Path,
) -> None:
    """AC-044: the two thresholds are an AND, not an OR."""
    settings = _settings(corpus_root)

    assert (
        abstain_reason(ocr_char_count=5, entropy=0.0, settings=settings)
        is Reason.EMPTY_IMAGE
    )
    assert abstain_reason(ocr_char_count=5, entropy=HIGH_ENTROPY, settings=settings) is None
    assert abstain_reason(ocr_char_count=500, entropy=0.0, settings=settings) is None
    assert (
        abstain_reason(
            ocr_char_count=DEFAULT_MIN_OCR_CHARS, entropy=0.0, settings=settings
        )
        is None
    ), "the threshold is exclusive: 12 characters is enough"


def test_ac044_blank_image_has_lower_entropy_than_a_busy_one(tmp_path: Path) -> None:
    """AC-044: the entropy measure separates a blank capture from a real one."""
    blank = write_solid_png(tmp_path / "blank.png")
    busy = write_noise_png(tmp_path / "busy.png", seed=11)

    assert image_entropy(blank) < DEFAULT_MIN_ENTROPY
    assert image_entropy(busy) > DEFAULT_MIN_ENTROPY


def test_ac044_near_blank_capture_is_recorded_as_abstain(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-044: end to end, the near-blank fixture abstains."""
    corpus = build_corpus(corpus_root)
    ocr = FakeOcr(results={corpus.near_blank: ocr_result("hi")})
    inject(probe=corpus.probe(), ocr=ocr, transport=FakeTransport())

    result = run_cli(runner, corpus_root, "--wait")
    record = records_by_name(plan_records())[corpus.near_blank.name]

    assert result.exit_code == 0
    assert record.action is Action.ABSTAIN
    assert record.reason == Reason.EMPTY_IMAGE.value
    assert record.proposed_name is None


# --------------------------------------------------------------------------- #
# AC-045
# --------------------------------------------------------------------------- #


def test_ac045_abstained_file_keeps_its_name_after_apply(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-045: abstaining is never a rename, even with --apply."""
    corpus = build_corpus(corpus_root)
    ocr = FakeOcr(results={corpus.near_blank: ocr_result("hi")})
    inject(probe=corpus.probe(), ocr=ocr, transport=FakeTransport())

    result = run_cli(runner, corpus_root, "--apply", "--wait")

    assert result.exit_code == 0
    assert records_by_name(plan_records())[corpus.near_blank.name].action is Action.ABSTAIN
    assert corpus.near_blank.exists()


# --------------------------------------------------------------------------- #
# AC-046
# --------------------------------------------------------------------------- #


def test_ac046_model_refusal_abstains_and_does_not_affect_the_exit_code(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-046: a refusal is reason "model_declined" and the run carries on."""
    corpus = build_corpus(corpus_root)
    refused = content_hash(corpus.narrow_space)

    def stop_reason_for(request: ModelRequest) -> str:
        return STOP_REASON_REFUSAL if request.custom_id == refused else STOP_REASON_END_TURN

    inject(
        probe=corpus.probe(),
        ocr=FakeOcr(),
        transport=FakeTransport(stop_reason_for=stop_reason_for),
    )

    result = run_cli(runner, corpus_root, "--apply", "--sync")
    records = records_by_name(plan_records())

    assert result.exit_code == 0
    assert records[corpus.narrow_space.name].action is Action.ABSTAIN
    assert records[corpus.narrow_space.name].reason == Reason.MODEL_DECLINED.value
    assert corpus.narrow_space.exists()
    assert records[corpus.uppercase_ext.name].action is Action.RENAME


def test_ac046_refusal_detected_in_the_response_text_also_abstains(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-046: the refusal check applies to the text, not only the stop reason."""
    corpus = build_corpus(corpus_root)
    refused = content_hash(corpus.narrow_space)

    def text_for(request: ModelRequest) -> str:
        if request.custom_id == refused:
            return "I can't help with identifying the contents of this image."
        return suggestion_text()

    inject(
        probe=corpus.probe(),
        ocr=FakeOcr(),
        transport=FakeTransport(text_for=text_for),
    )

    result = run_cli(runner, corpus_root, "--apply", "--sync")
    record = records_by_name(plan_records())[corpus.narrow_space.name]

    assert result.exit_code == 0
    assert record.action is Action.ABSTAIN
    assert record.reason == Reason.MODEL_DECLINED.value


# --------------------------------------------------------------------------- #
# AC-047
# --------------------------------------------------------------------------- #


def test_ac047_schema_failure_is_retried_once_then_recorded_as_error(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-047: two validation failures mean action "error", and the run continues."""
    corpus = build_corpus(corpus_root)
    broken = content_hash(corpus.narrow_space)

    def text_for(request: ModelRequest) -> str:
        if request.custom_id == broken:
            return "the screenshot shows a slack thread"  # prose, not the schema
        return suggestion_text()

    transport = FakeTransport(text_for=text_for)
    inject(probe=corpus.probe(), ocr=FakeOcr(), transport=transport)

    result = run_cli(runner, corpus_root, "--apply", "--sync")
    records = records_by_name(plan_records())

    assert result.exit_code == 0
    assert len(transport.requests_for(broken)) == 2, "the schema failure was not retried once"
    assert records[corpus.narrow_space.name].action is Action.ERROR
    assert records[corpus.narrow_space.name].reason == Reason.SCHEMA_INVALID.value
    assert corpus.narrow_space.exists()
    assert records[corpus.uppercase_ext.name].action is Action.RENAME


# --------------------------------------------------------------------------- #
# AC-048
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "flags",
    [
        ("--apply", "--wait"),
        ("--apply", "--sync"),
        ("--apply", "--sync", "--all-images"),
        ("--apply", "--wait", "--recursive"),
        ("--apply", "--sync", "--materialize"),
    ],
)
def test_ac048_abstained_and_errored_files_are_never_renamed(
    runner: CliRunner, corpus_root: Path, inject: Inject, flags: tuple[str, ...]
) -> None:
    """AC-048: no combination of flags turns a non-verdict into a rename."""
    corpus = build_corpus(corpus_root)
    broken = content_hash(corpus.hand_renamed)

    def text_for(request: ModelRequest) -> str:
        return "not json at all" if request.custom_id == broken else suggestion_text()

    ocr = FakeOcr(results={corpus.near_blank: ocr_result("hi")})
    inject(probe=corpus.probe(), ocr=ocr, transport=FakeTransport(text_for=text_for))

    result = run_cli(runner, corpus_root, *flags)
    records = plan_records()

    assert result.exit_code == 0
    declined = [r for r in records if r.action in {Action.ABSTAIN, Action.ERROR}]
    assert declined, "no file abstained or errored, so the guarantee is untested"
    for record in declined:
        assert record.path.exists(), f"{record.path.name} was renamed despite {record.action}"
        assert record.proposed_name is None


# --------------------------------------------------------------------------- #
# AC-080
# --------------------------------------------------------------------------- #


def test_ac080_low_model_confidence_alone_never_abstains(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-080: a nameable slug is used even when the model is barely sure."""
    corpus = build_corpus(corpus_root)

    def text_for(request: ModelRequest) -> str:
        return suggestion_text(app="Slack", subject="stripe outage thread", confidence=0.01)

    inject(
        probe=corpus.probe(),
        ocr=FakeOcr(),
        transport=FakeTransport(text_for=text_for),
    )

    result = run_cli(runner, corpus_root, "--apply", "--wait")
    record = records_by_name(plan_records())[corpus.narrow_space.name]

    assert result.exit_code == 0
    assert record.action is Action.RENAME
    assert record.proposed_name == "2026-01-14-slack-stripe-outage-thread.png"


def test_ac080_a_thin_one_segment_name_is_still_a_rename(
    runner: CliRunner, corpus_root: Path, inject: Inject
) -> None:
    """AC-080: abstention is not triggered by a thin name either."""
    corpus = build_corpus(corpus_root)

    def text_for(request: ModelRequest) -> str:
        return suggestion_text(app=None, subject="terminal", confidence=0.05)

    inject(
        probe=corpus.probe(),
        ocr=FakeOcr(),
        transport=FakeTransport(text_for=text_for),
    )

    result = run_cli(runner, corpus_root, "--apply", "--wait")
    record = records_by_name(plan_records())[corpus.narrow_space.name]

    assert result.exit_code == 0
    assert record.action is Action.RENAME
    assert record.proposed_name == "2026-01-14-terminal.png"
