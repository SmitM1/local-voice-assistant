import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from assistant import AppConfig, Recorder, VoiceAssistant
from gui_agent import GUIResult
from system_tools import SystemToolResult, SystemTools


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
    assistant.history = []
    assistant.pending_action = None
    assistant.pending_system_action = None
    assistant.pending_plan = None
    assistant.system_tools = SystemTools(root)
    assistant.approval_ui = None
    assistant.gui_agent = None
    return assistant


class SafetyTests(unittest.TestCase):
    def test_vad_rejects_silence(self) -> None:
        recorder = Recorder(vad_enabled=True, vad_rms_threshold=0.01)
        silence = np.zeros((1_600, 1), dtype=np.float32)
        self.assertIsNone(recorder._trim_to_voice(silence))

    def test_vad_trims_silence_around_voice(self) -> None:
        recorder = Recorder(
            vad_enabled=True, vad_rms_threshold=0.01, vad_padding_ms=0
        )
        silence = np.zeros((320, 1), dtype=np.float32)
        voice = np.full((320, 1), 0.1, dtype=np.float32)
        trimmed = recorder._trim_to_voice(np.concatenate([silence, voice, silence]))
        self.assertIsNotNone(trimmed)
        self.assertEqual(320, len(trimmed))

    @patch("assistant.requests.post")
    def test_second_model_pass_can_veto_a_plan(self, post: Mock) -> None:
        planner = Mock()
        planner.json.return_value = {
            "message": {
                "content": json.dumps(
                    {
                        "mode": "command",
                        "response": "I propose a command.",
                        "command": "Remove-Item -LiteralPath 'C:\\invented'",
                        "explanation": "Deletes a folder.",
                        "tool": "none",
                        "arguments": {},
                        "steps": [],
                    }
                )
            }
        }
        reviewer = Mock()
        reviewer.json.return_value = {
            "message": {
                "content": json.dumps(
                    {
                        "verdict": "block",
                        "risk": "high",
                        "summary": "The path was invented.",
                        "issues": ["Invented destructive path"],
                    }
                )
            }
        }
        post.side_effect = [planner, reviewer]
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            assistant.config = AppConfig(
                **{**assistant.config.__dict__, "reviewer_enabled": True}
            )
            content, calls = assistant.ask_ollama("Clean up old files")
        self.assertIn("blocked", content)
        self.assertEqual([], calls)
        self.assertEqual(2, post.call_count)

    @patch("assistant.requests.post")
    def test_ollama_receives_no_desktop_tools(self, post: Mock) -> None:
        response = Mock()
        response.json.return_value = {
            "message": {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "mode": "reply",
                        "response": "Four.",
                        "command": "",
                        "explanation": "",
                    }
                ),
            }
        }
        post.return_value = response
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            assistant.history = []
            content, calls = assistant.ask_ollama("What is two plus two?")

        payload = post.call_args.kwargs["json"]
        self.assertNotIn("tools", payload)
        self.assertEqual("qwen2.5-coder:3b", payload["model"])
        self.assertEqual("object", payload["format"]["type"])
        self.assertEqual(0, payload["keep_alive"])
        self.assertEqual("Four.", content)
        self.assertEqual([], calls)

    @patch("assistant.requests.post")
    def test_ollama_can_propose_but_not_execute_a_command(self, post: Mock) -> None:
        response = Mock()
        response.json.return_value = {
            "message": {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "mode": "command",
                        "response": "I can list the largest files.",
                        "command": "powershell.exe -NoProfile -NonInteractive -Command \"Get-ChildItem -File | Sort-Object Length -Descending | Select-Object -First 5\"",
                        "explanation": "Lists the five largest files in the working directory.",
                    }
                ),
            }
        }
        post.return_value = response
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            content, calls = assistant.ask_ollama("Show me the five biggest files here")

        self.assertIn("largest files", content)
        self.assertEqual(1, len(calls))
        self.assertEqual("propose_command", calls[0]["function"]["name"])

    @patch("assistant.requests.post")
    def test_ollama_prefers_a_typed_tool(self, post: Mock) -> None:
        response = Mock()
        response.json.return_value = {
            "message": {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "mode": "tool",
                        "response": "I can inspect that folder.",
                        "command": "",
                        "explanation": "Lists the five largest files safely.",
                        "tool": "list_largest_files",
                        "arguments": {"path": r"C:\Users\test\Downloads", "count": 5},
                    }
                ),
            }
        }
        post.return_value = response
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            _, calls = assistant.ask_ollama("Show my five largest downloads")
        self.assertEqual(1, len(calls))
        self.assertEqual("propose_typed_tool", calls[0]["function"]["name"])
        self.assertEqual(
            "list_largest_files", calls[0]["function"]["arguments"]["tool"]
        )

    @patch("assistant.requests.post")
    def test_typed_tool_discards_model_command_fallback(self, post: Mock) -> None:
        response = Mock()
        response.json.return_value = {
            "message": {
                "content": json.dumps(
                    {
                        "mode": "tool",
                        "response": "I can create that folder.",
                        "command": "New-Item -ItemType Directory -Path 'wrong'",
                        "explanation": "Creates the requested folder.",
                        "tool": "create_folder",
                        "arguments": {"path": r"C:\Users\test\Desktop\new folder"},
                    }
                )
            }
        }
        post.return_value = response
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            _, calls = assistant.ask_ollama("Create the folder")
        self.assertEqual(1, len(calls))
        self.assertEqual("propose_typed_tool", calls[0]["function"]["name"])
        self.assertNotIn("command", calls[0]["function"]["arguments"])

    @patch("assistant.requests.post")
    def test_ollama_can_return_a_typed_multistep_plan(self, post: Mock) -> None:
        response = Mock()
        response.json.return_value = {
            "message": {
                "content": json.dumps(
                    {
                        "mode": "plan",
                        "response": "I propose a two-step file plan.",
                        "command": "",
                        "explanation": "Creates a folder and finds reports.",
                        "tool": "none",
                        "arguments": {},
                        "steps": [
                            {
                                "tool": "create_folder",
                                "arguments": {"path": r"C:\Users\test\Desktop\Reports"},
                                "description": "Create the Reports folder.",
                            },
                            {
                                "tool": "find_files",
                                "arguments": {
                                    "path": r"C:\Users\test\Documents",
                                    "query": "report",
                                    "count": 10,
                                },
                                "description": "Find report files.",
                            },
                        ],
                    }
                )
            }
        }
        post.return_value = response
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            _, calls = assistant.ask_ollama("Create Reports and find my reports")
        self.assertEqual("propose_plan", calls[0]["function"]["name"])
        self.assertEqual(2, len(calls[0]["function"]["arguments"]["steps"]))

    @patch("assistant.requests.post")
    def test_command_planning_can_be_disabled(self, post: Mock) -> None:
        response = Mock()
        response.json.return_value = {"message": {"content": "Planning is disabled."}}
        post.return_value = response
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            assistant.config = AppConfig(
                **{
                    **assistant.config.__dict__,
                    "ollama_command_planning_enabled": False,
                }
            )
            content, calls = assistant.ask_ollama("Create a folder")
        payload = post.call_args.kwargs["json"]
        self.assertEqual("llama3.2:3b", payload["model"])
        self.assertNotIn("format", payload)
        self.assertEqual("Planning is disabled.", content)
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

    def test_unknown_application_uses_installed_app_resolver(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            assistant.system_tools = Mock()
            assistant.system_tools.open_installed_app.return_value = SystemToolResult(
                "Opened example."
            )
            reply = assistant.execute_tool(
                {"function": {"name": "open_app", "arguments": {"app": "example"}}}
            )
            self.assertEqual("Opened example.", reply)
            assistant.system_tools.open_installed_app.assert_called_once_with("example")

    def test_file_search_is_not_mistaken_for_package_search(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            self.assertIsNone(
                assistant.deterministic_system_request("Search for invoice in documents")
            )

    def test_explicit_command_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            assistant.system_tools = Mock()
            pending = Mock()
            assistant.system_tools.prepare_command.return_value = SystemToolResult(
                "Review it.", pending
            )
            assistant.system_tools.confirm.return_value = SystemToolResult("Finished.")

            self.assertEqual("Review it.", assistant.handle_transcript("Command: git status"))
            assistant.system_tools.confirm.assert_not_called()
            self.assertEqual("Finished.", assistant.handle_transcript("confirm"))
            assistant.system_tools.confirm.assert_called_once_with(pending)

    def test_web_search_routes_without_llm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            assistant.system_tools = Mock()
            assistant.system_tools.search_web.return_value = SystemToolResult("Opened search.")
            reply = assistant.handle_transcript("Search the web for CUDA documentation")
            self.assertEqual("Opened search.", reply)
            assistant.system_tools.search_web.assert_called_once_with("CUDA documentation")

    def test_model_command_is_previewed_and_marked_as_generated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            assistant.system_tools = Mock()
            assistant.system_tools.prepare_command.return_value = SystemToolResult(
                "Generated command ready.", Mock()
            )
            reply = assistant.execute_tool(
                {
                    "function": {
                        "name": "propose_command",
                        "arguments": {
                            "command": "dir",
                            "explanation": "Lists this directory.",
                        },
                    }
                }
            )
            self.assertEqual("Generated command ready.", reply)
            assistant.system_tools.prepare_command.assert_called_once_with(
                "dir",
                generated_by_model=True,
                explanation="Lists this directory.",
            )

    def test_model_typed_tool_is_validated_and_dispatched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            assistant.system_tools = Mock()
            assistant.system_tools.execute_typed_tool.return_value = SystemToolResult(
                "Found files."
            )
            reply = assistant.execute_tool(
                {
                    "function": {
                        "name": "propose_typed_tool",
                        "arguments": {
                            "tool": "find_files",
                            "arguments": {
                                "path": str(Path(temp_dir)),
                                "query": "invoice",
                                "count": 10,
                            },
                            "explanation": "Searches for matching files.",
                        },
                    }
                }
            )
        self.assertEqual("Found files.", reply)
        assistant.system_tools.execute_typed_tool.assert_called_once_with(
            "find_files",
            path=str(Path(temp_dir)),
            source="",
            destination="",
            query="invoice",
            app="",
            package_id="",
            url="",
            link_number=0,
            count=10,
            explanation="Searches for matching files.",
        )

    def test_plan_waits_for_spoken_initial_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            assistant.system_tools = Mock()
            reply = assistant.execute_tool(
                {
                    "function": {
                        "name": "propose_plan",
                        "arguments": {
                            "request": "Create Reports and find files.",
                            "summary": "Create a folder and find files.",
                            "steps": [
                                {
                                    "tool": "create_folder",
                                    "arguments": {"path": str(Path(temp_dir) / "reports")},
                                    "description": "Create reports.",
                                },
                                {
                                    "tool": "find_files",
                                    "arguments": {
                                        "path": str(Path(temp_dir)),
                                        "query": "report",
                                        "count": 5,
                                    },
                                    "description": "Find reports.",
                                },
                            ],
                        },
                    }
                }
            )
        self.assertIn("Say confirm", reply)
        self.assertIsNotNone(assistant.pending_plan)
        assistant.system_tools.execute_typed_tool.assert_not_called()

    def test_plan_confirms_mutation_and_continues_after_verified_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            pending = Mock(description="create folder reports")
            assistant.system_tools = Mock()
            assistant.system_tools.execute_typed_tool.side_effect = [
                SystemToolResult("Ready.", pending),
                SystemToolResult("Found files."),
            ]
            assistant.system_tools.confirm.return_value = SystemToolResult(
                "Created and verified."
            )
            proposal = assistant.execute_tool(
                {
                    "function": {
                        "name": "propose_plan",
                        "arguments": {
                            "request": "Create and inspect.",
                            "summary": "Create and inspect.",
                            "steps": [
                                {
                                    "tool": "create_folder",
                                    "arguments": {"path": str(Path(temp_dir) / "reports")},
                                    "description": "Create reports.",
                                },
                                {
                                    "tool": "find_files",
                                    "arguments": {
                                        "path": str(Path(temp_dir)),
                                        "query": "report",
                                        "count": 5,
                                    },
                                    "description": "Find reports.",
                                },
                            ],
                        },
                    }
                }
            )
            self.assertIn("Say confirm", proposal)
            reply = assistant.handle_transcript("confirm")
        self.assertEqual("Completed all 2 plan steps successfully.", reply)
        assistant.system_tools.confirm.assert_called_once_with(pending)

    def test_plan_can_select_a_path_from_an_earlier_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "report-one.txt"
            second = root / "report-two.txt"
            destination = root / "chosen.txt"
            first.write_text("one", encoding="utf-8")
            second.write_text("two", encoding="utf-8")
            assistant = make_assistant(root)
            assistant.system_tools = SystemTools(root, allowed_roots=(root,))
            assistant.approval_ui = Mock()
            assistant.approval_ui.select_one.return_value = str(second)
            proposal = assistant.execute_tool(
                {
                    "function": {
                        "name": "propose_plan",
                        "arguments": {
                            "request": "Find a report and copy the one I choose.",
                            "summary": "Find and copy a selected report.",
                            "steps": [
                                {
                                    "tool": "find_files",
                                    "arguments": {
                                        "path": str(root),
                                        "query": "report",
                                        "count": 5,
                                    },
                                    "description": "Find reports.",
                                },
                                {
                                    "tool": "copy_file",
                                    "arguments": {
                                        "source": "{{step1.selected_path}}",
                                        "destination": str(destination),
                                    },
                                    "description": "Copy the selected report.",
                                },
                            ],
                        },
                    }
                }
            )
            self.assertIn("Say confirm", proposal)
            reply = assistant.handle_transcript("confirm")
            self.assertEqual("two", destination.read_text(encoding="utf-8"))
        self.assertIn("Completed all 2", reply)
        assistant.approval_ui.select_one.assert_called_once()

    def test_emergency_stop_clears_pending_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            assistant.stop_event = __import__("threading").Event()
            assistant.pending_system_action = Mock()
            result = assistant.handle_transcript("stop task")
        self.assertTrue(assistant.stop_event.is_set())
        self.assertIsNone(assistant.pending_system_action)
        self.assertIn("Stopped", result)

    def test_system_action_waits_for_spoken_confirmation_without_modal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            pending = Mock()
            assistant.approval_ui = Mock()
            assistant.system_tools = Mock()
            assistant.system_tools.confirm.return_value = SystemToolResult("Ran it.")
            prompt = assistant._handle_system_result(
                SystemToolResult("Say confirm or cancel.", pending)
            )
            self.assertEqual("Say confirm or cancel.", prompt)
            assistant.approval_ui.request.assert_not_called()
            self.assertEqual("Ran it.", assistant.handle_transcript("confirm"))
            assistant.system_tools.confirm.assert_called_once_with(pending)

    def test_natural_chrome_kiosk_request_uses_direct_allowlisted_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            assistant.system_tools = Mock()
            assistant.system_tools.open_chrome_kiosk.return_value = SystemToolResult(
                "Opened Chrome in kiosk mode."
            )
            result = assistant.deterministic_system_request(
                "Open Chrome in kiosk mode"
            )
        self.assertEqual("Opened Chrome in kiosk mode.", result.message)
        assistant.system_tools.open_chrome_kiosk.assert_called_once_with()

    @patch.object(VoiceAssistant, "ask_ollama")
    def test_system_prefix_bypasses_foreground_gui_routing(self, ask: Mock) -> None:
        ask.return_value = ("Need a command.", [])
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            assistant.gui_agent = Mock()
            assistant.gui_agent.foreground_process.return_value = "notepad.exe"
            assistant.config = AppConfig(
                **{
                    **assistant.config.__dict__,
                    "gui_enabled": True,
                    "gui_natural_apps": ("notepad.exe",),
                }
            )
            reply = assistant.handle_transcript(
                "System, find the five largest files in Downloads"
            )
        self.assertEqual("Need a command.", reply)
        assistant.gui_agent.run_task.assert_not_called()
        ask.assert_called_once_with("find the five largest files in Downloads")

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

    def test_explicit_computer_prefix_routes_to_gui(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            assistant.gui_agent = Mock()
            self.assertEqual(
                "click the Replace button",
                assistant.gui_objective("Computer, click the Replace button"),
            )

    def test_visible_button_request_routes_to_gui_in_chrome(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            assistant.gui_agent = Mock()
            assistant.gui_agent.foreground_process.return_value = "chrome.exe"
            request = "press on the continue watching button in the chrome tab"
            self.assertEqual(request, assistant.gui_objective(request))

    def test_chrome_button_request_never_falls_through_to_command_planning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            assistant.gui_agent = Mock()
            assistant.gui_agent.foreground_process.return_value = "chrome.exe"
            assistant.gui_agent.run_task.return_value = GUIResult(
                "done", "Pressed Continue watching."
            )
            assistant.ask_ollama = Mock()

            reply = assistant.handle_transcript(
                "press on the continue watching button in the chrome tab"
            )

            self.assertEqual("Pressed Continue watching.", reply)
            assistant.gui_agent.run_task.assert_called_once()
            assistant.ask_ollama.assert_not_called()

    def test_natural_edit_request_only_routes_in_configured_app(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            assistant = make_assistant(Path(temp_dir))
            assistant.config = AppConfig(
                **{
                    **assistant.config.__dict__,
                    "gui_natural_apps": ("notepad.exe",),
                    "gui_enabled": True,
                }
            )
            assistant.gui_agent = Mock()
            assistant.gui_agent.foreground_process.return_value = "notepad.exe"
            self.assertEqual(
                "Write a short follow-up email",
                assistant.gui_objective("Write a short follow-up email"),
            )
            assistant.gui_agent.foreground_process.return_value = "outlook.exe"
            self.assertIsNone(assistant.gui_objective("Write a short follow-up email"))


if __name__ == "__main__":
    unittest.main()
