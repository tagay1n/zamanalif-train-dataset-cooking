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
        html = render_project_instructions("e_glide", "E glide", ["E_GLIDE"])

        self.assertIn("проект", html)
        self.assertIn("proekt", html)
        self.assertIn("proyekt", html)
        self.assertIn("Enter the complete word, not DSL syntax", html)
        self.assertNotIn("Origin Labels", html)
        self.assertNotIn("Choose <b>N</b> or <b>RL</b>", html)

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
        self.assertIn("саклау → saqlaw", html)
        self.assertIn("defaults to ts", html)
        self.assertIn("цирк → tsirk", html)
        self.assertIn("федераль → federalʼ", html)
        self.assertIn("Edit the plain text to keep or remove ʼ", html)


if __name__ == "__main__":
    unittest.main()
