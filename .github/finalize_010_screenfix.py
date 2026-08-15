from pathlib import Path

path = Path("benchmarks/reference_probe.py")
text = path.read_text(encoding="utf-8")
old = '''            combined_output = capture.getvalue()\n            incremental_output = combined_output[first_end:].encode("utf-8")\n        finally:\n            sys.stdout = old_stdout\n            close = getattr(application, "close_native_watch", None)\n            if close is not None:\n                close()\n        behavior["final_terminal_frame"] = emulate_terminal(combined_output, 120, 40)\n'''
new = '''            status_end = capture.tell()\n            incremental_output = capture.getvalue()[first_end:status_end].encode("utf-8")\n            # Exercise a body-changing redraw too. The optimized terminal\n            # command stream may differ, but after applying it to the previous\n            # frame the content area must be character-for-character equal to\n            # v0.9.0. Footer text is excluded because 0.10.0 intentionally\n            # adds new controls/version text there.\n            application.active_pane().scroll("UP", 5)\n            application.dirty = True\n            application.render()\n            combined_output = capture.getvalue()\n        finally:\n            sys.stdout = old_stdout\n            close = getattr(application, "close_native_watch", None)\n            if close is not None:\n                close()\n        behavior["final_terminal_body"] = emulate_terminal(combined_output, 120, 40)[:-2]\n'''
count = text.count(old)
if count != 1:
    raise RuntimeError(f"screen reference patch expected one match, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("terminal reference scope corrected")
