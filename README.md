# Nova Voice Pilot

A private, fully local Windows voice assistant using:

- `whisper.cpp` for speech recognition
- `llama3.2:3b` through Ollama for short conversational answers
- `qwen2.5-coder:3b` through Ollama for strict, local command planning
- a 4K-context `qwen3-vl:2b-instruct` Ollama profile for guarded foreground-window planning
- Piper for local speech output
- Windows UI Automation, with a target-window screenshot fallback

The vision model proposes one constrained action at a time. The Python controller
validates every proposal, keeps actions inside the current foreground window, and
observes the window again after each action. Screenshot fallback captures only that
window and is sent only to the loopback Ollama service.

The assistant can open installed apps, launch validated browser searches, search
`winget`, install an exact package ID, run a command you explicitly prefix with
`command:`, or ask Ollama to translate a vague system request into one command.
Commands and installs are printed for review and require a fresh spoken confirmation.
No command is executed directly from a model response, screenshot, webpage, or
document.

For common requests, Ollama now selects a typed Python tool instead of writing shell:

- list the largest files in a configured folder
- find files by name
- create a folder
- copy or move one file without overwriting an existing destination
- calculate a file's SHA-256 hash or open a configured file/folder
- open an installed application
- search/open the web, navigate structured public pages, download a reviewed file, or
  install an exact package ID

Typed filesystem tools are confined to the folders in `search_locations`. Read-only
inspection runs immediately; create, copy, move, and install actions pause for review
and confirmation, then verify their result.

Requests that need several verifiable filesystem actions can become a plan of up to
five steps. Plans support listing, finding, creating folders, copying, moving, hashing,
opening a path, and inspecting a public webpage. A
dashboard shows the original request, every tool, and every argument before the plan
starts. Say **“Nova, confirm”** to approve the complete preflighted plan or **“Nova,
cancel”** to discard it; no approval-button click is required. The plan stops immediately
if a step fails validation or cannot verify its result.
Later steps can consume an earlier step's output. When a search returns several paths,
the assistant opens a local chooser instead of letting the model guess which file you
meant.

Plans are preflighted before approval. Once started, progress is journaled after every
verified step. If a later step fails, the dashboard can offer a reverse-order rollback
for completed reversible changes. If Windows or the assistant stops unexpectedly, the
next launch reports the incomplete plan and never resumes it automatically.

A second constrained Ollama pass reviews proposed actions before spoken confirmation.
It is veto-only: it can block an unsafe or mismatched proposal, but cannot edit or execute
it. Its risk rating, summary, and exact operation details appear in the dashboard.

Every typed action and approved command is recorded in a local append-only JSONL audit
log. Folder creation, file copying, and file moving include verified undo metadata. Say
**undo last action** to preview and confirm the most recent reversible operation. Undo is
refused when a copied file changed or the original location is no longer safe.

This is deliberately not silent administrator access. Password fields, password
managers, authentication dialogs, Windows Security, security/privacy changes, and UAC
approval remain human-only. An installer may display UAC, but you must inspect and
approve it yourself. The foreground-window agent can control ordinary visible controls
in Chrome, Edge, Firefox, Brave, Opera, and desktop apps. It does not control terminals;
browser opening/search and command execution also have separate deterministic controllers.

## Setup

Create your local configuration, update its paths for your computer, then run setup:

```cmd
copy config.example.json config.json
```

```cmd
setup.cmd
```

Make sure Ollama is running, then start the assistant:

```cmd
start.cmd
```

`setup.cmd` also creates a **Nova Voice Pilot** desktop shortcut. The shortcut opens a
dark dashboard and tray icon without a terminal window. Closing the dashboard or choosing
**Turn off** from the tray stops listeners, speech, and tracked child processes. Use
`start-console.cmd` for debugging. The shortcut can be recreated with
`install-desktop-shortcut.cmd` or removed with `uninstall-desktop-shortcut.cmd`.

The dashboard includes **Input microphone** and **Output speaker** selectors. Choose the
devices and click **Save and apply devices**. The assistant atomically stores stable
host-API/device-name identifiers in `config.json`, restarts its audio engine, and restores
the same choices on later launches. If a saved USB or Bluetooth device is disconnected,
the assistant falls back to the Windows default without erasing the saved choice. Use
**Refresh device list** after connecting new hardware.

The same card has a live **Microphone Level** meter measured in dBFS. The colored fill
tracks the selected microphone and the white line is the speech-capture threshold. Drag
the line just above the room's steady background-noise level, then speak normally to
verify that the fill crosses it. The new threshold takes effect immediately and is saved
to `config.json` when the pointer is released.

The GUI model is installed by `setup.cmd`. The small local profile prevents the
model's native 256K context from exhausting this laptop's RAM. To create it manually:

```cmd
ollama pull qwen3-vl:2b-instruct && ollama create local-gui-vision -f Modelfile.qwen3-vl-4k
```

The supplied configuration uses hands-free wake-word mode. Start requests with
**Nova**; no push-to-talk key is needed. Say **“Nova, stop listening”** to enter sleep
mode and **“Nova, start listening”** to resume, or use the matching dashboard toggle.
Sleep mode ignores normal commands but keeps the local microphone path available solely
so the resume phrase can be recognized. Press **Esc** to quit.

For hands-free use, set `activation_mode` to `wake_word`. Audio is segmented locally
and Whisper must transcribe the configured `wake_word_phrase` at the beginning before
any request is dispatched. The included configuration enables this mode; no cloud
wake-word service is used. Whisper, Piper, package searches, and supervised commands run with the
Windows no-console flag, so background analysis does not flash Command Prompt windows.

Before audio is sent to Whisper, an optional local energy-based voice activity detector
removes leading/trailing silence and rejects recordings with no speech-level audio. Tune
the threshold from the dashboard (or edit `vad_rms_threshold`) if a quiet microphone is
clipped or background noise is retained;
set `vad_enabled` to `false` to pass the original recording through unchanged.
`wake_silence_seconds` defaults to 1.8 seconds, so short pauses stay in the same request;
`wake_max_utterance_seconds` defaults to 30 seconds.

Press **F9** at any time to stop the current plan or spoken response and clear pending
actions. Confirmed commands and package-manager processes are registered with a backend
process supervisor; F9, Esc, or "stop task" terminates the tracked process and its current
child process tree. If you misspoke while an action is awaiting confirmation, say
**correction:** followed by the replacement request.

Example requests:

- "Open Notepad"
- "Open Visual Studio Code"
- "Open Windows Terminal"
- "Search for invoice in Downloads"
- "Search the web for CUDA 12 documentation"
- "Open website ollama.com"
- "Inspect website https://example.com" (prints untrusted page text locally; it is not
  fed back as instructions)
- "Summarize current webpage" (uses a separate, tool-less local Ollama call and treats
  all page text as untrusted data)
- "Follow browser link 3" and "Go back in the browser" (operate on the assistant's
  structured browsing session)
- "Download https://example.com/report.pdf to C:\Users\me\Downloads\report.pdf"
  (requires approval, never runs the file, and supports verified undo)
- "Find app Firefox"
- "Install package Mozilla.Firefox" (then say "confirm")
- "Command: git status" (then say "confirm")
- "Open Chrome in kiosk mode" launches the installed, allowlisted Chrome executable
  directly with only `--kiosk`; this low-risk app launch does not require confirmation
- "Show me the five largest files in Downloads" (review Ollama's command, then confirm)
- "Create a folder named tax receipts on my Desktop" (review, then confirm)
- "Undo last action" (review, then confirm)
- "System, find the five biggest files in Downloads" (forces command planning when
  the foreground app would otherwise make the request ambiguous)
- "System, create an Archive folder in Downloads, find my invoice PDFs, and copy the
  selected invoice into Archive" (reviews a multi-step plan; ambiguous selections are
  rejected rather than guessed)
- "Dictate: The meeting has moved to three PM."
- "Nova, click the Replace button"
- With Notepad focused: "Write a short meeting follow-up email"
- "Nova, replace the first heading with Project Status"
- "Nova, play" or "Play the current video in Chrome" sends the reversible
  Windows Play/Pause media key directly, without relying on Ollama or visual clicking
- "Pause", "Resume", "Stop the current video", "Next track", and "Previous track"
  use the same deterministic Windows media-session controller
- "Play the current video, make it fullscreen, and reduce the volume a little" runs
  all three media controls in the spoken order
- "App, inspect current document" and "Nova, replace document with: ..." use
  semantic Notepad/VS Code adapters rather than screenshot guessing where possible
- "Explain recursion in one sentence"

For apps other than those in `gui_natural_apps`, start the request with
**Nova**. Say **confirm** or **cancel** when an action is paused. Press **Esc**
between steps to stop a GUI task.

## Configuration

Edit `config.json` to change the hotkey, models, GUI step limit, TTS setting,
approved applications, command working directory, search provider, or searchable
folders. Known application commands and search roots use exact allowlist keys. Other
apps are launched only from an exact installed Start Menu shortcut or executable on
`PATH`; spoken text cannot supply an executable path.

`input_audio_device` and `output_audio_device` contain the persisted device keys selected
in the dashboard. Empty strings mean the current Windows defaults. Piper's high-quality
local voice is cached in-process and streamed in chunks directly to the selected output
device, avoiding a new console process or full-file render for every update.

`permission_profile` controls the available capability tier:

- `read_only`: folder/file inspection, hashing, and public webpage inspection
- `files_and_apps`: read-only tools plus opening apps/paths and confirmed typed file edits
- `developer`: all of the above plus confirmed shell commands and exact-ID package installs

The supplied local configuration uses `developer`. Changing the profile takes effect at
both proposal and confirmation time, so a pending command cannot bypass a later downgrade.
`capability_overrides` can independently enable or disable `apps.control`, `apps.launch`,
`files.read`, `files.write`, `media.control`, `packages.install`, `shell.run`,
`web.download`, `web.open`, or `web.read`. Unknown capability names fail startup instead
of being ignored.
`reviewer_enabled` controls the second Ollama review pass. `journal_path` controls the
local audit log. `stop_key` defaults to F9.

Run a dependency and service check with:

```cmd
health-check.cmd
```

Run the regression suite with `run-tests.cmd`. `start.cmd` remains the one-click launcher.
To launch it automatically for the current Windows account, run `install-startup.cmd`;
remove that shortcut with `uninstall-startup.cmd`. Neither script needs administrator
rights.

## System-tool safety boundary

- `command:` accepts only the user's literal, single-line transcript. It is never
  rewritten by Ollama and always pauses for confirmation.
- Other requests may be classified by `command_model` as a system action. Ollama must
  prefer one of the controller's typed tools. Only when no typed tool fits may it
  return a validated JSON decision and one single-line PowerShell script. The
  controller labels it as model-generated, applies the same deny rules, prints the
  exact script, and pauses for confirmation. At confirmation it passes the script as
  a direct PowerShell argument rather than reparsing it through CMD.
- `find app NAME` runs a read-only `winget search`. Installation requires the exact
  package ID with `install package ID`, then a second confirmation.
- Browser searches and HTTP(S) sites can be opened, but browser form filling and
  account/authentication automation are outside the vision agent.
- Structured public-page navigation rejects embedded credentials and validates every
  redirect and followed link against local/private addresses. Pages are capped at 2 MB,
  scripts/styles are excluded from extracted text, and up to 200 semantic links receive
  stable numbers for the current page.
- The structured HTTP client ignores environment proxy settings, supports local domain
  allow/deny policy, resolves only globally routable addresses, and verifies the actual
  connected peer against public DNS to reduce proxy, DNS-rebinding, and SSRF risk.
- Downloads are capped at 250 MB, stream through a temporary file, never overwrite an
  existing destination, require approval, are hash-verified, and are never executed.
  Their audit record excludes URL query strings, and undo refuses to delete a file that
  changed after download.
- Credential extraction, encoded/evaluated shell payloads, elevation commands,
  anti-malware/firewall changes, disk formatting, boot changes, and secure-erasure
  commands are refused.
- A confirmation expires after `confirmation_timeout_seconds`.
- Commands run with no interactive standard input inside a Windows Job Object. The
  configured timeout, app shutdown, and emergency stop terminate tracked descendants as
  well as the root process, including descendants that outlive their parent; cancellation
  is written to the audit journal.

Set `ollama_command_planning_enabled` to `false` to disable natural-language command
generation while keeping explicit `command:` requests available. Model-generated
commands can be wrong: read the complete preview before saying **confirm**.

Single actions and complete multi-step plans use spoken confirmation while the listener
remains active. `approval_window_enabled` is now used only for result selection when a
plan finds several matching files and needs the user to choose one. `max_plan_steps` is
capped at five.

Set `tts_enabled` to `false` to disable spoken responses.

## Adapter status

The structured browser adapter supports opening/searching, public-page navigation,
numbered links, history, and reviewed downloads. For interactive or JavaScript-heavy
pages, the guarded foreground-window agent can use accessibility controls and a screenshot
fallback in supported browsers. Password entry, authentication dialogs, security/privacy
settings, terminal UI, and UAC remain blocked; consequential browser actions pause for
confirmation. The application adapters support configured executables, exact installed
shortcuts, default Windows file/folder handlers, dictation, guarded foreground-window
UI Automation, and semantic inspection/replacement adapters for Notepad, Explorer, and
VS Code. Deep DOM control,
password entry, security dialogs, UAC, and application-specific
Office APIs are intentionally not generalized as unrestricted access.
