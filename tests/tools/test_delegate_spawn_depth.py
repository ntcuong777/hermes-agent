"""max_spawn_depth clamping (split from ``tests/tools/test_delegate.py``)."""

import unittest
from unittest.mock import patch

from tools.delegate_tool import _load_config


# =========================================================================
# max_spawn_depth clamping
# =========================================================================

class TestMaxSpawnDepth(unittest.TestCase):
    """Tests for _get_max_spawn_depth clamping and fallback behavior."""


    @patch("tools.delegate_tool._load_config",
           return_value={"max_spawn_depth": 0})
    def test_max_spawn_depth_clamped_below_one(self, mock_cfg):
        from tools.delegate_tool import _get_max_spawn_depth
        self.assertEqual(_get_max_spawn_depth(), 1)
