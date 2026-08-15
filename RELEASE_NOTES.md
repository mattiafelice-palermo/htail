# htail 0.8.2

## New features

- When an updated file fits completely inside its pane, htail now shows the full current file and marks the changed rows with the cyan change gutter instead of showing only the changed fragment. Manual scrolling or update navigation still opens the retained update history.

## Bug fixes

- Mouse focus now reacts only to the button press, so the release event cannot move focus to whichever pane the pointer reached afterward.
- Queued keyboard and mouse events are drained in one UI frame, making repeated arrow-key scrolling and fast pane clicks responsive instead of advancing roughly once per watcher poll.
- Fixed ANSI state leakage from wrapped cyan change gutters that could turn apparently random Markdown continuation text cyan/bold.
- Modal backgrounds now reliably dim even when the underlying pane contains syntax-highlight ANSI resets.
- Fixed pane top-border width calculation so the top-right corner joins the right border without a one-cell gap.
