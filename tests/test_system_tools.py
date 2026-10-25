import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from action_journal import ActionJournal
from browser_controller import BrowserDownload
from process_supervisor import ActiveProcess, ProcessExecution
from system_tools import PendingSystemAction, SystemTools


class SystemToolsTests(unittest.TestCase):
    @patch("system_tools.subprocess.Popen")
    @patch("system_tools.Path.is_file", return_value=True)
    def test_exact_chrome_kiosk_launch_runs_without_confirmation(
        self, _is_file: Mock, popen: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = SystemTools(Path(temp_dir), permission_profile="files_and_apps")
            result = tools.prepare_command(
                "Start-Process -FilePath "
                "'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' "
                "-ArgumentList '--kiosk'",
                generated_by_model=True,
            )
        self.assertTrue(result.success)
        self.assertIsNone(result.pending)
        popen.assert_called_once()
        self.assertEqual("--kiosk", popen.call_args.args[0][1])
        self.assertFalse(popen.call_args.kwargs["shell"])

    @patch("system_tools.subprocess.Popen")
    def test_chrome_kiosk_launch_with_extra_arguments_is_not_auto_approved(
        self, popen: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = SystemTools(Path(temp_dir), permission_profile="files_and_apps")
            result = tools.prepare_command(
                "Start-Process -FilePath "
                "'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' "
                "-ArgumentList '--kiosk','--load-extension=unknown'",
                generated_by_model=True,
            )
        self.assertFalse(result.success)
        popen.assert_not_called()

    def test_download_requires_confirmation_and_verifies_file(self) -> None:
        browser = Mock()
        browser.public_url.return_value = "https://example.com/file.bin"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            destination = root / "file.bin"

            def download(_url: str, target: Path) -> BrowserDownload:
                target.write_bytes(b"payload")
                return BrowserDownload(
                    "https://example.com/file.bin",
                    target,
                    len(b"payload"),
                    "application/octet-stream",
                )

            browser.download.side_effect = download
            tools = SystemTools(
                root, allowed_roots=(root,), browser_controller=browser
            )
            prepared = tools.execute_typed_tool(
                "download_url",
                url="https://example.com/file.bin",
                destination=str(destination),
            )
            self.assertIsNotNone(prepared.pending)
            self.assertFalse(destination.exists())
            result = tools.confirm(prepared.pending)
            self.assertEqual(b"payload", destination.read_bytes())
        self.assertIn("Downloaded and verified", result.message)

    def test_read_only_profile_blocks_mutations_and_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tools = SystemTools(
                root, allowed_roots=(root,), permission_profile="read_only"
            )
            folder = tools.execute_typed_tool(
                "create_folder", path=str(root / "blocked")
            )
            command = tools.prepare_command("echo blocked")
        self.assertFalse(folder.success)
        self.assertFalse(command.success)

    def test_action_journal_can_undo_created_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "undo-me"
            journal = ActionJournal(root / "actions.jsonl")
            tools = SystemTools(root, allowed_roots=(root,), journal=journal)
            create = tools.execute_typed_tool("create_folder", path=str(target))
            tools.confirm(create.pending)
            undo = tools.prepare_undo()
            self.assertIsNotNone(undo.pending)
            result = tools.confirm(undo.pending)
            self.assertFalse(target.exists())
        self.assertIn("Undid", result.message)

    def test_hash_file_returns_verified_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "hello.txt"
            path.write_text("hello", encoding="utf-8")
            tools = SystemTools(root, allowed_roots=(root,))
            result = tools.execute_typed_tool("hash_file", path=str(path))
        self.assertEqual(
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
            result.data["sha256"],
        )

    @patch("browser_controller.socket.getaddrinfo")
    def test_web_page_reader_blocks_private_addresses(self, getaddrinfo: Mock) -> None:
        getaddrinfo.return_value = [(None, None, None, None, ("127.0.0.1", 80))]
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = SystemTools(Path(temp_dir))
            with self.assertRaisesRegex(ValueError, "private"):
                tools.read_web_page("http://example.test")

    def test_largest_files_tool_is_scoped_and_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "small.bin").write_bytes(b"1")
            (root / "large.bin").write_bytes(b"12345")
            tools = SystemTools(root, allowed_roots=(root,))
            result = tools.list_largest_files(str(root), 1)
        self.assertIn("1 files", result.message)

    def test_typed_tool_rejects_paths_outside_configured_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tools = SystemTools(root, allowed_roots=(root / "allowed",))
            with self.assertRaisesRegex(ValueError, "outside"):
                tools.list_largest_files(str(root), 5)

    def test_typed_tool_normalizes_one_outer_quote_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "file.txt").write_text("data", encoding="utf-8")
            tools = SystemTools(root, allowed_roots=(root,))
            result = tools.find_files(f"'{root}'", "file", 5)
        self.assertIn("1 matching", result.message)

    def test_create_folder_requires_confirmation_and_verifies_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "voice notes"
            tools = SystemTools(root, allowed_roots=(root,))
            prepared = tools.execute_typed_tool(
                "create_folder",
                path=str(target),
                explanation="Creates the requested folder.",
            )
            self.assertIsNotNone(prepared.pending)
            self.assertFalse(target.exists())
            result = tools.confirm(prepared.pending)
            self.assertTrue(target.is_dir())
        self.assertIn("verified", result.message)

    def test_copy_and_move_tools_do_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.txt"
            copied = root / "copied.txt"
            moved = root / "moved.txt"
            source.write_text("hello", encoding="utf-8")
            tools = SystemTools(root, allowed_roots=(root,))

            copy_pending = tools.execute_typed_tool(
                "copy_file", source=str(source), destination=str(copied)
            ).pending
            self.assertIsNotNone(copy_pending)
            tools.confirm(copy_pending)
            self.assertEqual("hello", copied.read_text(encoding="utf-8"))

            move_pending = tools.execute_typed_tool(
                "move_file", source=str(source), destination=str(moved)
            ).pending
            self.assertIsNotNone(move_pending)
            tools.confirm(move_pending)
            self.assertFalse(source.exists())
            self.assertEqual("hello", moved.read_text(encoding="utf-8"))

            with self.assertRaisesRegex(ValueError, "already exists"):
                tools.execute_typed_tool(
                    "copy_file", source=str(copied), destination=str(moved)
                )

    def test_command_is_only_prepared_until_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = SystemTools(Path(temp_dir))
            result = tools.prepare_command("echo hello")
            self.assertIsNotNone(result.pending)
            self.assertEqual("echo hello", result.pending.command_text)

    def test_generated_command_is_identified_in_pending_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = SystemTools(Path(temp_dir))
            result = tools.prepare_command(
                "dir", generated_by_model=True, explanation="Lists files."
            )
        self.assertIsNotNone(result.pending)
        self.assertTrue(result.pending.generated_by_model)
        self.assertIn("Ollama-generated", result.message)

    def test_generated_command_executes_as_direct_powershell_argument(self) -> None:
        supervisor = Mock()
        supervisor.run.return_value = ProcessExecution((), 0, "ok\n", "")
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = SystemTools(Path(temp_dir), process_supervisor=supervisor)
            pending = tools.prepare_command(
                "Get-ChildItem -LiteralPath '.'", generated_by_model=True
            ).pending
            self.assertIsNotNone(pending)
            tools.confirm(pending)
        argv = supervisor.run.call_args.args[0]
        self.assertTrue(argv[0].endswith("powershell.exe"))
        self.assertEqual("-Command", argv[-2])
        self.assertEqual("Get-ChildItem -LiteralPath '.'", argv[-1])

    def test_generated_command_cannot_nest_another_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = SystemTools(Path(temp_dir))
            result = tools.prepare_command(
                'powershell.exe -Command "Get-ChildItem"', generated_by_model=True
            )
        self.assertIsNone(result.pending)
        self.assertIn("nested shell", result.message)

    def test_confirmed_command_uses_backend_without_shell_flag(self) -> None:
        supervisor = Mock()
        supervisor.run.return_value = ProcessExecution((), 0, "hello\n", "")
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = SystemTools(Path(temp_dir), process_supervisor=supervisor)
            pending = tools.prepare_command("echo hello").pending
            self.assertIsNotNone(pending)
            result = tools.confirm(pending)

        self.assertIn("exit code 0", result.message)
        self.assertEqual("/c", supervisor.run.call_args.args[0][3])
        self.assertEqual("echo hello", supervisor.run.call_args.args[0][4])

    def test_cancelled_command_is_reported_as_failed(self) -> None:
        supervisor = Mock()
        supervisor.run.return_value = ProcessExecution(
            (), -9, "", "", cancelled=True
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = SystemTools(Path(temp_dir), process_supervisor=supervisor)
            pending = tools.prepare_command("echo hello").pending
            result = tools.confirm(pending)
        self.assertFalse(result.success)
        self.assertIn("stopped", result.message)

    def test_timeout_stops_command_tree(self) -> None:
        supervisor = Mock()
        supervisor.run.return_value = ProcessExecution(
            (), -9, "", "", timed_out=True
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = SystemTools(Path(temp_dir), process_supervisor=supervisor)
            pending = tools.prepare_command("echo hello").pending
            result = tools.confirm(pending)
        self.assertFalse(result.success)
        self.assertIn("time limit", result.message)

    def test_encoded_and_security_commands_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = SystemTools(Path(temp_dir))
            encoded = tools.prepare_command("powershell -EncodedCommand ZQBjAGgAbwA=")
            security = tools.prepare_command("Set-MpPreference -DisableRealtimeMonitoring $true")
        self.assertIsNone(encoded.pending)
        self.assertIn("encoded", encoded.message)
        self.assertIsNone(security.pending)
        self.assertIn("security", security.message)

    def test_disk_format_command_is_refused_even_when_nested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = SystemTools(Path(temp_dir))
            result = tools.prepare_command("cmd /c format D: /q")
        self.assertIsNone(result.pending)
        self.assertIn("disk", result.message)

    @patch("system_tools.webbrowser.open")
    def test_web_search_url_encodes_query(self, open_browser: Mock) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = SystemTools(Path(temp_dir))
            result = tools.search_web("CUDA 12 & Whisper")
        open_browser.assert_called_once_with(
            "https://www.google.com/search?q=CUDA+12+%26+Whisper", new=2
        )
        self.assertIn("Opened", result.message)

    @patch("system_tools.webbrowser.open")
    def test_website_rejects_embedded_credentials(self, open_browser: Mock) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = SystemTools(Path(temp_dir))
            result = tools.open_website("https://user:password@example.com")
        open_browser.assert_not_called()
        self.assertIn("Only normal", result.message)

    @patch("system_tools.SystemTools._winget_path", return_value=r"C:\winget.exe")
    def test_install_uses_exact_id_and_confirmation(
        self, _winget_path: Mock
    ) -> None:
        supervisor = Mock()
        supervisor.start_background.return_value = ActiveProcess(1234, "install", 0)
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = SystemTools(Path(temp_dir), process_supervisor=supervisor)
            prepared = tools.prepare_package_install("Mozilla.Firefox")
            supervisor.start_background.assert_not_called()
            self.assertIsNotNone(prepared.pending)
            result = tools.confirm(prepared.pending)
        argv = supervisor.start_background.call_args.args[0]
        self.assertIn("--exact", argv)
        self.assertIn("Mozilla.Firefox", argv)
        self.assertIn("started", result.message)

    def test_expired_action_is_not_executed(self) -> None:
        supervisor = Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = SystemTools(Path(temp_dir), process_supervisor=supervisor)
            pending = PendingSystemAction(
                kind="command",
                description="echo no",
                command_text="echo no",
                argv=None,
                expires_at=time.monotonic() - 1,
            )
            result = tools.confirm(pending)
        supervisor.run.assert_not_called()
        self.assertIn("expired", result.message)

    @patch("system_tools.os.startfile", create=True)
    def test_installed_app_refuses_weak_partial_shortcut_name(self, startfile: Mock) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            shortcut = root / "Example Editor.lnk"
            shortcut.touch()
            tools = SystemTools(root)
            with patch.object(tools, "_shortcut_roots", return_value=(root,)):
                result = tools.open_installed_app("Example Editor")
                missing = tools.open_installed_app("Example")
        startfile.assert_called_once_with(str(shortcut))
        self.assertIn("Opened", result.message)
        self.assertIn("could not find", missing.message)

    @patch("system_tools.os.startfile", create=True)
    def test_installed_app_ignores_generic_software_suffix(self, startfile: Mock) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            shortcut = root / "Playnite.lnk"
            shortcut.touch()
            tools = SystemTools(root)
            with patch.object(tools, "_shortcut_roots", return_value=(root,)):
                result = tools.open_installed_app("Playnite software")
        startfile.assert_called_once_with(str(shortcut))
        self.assertEqual("Opened Playnite.", result.message)

    @patch("system_tools.os.startfile", create=True)
    def test_installed_app_accepts_unique_high_confidence_speech_typo(
        self, startfile: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            shortcut = root / "Playnite.lnk"
            shortcut.touch()
            tools = SystemTools(root)
            with patch.object(tools, "_shortcut_roots", return_value=(root,)):
                result = tools.open_installed_app("Playnight software")
        startfile.assert_called_once_with(str(shortcut))
        self.assertEqual("Opened Playnite.", result.message)

    @patch("system_tools.os.startfile", create=True)
    def test_installed_app_refuses_ambiguous_fuzzy_match(self, startfile: Mock) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Playnite.lnk").touch()
            (root / "Playnight.lnk").touch()
            tools = SystemTools(root)
            with patch.object(tools, "_shortcut_roots", return_value=(root,)):
                result = tools.open_installed_app("Playnigt")
        startfile.assert_not_called()
        self.assertIn("could not find", result.message)

    @patch("system_tools.shutil.which")
    def test_terminal_alias_resolves_to_windows_terminal(self, which: Mock) -> None:
        which.side_effect = lambda name: r"C:\WindowsApps\wt.exe" if name == "wt.exe" else None
        with tempfile.TemporaryDirectory() as temp_dir:
            tools = SystemTools(Path(temp_dir))
            path = tools.find_installed_app("Windows Terminal")
        self.assertEqual(Path(r"C:\WindowsApps\wt.exe"), path)


if __name__ == "__main__":
    unittest.main()
