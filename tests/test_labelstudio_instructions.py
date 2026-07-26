from __future__ import annotations

import unittest

from tatar_preannotator.conversion import RULES
from tatar_preannotator.labelstudio_instructions import (
    RULE_GUIDANCE,
    render_project_instructions,
)


class LabelStudioInstructionTests(unittest.TestCase):
    def test_every_dsl_rule_has_curated_guidance(self) -> None:
        self.assertEqual(set(RULE_GUIDANCE), set(RULES))

    def test_rule_project_uses_curated_examples_and_plain_output_instruction(self) -> None:
        html = render_project_instructions("iya", "IYA", ["IYA"])

        self.assertIn("орфография", html)
        self.assertIn("orfografiä", html)
        self.assertIn("orfografiyä", html)
        self.assertIn("Enter the complete word, not DSL syntax", html)
        self.assertIn("<b>N</b>", html)
        self.assertIn("<b>RL</b>", html)

    def test_unknown_project_tells_annotator_to_skip_uncertain_items(self) -> None:
        html = render_project_instructions(
            "u_hyphenated",
            "Unknown hyphenated compounds",
            [],
        )

        self.assertIn("hyphenated compound", html)
        self.assertIn("skip the task", html)
        self.assertNotIn("Examples:", html)

    def test_catchall_has_short_conditional_letter_examples(self) -> None:
        html = render_project_instructions("catchall", "Catchall word review", [])

        self.assertIn("вакыт → waqıt", html)
        self.assertIn("проект → proyekt", html)
        self.assertIn("саклау → saqlaw", html)


if __name__ == "__main__":
    unittest.main()
