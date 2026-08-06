from .accuracy_patch import apply as _apply_accuracy_patch

_apply_accuracy_patch()

from .strict_name_patch import apply as _apply_strict_name_patch

_apply_strict_name_patch()

from .legacy_epic_patch import apply as _apply_legacy_epic_patch

_apply_legacy_epic_patch()

from .strict_epic_repair import apply as _apply_strict_epic_repair

_apply_strict_epic_repair()
