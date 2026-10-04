"""A compilation checkpoint never substitutes for current validation."""
import unittest
import RobeCompilationCheckpoint as cache


class CompilationCheckpointTests(unittest.TestCase):
    def test_every_compilation_input_and_output_must_match(self):
        inputs = [b"source", "compiler", b"parent", b"original", b"base", "processors"]
        key = cache.key(*inputs)
        saved = cache.record(key, b"binary")
        self.assertTrue(cache.matches(saved, key, b"binary"))
        self.assertFalse(cache.matches(saved, key, b"changed"))
        self.assertFalse(cache.matches(None, key, b"binary"))
        for index, value in enumerate(inputs):
            changed = inputs.copy()
            changed[index] = b"changed" if isinstance(value, bytes) else "changed"
            self.assertFalse(cache.matches(saved, cache.key(*changed), b"binary"), index)
        self.assertNotIn("validation", saved)


if __name__ == "__main__":
    unittest.main()
