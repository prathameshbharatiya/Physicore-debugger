import pytest

from physicore_collector import errors


def test_message_and_hint_rendering():
    e = errors.InputError("bad path", hint="check the spelling")
    assert str(e) == "bad path\nHint: check the spelling"
    assert e.message == "bad path"


def test_message_without_hint():
    assert str(errors.McapReadError("cannot read")) == "cannot read"


@pytest.mark.parametrize(
    "cls",
    [
        errors.InputError, errors.ProfileNotFoundError, errors.ProfileParseError,
        errors.ProfileValidationError, errors.McapReadError, errors.TriggerError,
        errors.ExportTargetExistsError, errors.ChecksumMismatchError,
    ],
)
def test_all_errors_derive_from_base_with_exit_code_2(cls):
    assert issubclass(cls, errors.PhysiCoreError)
    assert cls("x").exit_code == 2


def test_hierarchy():
    assert issubclass(errors.ProfileNotFoundError, errors.ProfileError)
    assert issubclass(errors.ExportTargetExistsError, errors.ExportError)
    assert issubclass(errors.ChecksumMismatchError, errors.ExportError)
