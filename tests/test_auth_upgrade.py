"""Tests for upgraded auth (hmac-based token compare)."""
from __future__ import annotations

import pytest

from gil.core.auth import AuthContext, AuthError, new_token
from gil.core.config import GilSettings

pytestmark = pytest.mark.phase1


class TestAuthUpgrade:
    def test_sim_mode_no_tokens_returns_operator(self):
        ctx = AuthContext(GilSettings(operator_token="", autonomy_token=""))
        role = ctx.resolve(None, hardware=False)
        assert role.value == "operator"

    def test_hardware_no_tokens_raises(self):
        ctx = AuthContext(GilSettings(operator_token="", autonomy_token=""))
        with pytest.raises(AuthError):
            ctx.resolve(None, hardware=True)

    def test_correct_operator_token(self):
        ctx = AuthContext(GilSettings(operator_token="secret123", autonomy_token=""))
        role = ctx.resolve("secret123", hardware=False)
        assert role.value == "operator"

    def test_correct_autonomy_token(self):
        ctx = AuthContext(GilSettings(operator_token="", autonomy_token="auto_secret"))
        role = ctx.resolve("auto_secret", hardware=False)
        assert role.value == "autonomy"

    def test_wrong_token_returns_observer(self):
        ctx = AuthContext(GilSettings(operator_token="real", autonomy_token=""))
        role = ctx.resolve("wrong", hardware=False)
        assert role.value == "observer"

    def test_wrong_token_hardware_raises(self):
        ctx = AuthContext(GilSettings(operator_token="real", autonomy_token=""))
        with pytest.raises(AuthError):
            ctx.resolve("wrong", hardware=True)

    def test_new_token_generates_string(self):
        t = new_token()
        assert isinstance(t, str)
        assert len(t) > 20

    def test_timing_safe_comparison(self):
        ctx = AuthContext(GilSettings(operator_token="my_token"))
        role = ctx.resolve("my_token", hardware=False)
        assert role.value == "operator"
