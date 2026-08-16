# Performance tracing

htail's normal interactive path does not enable profiling or write performance logs.
For a real-use trace, opt in for one run with `HTAIL_PERF_TRACE`:

```bash
HTAIL_PERF_TRACE=~/htail-perf.jsonl ht path/to/file
```

`HTAIL_PERF_TRACE=auto` writes to `~/.cache/htail/perf/htail-<pid>.jsonl`.

The JSONL trace is deliberately coarse. It emits one aggregate sample per second plus exceptional events when a render exceeds the 60 Hz frame budget (16.7 ms) or when a full-frame redraw is required. Samples include render time, pane/viewport cache activity, rectangular-write and scroll-region counts, terminal rows/bytes written, and arrow-repeat coalescing/acceleration counters.

This mode is intended for diagnosing intermittent redraws or slowdowns during normal terminal use. When `HTAIL_PERF_TRACE` is unset, the trace extension installs no render wrapper and performs no file I/O.
