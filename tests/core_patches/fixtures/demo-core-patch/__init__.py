"""Worked example of ``ctx.register_core_patch``: wrap ``cpdemo_target.greet`` to upper-case it.

The author's workflow: ``hermes plugins patches --sha cpdemo_target:greet`` prints the fingerprint
pasted below; ``hermes plugins patches --lock <this dir> --name shout --target cpdemo_target:greet``
records the source it was taken from in core_patches.lock.json; the plugin's tests call
``hermes_cli.core_patches_testing.assert_core_patches_current``. Enabling it needs
``plugins.entries.demo-core-patch.allow_core_patch: true``.
"""

import functools

EXPECT_GREET_SHA = "8ac9a553d27b157324c2b1e0477f32ed396d2195aec4e09bb1a95cca39ff219a"


def _shout(original):
    @functools.wraps(original)
    def greet(name):
        return original(name).upper()

    return greet


def register(ctx):
    ctx.register_core_patch(
        "shout", target="cpdemo_target:greet", kind="wrap", fn=_shout, expect_ast_sha=EXPECT_GREET_SHA,
    )
