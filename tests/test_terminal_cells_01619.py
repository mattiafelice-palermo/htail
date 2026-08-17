from pathlib import Path
import unittest

from htail_app import core
from htail_app.pane import Pane
from htail_app import terminal_cells


class TerminalCellTests(unittest.TestCase):
    def make_pane(self) -> Pane:
        path = Path("test_standard_name_first_full.txt")
        return Pane(
            path,
            core.SyntaxHighlighter(path, "none", False),
            core.DisplayFilter(),
            False,
            300,
        )

    def test_expands_tabs_without_counting_ansi_bytes_as_columns(self):
        styled = core.paint("name", core.BOLD_LIGHT_CYAN, True) + "\tvalue"
        expanded = terminal_cells.expand_tabs_ansi(styled)
        self.assertEqual(core.strip_ansi(expanded), "name    value")
        self.assertNotIn("\t", expanded)

    def test_tab_separated_content_keeps_pane_border_at_exact_width(self):
        pane = self.make_pane()
        pane.add_initial(
            [
                "acyl chloride\t*-C(=O)-Cl\n",
                "mixed anhydride (acetyl)\t*-C(=O)-O-[C;D3](=O)-[C;D1;H3]\n",
                "carbamate (primary)\t*-[O;D2]-[C;D3](=O)-[N;D1;H2]\n",
            ]
        )

        rows = [core.strip_ansi(row) for row in pane.render_box(52, 8, True, 0)]

        self.assertTrue(all("\t" not in row for row in rows))
        self.assertTrue(all(len(row) == 52 for row in rows))
        self.assertTrue(all(row.endswith("│") for row in rows[1:-1]))


if __name__ == "__main__":
    unittest.main()
