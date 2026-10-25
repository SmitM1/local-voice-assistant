import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from process_supervisor import ProcessSupervisor


class ProcessSupervisorTests(unittest.TestCase):
    def test_cancel_all_stops_running_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            supervisor = ProcessSupervisor()
            result_holder = []

            def run_process() -> None:
                result_holder.append(
                    supervisor.run(
                        [sys.executable, "-c", "import time; time.sleep(30)"],
                        cwd=Path(temp_dir),
                        timeout=60,
                        label="test sleeper",
                    )
                )

            thread = threading.Thread(target=run_process)
            thread.start()
            deadline = time.monotonic() + 5
            while not supervisor.active() and time.monotonic() < deadline:
                time.sleep(0.02)
            active = supervisor.active()
            self.assertEqual(1, len(active))
            cancelled = supervisor.cancel_all()
            thread.join(timeout=5)
            if thread.is_alive():
                supervisor.cancel_all()
            self.assertFalse(thread.is_alive())
            self.assertEqual(active[0].pid, cancelled[0].pid)
            self.assertTrue(result_holder[0].cancelled)
            self.assertFalse(supervisor.active())


if __name__ == "__main__":
    unittest.main()
