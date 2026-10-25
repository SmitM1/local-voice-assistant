import time
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError

from gui_agent import (
    ActionPolicy,
    ElementSignature,
    ElementSnapshot,
    GUIAgent,
    GUIPlan,
    PendingAction,
    VisionPlanner,
    WindowObserver,
    WindowObservation,
    normalized_key_sequence,
)


def make_element(
    name: str = "Document",
    control_type: str = "Document",
    element_id: int = 1,
    is_password: bool = False,
) -> ElementSnapshot:
    return ElementSnapshot(
        element_id=element_id,
        name=name,
        control_type=control_type,
        automation_id="TextArea",
        bounds=(10, 10, 500, 400),
        enabled=True,
        visible=True,
        focused=True,
        is_password=is_password,
        value="",
        wrapper=Mock(),
    )


def make_observation(element: ElementSnapshot | None = None) -> WindowObservation:
    return WindowObservation(
        hwnd=42,
        title="Untitled - Notepad",
        process_name="notepad.exe",
        bounds=(0, 0, 800, 600),
        elements=(element or make_element(),),
        window_wrapper=Mock(),
    )


class GUIValidationTests(unittest.TestCase):
    def test_regular_browser_window_is_an_allowed_gui_target(self) -> None:
        WindowObserver._ensure_allowed("chrome.exe", "Video - YouTube")

    def test_password_manager_window_remains_blocked(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            WindowObserver._ensure_allowed("chrome.exe", "Google Password Manager")

    def test_action_requires_its_arguments(self) -> None:
        with self.assertRaises(ValidationError):
            GUIPlan(
                status="action",
                action_type="click_point",
                reason="Click a visible item.",
                spoken_response="Clicking.",
            )

    def test_non_action_cannot_smuggle_action(self) -> None:
        with self.assertRaises(ValidationError):
            GUIPlan(
                status="done",
                action_type="press_keys",
                keys=["CTRL", "A"],
                reason="Finished.",
                spoken_response="Done.",
            )

    def test_inactive_placeholder_values_are_discarded(self) -> None:
        plan = GUIPlan.model_validate(
            {
                "status": "action",
                "action_type": "type_text",
                "element_id": 1,
                "x": 0,
                "y": 0,
                "text": "hello",
                "keys": "",
                "direction": "",
                "amount": 0,
                "reason": "Enter text.",
                "spoken_response": "Typing.",
            }
        )
        self.assertIsNone(plan.amount)
        self.assertIsNone(plan.x)
        self.assertIsNone(plan.keys)

    def test_windows_and_alt_shortcuts_are_not_allowed(self) -> None:
        for keys in (["WIN", "R"], ["ALT", "F4"], ["CTRL", "ENTER"]):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                normalized_key_sequence(list(keys))

    def test_send_button_requires_confirmation(self) -> None:
        observation = make_observation(make_element("Send", "Button"))
        action = GUIPlan(
            status="action",
            action_type="click_element",
            element_id=1,
            reason="Click the requested button.",
            spoken_response="Sending.",
        )
        decision, _reason = ActionPolicy().evaluate("send this message", observation, action)
        self.assertEqual("confirm", decision)

    def test_password_field_is_blocked(self) -> None:
        observation = make_observation(make_element("[password field]", "Edit", is_password=True))
        action = GUIPlan(
            status="action",
            action_type="type_text",
            element_id=1,
            text="secret",
            reason="Enter text.",
            spoken_response="Typing.",
        )
        decision, _reason = ActionPolicy().evaluate("type text", observation, action)
        self.assertEqual("block", decision)

    def test_security_objective_is_blocked(self) -> None:
        observation = make_observation()
        action = GUIPlan(
            status="action",
            action_type="type_text",
            element_id=1,
            text="anything",
            reason="Type text.",
            spoken_response="Typing.",
        )
        decision, _reason = ActionPolicy().evaluate(
            "open powershell and run a command", observation, action
        )
        self.assertEqual("block", decision)

    @patch("gui_agent.requests.post")
    def test_planner_sends_image_only_for_screenshot_fallback(self, post: Mock) -> None:
        response = Mock()
        response.json.return_value = {
            "message": {
                "content": (
                    '{"status":"done","action_type":null,"element_id":null,'
                    '"x":null,"y":null,"text":null,"keys":null,"direction":null,'
                    '"amount":null,"reason":"Complete.","spoken_response":"Done."}'
                )
            }
        }
        post.return_value = response
        planner = VisionPlanner("http://127.0.0.1:11434/api/chat", "qwen3-vl:2b")
        planner.plan("inspect the window", make_observation())
        first_message = post.call_args.kwargs["json"]["messages"][1]
        self.assertNotIn("images", first_message)

        planner.plan("inspect the window", make_observation(), b"png-data")
        second_message = post.call_args.kwargs["json"]["messages"][1]
        self.assertIn("images", second_message)

    def test_non_loopback_planner_url_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback-only"):
            VisionPlanner("https://example.com/api/chat", "model")


class ConfirmationTests(unittest.TestCase):
    def test_single_text_field_write_finishes_after_verified_insert(self) -> None:
        before = make_observation(make_element())
        after_element = make_element()
        after_element = ElementSnapshot(
            **{**after_element.__dict__, "value": "hello"}
        )
        after = make_observation(after_element)
        observer = Mock()
        observer.observe.side_effect = [before, after]
        planner = Mock()
        planner.plan.return_value = GUIPlan(
            status="action",
            action_type="type_text",
            element_id=1,
            text="hello",
            reason="Write the requested text.",
            spoken_response="Typing.",
        )
        policy = Mock()
        policy.evaluate.return_value = ("allow", "low risk")
        executor = Mock()
        agent = GUIAgent(
            "http://127.0.0.1:11434/api/chat",
            "model",
            observer=observer,
            planner=planner,
            policy=policy,
            executor=executor,
            stop_requested=lambda: False,
        )
        result = agent.run_task("write hello")
        self.assertEqual("done", result.status)
        executor.execute.assert_called_once()
        planner.plan.assert_called_once()

    def test_expired_confirmation_is_not_executed(self) -> None:
        observer = Mock()
        planner = Mock()
        policy = Mock()
        executor = Mock()
        agent = GUIAgent(
            "http://127.0.0.1:11434/api/chat",
            "qwen3-vl:2b",
            observer=observer,
            planner=planner,
            policy=policy,
            executor=executor,
            stop_requested=lambda: False,
        )
        observation = make_observation(make_element("Delete", "Button"))
        action = GUIPlan(
            status="action",
            action_type="click_element",
            element_id=1,
            reason="Delete the selected item.",
            spoken_response="Deleting.",
        )
        pending = PendingAction(
            hwnd=observation.hwnd,
            title=observation.title,
            process_name=observation.process_name,
            action=action,
            summary="the action may delete",
            expires_at=time.monotonic() - 1,
            element_signature=ElementSignature.from_element(observation.elements[0]),
        )
        result = agent.confirm(pending)
        self.assertEqual("blocked", result.status)
        executor.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
