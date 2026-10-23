import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from assistant import AppConfig, VoiceAssistant


def make_assistant(root: Path) -> VoiceAssistant:
    assistant = object.__new__(VoiceAssistant)
    assistant.config = AppConfig(
        whisper_exe=root / "whisper-cli.exe",
        whisper_model=root / "model.bin",
        ollama_url="http://127.0.0.1:11434/api/chat",
        ollama_model="llama3.2:3b",
        piper_voice="test",
        piper_data_dir=root,
        apps={"notepad": ["notepad.exe"]},
        search_locations={"documents": root},
        push_to_talk_key="f8",
        quit_key="esc",
        tts_enabled=False,
        max_search_results=2,
    )
    return assistant


class SafetyTests(unittest.TestCase):
    @patch("assistant.requests.post")
    def test_ollama_receives_no_desktop_tools(self, post: Mock) -> None:
        response = Mock()
        response.json.return_value = {
            "message": {"role": "assistant", "content": "Four."}
        }
        post.return_value = response
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            assistant.history = []
            content, calls = assistant.ask_ollama("What is two plus two?")

        payload = post.call_args.kwargs["json"]
        self.assertNotIn("tools", payload)
        self.assertEqual(0, payload["keep_alive"])
        self.assertEqual("Four.", content)
        self.assertEqual([], calls)

    def test_explicit_dictation_preserves_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            call = assistant.deterministic_tool_call("Dictate: Meet me at 3 PM, please.")
            self.assertIsNotNone(call)
            self.assertEqual(
                "Meet me at 3 PM, please.",
                call["function"]["arguments"]["text"],
            )

    def test_known_app_is_routed_without_llm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            call = assistant.deterministic_tool_call("Please open Notepad.")
            self.assertEqual("open_app", call["function"]["name"])
            self.assertEqual("notepad", call["function"]["arguments"]["app"])

    def test_unknown_tool_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            with self.assertRaisesRegex(ValueError, "Unapproved tool"):
                assistant.execute_tool(
                    {"function": {"name": "run_shell", "arguments": {"command": "whoami"}}}
                )

    def test_unknown_application_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            with self.assertRaisesRegex(ValueError, "not an approved application"):
                assistant.execute_tool(
                    {"function": {"name": "open_app", "arguments": {"app": "powershell"}}}
                )

    def test_extra_tool_arguments_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            with self.assertRaises(Exception):
                assistant.execute_tool(
                    {
                        "function": {
                            "name": "search_files",
                            "arguments": {
                                "query": "report",
                                "location": "documents",
                                "command": "whoami",
                            },
                        }
                    }
                )

    def test_search_is_bounded_and_skips_build_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "first-report.txt").write_text("one", encoding="utf-8")
            (root / "second-report.txt").write_text("two", encoding="utf-8")
            (root / "third-report.txt").write_text("three", encoding="utf-8")
            (root / "build").mkdir()
            (root / "build" / "hidden-report.txt").write_text("hidden", encoding="utf-8")

            assistant = make_assistant(root)
            results = assistant._search_files(root, "report")

            self.assertEqual(2, len(results))
            self.assertTrue(all("build" not in result.parts for result in results))

    def test_stringified_json_arguments_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            reply = assistant.execute_tool(
                {
                    "function": {
                        "name": "search_files",
                        "arguments": json.dumps(
                            {"query": "missing", "location": "documents"}
                        ),
                    }
                }
            )
            self.assertIn("found no files", reply)


if __name__ == "__main__":
    unittest.main()
