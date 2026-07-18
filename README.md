# k5qref

k5qref is a small Windows tray utility for finding and pasting saved snippets.
Press a global hotkey, search, and select an entry. k5qref copies the text and
can paste it back into the window you were using.

## Download for Windows

1. Open this repository's **Releases** page on GitHub.
2. Download `k5qref-windows-x64.zip` from the latest release.
3. Extract the ZIP to a folder you can keep, such as `Documents\k5qref`.
4. Run `k5qref.exe`. Its icon will appear in the Windows notification area.

Keep `config.json` beside `k5qref.exe`. The download includes a working sample
with two tabs and two populated groups in each tab. Edit that file to add your
own snippets. Windows SmartScreen may ask for confirmation because the app is
not code-signed.

## Using k5qref

- Press **Ctrl+F1** to open the picker.
- Type to search titles, text, and group names.
- Use **Up/Down** and **Enter**, or click an entry, to select it.
- Use **Left/Right** or click a tab to switch tabs.
- Press **Esc** to close without selecting anything.
- Right-click the tray icon to show the picker, edit or reload the config, or
  quit the app.

By default, a selected snippet is copied and pasted into the window that was
active before the picker opened. Set `auto_paste` to `false` for copy-only mode.

## Configuration

`config.json` is read from the same folder as the executable. Changes are
detected automatically within about two seconds. If the JSON is invalid, the
app keeps the last working configuration and displays the error.

```json
{
    "hotkey": "ctrl+f1",
    "auto_paste": true,
    "tabs": {
        "Developer": {
            "Git": [
                { "title": "Check status", "text": "git status --short" },
                { "title": "Recent commits", "text": "git log --oneline -10" }
            ],
            "Shell": [
                { "title": "List files", "text": "Get-ChildItem -Force" }
            ]
        },
        "Writing": {
            "Markdown": [
                { "title": "Link", "text": "[label](https://example.com)" }
            ],
            "Symbols": [
                { "title": "Right arrow", "text": "→" }
            ]
        }
    }
}
```

- `hotkey`: modifiers (`ctrl`, `alt`, `shift`, `win`) plus one key, such as
  `ctrl+f1`, `ctrl+shift+space`, or `win+insert`. Keys: `f1`–`f24`, letters,
  digits, and names like `space`, `tab`, `enter`, `insert`, `delete`, `home`,
  `end`, `page up`, `page down`, and the arrow keys.
- `auto_paste`: when `true`, k5qref returns focus and sends Ctrl+V. When
  `false`, it only copies the selected text.
- `tabs`: maps each tab name to its groups. Each group maps to a list of
  snippets containing a `title` and `text`.

A top-level `snippets` value is also supported for configurations without
tabs. It can contain grouped snippets or a flat list.

## Run from source

Python 3.10 or newer is recommended.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python main.py
```

## Build the Windows download

Run the included build script from PowerShell:

```powershell
.\tools\build-windows.ps1
```

It installs the required packages, compiles the app with PyInstaller, and
creates `release\k5qref-windows-x64.zip`. The ZIP contains:

```text
k5qref-windows-x64/
|-- k5qref.exe
|-- config.json
|-- README.md
`-- LICENSE
```

## Publishing a GitHub release

The workflow in `.github/workflows/windows-release.yml` builds the ZIP on
GitHub. It can be run manually from the **Actions** tab for a test artifact.
To publish a release, push a version tag:

```powershell
git tag v1.0.0
git push origin v1.0.0
```

GitHub will create the release and attach `k5qref-windows-x64.zip`. Generated
executables and ZIPs stay out of the source history.

## Notes

- Only one copy of k5qref runs at a time.
- To use the hotkey while an administrator-level app is focused, k5qref must
  also be run as administrator due to Windows input security.
- Some applications use a paste shortcut other than Ctrl+V. The snippet will
  still be available on the clipboard.

## License

Released under the [MIT License](LICENSE).
