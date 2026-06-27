# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Compatibility entrypoint for provider registry tests.

The historical test module is named ``test_p5s2_provider_registry.py``.
Phase B verification invokes ``tests/test_provider_registry.py`` directly,
so re-export the existing test suite here.
"""

from tests.test_p5s2_provider_registry import *  # noqa: F401,F403
