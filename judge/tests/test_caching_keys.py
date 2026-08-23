"""
Cache-key hashing across xxhash versions.

xxhash 4.0 stopped accepting str — "Strings must be encoded before hashing" —
so every cached property that hashes a long argument raised TypeError on a box
that had picked up the new release. requirements.txt does not pin xxhash, so
that is one `pip install -r requirements.txt` away on any deploy.

3.x encoded as UTF-8 internally, so encoding explicitly gives the identical
digest on both and no cache key changes.
"""

import xxhash
from django.test import SimpleTestCase

from judge.caching import MAX_NUM_CHAR, arg_to_str


class ArgToStrTest(SimpleTestCase):
    def test_short_string_is_returned_verbatim(self):
        self.assertEqual(arg_to_str("abc"), "abc")

    def test_long_string_is_hashed(self):
        arg = "x" * (MAX_NUM_CHAR + 50)
        out = arg_to_str(arg)
        self.assertEqual(len(out), MAX_NUM_CHAR)
        self.assertTrue(out.startswith("xxxx"))

    def test_long_unicode_string_is_hashed(self):
        """Vietnamese titles are the realistic long argument here."""
        arg = "Đề thi Tin học trẻ toàn quốc " * 10
        out = arg_to_str(arg)
        self.assertEqual(len(out), MAX_NUM_CHAR)

    def test_list_is_hashed(self):
        out = arg_to_str([1, 2, 3])
        self.assertEqual(len(out), MAX_NUM_CHAR)

    def test_object_with_id_uses_the_id(self):
        class Thing:
            id = 7

        self.assertEqual(arg_to_str(Thing()), "7")

    def test_hashing_matches_the_pre_4_0_digest(self):
        """Encoding must not change any existing cache key.

        These are the digests xxhash 3.8.1 produced from the *str* input, read
        off the production boxes before the change.
        """
        expected = {
            "abc": "44bc2cf5ad770999",
            "Tiếng Việt": "ac82b69fcb6d2704",
            "x" * 300: "8377db93c80fd5bf",
        }
        for value, digest in expected.items():
            self.assertEqual(xxhash.xxh64_hexdigest(value.encode("utf-8")), digest)

    def test_no_typeerror_on_a_long_argument(self):
        """The exact failure: TypeError from xxhash on a str argument."""
        try:
            arg_to_str("Đề thi " * 100)
        except TypeError as exc:  # pragma: no cover
            self.fail(f"arg_to_str raised TypeError: {exc}")
