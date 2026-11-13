import unittest

from assistant.textutil import cache_key, preview, split_for_tts, strip_for_speech


class TextUtilTests(unittest.TestCase):
    def test_strip_markdown(self):
        text = "**Ciao** [mondo](https://example.com) `codice`\n\n```py\nprint(1)\n```"
        self.assertEqual(strip_for_speech(text), "Ciao mondo codice")

    def test_removes_urls(self):
        text = "Vedi https://example.com/articolo per dettagli."
        self.assertEqual(strip_for_speech(text), "Vedi per dettagli.")

    def test_split_short_text(self):
        self.assertEqual(split_for_tts("Ciao mondo."), ["Ciao mondo."])

    def test_split_respects_sentences(self):
        text = "Prima frase. Seconda frase! Terza frase?"
        chunks = split_for_tts(text, max_chars=20)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(" ".join(chunks), text)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 20)

    def test_split_long_piece(self):
        text = "parola " * 100
        chunks = split_for_tts(text, max_chars=50)
        self.assertTrue(all(len(chunk) <= 50 for chunk in chunks))

    def test_max_total(self):
        text = "Una frase lunga. " * 100
        chunks = split_for_tts(text, max_chars=320, max_total=200)
        self.assertLessEqual(sum(len(chunk) for chunk in chunks), 210)

    def test_empty(self):
        self.assertEqual(split_for_tts("   \n "), [])

    def test_preview(self):
        self.assertEqual(preview("ciao mondo", 20), "ciao mondo")
        self.assertTrue(preview("a" * 50, 10).endswith("…"))

    def test_cache_key_deterministic(self):
        self.assertEqual(cache_key("it", "voce", "testo"), cache_key("it", "voce", "testo"))
        self.assertNotEqual(cache_key("it", "voce", "testo"), cache_key("en", "voce", "testo"))


if __name__ == "__main__":
    unittest.main()
