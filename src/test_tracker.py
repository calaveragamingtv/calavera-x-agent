
import tempfile
from pathlib import Path

from src.post_tracker import PostTracker


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        file_path = Path(temp_dir) / "processed_posts.json"
        tracker = PostTracker(file_path)

        posts = [
            {
                "id": "TEST_001",
                "text": "Publicación de prueba",
                "url": "https://x.com/test/TEST_001"
            },
            {
                "id": "TEST_002",
                "text": "Otra publicación de prueba",
                "url": "https://x.com/test/TEST_002"
            }
        ]

        # Primera ejecución: registra dos publicaciones.
        new_ids = tracker.register_posts(posts)
        assert len(new_ids) == 2

        # Segunda ejecución: no registra duplicados.
        new_ids = tracker.register_posts(posts)
        assert len(new_ids) == 0

        # Comprueba los pendientes de análisis.
        pending = tracker.get_unanalyzed_ids()
        assert len(pending) == 2

        # Marca el análisis de una publicación.
        tracker.mark_analysis_completed(
            "TEST_001",
            {"score": 85, "recommended_action": "reply"}
        )

        # Marca un like como completado.
        tracker.mark_action_started("TEST_001", "like")
        tracker.mark_action_result("TEST_001", "like", "completed")

        # Verifica que no vuelva a aparecer como pendiente.
        assert "TEST_001" not in tracker.get_unanalyzed_ids()
        assert "TEST_001" not in tracker.get_pending_action_ids("like")

        # Comprueba que la segunda publicación sigue pendiente.
        assert "TEST_002" in tracker.get_unanalyzed_ids()

        print("TEST 1 OK: registro de publicaciones")
        print("TEST 2 OK: detección de duplicados")
        print("TEST 3 OK: persistencia del análisis")
        print("TEST 4 OK: registro de likes")
        print("TEST 5 OK: separación de estados")
        print("TODAS LAS PRUEBAS FINALIZARON CORRECTAMENTE")


if __name__ == "__main__":
    main()
