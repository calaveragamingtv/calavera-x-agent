
import tempfile
import unittest
from pathlib import Path

from src.post_tracker import PostTracker


class PostTrackerTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tracker_path = Path(self.temp_dir.name) / "tracker.json"
        self.tracker = PostTracker(self.tracker_path)

        self.tracker.register_posts([
            {
                "id": "post-123",
                "author_id": "author-456",
                "text": "Test post"
            }
        ])

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_draft_to_processing_to_completed(self):
        self.tracker.mark_action_drafted(
            "post-123",
            "reply",
            {
                "reply_text": "Test reply"
            }
        )

        self.assertEqual(
            self.tracker.get_post("post-123")["actions"]["reply"]["status"],
            "drafted"
        )

        self.tracker.mark_action_started(
            "post-123",
            "reply"
        )

        self.assertEqual(
            self.tracker.get_post("post-123")["actions"]["reply"]["status"],
            "processing"
        )

        self.tracker.mark_action_result(
            "post-123",
            "reply",
            "completed",
            external_id="reply-789"
        )

        action = self.tracker.get_post("post-123")["actions"]["reply"]

        self.assertEqual(action["status"], "completed")
        self.assertEqual(action["external_id"], "reply-789")

    def test_interrupted_action_becomes_unknown(self):
        self.tracker.mark_action_drafted(
            "post-123",
            "reply",
            {
                "reply_text": "Test reply"
            }
        )

        self.tracker.mark_action_started(
            "post-123",
            "reply"
        )

        recovered = self.tracker.recover_interrupted_actions("reply")

        action = self.tracker.get_post("post-123")["actions"]["reply"]

        self.assertEqual(recovered, 1)
        self.assertEqual(action["status"], "unknown")

    def test_unknown_reply_counts_towards_limits(self):
        self.tracker.mark_action_drafted(
            "post-123",
            "reply",
            {
                "reply_text": "Test reply"
            }
        )

        self.tracker.mark_action_started(
            "post-123",
            "reply"
        )

        self.tracker.mark_action_result(
            "post-123",
            "reply",
            "unknown",
            error="Connection interrupted"
        )

        usage = self.tracker.get_reply_usage("author-456")

        self.assertEqual(usage["total_replies"], 1)
        self.assertEqual(usage["author_replies_today"], 1)

    def test_completed_requires_external_id(self):
        self.tracker.mark_action_drafted(
            "post-123",
            "reply",
            {
                "reply_text": "Test reply"
            }
        )

        self.tracker.mark_action_started(
            "post-123",
            "reply"
        )

        with self.assertRaises(ValueError):
            self.tracker.mark_action_result(
                "post-123",
                "reply",
                "completed"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
