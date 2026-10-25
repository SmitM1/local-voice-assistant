from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ApprovalRequest:
    title: str
    summary: str
    details: str
    approve_label: str = "Approve"
    risk_level: str = "medium"
    affected_items: int | None = None


class ApprovalUI:
    """Small local modal used for action-time review; returns None if UI is unavailable."""

    def request(self, approval: ApprovalRequest) -> bool | None:
        try:
            import tkinter as tk
            from tkinter import ttk
        except ImportError:
            return None

        decision: bool | None = None
        try:
            root = tk.Tk()
            root.title(approval.title)
            root.attributes("-topmost", True)
            root.resizable(True, True)
            root.minsize(620, 380)

            frame = ttk.Frame(root, padding=16)
            frame.grid(row=0, column=0, sticky="nsew")
            root.rowconfigure(0, weight=1)
            root.columnconfigure(0, weight=1)
            frame.rowconfigure(2, weight=1)
            frame.columnconfigure(0, weight=1)

            heading = ttk.Label(
                frame,
                text=approval.summary,
                font=("Segoe UI", 12, "bold"),
                wraplength=760,
                justify="left",
            )
            heading.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

            warning = ttk.Label(
                frame,
                text=(
                    f"Risk: {approval.risk_level.upper()}"
                    + (
                        f"   |   Affected items: {approval.affected_items}"
                        if approval.affected_items is not None
                        else ""
                    )
                    + "\nReview every path and operation before approving."
                ),
                wraplength=760,
                justify="left",
            )
            warning.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))

            text = tk.Text(
                frame,
                wrap="word",
                font=("Consolas", 10),
                height=15,
                padx=10,
                pady=10,
            )
            text.insert("1.0", approval.details)
            text.configure(state="disabled")
            text.grid(row=2, column=0, columnspan=2, sticky="nsew")

            def finish(value: bool) -> None:
                nonlocal decision
                decision = value
                root.destroy()

            cancel = ttk.Button(frame, text="Cancel", command=lambda: finish(False))
            cancel.grid(row=3, column=0, sticky="e", padx=(0, 8), pady=(14, 0))
            approve = ttk.Button(
                frame,
                text=approval.approve_label,
                command=lambda: finish(True),
            )
            approve.grid(row=3, column=1, sticky="e", pady=(14, 0))

            root.protocol("WM_DELETE_WINDOW", lambda: finish(False))
            root.bind("<Escape>", lambda _event: finish(False))
            root.bind("<Return>", lambda _event: None)
            root.update_idletasks()
            width = max(root.winfo_width(), 720)
            height = max(root.winfo_height(), 460)
            x = max((root.winfo_screenwidth() - width) // 2, 0)
            y = max((root.winfo_screenheight() - height) // 3, 0)
            root.geometry(f"{width}x{height}+{x}+{y}")
            root.lift()
            root.focus_force()
            root.mainloop()
            return decision
        except Exception as exc:
            print(f"[approval-ui] unavailable: {exc}")
            try:
                root.destroy()
            except Exception:
                pass
            return None

    def select_one(self, title: str, summary: str, items: list[str]) -> str | None:
        if not items:
            return None
        if len(items) == 1:
            return items[0]
        try:
            import tkinter as tk
            from tkinter import ttk
        except ImportError:
            return None

        selection: str | None = None
        try:
            root = tk.Tk()
            root.title(title)
            root.attributes("-topmost", True)
            root.minsize(720, 420)
            frame = ttk.Frame(root, padding=16)
            frame.pack(fill="both", expand=True)
            ttk.Label(
                frame,
                text=summary,
                font=("Segoe UI", 11, "bold"),
                wraplength=760,
            ).pack(anchor="w", pady=(0, 10))
            listbox = tk.Listbox(frame, font=("Consolas", 10), selectmode="browse")
            for item in items:
                listbox.insert("end", item)
            listbox.pack(fill="both", expand=True)
            listbox.selection_set(0)

            def finish(value: str | None) -> None:
                nonlocal selection
                selection = value
                root.destroy()

            buttons = ttk.Frame(frame)
            buttons.pack(fill="x", pady=(12, 0))
            ttk.Button(buttons, text="Cancel", command=lambda: finish(None)).pack(
                side="right"
            )

            def choose() -> None:
                indexes = listbox.curselection()
                finish(items[indexes[0]] if indexes else None)

            ttk.Button(buttons, text="Use selected", command=choose).pack(
                side="right", padx=(0, 8)
            )
            listbox.bind("<Double-Button-1>", lambda _event: choose())
            root.protocol("WM_DELETE_WINDOW", lambda: finish(None))
            root.bind("<Escape>", lambda _event: finish(None))
            root.geometry("820x500")
            root.lift()
            root.focus_force()
            root.mainloop()
            return selection
        except Exception as exc:
            print(f"[approval-ui] selection unavailable: {exc}")
            try:
                root.destroy()
            except Exception:
                pass
            return None
