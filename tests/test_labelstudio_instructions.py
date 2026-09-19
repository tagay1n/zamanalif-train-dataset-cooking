from __future__ import annotations

import unittest

from tatar_preannotator.conversion import ACTIVE_RULES
from tatar_preannotator.labelstudio_instructions import (
    RULE_GUIDANCE,
    render_project_instructions,
)


class LabelStudioInstructionTests(unittest.TestCase):
    def test_every_dsl_rule_has_curated_guidance(self) -> None:
        self.assertEqual(set(RULE_GUIDANCE), set(ACTIVE_RULES))

    def test_unknown_project_tells_annotator_to_skip_uncertain_items(self) -> None:
        html = render_project_instructions(
            "unknown_origin",
            "Unknown-origin word review",
            [],
        )

        self.assertIn("hyphenated compound", html)
        self.assertIn("abbreviation or", html)
        self.assertIn("Tatar-specific word", html)
        self.assertIn("skip the task", html)
        self.assertNotIn("Examples:", html)
        self.assertNotIn("Replace an invalid variant line", html)

    def test_catchall_has_short_conditional_letter_examples(self) -> None:
        html = render_project_instructions("catchall", "Catchall word review", [])

        self.assertIn("<b>Homonym</b>", html)
        self.assertIn("вакыт → waqıt", html)
        self.assertIn("проект → proyekt", html)
        self.assertIn("тиеш → tiyeş", html)
        self.assertIn("ие</b> is deterministically <b>iye", html)
        self.assertIn("саклау → saqlaw", html)
        self.assertIn("defaults to ts", html)
        self.assertIn("цирк → tsirk", html)
        self.assertIn("федераль → federalʼ", html)
        self.assertIn("ье converts to ʼye and ъе to ye", html)


if __name__ == "__main__":
    unittest.main()
