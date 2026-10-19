"""A profile id is its home path. A bare name must not resolve against the cwd and write there."""

import pytest

from gateway.session_authorities import authority_for_profile_id


def test_bare_profile_name_is_refused_and_writes_nothing_under_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="absolute home path"):
        authority_for_profile_id(object(), "owned")
    assert not (tmp_path / "owned").exists()
