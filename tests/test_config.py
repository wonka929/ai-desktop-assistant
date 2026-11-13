import tempfile
import unittest
from pathlib import Path

from assistant.config import Config


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        config = Config.load(Path("/nonexistent/config.toml"))
        self.assertEqual(config.server.base_url, "http://127.0.0.1:8090")
        self.assertEqual(config.ui.hotkey, "<Control><Alt>space")
        self.assertEqual(config.tts.lang, "it")
        self.assertEqual(config.llm.temperature, 0.0)

    def test_load_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                """
[ui]
hotkey = "<Super>j"
hide_on_focus_loss = false

[tts]
voice = "male"
chunk_chars = 100

[llm]
temperature = 0.2
sconosciuta = "ignorata"
""",
                encoding="utf-8",
            )
            config = Config.load(path)
            self.assertEqual(config.ui.hotkey, "<Super>j")
            self.assertFalse(config.ui.hide_on_focus_loss)
            self.assertEqual(config.tts.voice, "male")
            self.assertEqual(config.tts.chunk_chars, 100)
            self.assertEqual(config.llm.temperature, 0.2)

    def test_ensure_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested/config.toml"
            config = Config.load(path)
            created = config.ensure_file()
            self.assertTrue(created.is_file())
            loaded = Config.load(created)
            self.assertEqual(loaded.ui.speak_hotkey, "<Control><Alt>r")


if __name__ == "__main__":
    unittest.main()
