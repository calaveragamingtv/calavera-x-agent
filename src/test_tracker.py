
from src.post_tracker import PostTracker

tracker = PostTracker()

posts = [
    {
        "id": "TEST_001",
        "text": "Publicación de prueba de CalaveraGaming",
        "url": "https://x.com/CalaveraGaming/status/TEST_001",
        "created_at": "2026-11-06T20:00:00Z"
    },
    {
        "id": "TEST_002",
        "text": "Otra publicación de prueba",
        "url": "https://x.com/CalaveraGaming/status/TEST_002",
        "created_at": "2026-11-06T20:01:00Z"
    }
]

new_ids = tracker.register_posts(posts)

print("Nuevos:", new_ids)

# Intentamos registrar nuevamente las mismas publicaciones.
new_ids = tracker.register_posts(posts)

print("Nuevos en segunda ejecución:", new_ids)

print("Pendientes de análisis:", tracker.get_unanalyzed_ids())

tracker.mark_analysis_completed(
    "TEST_001",
    {
        "score": 85,
        "recommended_action": "reply"
    }
)

tracker.mark_action_started("TEST_001", "like")

tracker.mark_action_result(
    "TEST_001",
    "like",
    "completed"
)

print("Estado final:")
print(tracker.get_post("TEST_001"))
