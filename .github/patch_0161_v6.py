from pathlib import Path


def replace_once(path, old, new, label):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "tools/build_release.py",
    '''    env_dir = _extract_app(payload)\n    abi = f"cp{{sys.version_info.major}}{{sys.version_info.minor}}"\n''',
    '''    env_dir = _extract_app(payload)\n    if sys.argv[1:] == ["--prepare-core"]:\n        return 0\n    abi = f"cp{{sys.version_info.major}}{{sys.version_info.minor}}"\n''',
    "prepare-core wrapper command",
)

replace_once(
    "src/htail_app/core.py",
    '''            os.chmod(temp_path, target.stat().st_mode)\n\n            report("Backing up current executable…")\n''',
    '''            os.chmod(temp_path, target.stat().st_mode)\n\n            report("Unpacking application…")\n            prepared = subprocess.run(\n                [sys.executable, str(temp_path), "--prepare-core"],\n                stdout=subprocess.DEVNULL,\n                stderr=subprocess.PIPE,\n                text=True,\n                timeout=20.0,\n                check=False,\n            )\n            if prepared.returncode != 0:\n                detail = prepared.stderr.strip() or f"exit code {prepared.returncode}"\n                return False, f"could not prepare updated application: {detail}"\n\n            report("Backing up current executable…")\n''',
    "prepare application before replacement",
)

replace_once(
    "src/htail_app/app.py",
    '''            elif stage.startswith("Runtime already"):\n                self.update_overall_progress = max(self.update_overall_progress, 0.94)\n            elif stage.startswith("Backing up"):\n                self.update_overall_progress = max(self.update_overall_progress, 0.96)\n            elif stage.startswith("Installing") or stage.startswith("Replacing"):\n                self.update_overall_progress = max(self.update_overall_progress, 0.98)\n''',
    '''            elif stage.startswith("Runtime already"):\n                self.update_overall_progress = max(self.update_overall_progress, 0.94)\n            elif stage.startswith("Unpacking application"):\n                self.update_overall_progress = max(self.update_overall_progress, 0.95)\n            elif stage.startswith("Backing up"):\n                self.update_overall_progress = max(self.update_overall_progress, 0.97)\n            elif stage.startswith("Installing") or stage.startswith("Replacing"):\n                self.update_overall_progress = max(self.update_overall_progress, 0.99)\n''',
    "application unpack progress",
)

path = Path("tests/test_update_runtime_0161.py")
text = path.read_text(encoding="utf-8")
old = '''        self.assertTrue(any(stage.startswith("Verifying runtime cp313") for stage in labels))\n        unpack = [(current, total) for stage, current, total in stages if stage.startswith("Unpacking runtime cp313")]\n'''
new = '''        self.assertTrue(any(stage.startswith("Verifying runtime cp313") for stage in labels))\n        self.assertIn("Unpacking application…", labels)\n        unpack = [(current, total) for stage, current, total in stages if stage.startswith("Unpacking runtime cp313")]\n'''
if text.count(old) != 1:
    raise RuntimeError("runtime update test insertion point missing")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
