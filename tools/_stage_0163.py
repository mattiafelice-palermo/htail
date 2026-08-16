from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one replacement target, found {count}")
    p.write_text(text.replace(old, new), encoding="utf-8")


replace_once(
    "src/htail_app/core.py",
    '''            report("Backing up current executable…")
            backup = target.with_name(target.name + ".bak")
            shutil.copy2(target, backup)
            report("Installing update…")
            os.replace(temp_path, target)
            temp_path = None
            return True, f"updated {target.name} {HTAIL_VERSION} → {release.version}" + (" (SHA-256 verified)" if expected_sha256 else "")
''',
    '''            report("Backing up current executable…")
            backup = target.with_name(target.name + ".bak")
            shutil.copy2(target, backup)
            report("Installing update…")
            os.replace(temp_path, target)
            temp_path = None

            # Do not report success merely because the wrapper was replaced.
            # Launch the installed wrapper from the same inherited environment
            # as an interactive restart and require both wrapper and bundled
            # application verification before considering the update complete.
            report("Verifying installed application…")
            verification_error: Optional[str] = None
            try:
                version_check = subprocess.run(
                    [sys.executable, str(target), "--version"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=20.0,
                    check=False,
                )
                expected_version = f"htail {release.version}"
                if version_check.returncode != 0 or version_check.stdout.strip() != expected_version:
                    detail = (
                        version_check.stderr.strip()
                        or version_check.stdout.strip()
                        or f"exit code {version_check.returncode}"
                    )
                    verification_error = f"version check failed: {detail}"
                else:
                    bundle_check = subprocess.run(
                        [sys.executable, str(target), "--bundle-self-test"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=20.0,
                        check=False,
                    )
                    if bundle_check.returncode != 0:
                        detail = (
                            bundle_check.stderr.strip()
                            or bundle_check.stdout.strip()
                            or f"exit code {bundle_check.returncode}"
                        )
                        verification_error = f"bundle self-test failed: {detail}"
            except Exception as exc:
                verification_error = str(exc)

            if verification_error is not None:
                try:
                    shutil.copy2(backup, target)
                except OSError as restore_exc:
                    return False, (
                        f"installed update failed verification ({verification_error}); "
                        f"backup restore also failed: {restore_exc}"
                    )
                return False, f"installed update failed verification ({verification_error}); restored backup"

            return True, f"updated {target.name} {HTAIL_VERSION} → {release.version}" + (" (SHA-256 verified)" if expected_sha256 else "")
''',
)

replace_once(
    "src/htail_app/app.py",
    '''    if args.bundle_self_test:
        backend = fuzzy_backend()
        if backend == "unavailable":
            print("htail bundle self-test failed: RapidFuzz unavailable", file=sys.stderr)
            return 1
        print(f"htail bundle self-test: {backend}")
        return 0
''',
    '''    if args.bundle_self_test:
        wrapper_version = os.environ.get("HTAIL_WRAPPER_VERSION")
        if wrapper_version and wrapper_version != VERSION:
            print(
                f"htail bundle self-test failed: wrapper {wrapper_version} loaded app {VERSION}",
                file=sys.stderr,
            )
            return 1
        active_app = os.environ.get("HTAIL_ACTIVE_APP")
        if active_app:
            loaded_app = Path(__file__).resolve().parents[1]
            expected_app = Path(active_app).expanduser().resolve()
            if loaded_app != expected_app:
                print(
                    f"htail bundle self-test failed: loaded app {loaded_app}, expected {expected_app}",
                    file=sys.stderr,
                )
                return 1
        backend = fuzzy_backend()
        if backend == "unavailable":
            print("htail bundle self-test failed: RapidFuzz unavailable", file=sys.stderr)
            return 1
        print(f"htail bundle self-test: app {VERSION}; {backend}")
        return 0
''',
)

replace_once(
    "src/htail_app/app.py",
    '''            elif stage.startswith("Installing") or stage.startswith("Replacing"):
                self.update_overall_progress = max(self.update_overall_progress, 0.99)
            else:
''',
    '''            elif stage.startswith("Installing") or stage.startswith("Replacing"):
                self.update_overall_progress = max(self.update_overall_progress, 0.99)
            elif stage.startswith("Verifying installed application"):
                self.update_overall_progress = max(self.update_overall_progress, 0.995)
            else:
''',
)
